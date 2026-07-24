#!/usr/bin/env bash
# build.sh — build e push dell'immagine podcast-italiano-generator su DockerHub.
#
# Uso:
#   ./build.sh <versione> <canale>
#
# Esempi:
#   ./build.sh 1.0.0 latest    → tagga :1.0.0 e :latest, poi fa push di entrambi
#   ./build.sh 1.1.0-rc1 dev   → tagga :1.1.0-rc1 e :dev, poi fa push di entrambi
#
# Prerequisiti:
#   - Docker installato e running
#   - docker login eseguito (credenziali DockerHub valide)
#   - GENERATOR_IMAGE impostato nel .env (es. tuousername/podcast-italiano-generator)
#     oppure passato come variabile d'ambiente:
#     GENERATOR_IMAGE=tuousername/podcast-italiano-generator ./build.sh 1.0.0 latest

set -euo pipefail

# ---------------------------------------------------------------------------
# Argomenti
# ---------------------------------------------------------------------------
if [ $# -ne 2 ]; then
    echo "Uso: $0 <versione> <canale>"
    echo "Esempi:"
    echo "  $0 1.0.0 latest"
    echo "  $0 1.1.0-rc1 dev"
    exit 1
fi

VERSION="$1"
CHANNEL="$2"

# Valida canale
if [[ "$CHANNEL" != "latest" && "$CHANNEL" != "dev" ]]; then
    echo "ERRORE: il canale deve essere 'latest' o 'dev' (ricevuto: '$CHANNEL')"
    exit 1
fi

# ---------------------------------------------------------------------------
# Leggi GENERATOR_IMAGE da .env se non già impostato nell'ambiente
# ---------------------------------------------------------------------------
if [ -z "${GENERATOR_IMAGE:-}" ]; then
    ENV_FILE="$(dirname "$0")/.env"
    if [ ! -f "$ENV_FILE" ]; then
        echo "ERRORE: GENERATOR_IMAGE non impostato e .env non trovato."
        echo "Copia .env.example in .env e imposta GENERATOR_IMAGE, oppure esporta la variabile:"
        echo "  GENERATOR_IMAGE=tuousername/podcast-italiano-generator ./build.sh $VERSION $CHANNEL"
        exit 1
    fi
    # Estrae solo GENERATOR_IMAGE dal .env, senza eseguire il file
    GENERATOR_IMAGE=$(grep -E '^GENERATOR_IMAGE=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
    if [ -z "$GENERATOR_IMAGE" ]; then
        echo "ERRORE: GENERATOR_IMAGE non trovato nel .env"
        exit 1
    fi
fi

# Rimuove eventuale tag già presente nell'immagine base (es. :latest dal .env)
IMAGE_BASE="${GENERATOR_IMAGE%%:*}"

TAG_VERSION="${IMAGE_BASE}:${VERSION}"
TAG_CHANNEL="${IMAGE_BASE}:${CHANNEL}"

DOCKERFILE_DIR="$(dirname "$0")/container1-generator"

# ---------------------------------------------------------------------------
# Riepilogo prima di procedere
# ---------------------------------------------------------------------------
echo ""
echo "=============================="
echo "  podcast-italiano — build"
echo "=============================="
echo "  Dockerfile : ${DOCKERFILE_DIR}/Dockerfile"
echo "  Tag versione: ${TAG_VERSION}"
echo "  Tag canale  : ${TAG_CHANNEL}"
echo ""
read -r -p "Procedere con build e push? [y/N] " confirm
if [[ "${confirm,,}" != "y" ]]; then
    echo "Annullato."
    exit 0
fi
echo ""

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
echo "[1/3] Build immagine..."
docker build \
    --platform linux/amd64 \
    --label "org.opencontainers.image.version=${VERSION}" \
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --label "org.opencontainers.image.source=https://github.com/tuousername/podcast-italiano" \
    -t "${TAG_VERSION}" \
    -t "${TAG_CHANNEL}" \
    "${DOCKERFILE_DIR}"

echo ""
echo "[2/3] Push ${TAG_VERSION}..."
docker push "${TAG_VERSION}"

echo ""
echo "[3/3] Push ${TAG_CHANNEL}..."
docker push "${TAG_CHANNEL}"

echo ""
echo "=============================="
echo "  Build completata"
echo "  ${TAG_VERSION}"
echo "  ${TAG_CHANNEL}"
echo "=============================="
echo ""
echo "Per aggiornare il deploy:"
echo "  GENERATOR_IMAGE=${TAG_VERSION} docker compose pull generator"
echo "  docker compose up -d generator"
