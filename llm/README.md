# LLM — vLLM OpenAI-совместимый сервер

Отдельный процесс, как STT и TTS: агент общается с ним только по HTTP
(OpenAI API) и не запускает и не останавливает его. На GPU-ноде им управляет
supervisor как `caller-llm` (`autorestart=true`).

```
AgentSession ──HTTP (OpenAI API)──► vLLM :8000 ──► Qwen3-14B-FP8
```

## Запуск

```bash
# на GPU-ноде (нужен .env в /workspace/caller)
/workspace/caller/llm/serve.sh
```

Supervisor-юнит: `deploy/gpu-node/supervisor/caller-llm.conf`, обёртка:
`deploy/gpu-node/run/llm.sh`.

## Параметры

Задаются в `.env`:

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `LLM_MODEL_ID` | `Qwen/Qwen3-14B-FP8` | HF-репозиторий модели |
| `LLM_MODEL` | `qwen3-14b` | имя модели в API (`--served-model-name`) |
| `GPU_MEMORY_UTILIZATION` | `0.40` | доля VRAM под vLLM |
| `VLLM_API_KEY` | — | Bearer-ключ API (обязателен) |
| `VLLM_USE_DEEP_GEMM` | — | на Blackwell ставить `0` (обход ошибок DeepGEMM) |

Флаги фиксированы в `serve.sh`: `--max-model-len 8192`, `--enable-auto-tool-choice`,
`--tool-call-parser hermes`.

## Эндпоинты

| Метод | Назначение |
|---|---|
| `POST /v1/chat/completions` | чат (агент использует `stream=true`) |
| `GET /v1/models` | список моделей (`qwen3-14b`) |
| `GET /health` | готовность сервера |

Проверка:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $VLLM_API_KEY"
```

## Независимость от агента

- Процессы независимы: рестарт vLLM не останавливает `caller-agent`.
- Агент обращается к любому OpenAI-совместимому серверу: достаточно указать
  `LLM_BASE_URL`, `VLLM_API_KEY`, `LLM_MODEL` — код агента не меняется, LLM
  может жить на другой машине или в облаке.
- Если LLM недоступен, звонки продолжаются, но генерация ответов падает —
  перезапуск сервиса (`supervisorctl restart caller-llm`) возвращает всё в норму.
