# GPU-нода (vast.ai): vLLM + STT + TTS + агент

Нативное развёртывание без Docker: на vast.ai инстанс — это непривилегированный
контейнер, Docker-in-Docker запрещён. Долгоживущие процессы держит `supervisor`.

## Требования к инстансу

| Параметр | Минимум | Рекомендуется |
|----------|---------|---------------|
| GPU | 48 ГБ VRAM (с FP8) | RTX PRO 6000 96 ГБ |
| Диск | 48 ГБ (FP8) | ≥ 100 ГБ (bf16 + запас) |
| RAM | 32 ГБ | 64–96 ГБ |
| Образ | Vast base image (есть `uv`, `supervisor`, `ffmpeg`, CUDA) | — |

Модели: Qwen3-14B bf16 —29.6 ГБ, FP8 —16.3 ГБ, GigaAM-v3 —0.45 ГБ,
Qwen3-TTS 0.6B —2.5 ГБ. Окружения Python —~10–12 ГБ. На диске32 ГБ bf16 не
поместится, поэтому там используется FP8-чекпоинт (`LLM_MODEL_ID=Qwen/Qwen3-14B-FP8`).

**Персистентность:** на vast.ai stop/start сохраняет файловую систему, а
recycle/destroy — стирает. `/workspace` не volume, если инстанс создан без него.
Всё нужное после потери переустанавливается этим скриптом.

## Состав

```
deploy/gpu-node/
  install.sh            # установка окружений, моделей, supervisor-юнитов
  env.gpu.example       # шаблон /workspace/caller/.env
  run/{llm,stt,tts,agent}.sh
  supervisor/caller-*.conf
```

## Установка

На инстансе (root):

```bash
# 1. Копия репозитория (с локальной машины):
rsync -az --exclude .venv --exclude __pycache__ --exclude .git --exclude .env \
  -e "ssh -p <PORT>" ./ root@<HOST>:/workspace/caller/

# 2. .env (на инстансе):
cp /workspace/caller/deploy/gpu-node/env.gpu.example /workspace/caller/.env
chmod 600 /workspace/caller/.env
# заполнить HF_TOKEN, VLLM_API_KEY, LIVEKIT_*, при необходимости LLM_MODEL_ID

# 3. Установка (окружения + модели + supervisor):
/workspace/caller/deploy/gpu-node/install.sh
```

`install.sh` идемпотентен: повторный запуск доустанавливает недостающее.

## Управление

```bash
supervisorctl status                      # все сервисы
supervisorctl restart caller-llm          # llm | stt | tts | agent
tail -f /workspace/caller/logs/llm.log
```

Порты (только localhost): vLLM —8000, STT —8001, TTS —8002. Наружу ничего
не публикуется: агенту входящие соединения не нужны, он сам подключается к
LiveKit по `wss://`.

## Проверки

```bash
curl -s http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $VLLM_API_KEY"
curl -s http://127.0.0.1:8001/health
curl -s http://127.0.0.1:8002/health

# LLM-ответ (qwen3-14b):
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $VLLM_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-14b","messages":[{"role":"user","content":"Привет"}],"max_tokens":32}'

# TTS (WAV):
curl -s http://127.0.0.1:8002/v1/tts -H 'Content-Type: application/json' \
  -d '{"text":"Проверка связи","speaker":"Aiden","language":"ru"}' -o /tmp/tts.wav
```

## Агент

Агент (`caller-agent`) выключен из автозапуска: он стартует после того, как
поднят LiveKit на VPS и в `.env` прописаны `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`:

```bash
supervisorctl start caller-agent
```

Для исходящих звонков также нужен `SIP_OUTBOUND_TRUNK_ID` из
`deploy/livekit/` (создаётся там же).

## Обновление кода

Повторить `rsync` (без `.env`) и `supervisorctl restart caller-stt caller-tts caller-agent`
по необходимости.
