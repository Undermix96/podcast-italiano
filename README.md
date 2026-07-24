# podcast-italiano

Stack Docker self-hosted che genera automaticamente, una volta al giorno, un
episodio podcast in italiano a partire dalle ultime notizie online: fetch RSS,
riassunto e fusione in uno script tramite LLM (via OpenRouter), sintesi vocale
con XTTS-v2 self-hosted, pubblicazione come feed podcast standard.

Per il contesto completo del progetto, le decisioni tecniche prese e le
istruzioni per chi (o cosa) contribuisce al codice, vedi [`AGENTS.md`](./AGENTS.md).

## Stato

Progetto in costruzione. Vedi la sezione "Stato di avanzamento" in
`AGENTS.md` per cosa è già stato fatto e cosa manca.

## Requisiti

- Docker + Docker Compose
- Una chiave API [OpenRouter](https://openrouter.ai/)
- Un campione audio (6-30 secondi, pulito) della voce italiana da clonare per XTTS
- Account DockerHub (solo per chi fa la build — non necessario per il deploy)

## Flusso build → deploy

Le due fasi sono separate: la **build** avviene sulla macchina di sviluppo e
pubblica l'immagine su DockerHub; il **deploy** avviene sull'host Docker di
produzione e si limita a fare il pull.

### 1. Build e push (macchina di sviluppo)

```bash
# Prima volta: accedi a DockerHub
docker login

# Build e push con tag versione + canale
./build.sh 1.0.0 latest
# oppure per una release candidata:
./build.sh 1.1.0-rc1 dev
```

`build.sh` taglia l'immagine `generator` con entrambi i tag (`:<versione>` e
`:<canale>`) e fa il push su DockerHub. Le immagini `xtts` e `podcastify` sono
pubbliche upstream e non richiedono build.

### 2. Configurazione (prima del primo deploy)

```bash
# Copia il template e compila i valori obbligatori
cp .env.example .env
# Imposta almeno: GENERATOR_IMAGE, OPENROUTER_API_KEY, RSS_FEEDS

# Prepara il campione vocale italiano per XTTS
ffmpeg -i tua-voce.mp3 -ac 1 -ar 22050 -t 20 xtts-config/voices/it_voce.wav
```

### 3. Deploy (host Docker di produzione)

```bash
# Pull di tutte le immagini (inclusa quella appena publishata)
docker compose pull

# Avvio stack completo
docker compose up -d

# Verifica che i container siano running
docker compose ps

# Segui i log del generator in tempo reale
docker compose logs -f generator
```

### 4. Test manuale della pipeline

```bash
# Avvia la generazione subito, senza aspettare il cron
docker compose run --rm generator run
```

### 5. Aggiornamento immagine generator

```bash
# Sulla macchina di sviluppo: nuova build e push
./build.sh 1.1.0 latest

# Sull'host di produzione: pull e riavvio del solo container generator
docker compose pull generator
docker compose up -d generator
```

## Feed RSS per Audiobookshelf

Una volta avviato lo stack, il feed è disponibile all'indirizzo:

```
http://<IP-HOST>:<PODCASTIFY_HOST_PORT>/rassegna-stampa.xml
```

Aggiungilo come podcast personalizzato in Audiobookshelf.

## Licenza

MIT — vedi [`LICENSE`](./LICENSE).
