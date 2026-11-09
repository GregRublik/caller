# LiveKit Server + SIP на VPS

Минимальный стек для проекта: `livekit-server`, `livekit-sip`, Redis и Caddy
(TLS для `wss://`). Модели и агент сюда не входят — они запускаются на GPU-ноде
и подключаются к этому серверу по `wss://<домен>`.

## Требования

- Linux-сервер с публичным IPv4 (подойдёт Ubuntu 22.04/24.04).
- Docker Engine и плагин `docker compose` (`docker compose version`).
- Домен или поддомен, A-запись которого указывает на IP сервера. Если домена
  нет, для теста можно взять `IP-С-ДЕФИСАМИ.sslip.io` (например,
  `203-0-113-10.sslip.io`) — он автоматически резолвится в этот IP.
- Свободные порты 80/443 (Caddy). Если на сервере уже есть nginx, см.
  раздел «Без Caddy».

## Порты (открыть в файрволе)

| Порт          | Протокол | Зачем                                  |
|---------------|----------|----------------------------------------|
| 80            | TCP      | ACME-проверка Let's Encrypt            |
| 443           | TCP      | `wss://` для агента и браузеров        |
| 7881          | TCP      | WebRTC TCP fallback                    |
| 50000–60000   | UDP      | WebRTC media (agent ↔ LiveKit)         |
| 5060          | TCP+UDP  | SIP-сигнализация                       |
| 10000–20000   | UDP      | RTP (SIP ↔ провайдер/тестовая АТС)     |

Порт 7880 (API LiveKit) наружу **не открываем** — к нему обращается только
Caddy через localhost. Redis слушает только `127.0.0.1`.

Пример для UFW:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 7881/tcp
sudo ufw allow 50000:60000/udp
sudo ufw allow 5060/tcp
sudo ufw allow 5060/udp
sudo ufw allow 10000:20000/udp
sudo ufw enable
```

## Запуск

```bash
cd deploy/livekit
cp .env.example .env
# впиши LIVEKIT_API_KEY / LIVEKIT_API_SECRET из корневого .env проекта
# и LIVEKIT_DOMAIN
docker compose up -d
docker compose logs -f livekit sip caddy
```

Проверка:

```bash
curl -sS https://<LIVEKIT_DOMAIN>/          # ожидается "OK"
docker run --rm --network host livekit/livekit-cli:v2.19.3 \
  lk room list --url wss://<LIVEKIT_DOMAIN> \
  --api-key <key> --api-secret <secret>
