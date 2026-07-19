#!/usr/bin/env python3
"""
generate_episode.py
Pipeline completa: RSS → estrazione → deduplicazione semantica →
riassunto LLM → fusione script podcast → sintesi vocale XTTS →
aggiornamento feed Podcastify.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import feedparser
import requests
import trafilatura
import yaml
from dotenv import load_dotenv
from fastembed import TextEmbedding
from numpy import dot
from numpy.linalg import norm

# ---------------------------------------------------------------------------
# Costanti modificabili a mano (non da .env — sono soglie/limiti applicativi)
# ---------------------------------------------------------------------------

# Deduplicazione semantica
SIMILARITY_THRESHOLD_DISCARD = 0.90   # sopra → stessa notizia, scarta
SIMILARITY_THRESHOLD_UPDATE  = 0.75   # tra UPDATE e DISCARD → possibile sviluppo, chiedi all'LLM

# Controllo durata episodio
MAX_ARTICLES_PER_EPISODE = 12         # tetto sul numero di notizie elaborate
MAX_WORDS_EPISODE_SCRIPT  = 2500      # tetto di parole sullo script finale fuso

# Finestra storica deduplicazione (giorni)
DEDUP_WINDOW_DAYS = 7

# Ritenzione episodi pubblicati (giorni)
EPISODE_RETENTION_DAYS = 14

# Modello fastembed per gli embedding (multilingue, leggero, ONNX)
FASTEMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# ---------------------------------------------------------------------------
# Logging: sia su stdout che su file
# ---------------------------------------------------------------------------

def setup_logging(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("podcast")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    # stdout
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    # file
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


# ---------------------------------------------------------------------------
# Configurazione da .env
# ---------------------------------------------------------------------------

def load_config() -> dict:
    load_dotenv()

    def require(key: str) -> str:
        val = os.getenv(key)
        if not val:
            raise RuntimeError(f"Variabile d'ambiente obbligatoria mancante: {key}")
        return val

    feeds_raw = require("RSS_FEEDS")
    feeds = [f.strip() for f in feeds_raw.split(",") if f.strip()]

    return {
        "feeds":            feeds,
        "openrouter_key":   require("OPENROUTER_API_KEY"),
        "openrouter_model": os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-5"),
        "max_articles":     int(os.getenv("MAX_ARTICLES", MAX_ARTICLES_PER_EPISODE)),
        "podcast_title":    os.getenv("PODCAST_TITLE", "Rassegna Stampa"),
        "podcast_author":   os.getenv("PODCAST_AUTHOR", "Giulia"),
        "podcast_email":    os.getenv("PODCAST_EMAIL", "podcast@local.invalid"),
        "xtts_url":         os.getenv("XTTS_URL", "http://xtts:8000/v1/audio/speech"),
        "xtts_voice":       os.getenv("XTTS_VOICE", "it_voce"),
        "output_dir":       Path(os.getenv("OUTPUT_DIR", "/data/public/rassegna-stampa")),
        "yaml_path":        Path(os.getenv("YAML_PATH",  "/data/podcasts/rassegna-stampa-podcast.yaml")),
        "state_path":       Path(os.getenv("STATE_PATH", "/data/state/seen.json")),
        "log_path":         Path(os.getenv("LOG_PATH",   "/data/logs/podcast.log")),
    }


# ---------------------------------------------------------------------------
# Stato deduplicazione (JSON con finestra a 7 giorni)
# ---------------------------------------------------------------------------

def load_seen_state(state_path: Path) -> list[dict]:
    """
    Carica lo storico delle notizie già trattate e scarta quelle più
    vecchie di DEDUP_WINDOW_DAYS giorni.
    """
    if not state_path.exists():
        return []
    with open(state_path, encoding="utf-8") as f:
        data = json.load(f)
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)
    return [
        item for item in data
        if datetime.fromisoformat(item["date"]) > cutoff
    ]


def save_seen_state(state_path: Path, seen: list[dict]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Fetch RSS + estrazione testo
# ---------------------------------------------------------------------------

def fetch_articles(feed_urls: list[str], logger: logging.Logger) -> list[dict]:
    """
    Scarica tutti i feed RSS e tenta di estrarre il testo completo di
    ogni articolo. Skip silenzioso in caso di errore su singolo feed o articolo.
    """
    articles = []
    for url in feed_urls:
        logger.info(f"Feed: {url}")
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            logger.warning(f"Errore parsing feed {url}: {e}")
            continue

        for entry in parsed.entries:
            link = getattr(entry, "link", None)
            title = getattr(entry, "title", "").strip()
            if not link or not title:
                continue

            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6],
                                     tzinfo=timezone.utc).isoformat()

            try:
                downloaded = trafilatura.fetch_url(link)
                text = trafilatura.extract(downloaded,
                                           include_comments=False,
                                           include_tables=False) or ""
            except Exception as e:
                logger.warning(f"Estrazione fallita per {link}: {e}")
                text = getattr(entry, "summary", "") or ""

            articles.append({
                "guid":      getattr(entry, "id", link),
                "title":     title,
                "url":       link,
                "text":      text,
                "published": published,
            })

    logger.info(f"Articoli recuperati dai feed: {len(articles)}")
    return articles


# ---------------------------------------------------------------------------
# Deduplicazione semantica
# ---------------------------------------------------------------------------

def cosine_similarity(a, b) -> float:
    denom = norm(a) * norm(b)
    if denom == 0:
        return 0.0
    return float(dot(a, b) / denom)


def deduplicate(
    candidates: list[dict],
    seen: list[dict],
    embedder: TextEmbedding,
    logger: logging.Logger,
) -> tuple[list[dict], list[dict]]:
    """
    Classifica i candidati in:
    - new:    notizia nuova, da trattare normalmente
    - update: probabile sviluppo di una storia già vista (0.75–0.90)
              → richiede valutazione LLM
    - skip:   duplicato (>0.90), scartato subito

    Restituisce (new_articles, update_candidates).
    """
    # Testi da embeddare: titolo + primo paragrafo (più discriminante della pagina intera)
    def fingerprint(a: dict) -> str:
        excerpt = " ".join(a["text"].split()[:80])
        return f"{a['title']}. {excerpt}"

    candidate_texts = [fingerprint(c) for c in candidates]
    candidate_embeddings = list(embedder.embed(candidate_texts))

    seen_embeddings = []
    if seen:
        seen_texts = [f"{s['title']}. {s['excerpt']}" for s in seen]
        seen_embeddings = list(embedder.embed(seen_texts))

    new_articles   = []
    update_candidates = []

    for i, candidate in enumerate(candidates):
        emb = candidate_embeddings[i]
        max_sim = 0.0
        most_similar_seen = None

        for j, seen_emb in enumerate(seen_embeddings):
            sim = cosine_similarity(emb, seen_emb)
            if sim > max_sim:
                max_sim = sim
                most_similar_seen = seen[j]

        if max_sim >= SIMILARITY_THRESHOLD_DISCARD:
            logger.info(f"SKIP (sim={max_sim:.2f}): {candidate['title'][:60]}")
        elif max_sim >= SIMILARITY_THRESHOLD_UPDATE:
            logger.info(f"UPDATE? (sim={max_sim:.2f}): {candidate['title'][:60]}")
            candidate["_similar_to"] = most_similar_seen
            candidate["_similarity"] = max_sim
            candidate["_embedding"] = emb.tolist()
            update_candidates.append(candidate)
        else:
            logger.info(f"NUOVO (sim={max_sim:.2f}): {candidate['title'][:60]}")
            candidate["_embedding"] = emb.tolist()
            new_articles.append(candidate)

    return new_articles, update_candidates


# ---------------------------------------------------------------------------
# OpenRouter: chiamate LLM
# ---------------------------------------------------------------------------

def openrouter_chat(
    messages: list[dict],
    api_key: str,
    model: str,
    logger: logging.Logger,
    max_tokens: int = 1024,
) -> Optional[str]:
    """
    Chiama /chat/completions via OpenRouter. Restituisce il testo della
    risposta o None in caso di errore (log del problema, non eccezione).
    """
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization":  f"Bearer {api_key}",
                "Content-Type":   "application/json",
                "HTTP-Referer":   "https://local.podcast/",
                "X-Title":        "podcast-italiano",
            },
            json={
                "model":      model,
                "messages":   messages,
                "max_tokens": max_tokens,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Errore chiamata OpenRouter: {e}")
        return None


def summarize_article(
    article: dict,
    api_key: str,
    model: str,
    logger: logging.Logger,
) -> Optional[str]:
    """Genera un riassunto in italiano per un singolo articolo."""
    prompt = f"""Il tuo compito è trasformare questo articolo in un riassunto \
