# Развертывание Telegram Mini Shop в Docker

## Что входит
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

Контейнер запускает приложение через `aioapp.py`.
База данных SQLite (`shop.db`) и загруженные изображения (`static/uploads`) вынесены в volume, чтобы данные не терялись после пересоздания контейнера.

## Требования
- VPS с Ubuntu 22.04 / Debian 12
- Docker и Docker Compose plugin
- домен или поддомен
- HTTPS для Telegram Mini App

## 1. Установить Docker
```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable docker
sudo systemctl start docker
```

## 2. Загрузить проект на сервер
Скопируй архив или репозиторий на сервер и перейди в папку проекта.

Пример:
```bash
scp -r telegram_shop_sqlite root@YOUR_SERVER_IP:/root/
ssh root@YOUR_SERVER_IP
cd /root/telegram_shop_sqlite
```

## 3. Подготовить `.env`
Если файла `.env` нет, создай его из примера:

```bash
cp .env.example .env
nano .env
```

Минимально заполни:
```env
BOT_TOKEN=your_bot_token
APP_URL=https://your-domain.com
ADMIN_CHAT_ID=123456789
MANAGER_LINK=https://t.me/username
PORT=8080
```

Важно:
- `APP_URL` должен быть именно `https://...`
- не используй `http://127.0.0.1:8080` в проде
- `BOT_TOKEN` должен быть от того же бота, через которого открывается Mini App

## 4. Собрать и запустить контейнер
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

## 5. Проверить локально на сервере
Пока без Nginx можно проверить так:
```bash
curl http://127.0.0.1:8080/
```

Если всё в порядке, контейнер уже слушает порт `8080`.

## 6. Настроить Nginx
Установить:
```bash
sudo apt install -y nginx
```

Создать конфиг:
```bash
sudo nano /etc/nginx/sites-available/telegram-shop
```

Вставить:
```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активировать:
```bash
sudo ln -s /etc/nginx/sites-available/telegram-shop /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## 7. Включить HTTPS
Установить Certbot:
```bash
sudo apt install -y certbot python3-certbot-nginx
```

Получить сертификат:
```bash
sudo certbot --nginx -d your-domain.com
```

После этого твой магазин будет доступен по:
```text
https://your-domain.com
```

## 8. Проверить Telegram Mini App
Открывай магазин и админку только через Telegram-кнопки бота.

Если открыть URL вручную в браузере, можно получить ошибку:
- `Missing Telegram init data`

Это нормально: Telegram `initData` передаётся только внутри Mini App.

## 9. Обновление проекта
После изменения кода:
```bash
cd /root/telegram_shop_sqlite
sudo docker compose up -d --build
```

## 10. Полезные команды
Остановить:
```bash
sudo docker compose down
```

Перезапустить:
```bash
sudo docker compose restart
```

Удалить контейнеры и сеть:
```bash
sudo docker compose down
```

Удалить и пересобрать:
```bash
sudo docker compose down
sudo docker compose up -d --build
```

## 11. Где хранятся данные
- база SQLite: `shop.db`
- загруженные изображения: `static/uploads`

Они примонтированы из хоста в контейнер и сохраняются между перезапусками.

## 12. Частые проблемы

### `Invalid Telegram signature`
Проверь:
- правильный ли `BOT_TOKEN`
- совпадает ли `APP_URL` с реальным HTTPS-доменом
- Mini App открыт именно через этого бота

### `Missing Telegram init data`
Значит страница открыта не внутри Telegram Mini App.

### `Permission denied` для админки
Проверь, что Telegram user id совпадает с `ADMIN_CHAT_ID`.

## 13. Примечание
Сейчас приложение запускается Flask dev server внутри контейнера. Для небольшого проекта это допустимо, но для более серьёзной нагрузки лучше перейти на:
- Gunicorn
- Nginx
- PostgreSQL вместо SQLite
