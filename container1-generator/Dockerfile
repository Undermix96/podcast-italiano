FROM python:3.11-slim

# ---------------------------------------------------------------------------
# Dipendenze di sistema
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        cron \
        curl \
        tzdata \
        gettext-base \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Timezone (configurabile via TZ nel docker-compose, default Europe/Rome)
# ---------------------------------------------------------------------------
ENV TZ=Europe/Rome
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime && echo ${TZ} > /etc/timezone

# ---------------------------------------------------------------------------
# Dipendenze Python
# ---------------------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Pre-scarica il modello fastembed al build (evita il download al primo run
# notturno, che potrebbe essere lento o fallire senza rete)
# ---------------------------------------------------------------------------
RUN python -c "\
from fastembed import TextEmbedding; \
TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# ---------------------------------------------------------------------------
# Script applicativo e entrypoint
# ---------------------------------------------------------------------------
COPY generate_episode.py .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Il crontab viene generato a runtime dall'entrypoint sulla base di CRON_SCHEDULE
COPY crontab /etc/cron.d/podcast-crontab.template
RUN chmod 0644 /etc/cron.d/podcast-crontab.template

# ---------------------------------------------------------------------------
# Volumi attesi (montati dal docker-compose)
# ---------------------------------------------------------------------------
# /data/public/   → mp3 generati (condiviso con podcastify)
# /data/podcasts/ → YAML episodi (condiviso con podcastify)
# /data/state/    → seen.json deduplicazione (persistente)
# /data/logs/     → log applicativi
# /app/.env       → configurazione (montato read-only)

ENTRYPOINT ["/entrypoint.sh"]
