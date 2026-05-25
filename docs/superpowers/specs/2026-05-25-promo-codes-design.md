# Промокоды — Дизайн

## Контекст

Telegram Mini App магазин одежды XTINCT. Нужно добавить систему промокодов со скидками. Управление через новую вкладку «Утилиты» в админке (переименованная вкладка «Пост»).

---

## 1. База данных

### Новая таблица `promo_codes`

```sql
CREATE TABLE IF NOT EXISTS promo_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK(type IN ('percent', 'fixed')),
    value REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT,
    created_at TEXT NOT NULL
)
```

- `code` — строка промокода, хранится в верхнем регистре (SALE20)
- `type` — `percent` (скидка в %) или `fixed` (скидка в ₽)
- `value` — число: 20 для 20%, 500 для 500 ₽
- `is_active` — 1/0, управляется вручную через админку
- `expires_at` — ISO дата `YYYY-MM-DD` или NULL (без срока)

### Изменения в таблице `orders`

Два новых столбца добавляются через `ALTER TABLE` при миграции в `init_db()`:

```sql
ALTER TABLE orders ADD COLUMN promo_code TEXT;
ALTER TABLE orders ADD COLUMN discount_amount REAL DEFAULT 0;
```

---

## 2. API

### Публичный эндпоинт (клиент)

**`POST /api/promo/validate`**

Запрос:
```json
{ "code": "SALE20", "cart_total": 4990 }
```

Ответ (успех):
```json
{ "ok": true, "discount_amount": 998, "message": "−20% применено" }
```

Ответ (ошибка):
```json
{ "ok": false, "error": "Промокод не найден" }
```

Возможные ошибки: «Промокод не найден», «Промокод неактивен», «Срок действия промокода истёк».

Расчёт скидки:
- `percent`: `discount_amount = round(cart_total * value / 100)`
- `fixed`: `discount_amount = min(value, cart_total)` — скидка не может превысить сумму заказа

Промокод **не сгорает** при валидации — только при успешном сохранении заказа.

### Админские эндпоинты (требуют `require_admin_context`)

| Метод | URL | Действие |
|---|---|---|
| `GET` | `/api/admin/promos` | Список всех промокодов |
| `POST` | `/api/admin/promos` | Создать промокод |
| `POST` | `/api/admin/promos/<id>/status` | Переключить is_active |
| `POST` | `/api/admin/promos/<id>/delete` | Удалить промокод |

Тело запроса создания:
```json
{ "code": "SALE20", "type": "percent", "value": 20, "expires_at": "2026-12-31" }
```

---

## 3. Клиентская часть

### Изменения в `state`

```javascript
promoCode: '',        // введённый промокод
promoDiscount: 0,     // сумма скидки в ₽
```

### UI в корзине

Блок появляется между списком товаров и итоговой суммой:

```
[ ВВЕДИТЕ ПРОМОКОД ] [Применить]
  ✓ SALE20 — −998 ₽    ← зелёным при успехе
  ✗ Промокод не найден ← красным при ошибке
```

- Кнопка «Применить» вызывает `POST /api/promo/validate`
- При успехе: `state.promoCode` и `state.promoDiscount` обновляются, итог пересчитывается
- При применённом промокоде рядом с полем появляется кнопка «✕» для сброса
- Итоговая строка корзины: `Итого: 4990 ₽ → 3992 ₽ (−998 ₽)`

### Изменения при отправке заказа

В payload добавляется:
```json
{ "customer": { "comment": "..." }, "promo_code": "SALE20" }
```

Сервер в `/api/order`:
1. Повторно валидирует промокод
2. Пересчитывает `total` с учётом скидки
3. Сохраняет `promo_code` и `discount_amount` в таблицу `orders`
4. Добавляет строку в уведомление боту: `Промокод: SALE20 (−998 ₽)`

---

## 4. Админка: вкладка «Утилиты»

### Переименование

- Вкладка `drop` → `utils`
- `dropScreen` → `utilsScreen`
- `navDrop` → `navUtils`
- Лейбл навбара: «Пост» → «Утилиты»

### Структура экрана

Два независимых аккордеон-блока. Оба могут быть открыты одновременно. По умолчанию — оба свёрнуты.

**Блок 1 — Промокоды**

Заголовок-кнопка с иконкой ▶/▼. При раскрытии:
- Форма создания: поле кода, селект типа (% / ₽), поле значения, date-picker срока, кнопка «Создать»
- Список промокодов: карточки в стиле проекта — код, тип+значение, срок, статус (активен/неактивен)
- Кнопки у каждой карточки: «Активировать» / «Деактивировать» + «Удалить»

**Блок 2 — Пост**

Заголовок-кнопка с иконкой ▶/▼. При раскрытии: существующий функционал без изменений (textarea + фото + кнопка «Отправить пост»).

### Анимация раскрытия

CSS `max-height` transition: `0 → auto` через `max-height: 1000px` с `overflow: hidden` и `transition: max-height 0.3s ease`.
