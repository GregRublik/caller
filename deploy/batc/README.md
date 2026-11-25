# Exolve BATC: исходящий обзвон через Asterisk

Схема:

```
клиент/PSTN ⇄ Exolve BATC (номер 74951347654) ⇄ Asterisk ⇄ LiveKit SIP ⇄ агент
```

Asterisk держит SIP-регистрацию сотрудника BATC (LiveKit SIP не умеет REGISTER),
LiveKit SIP общается с Asterisk по loopback, агент работает без изменений.

## 1. Сотрудник для агента

В `.env` должны быть `MTC_API_KEY` и `MTC_ATC_URL`. Скрипт создаёт отдельного
сотрудника `AI Sales Agent` (админский аккаунт не затрагивается), генерирует
пароль и записывает учётные данные в `.env`:

```bash
.venv/bin/python scripts/batc.py create-agent
```

Повторный запуск безопасен: если `BATC_AGENT_LOGIN` уже задан, скрипт
остановится. Для второго сотрудника нужен `--force`.

Если BATC отвечает `403 BLOCKED`, запустите ту же команду с того IP, который
разрешён в настройках интеграции (например, с CRM-сервера
`152.228.116.100`), или создайте сотрудника вручную в ЛК BATC и впишите
`BATC_AGENT_LOGIN` / `BATC_AGENT_PASSWORD` в `.env`.

## 2. Asterisk

```bash
cd deploy/asterisk
docker compose --env-file ../../.env up -d --build
docker logs -f asterisk-asterisk-1
```

Проверка регистрации:

```bash
docker exec asterisk-asterisk-1 asterisk -rx 'pjsip show registrations'
```

Ожидается `batc-reg ... Registered`. Если статус `Rejected`, проверьте
`BATC_SIP_DOMAIN`/`BATC_SIP_PORT` (по умолчанию `sip.exolve.ru:5060`) и пароль.

### Если регистрация отклонена (403 Forbidden)

Сервер ответил, но не разрешил регистрацию. Проверьте в ЛК:

- включён ли у сотрудника приём звонков по SIP (в API это `calls_forwarding.sip`);
- SIP-сервер, порт и транспорт — ровно как в карточке SIP-телефона;
- SIP-логин: если он отличается от логина сотрудника, задайте `BATC_SIP_LOGIN`;
  если auth-username отличается от логина — задайте `BATC_SIP_AUTH_USER`;
- нет ли ограничения по IP: BATC API с этого же адреса отвечал `403 BLOCKED`,
  поэтому SIP-регистрация с него тоже может быть закрыта. В этом случае
  регистрируйтесь с разрешённого сервера (например, `152.228.116.100`).

Если в ЛК указан TLS и порт `5061`, текущий шаблон использует UDP; для TLS
нужно добавить TLS-транспорт в `pjsip.conf` — это отдельная правка.


Порты на VPS: `5062/udp`, `5062/tcp`, `21000-21100/udp` (RTP Asterisk), плюс
порты LiveKit. BATC должен дозвониться до Asterisk по публичному IP и порту из
REGISTER Contact; если провайдер разрешает только `5060`, Asterisk придётся
перенести на отдельный IP/VPS или освободить `5060`.

## 3. Транки LiveKit

На VPS, из `deploy/livekit/sip`:

```bash
LIVEKIT_URL=http://127.0.0.1:7880 \
LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... ./setup-batc.sh
```

Скрипт создаёт входящий транк (Asterisk → LiveKit), dispatch-правило агента и
исходящий транк (LiveKit → Asterisk → BATC). Полученный `ST_...` впишите в
`.env` как `SIP_OUTBOUND_TRUNK_ID` и перезапустите агента.

## 4. Проверка

```bash
.venv/bin/python scripts/dial.py +7...
```

В логах агента должно появиться `dialing ***1234 via trunk ST_...`, затем
участник `sip-...`. Проверьте двустороннее аудио, CLI и запись (локальный
рекордер агента работает независимо от `record` в BATC).

## 5. Вебхуки BATC (опционально)

`http://152.228.116.100:8100` сейчас не отвечает. Для `history`/`event`/
`contact`/`rating` нужен HTTP-сервис на этом адресе и `crm_token` из настроек
интеграции BATC. Это отдельный этап; на голосовой тракт не влияет.

## Безопасность

- Ключ BATC после настройки перевыпустите: он был опубликован в чате.
- Учётные данные SIP и ключи хранятся только в `.env` (файл в `.gitignore`).
- Asterisk слушает публичный SIP-порт: оставьте только нужные IP в
  `[identify-batc]`, не включайте `anonymous` в `endpoint_identifier_order`.
