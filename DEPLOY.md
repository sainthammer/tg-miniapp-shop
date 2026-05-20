# Деплой XTINCT Shop на VPS

## Стек
- **Приложение:** Flask + aiogram 3, запущен через Gunicorn (4 потока, 1 воркер)
- **База данных:** SQLite (файл `shop.db`, примонтирован с хоста)
- **Контейнер:** Docker + Docker Compose
- **Веб-сервер:** Nginx (SSL termination → proxy → порт 8080)

---

## Требования к серверу
- Debian 11 (bullseye)
- 1 vCPU / 512 MB RAM (минимум)
- Белый IP
- Домен или поддомен, направленный A-записью на IP сервера

---

## Шаг 1. Подключиться к серверу

```bash
ssh root@YOUR_SERVER_IP
```

---

## Шаг 2. Установить Docker

На Debian 11 пакет `docker.io` из стандартных репозиториев устаревший — ставим из официального репозитория Docker.

```bash
# Зависимости
sudo apt update
sudo apt install -y ca-certificates curl gnupg

# GPG-ключ Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Репозиторий Docker
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Установить Docker Engine + Compose plugin
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo systemctl enable --now docker
```

Проверить:
```bash
docker --version
docker compose version
```

---

## Шаг 3. Загрузить проект на сервер

**Вариант A — через Git (по SSH):**

Сначала создать SSH-ключ на сервере и добавить его в GitHub:

```bash
# Сгенерировать ключ (Enter три раза, без пароля)
ssh-keygen -t ed25519 -C "deploy@xtinct-shop"

# Показать публичный ключ — скопировать его
cat ~/.ssh/id_ed25519.pub
```

Открыть в браузере: **GitHub → Settings → SSH and GPG keys → New SSH key**, вставить скопированный ключ.

Проверить, что доступ работает:
```bash
ssh -T git@github.com
# Ответ: Hi YOUR_USERNAME! You've successfully authenticated...
```

Клонировать репозиторий:
```bash
git clone git@github.com:YOUR_USERNAME/YOUR_REPO.git /root/xtinct-shop
cd /root/xtinct-shop
```

**Вариант B — через scp (с локальной машины):**
```bash
scp -r /path/to/telegram_shop_sqlite_dockerized root@YOUR_SERVER_IP:/root/xtinct-shop
ssh root@YOUR_SERVER_IP
cd /root/xtinct-shop
```

---

## Шаг 4. Настроить `.env`

```bash
cp .env.example .env
nano .env
```

Заполнить:
```env
BOT_TOKEN=123456789:AABBccDDeeFFggHH...
APP_URL=https://your-domain.com
ADMIN_CHAT_IDS=123456789
MANAGER_LINK=https://t.me/your_manager_username
PORT=8080
```

> Если администраторов несколько, перечисли их через запятую:
> `ADMIN_CHAT_IDS=111111111,222222222`

**Важно:**
- `APP_URL` — только `https://`, без слеша в конце
- `BOT_TOKEN` — от того же бота, через которого открывается Mini App
- `ADMIN_CHAT_IDS` — Telegram user_id (не username), найти можно через [@userinfobot](https://t.me/userinfobot)

---

## Шаг 5. Собрать и запустить контейнер

```bash
sudo docker compose up -d --build
```

Проверить статус:
```bash
sudo docker compose ps
```

Посмотреть логи:
```bash
sudo docker compose logs -f
```

Проверить, что приложение отвечает локально:
```bash
curl http://127.0.0.1:8080/
```

---

## Шаг 6. Настроить Nginx

Установить:
```bash
sudo apt install -y nginx
```

Создать конфиг:
```bash
sudo nano /etc/nginx/sites-available/xtinct-shop
```

Вставить (заменить `your-domain.com` на свой домен):
```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 20M;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Активировать:
```bash
sudo ln -s /etc/nginx/sites-available/xtinct-shop /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## Шаг 7. Выпустить SSL-сертификат

На Debian 11 Certbot рекомендуется ставить через snap (apt-версия устарела).

```bash
# Установить snapd
sudo apt install -y snapd
sudo snap install core && sudo snap refresh core

# Установить Certbot
sudo snap install --classic certbot
sudo ln -s /snap/bin/certbot /usr/bin/certbot

# Выпустить сертификат
sudo certbot --nginx -d your-domain.com
```

Certbot сам обновит конфиг Nginx и настроит редирект с HTTP на HTTPS.

После успешного получения сертификата магазин будет доступен по адресу:
```
https://your-domain.com
```

---

## Шаг 8. Зарегистрировать вебхук бота

Telegram должен знать, куда слать апдейты. Бот регистрирует вебхук автоматически при старте — главное чтобы `APP_URL` в `.env` был правильным HTTPS-адресом.

Проверить, что вебхук зарегистрирован:
```
https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo
```

В ответе должно быть:
```json
{
  "url": "https://your-domain.com/webhook/<BOT_TOKEN>",
  "has_custom_certificate": false,
  "pending_update_count": 0
}
```

---

## Шаг 9. Проверить работу Mini App

Открывай магазин и админку **только через Telegram-кнопки бота** — не вставляй URL напрямую в браузер. Telegram передаёт `initData` только внутри Mini App, без него приложение вернёт ошибку `Missing Telegram init data`.

---

## Обновление проекта

После изменения кода на сервере:

```bash
cd /root/xtinct-shop

# Если деплой через Git — сначала обновить код:
git pull

# Пересобрать и перезапустить контейнер:
sudo docker compose up -d --build
```

---

## Полезные команды

| Действие | Команда |
|---|---|
| Логи в реальном времени | `sudo docker compose logs -f` |
| Перезапустить контейнер | `sudo docker compose restart` |
| Остановить | `sudo docker compose down` |
| Зайти внутрь контейнера | `sudo docker compose exec telegram-shop sh` |
| Статус Nginx | `sudo systemctl status nginx` |
| Перезагрузить Nginx | `sudo systemctl reload nginx` |

---

## Хранение данных

| Данные | Путь на хосте |
|---|---|
| База SQLite | `./shop.db` |
| Загруженные фото | `./static/uploads/` |

Оба пути примонтированы в контейнер через volume — данные сохраняются между пересборками.

---

## Частые проблемы

### `Invalid Telegram signature`
- Проверь `BOT_TOKEN` — должен совпадать с тем, через который открывается Mini App
- Убедись, что `APP_URL` точно совпадает с доменом из вебхука (без слеша на конце)

### `Missing Telegram init data`
Страница открыта вне Telegram. Используй кнопку в боте.

### `Permission denied` в админке
Telegram `user_id` не совпадает с `ADMIN_CHAT_IDS`. Проверь через [@userinfobot](https://t.me/userinfobot).

### Контейнер не стартует
Смотри логи:
```bash
sudo docker compose logs telegram-shop
```

### Nginx 502 Bad Gateway
Контейнер не запущен или слушает не тот порт:
```bash
sudo docker compose ps
curl http://127.0.0.1:8080/
```