che un conduttore di podcast possa leggere ad alta voce parola per parola.

Titolo: {article['title']}
Testo: {article['text'][:3000]}

REGOLE:
- Scrivi SOLO il testo del riassunto, senza prefissi, titoli o commenti meta.
- Italiano corretto e naturale, tono discorsivo, adatto all'ascolto.
- Lunghezza: 150-250 parole.
- Prima persona, come se il conduttore condividesse direttamente la notizia.
- Nessuna introduzione né saluto finale.
- Inizia direttamente con la notizia."""

    return openrouter_chat(
        [{"role": "user", "content": prompt}],
        api_key, model, logger, max_tokens=512,
    )


def evaluate_update(
    article: dict,
    api_key: str,
    model: str,
    logger: logging.Logger,
) -> Optional[str]:
    """
    Chiede all'LLM se un articolo (simile a uno già trattato) contiene
    sviluppi reali rispetto alla versione precedente.
    Se sì, restituisce un breve aggiornamento da leggere; se no, None.
    """
    similar = article.get("_similar_to", {})
    prompt = f"""Hai già raccontato questa notizia nel podcast:
Titolo precedente: {similar.get('title', 'N/D')}
Estratto precedente: {similar.get('excerpt', 'N/D')}

Ora hai questa notizia simile:
Titolo: {article['title']}
Testo: {article['text'][:2000]}

