# Агент на ноде (только агент)

Здесь устанавливается **только процесс агента**. LLM, STT и TTS — отдельные
сервисы, запускаются самостоятельно (проект `sporbita/voice-agent`, папки
`llm/`, `stt/`, `tts/`) и доступны агенту по HTTP. Агент их не запускает и не
останавливает.

```
Агент ──HTTP──► LLM  :8000 (vLLM, OpenAI API)
      ──HTTP──► STT  :8001 (GigaAM)
      ──HTTP──► TTS  :8002 (ESpeech)
      ──wss───► LiveKit (VPS)
```

## Требования

- Linux-хост с `uv` и `supervisor`;
- копия репозитория в `/workspace/caller`;
- запущенные сервисы LLM/STT/TTS (или их адреса в `.env`);
- доступный LiveKit с ключами.

## Установка

```bash
cp deploy/agent-node/env.agent.example /workspace/caller/.env
chmod 600 /workspace/caller/.env
# заполнить LIVEKIT_*, VLLM_API_KEY, при необходимости адреса сервисов

/workspace/caller/deploy/agent-node/install.sh
supervisorctl start caller-agent
```

## Проверки

```bash
curl -s http://127.0.0.1:8000/health   # LLM
curl -s http://127.0.0.1:8001/health   # STT
curl -s http://127.0.0.1:8002/health   # TTS
tail -f /workspace/caller/logs/agent.log
```

## Управление

```bash
supervisorctl status caller-agent
supervisorctl restart caller-agent
```

## Обновление кода

```bash
cd /workspace/caller && git pull
supervisorctl restart caller-agent
```
