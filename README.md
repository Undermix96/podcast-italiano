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

## Avvio rapido

_Istruzioni in arrivo non appena i container saranno pronti._

## Licenza

MIT — vedi [`LICENSE`](./LICENSE).