Domanda: questa notizia contiene sviluppi o informazioni nuove e rilevanti \
rispetto alla precedente, oppure è sostanzialmente la stessa storia?

Se NON ci sono sviluppi rilevanti, rispondi esattamente con la parola: SKIP

Se ci sono sviluppi rilevanti, scrivi un breve aggiornamento in italiano \
(80-150 parole) che un conduttore possa leggere ad alta voce, iniziando con \
una frase tipo "Aggiornamento su [tema]:" e descrivendo solo le novità, \
senza ripetere ciò che era già noto. Nessun prefisso aggiuntivo."""

    result = openrouter_chat(
        [{"role": "user", "content": prompt}],
        api_key, model, logger, max_tokens=300,
    )
    if result and result.strip().upper() == "SKIP":
        return None
    return result


def compose_episode_script(
    summaries: list[str],
    podcast_title: str,
    podcast_author: str,
    api_key: str,
    model: str,
    logger: logging.Logger,
) -> Optional[str]:
    """
    Chiamata LLM finale: fonde tutti i riassunti in un unico script
    podcast scorrevole con intro, transizioni naturali e chiusura.
    Rispetta MAX_WORDS_EPISODE_SCRIPT.
    """
    summaries_block = "\n\n---\n\n".join(
        f"[Notizia {i+1}]\n{s}" for i, s in enumerate(summaries)
    )

    prompt = f"""Sei il conduttore del podcast "{podcast_title}".
Il tuo nome è {podcast_author}.

Di seguito trovi i riassunti delle notizie di oggi, separati da "---".
Il tuo compito è trasformarli in un unico script podcast da leggere ad alta \
voce, scorrevole e piacevole da ascoltare.

REGOLE:
- Inizia con una breve introduzione originale (non ripetere sempre la stessa \
  formula) che accoglie l'ascoltatore e anticipa i temi del giorno.
- Collega le notizie con transizioni naturali (non "Passiamo alla notizia \
  numero 2" — trova connessioni tematiche o semplicemente frasi di raccordo).
- Chiudi con un saluto breve e caldo.
- Lunghezza totale: MASSIMO {MAX_WORDS_EPISODE_SCRIPT} parole.
- Scrivi SOLO il testo dello script, senza indicazioni di regia, titoli \
  di sezione o note.
- Italiano naturale, tono informale ma professionale.