```

Последняя команда должна вернуть пустой список комнат без ошибок — значит
TLS, ключи и сигнализация работают.

После этого в корневом `.env` проекта на GPU-ноде:

```
LIVEKIT_URL=wss://<LIVEKIT_DOMAIN>
LIVEKIT_API_KEY=<тот же key>
LIVEKIT_API_SECRET=<тот же secret>
```

## Без Caddy (уже есть nginx)

Убери сервис `caddy` из `docker-compose.yml` и добавь в nginx:

```nginx
server {
    listen 443 ssl;
    server_name livekit.example.com;

    ssl_certificate     /etc/letsencrypt/live/livekit.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/livekit.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:7880;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
}
```

## Проверка SIP без внешнего провайдера

LiveKit SIP не умеет REGISTER, поэтому «позвонить» в него можно только с
IP-авторизацией. Для тестов поднимаем рядом Asterisk как мини-АТС: софтфон
регистрируется на Asterisk, Asterisk шлёт INVITE в LiveKit SIP на
`127.0.0.1:5060`. Готовый стенд описан ниже в разделе
«Локальный стенд (без сервера)».

Пока SIP не настроен, агента можно прогнать через LiveKit Agents Playground
(WebRTC, без телефона): в playground выбирается self-hosted LiveKit и
указываются URL/ключи.

## Локальный стенд (без сервера)

Стенд для проверки SIP-связки на одной машине, пока сервер не готов:
`docker-compose.local.yml` (LiveKit + SIP + Redis) плюс Asterisk как тестовая
АТС (`../asterisk/`). Все сервисы работают с `network_mode: host` и видят
друг друга по `127.0.0.1`. На macOS проверено с OrbStack — host networking
пробрасывает UDP в обе стороны.

Отличия локального конфига от серверного: ключи `devkey`/`secret`, Redis на
порту `6380`, уменьшенные диапазоны портов (ICE 50000–50050, RTP
10000–10050), внешний IP отключён.

### 1. Запуск LiveKit + SIP

```bash
cd deploy/livekit
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml logs -f livekit sip
```

### 2. Запуск тестовой АТС Asterisk

```bash
cd deploy/asterisk
docker compose up -d --build
docker logs -f asterisk-asterisk-1
```

### 3. Транки и правила маршрутизации

```bash
cd deploy/livekit/sip
./setup-local.sh --outbound
```

Скрипт создаёт входящий транк (`allowed_addresses: 127.0.0.1`), тестовое
правило диспетчеризации без агента и исходящий транк на Asterisk
(`127.0.0.1:5062`). Полный список — командами `sip inbound list`,
`sip dispatch list`, `sip outbound list`.

Примечание: Asterisk определяет вызовы от LiveKit по IP — в
`../asterisk/config/pjsip.conf` для этого есть секция `[identify-livekit]`
(`match=127.0.0.1`). Username-идентификация не подходит: LiveKit шлёт в
From номер вызывающего (`numbers`), а не имя эндпоинта.

### 4. Софтфон

Поставь любой SIP-софтфон (Linphone, Zoiper, Telephone для macOS) и
зарегистрируй аккаунт:

| Параметр | Значение                       |
|----------|--------------------------------|
| Server   | `127.0.0.1` (или IP машины), порт `5062`, UDP |
| Username | `1000`                         |
| Password | `test1234`                     |
| Transport| UDP                            |

Это одноразовые локальные креды, наружу не выставляются.

### 5. Проверки

**Софтфон → LiveKit.** Набери `100` на софтфоне. Asterisk отправит INVITE в
LiveKit SIP, в LiveKit появится комната `sip-test-...` с одним участником:

```bash
docker run --rm --network host livekit/livekit-cli:v2.19.3 \
  --url http://127.0.0.1:7880 --api-key devkey --api-secret secret room list
```

**LiveKit → софтфон.** Сначала узнай ID исходящего транка (`sip outbound
list`), затем:

```bash
docker run --rm --network host livekit/livekit-cli:v2.19.3 \
  --url http://127.0.0.1:7880 --api-key devkey --api-secret secret \
  sip participant create --room out-test --identity caller \
  --to livekit --trunk <ST_...> --wait --timeout 15s
```

Софтфон должен зазвонить; после ответа канал соединяется с комнатой
`out-test`. Аудио пойдёт напрямую RTP ↔ WebRTC (в комнате, кроме SIP-
участника, пока никого нет — слушать некому, пока не запущен агент).

**Эхо-тест без софтфона.** Набери `9999` (extension `Answer()+Echo()` в
`../asterisk/config/extensions.conf`):

```bash
... sip participant create --room echo-test --identity caller \
  --to 9999 --trunk <ST_...> --wait --timeout 12s
```

Участник появляется в комнате и публикует аудио — значит сигнализация и RTP
медиа работают в обе стороны. Это же удобно для проверки агента: подключи
его к комнате `echo-test` через playground или агент и слушай себя.

### 6. Остановка

```bash
cd deploy/asterisk && docker compose down
cd deploy/livekit && docker compose -f docker-compose.local.yml down
```

## Если Docker Hub недоступен

Образы `livekit/livekit-server`, `livekit/sip`, `caddy`, `redis` можно
подтянуть через зеркало, если провайдер блокирует Docker Hub. Зеркало
настраивается в `/etc/docker/daemon.json`.
