# LLM — vLLM (Qwen3-14B-FP8)

OpenAI-совместимый сервер для агента. Отдельного репозитория не требует: это
стандартный `vllm serve`, конфигурация лежит здесь.

```
Агент ──HTTP (OpenAI API)──► vLLM :8000 ──► Qwen3-14B-FP8
```

## Docker Compose (обычный GPU-хост)

```bash
cd llm
cp .env.example .env
chmod 600 .env
# заполнить HF_TOKEN и VLLM_API_KEY

docker compose up -d
docker compose logs -f llm
```

Порт публикуется только на localhost (`127.0.0.1:8000`). Модель скачивается
в named volume `hf-cache` при первом старте.

## Без Docker (vast.ai и подобные контейнеры)

В таких окружениях Docker-in-Docker запрещён. Поставьте vLLM в любой venv и
запустите скрипт:

```bash
cd llm
cp .env.example .env   # заполнить
vllm serve --help >/dev/null   # проверить, что vllm доступен
./serve.sh
```

`serve.sh` читает `llm/.env` (или `LLM_ENV_FILE`) и запускает:

```
vllm serve $LLM_MODEL_ID --host 127.0.0.1 --port 8000
  --served-model-name $LLM_MODEL --max-model-len 8192
  --gpu-memory-utilization $GPU_MEMORY_UTILIZATION
  --enable-auto-tool-choice --tool-call-parser hermes --api-key $VLLM_API_KEY
```

## Проверка

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $VLLM_API_KEY"

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $VLLM_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-14b","messages":[{"role":"user","content":"Привет"}],
       "max_tokens":32,"stream":false}'
```

## Настройка агента

В `.env` агента (`deploy/agent-node/env.agent.example`) должны совпадать:

| Переменная агента | Значение |
|---|---|
| `LLM_BASE_URL` | `http://127.0.0.1:8000/v1` |
| `VLLM_API_KEY` | тот же ключ, что в `llm/.env` |
| `LLM_MODEL` | `--served-model-name` (по умолчанию `qwen3-14b`) |

## Замечания

- `--max-model-len 8192` достаточно для голосового диалога; больше — больше
  KV-кэша на сессию.
- `GPU_MEMORY_UTILIZATION`: 0.90 если GPU только под LLM, 0.40–0.50 если на
  той же карте живут STT/TTS.
- На Blackwell (RTX PRO 6000) DeepGEMM падал на этой модели — в `.env.example`
  выставлены `VLLM_USE_DEEP_GEMM=0` и `VLLM_MOE_USE_DEEP_GEMM=0`.
- Модель квантована в FP8 (~16 ГБ весов); для bf16 нужен диск 64 ГБ+.