RIASSUNTI:
{summaries_block}"""

    return openrouter_chat(
        [{"role": "user", "content": prompt}],
        api_key, model, logger,
        max_tokens=MAX_WORDS_EPISODE_SCRIPT * 2,  # margine per la tokenizzazione
    )


# ---------------------------------------------------------------------------
# Sintesi vocale (openedai-speech / XTTS-v2)
# ---------------------------------------------------------------------------

def synthesize_audio(
    script_text: str,
    output_path: Path,
    xtts_url: str,
    xtts_voice: str,
    logger: logging.Logger,
) -> bool:
    """
    Invia lo script a openedai-speech e scrive l'mp3 su disco.
    Timeout lungo perché XTTS su CPU può impiegare decine di minuti.
    Restituisce True se il file è stato scritto correttamente.
    """
    logger.info(f"Sintesi vocale in corso (potrebbe richiedere 30-90 minuti)...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.post(
            xtts_url,
            headers={"Content-Type": "application/json"},
            json={
                "model":           "tts-1-hd",
                "input":           script_text,
                "voice":           xtts_voice,
                "response_format": "mp3",
                "speed":           1.0,
            },
            timeout=5400,  # 90 minuti
        )
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
        size_mb = output_path.stat().st_size / 1024 / 1024
        logger.info(f"Audio scritto: {output_path} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        logger.error(f"Errore sintesi vocale: {e}")
        return False


# ---------------------------------------------------------------------------
# Podcastify: aggiornamento YAML episodi
# ---------------------------------------------------------------------------

def update_podcastify_yaml(
    yaml_path: Path,
    output_dir: Path,
    mp3_path: Path,
    podcast_title: str,
    podcast_author: str,
    podcast_email: str,
    episode_title: str,
    episode_description: str,
    logger: logging.Logger,
) -> None:
    """
    Aggiunge il nuovo episodio allo YAML di Podcastify e rimuove quelli
    più vecchi di EPISODE_RETENTION_DAYS giorni (mp3 su disco + voce nello YAML).
    Il formato YAML segue le specifiche di fnayou/podcastify:
      - name        → slug del podcast (corrisponde al nome cartella in public/)
      - author-name / author-email → metadati iTunes
      - episodes[].pub_date → data ISO 8601
    """
    yaml_path.parent.mkdir(parents=True, exist_ok=True)

    # Slug del podcast: basename del file YAML senza il suffisso -podcast.yaml
    podcast_slug = yaml_path.stem.replace("-podcast", "")

    # Carica YAML esistente o crea struttura base
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    # Struttura minima compatibile fnayou/podcastify
    data.setdefault("name",         podcast_slug)
    data.setdefault("title",        podcast_title)
    data.setdefault("author-name",  podcast_author)
    data.setdefault("author-email", podcast_email)
    data.setdefault("description",  f"Rassegna stampa quotidiana — {podcast_title}")
    data.setdefault("language",     "it")
    data.setdefault("explicit",     False)
    data.setdefault("episodes",     [])

    now = datetime.now(timezone.utc)

    new_episode = {
        "file":        mp3_path.name,
        "title":       episode_title,
        "description": episode_description,
        "pub_date":    now.isoformat(),
        "guid":        str(uuid.uuid4()),
    }
    data["episodes"].insert(0, new_episode)

    # Pulizia episodi scaduti
    cutoff = now - timedelta(days=EPISODE_RETENTION_DAYS)
    kept = []
    for ep in data["episodes"]:
        ep_date = datetime.fromisoformat(ep["pub_date"])
        if ep_date > cutoff:
            kept.append(ep)
        else:
            old_mp3 = output_dir / ep["file"]
            if old_mp3.exists():
                old_mp3.unlink()
                logger.info(f"Rimosso episodio scaduto: {old_mp3.name}")
    data["episodes"] = kept

    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
    logger.info(f"YAML Podcastify aggiornato: {yaml_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg    = load_config()
    logger = setup_logging(cfg["log_path"])
    logger.info("=== Avvio generazione episodio ===")

    # 1. Stato deduplicazione
    seen = load_seen_state(cfg["state_path"])
    logger.info(f"Notizie in memoria (ultimi {DEDUP_WINDOW_DAYS} giorni): {len(seen)}")

    # 2. Fetch articoli
    articles = fetch_articles(cfg["feeds"], logger)
    if not articles:
        logger.error("Nessun articolo recuperato dai feed. Uscita.")
        sys.exit(1)

    # 3. Deduplicazione semantica
    logger.info("Caricamento modello embedding...")
    embedder = TextEmbedding(FASTEMBED_MODEL)

    new_articles, update_candidates = deduplicate(articles, seen, embedder, logger)
    logger.info(f"Nuovi: {len(new_articles)} | Possibili aggiornamenti: {len(update_candidates)}")

    # 4. Valutazione aggiornamenti via LLM
    accepted_updates = []
    for candidate in update_candidates:
        result = evaluate_update(candidate, cfg["openrouter_key"],
                                 cfg["openrouter_model"], logger)
        if result:
            logger.info(f"Aggiornamento accettato: {candidate['title'][:60]}")
            accepted_updates.append({"article": candidate, "summary": result})
        else:
            logger.info(f"Aggiornamento scartato: {candidate['title'][:60]}")

    # 5. Applica tetto max articoli
    all_new = new_articles[:cfg["max_articles"]]
    # Gli update riempiono eventuale spazio residuo
    slots_left = cfg["max_articles"] - len(all_new)
    accepted_updates = accepted_updates[:slots_left]

    # 6. Riassunto articoli nuovi
    summaries = []
    for article in all_new:
        summary = summarize_article(article, cfg["openrouter_key"],
                                    cfg["openrouter_model"], logger)
        if summary:
            summaries.append(summary)
        else:
            logger.warning(f"Riassunto fallito per: {article['title'][:60]}")

    # Aggiunge gli update già riassunti
    for item in accepted_updates:
        summaries.append(item["summary"])

    if not summaries:
        logger.error("Nessun riassunto disponibile. Uscita.")
        sys.exit(1)

    # 7. Fusione script finale
    logger.info(f"Fusione di {len(summaries)} notizie in script podcast...")
    script = compose_episode_script(
        summaries,
        cfg["podcast_title"],
        cfg["podcast_author"],
        cfg["openrouter_key"],
        cfg["openrouter_model"],
        logger,
    )
    if not script:
        logger.error("Generazione script fallita. Uscita.")
        sys.exit(1)

    word_count = len(script.split())
    logger.info(f"Script generato: {word_count} parole")
    if word_count > MAX_WORDS_EPISODE_SCRIPT:
        logger.warning(
            f"Script supera il budget ({word_count} > {MAX_WORDS_EPISODE_SCRIPT} parole). "
            "Considera di ridurre MAX_ARTICLES_PER_EPISODE o MAX_WORDS_EPISODE_SCRIPT."
        )

    # 8. Sintesi audio
    date_str  = datetime.now().strftime("%Y-%m-%d")
    mp3_name  = f"rassegna-stampa-{date_str}.mp3"
    mp3_path  = cfg["output_dir"] / mp3_name
    cfg["output_dir"].mkdir(parents=True, exist_ok=True)

    ok = synthesize_audio(script, mp3_path, cfg["xtts_url"], cfg["xtts_voice"], logger)
    if not ok:
        logger.error("Sintesi audio fallita. Uscita.")
        sys.exit(1)

    # 9. Aggiorna YAML Podcastify
    episode_title       = f"{cfg['podcast_title']} — {date_str}"
    episode_description = f"Rassegna stampa del {date_str}: {len(summaries)} notizie."
    update_podcastify_yaml(
        cfg["yaml_path"],
        cfg["output_dir"],
        mp3_path,
        cfg["podcast_title"],
        cfg["podcast_author"],
        cfg["podcast_email"],
        episode_title,
        episode_description,
        logger,
    )

    # 10. Aggiorna stato deduplicazione
    now = datetime.now(timezone.utc)
    for article in all_new:
        excerpt = " ".join(article["text"].split()[:80])
        seen.append({
            "title":     article["title"],
            "excerpt":   excerpt,
            "url":       article["url"],
            "date":      now.isoformat(),
            "embedding": article.get("_embedding", []),
        })
    for item in accepted_updates:
        a = item["article"]
        excerpt = " ".join(a["text"].split()[:80])
        seen.append({
            "title":     a["title"],
            "excerpt":   excerpt,
            "url":       a["url"],
            "date":      now.isoformat(),
            "embedding": a.get("_embedding", []),
        })
    save_seen_state(cfg["state_path"], seen)

    logger.info("=== Episodio generato con successo ===")


if __name__ == "__main__":
    main()
