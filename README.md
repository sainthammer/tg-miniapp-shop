# Telegram Mini Shop (SQLite)

## Описание

Минимальный интернет-магазин одежды внутри Telegram Mini App с админ-панелью.

Проект включает:
- клиентский Mini App (`/`)
- админский режим (`?mode=admin`)
- Telegram авторизацию через `initData`
- backend на Flask
- SQLite база данных

---

## Возможности

### Клиент (Mini App)
- каталог товаров
- фильтрация по категориям
- карточка товара (размеры, описание)
- избранное
- корзина
- оформление заказа
- вкладка "Мои заказы"
- связь с менеджером

### Админка
- просмотр заказов
- смена статусов
- создание и редактирование товаров
- загрузка изображений
- управление категориями

---

## Архитектура

- Backend: Flask
- Bot: telebot (pyTelegramBotAPI)
- Авторизация: Telegram WebApp (`initData`)
- База данных: SQLite (`shop.db`)
- Статика: `static/uploads`

---

## Установка и запуск

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# отредактируй .env

python app.py
```

---

## .env

```
BOT_TOKEN=your_bot_token
APP_URL=https://your-domain.com
ADMIN_CHAT_ID=123456789
MANAGER_LINK=https://t.me/username
PORT=8080
```

---

## Локальное тестирование

Telegram Mini App требует HTTPS, поэтому:

```
ngrok http 8080
```

Далее:
- берёшь HTTPS URL
- вставляешь в APP_URL
- перезапускаешь приложение

---

## Важно про админку

Админка открывается через:

```
https://your-domain.com?mode=admin
```

Не использовать напрямую `/admin` из Telegram-кнопок.

---

## Команды бота

```
/start       — открыть магазин
/admin_app   — открыть админку (только для ADMIN_CHAT_ID)
/myid        — показать chat id
```

---

## Авторизация

Используется проверка подписи Telegram через:

- initData
- aiogram.utils.web_app.check_webapp_signature

Доступ в админку:

```
user.id == ADMIN_CHAT_ID
```

---

## Структура проекта

```
app.py
db.py
shop.db
/templates
    index.html
    admin.html
/static
    /uploads
```

---

## Деплой (Production)

Рекомендуемый стек:

- VPS (Hetzner / Fornex)
- Nginx
- Certbot (Let's Encrypt)
- systemd

Ключевые требования:

- только HTTPS
- корректный APP_URL
- запуск как сервис

---

## Частые проблемы

### Invalid Telegram signature
- неверный BOT_TOKEN
- неправильный способ проверки
- открытие не через Telegram

### initData пустой
- страница открыта в браузере
- или через старую кнопку

### админка не работает
- открыта не через кнопку Telegram
- не совпадает ADMIN_CHAT_ID
- используется `/admin` вместо `?mode=admin`

---

## Файлы

- база: shop.db
- изображения: static/uploads/

---

## Дальнейшее развитие

- PostgreSQL вместо SQLite
- Docker
- Gunicorn + Nginx
- роли (несколько админов)
- платежи (Telegram Payments / Stripe)
