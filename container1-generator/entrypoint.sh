#!/bin/bash
set -euo pipefail

LOG_FILE="${LOG_PATH:-/data/logs/podcast.log}"
LOG_DIR="$(dirname "$LOG_FILE")"
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# Modalità trigger manuale: CMD=run
# Esempio: docker compose run --rm generator run
# ---------------------------------------------------------------------------
if [ "${1:-}" = "run" ]; then
    echo "[entrypoint] Avvio manuale della pipeline..."
    exec /usr/local/bin/python /app/generate_episode.py
fi

# ---------------------------------------------------------------------------
# Modalità cron (default)
# ---------------------------------------------------------------------------

# Verifica che CRON_SCHEDULE sia definito
if [ -z "${CRON_SCHEDULE:-}" ]; then
    echo "[entrypoint] ERRORE: variabile CRON_SCHEDULE non definita nel .env" >&2
    exit 1
fi

# Genera il crontab reale sostituendo ${CRON_SCHEDULE} nel template.
# Un file in /etc/cron.d/ con permessi corretti viene letto automaticamente
# dal demone cron: non va (anzi non andrebbe) installato con `crontab file`,
# che si aspetta un formato senza il campo utente presente qui (root).
envsubst '${CRON_SCHEDULE}' \
    < /etc/cron.d/podcast-crontab.template \
    > /etc/cron.d/podcast

chmod 0644 /etc/cron.d/podcast

# I job lanciati da cron NON ereditano l'ambiente del container (Docker
# inietta le variabili solo per PID 1, non per i processi figli avviati
# dal demone cron). Catturiamo qui l'ambiente in un file sourceable, che
# il job cron caricherà esplicitamente prima di eseguire lo script
# (vedi crontab). declare -p gestisce correttamente il quoting anche per
# valori con spazi/caratteri speciali (es. RSS_FEEDS, OPENROUTER_API_KEY).
declare -p $(printenv | cut -d= -f1) > /app/container_env.sh 2>/dev/null || true
chmod 0644 /app/container_env.sh

echo "[entrypoint] Cron avviato con schedule: ${CRON_SCHEDULE}"
echo "[entrypoint] Timezone: $(cat /etc/timezone)"
echo "[entrypoint] Log applicativo: ${LOG_FILE}"
echo "[entrypoint] Per avviare manualmente: docker compose run --rm generator run"

# Resta in foreground redirigendo il log su stdout in tempo reale
# (cron scrive su file; tail -f permette a docker logs di mostrarlo)
touch "${LOG_FILE}"
cron -f &
exec tail -f "${LOG_FILE}"
