# AGENTS.md

Questo documento descrive il progetto **podcast-italiano**: a cosa serve, come è
strutturato, quali decisioni tecniche sono state prese (e perché), e come un
agente IA deve comportarsi quando lavora su questo repository.

Va tenuto aggiornato: se una modifica cambia un endpoint, una struttura dati o
una decisione progettuale, questo file (e il `README.md`) vanno aggiornati
nella stessa modifica.

---

## 0. Cos'è questo progetto

Uno stack Docker self-hosted che, una volta al giorno, genera automaticamente
un episodio podcast in italiano a partire dalle ultime notizie online:

1. Legge una lista configurabile di feed RSS di siti di notizie italiani.
2. Estrae il testo completo dei nuovi articoli.
3. Scarta le notizie già trattate nei giorni precedenti (deduplicazione
   semantica, non solo per URL/GUID).
4. Riassume le notizie nuove e fonde tutto in un unico script da podcast,
   scorrevole e con transizioni naturali, usando un LLM via OpenRouter.
5. Sintetizza lo script in audio con una voce italiana clonata, tramite
   XTTS-v2 (self-hosted, nessun servizio cloud a pagamento per il TTS).
6. Pubblica l'episodio come feed RSS podcast standard, ascoltabile da
   qualunque podcast client (l'uso previsto è Audiobookshelf).

Vincoli hardware di partenza: host Docker con CPU Intel i5-10600 (iGPU
integrata UHD 630, nessuna GPU dedicata). L'intera pipeline gira su CPU; un
tempo di generazione totale di 1-1.5 ore per un episodio di 10-20 minuti è
accettabile (gira di notte via cron).

---

## 1. Istruzioni operative per l'agente AI

### 1.1 Pianifica prima di agire — autorizzazione obbligatoria

**Non modificare mai file senza autorizzazione esplicita dell'utente.**

1. Analizza la richiesta e identifica tutti i file coinvolti
2. Esponi il piano: cosa cambia, dove, perché
3. Attendi conferma esplicita prima di scrivere qualsiasi file

### 1.2 Preferisci modifiche chirurgiche al refactoring

- Usa `str_replace` per porzioni specifiche invece di riscrivere l'intero file
- Riscrivi un file intero solo se la modifica tocca più del 60% del contenuto
- Quando riscrivi un file intero, segnalalo e spiega perché

### 1.3 Revisione obbligatoria dopo ogni modifica

1. Rileggi ogni blocco modificato nel contesto del file completo
2. Cerca attivamente errori di sintassi, logica, coerenza
3. Verifica nomi di funzioni, variabili, endpoint tra tutti i file toccati
4. Se non trovi errori, dillo esplicitamente

### 1.4 Non inventare — chiedi o cerca

- Non inventare risposte plausibili e presentarle come certe
- Chiedi se il dubbio riguarda una scelta progettuale
- Cerca online se il dubbio riguarda un fatto tecnico verificabile

### 1.5 File critici — attenzione massima

| File | Rischio |
|---|---|
| `container1-generator/generate_episode.py` | Logica centrale della pipeline (fetch, deduplicazione, riassunto, TTS, scrittura YAML). Un errore qui può generare episodi sbagliati, duplicati, o silenziosamente non generare nulla. |
| `container1-generator/crontab` / `entrypoint.sh` | Se lo scheduling si rompe, l'intero sistema smette di produrre episodi senza errori visibili se non nei log. |
| `.env` | Contiene la chiave API OpenRouter e la configurazione delle fonti. Non va mai committato con valori reali (solo `.env.example`). |
| `docker-compose.yml` | Definisce rete/volumi condivisi tra i 3 container; un errore nei nomi host o nei volumi rompe la comunicazione tra i servizi senza errori applicativi chiari. |
| `xtts-config/voice_to_speaker.yaml` | Se il nome voce non corrisponde a quello usato in `generate_episode.py`/`.env`, la sintesi vocale fallisce. |
| `podcastify/podcasts/*.yaml` | Storico degli episodi pubblicati. Va aggiornato in modo incrementale: sovrascriverlo per errore cancella la cronologia del podcast. |

### 1.6 Aggiorna la documentazione insieme al codice

Ogni volta che una modifica cambia endpoint, strutture dati, o decisioni
progettuali, aggiorna le sezioni corrispondenti di questo file e di
`README.md`.

---

## 2. Architettura

Tre container Docker, nessun orchestratore separato:

```
┌─────────────────────────────┐      ┌──────────────────┐      ┌──────────────┐
│ container1-generator         │      │ xtts              │      │ podcastify   │
│ (Python + cron)              │─────▶│ (openedai-speech, │      │ (RSS feed +  │
│ fetch RSS → estrazione →     │ HTTP │  XTTS-v2, CPU)    │      │  hosting mp3)│
│ dedup → riassunto OpenRouter │◀─────│                   │      │              │
│ → fusione script → TTS →     │ mp3  └──────────────────┘      └──────────────┘
│ scrittura mp3 + yaml         │──────────────────────────────────────▶ (file su volume condiviso)
└─────────────────────────────┘
```

- **container1-generator** è responsabile di tutta la logica: non c'è un
  orchestratore esterno. Contiene anche il cron interno che decide quando
  partire.
- **xtts** (immagine `openedai-speech`) espone solo l'API di sintesi vocale
  `/v1/audio/speech` (compatibile OpenAI). Non sa nulla di RSS, LLM o
  scheduling.
- **podcastify** serve gli mp3 e il feed RSS risultante. Legge solo file
  (mp3 + YAML) scritti da container1 su un volume condiviso; non comunica
  direttamente con gli altri due container.

---

## 3. Decisioni tecniche prese e motivazione

| Area | Decisione | Motivazione |
|---|---|---|
| Riassunto/fusione testo | OpenRouter (modello configurabile, es. `anthropic/claude-sonnet-5`) | Costo trascurabile per il volume di token coinvolto (~1-4 €/mese); qualità di scrittura in italiano nettamente superiore a un modello locale eseguibile su questo hardware. |
| TTS | XTTS-v2 via `openedai-speech`, self-hosted, CPU-only | Nessuna GPU dedicata disponibile (solo iGPU Intel UHD 630, poco supportata per inferenza). Il tempo di generazione (stimato 30-90 min per episodio) è accettabile per una generazione notturna via cron. |
| Formato audio da XTTS | mp3 diretto (`response_format: mp3`) | Evita la dipendenza da ffmpeg e uno step di conversione in container1. |
| Deduplicazione notizie | Embedding semantico (`fastembed`, libreria leggera basata su ONNX, no PyTorch) + confronto per similarità coseno, finestra storica di 7 giorni | Un confronto per URL/GUID non basta: la stessa notizia appare su più siti con URL diversi, o viene ripubblicata il giorno dopo da un sito più lento. Le soglie di similarità sono variabili impostate in cima allo script (facilmente modificabili senza toccare la logica). |
| Storage stato deduplicazione | File JSON | Volume di dati piccolo (poche settimane di notizie), non giustifica un database. |
| Gestione "notizia in fascia intermedia" (probabile aggiornamento di una storia nota) | L'LLM valuta se ci sono sviluppi reali; se sì genera un breve aggiornamento, altrimenti scarta | Evita sia di perdere sviluppi importanti di una storia, sia di ripetere la stessa notizia pari pari. |
| Formato script podcast finale | Fusione LLM di tutti i riassunti in un unico testo con transizioni naturali (non concatenazione secca dei singoli riassunti) | Effetto podcast reale invece di un bollettino letto a spezzoni. Costa una chiamata API aggiuntiva, trascurabile. |
| Controllo durata episodio | Doppio tetto: numero massimo di notizie/giorno **e** budget massimo di parole sullo script finale | Nessuno dei due singolarmente garantisce una durata costante (poche notizie molto lunghe, o molte notizie brevi, sfuggirebbero a un solo limite). |
| Nessuna notizia nuova in un giorno | Genera comunque un episodio breve con gli aggiornamenti minori disponibili | Evita giorni senza episodio, mantenendo comunque bassa la ridondanza. |
| Gestione errori per singolo feed/articolo | Skip e continua (log dell'errore, non interrompe l'intera generazione) | Un sito irraggiungibile non deve bloccare la generazione dell'episodio con le notizie degli altri siti. |
| Scheduling | Cron interno al container1 (non un container scheduler esterno tipo Ofelia) | Un solo pezzo in più nello stack per un flusso lineare non giustifica un orchestratore/scheduler separato. |
| Fuso orario cron | `Europe/Rome` | L'orario configurato in `.env` deve corrispondere all'ora locale attesa dall'utente. |
| Trigger manuale | Comando/entrypoint dedicato oltre al cron | Necessario per testare la pipeline senza aspettare lo scheduling. |
| Log | Sia su stdout (visibili con `docker logs`) sia su file dentro il container | Il cron di default scarta stdout: va reindirizzato esplicitamente per essere debuggabile sia in tempo reale che a posteriori. |
| Ritenzione episodi pubblicati | 14 giorni | Evita crescita illimitata dello storage mantenendo comunque un archivio recente disponibile. |
| Configurazione fonti/cron/chiavi | File `.env` (non hardcoded nello script) | Permette di modificare fonti RSS, orario di generazione e credenziali senza toccare il codice. |

---

## 4. Struttura del repository (pianificata)

```
podcast-italiano/
├── AGENTS.md                          # questo file
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── docker-compose.yml
├── container1-generator/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── crontab
│   ├── entrypoint.sh
│   └── generate_episode.py
├── xtts-config/
│   ├── voice_to_speaker.yaml
│   └── voices/                        # non versionato: contiene il campione vocale
└── podcastify/
    ├── podcasts/                      # YAML episodi, generato/aggiornato da container1
    └── public/                        # mp3 serviti + feed RSS, generato da Podcastify
```

I file marcati "generato/pianificato" non esistono ancora: verranno creati
nei prossimi passi, uno alla volta, con approvazione esplicita prima di
ciascuna scrittura (vedi §1.1).

---

## 5. Stato di avanzamento

- [x] Decisioni architetturali e tecniche definite (questo documento)
- [x] Struttura base repository (`.gitignore`, `LICENSE`, `README.md`)
- [ ] `container1-generator/generate_episode.py`
- [ ] `container1-generator/Dockerfile`, `crontab`, `entrypoint.sh`
- [ ] `xtts-config/voice_to_speaker.yaml` (voce italiana da configurare con
      campione audio fornito dall'utente)
- [ ] `docker-compose.yml`
- [ ] `.env.example`
- [ ] Test end-to-end della pipeline completa
