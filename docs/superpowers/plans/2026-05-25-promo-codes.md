# Promo Codes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a promo code system — admin creates/manages codes in a new "Утилиты" tab, clients enter codes in the cart to get percentage or fixed discounts.

**Architecture:** Four layers: DB functions in `db.py`, REST API in `aioapp.py`, client UI in `templates/index.html`, admin UI in `templates/admin.html`. Each task is independently committable.

**Tech Stack:** Python/Flask, SQLite, vanilla JS, Tailwind CSS CDN

---

## File Map

| File | Changes |
|---|---|
| `db.py` | Add `promo_codes` table, `orders` migration, 5 new functions |
| `aioapp.py` | Import new db functions, add 5 API endpoints, update `/api/order` |
| `templates/index.html` | Add promo UI in cart HTML, add `state.promoCode/promoDiscount`, update `getTotal()`, `renderCart()`, `renderCheckoutPreview()`, `sendOrder` payload |
| `templates/admin.html` | Rename drop→utils everywhere, wrap existing post in accordion, add promo accordion with form + list |

---

## Task 1: DB — таблица promo_codes и миграция orders

**Files:**
- Modify: `db.py`

- [ ] **Step 1: Добавить таблицу promo_codes в `init_db()`**

В `db.py`, в функции `init_db()`, внутри `conn.executescript(...)` добавить после блока `users`:

```python
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL CHECK(type IN ('percent', 'fixed')),
                value REAL NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                created_at TEXT NOT NULL
            );
```

- [ ] **Step 2: Добавить миграцию orders в `init_db()`**

После `conn.executescript(...)`, но до конца `init_db()`, добавить:

```python
        # migrations
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
        if "promo_code" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN promo_code TEXT")
        if "discount_amount" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN discount_amount REAL DEFAULT 0")
```

- [ ] **Step 3: Добавить функцию `get_promo_code()`**

В конце `db.py` добавить:

```python
def get_promo_code(code: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM promo_codes WHERE code = ?",
            (code.strip().upper(),),
        ).fetchone()
        return dict(row) if row else None


def calculate_discount(promo: dict[str, Any], cart_total: int) -> int:
    if promo["type"] == "percent":
        return round(cart_total * promo["value"] / 100)
    else:
        return min(int(promo["value"]), cart_total)
```

- [ ] **Step 4: Добавить функции CRUD для промокодов**

В конце `db.py` добавить:

```python
def list_promo_codes() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM promo_codes ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def create_promo_code(code: str, type_: str, value: float, expires_at: str | None) -> bool:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO promo_codes (code, type, value, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (code.strip().upper(), type_, value, expires_at or None, now_iso()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def set_promo_active(promo_id: int, is_active: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE promo_codes SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, promo_id),
        )


def delete_promo_code(promo_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
```

- [ ] **Step 5: Commit**

```bash
git add db.py
git commit -m "feat: add promo_codes table and db functions"
```

---

## Task 2: API — эндпоинты промокодов

**Files:**
- Modify: `aioapp.py`

- [ ] **Step 1: Добавить импорты новых функций из db**

В блоке `from db import (` добавить:

```python
    calculate_discount,
    create_promo_code,
    delete_promo_code,
    get_promo_code,
    list_promo_codes,
    set_promo_active,
```

- [ ] **Step 2: Добавить публичный эндпоинт `/api/promo/validate`**

После блока `@app.route("/api/categories")` добавить:

```python
@app.route("/api/promo/validate", methods=["POST"])
def api_promo_validate():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        code = (payload.get("code") or "").strip().upper()
        cart_total = int(payload.get("cart_total") or 0)

        if not code:
            return json_error("Укажи промокод", 400)

        promo = get_promo_code(code)
        if not promo:
            return jsonify({"ok": False, "error": "Промокод не найден"})
        if not promo["is_active"]:
            return jsonify({"ok": False, "error": "Промокод неактивен"})
        if promo["expires_at"]:
            from datetime import date
            if date.fromisoformat(promo["expires_at"]) < date.today():
                return jsonify({"ok": False, "error": "Срок действия промокода истёк"})

        discount = calculate_discount(promo, cart_total)
        if promo["type"] == "percent":
            msg = f"−{int(promo['value'])}% применено"
        else:
            msg = f"−{rub(int(discount))} применено"

        return jsonify({"ok": True, "discount_amount": discount, "message": msg})
    except Exception as exc:
        return json_error(str(exc), 500)
```

- [ ] **Step 3: Добавить админские эндпоинты промокодов**

После предыдущего эндпоинта добавить:

```python
@app.route("/api/admin/promos")
def api_admin_promos_list():
    try:
        require_admin_context()
        return jsonify(list_promo_codes())
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 500)


@app.route("/api/admin/promos", methods=["POST"])
def api_admin_promos_create():
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}
        code = (payload.get("code") or "").strip().upper()
        type_ = payload.get("type", "")
        value = float(payload.get("value") or 0)
        expires_at = payload.get("expires_at") or None

        if not code:
            return json_error("Укажи код", 400)
        if type_ not in ("percent", "fixed"):
            return json_error("Тип должен быть percent или fixed", 400)
        if value <= 0:
            return json_error("Значение должно быть больше 0", 400)
        if type_ == "percent" and value > 100:
            return json_error("Процент не может быть больше 100", 400)

        ok = create_promo_code(code, type_, value, expires_at)
        if not ok:
            return json_error("Промокод с таким кодом уже существует", 409)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 500)


@app.route("/api/admin/promos/<int:promo_id>/status", methods=["POST"])
def api_admin_promos_status(promo_id: int):
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}
        is_active = bool(payload.get("is_active", True))
        set_promo_active(promo_id, is_active)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 500)


@app.route("/api/admin/promos/<int:promo_id>/delete", methods=["POST"])
def api_admin_promos_delete(promo_id: int):
    try:
        require_admin_context()
        delete_promo_code(promo_id)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 500)
```

- [ ] **Step 4: Обновить `/api/order` — повторная валидация промокода**

В функции `create_order()` в `aioapp.py`, найти строку `status = "new"` и добавить после блока `if not normalized_items`:

```python
        # promo validation
        promo_code_str = (payload.get("promo_code") or "").strip().upper()
        discount_amount = 0
        if promo_code_str:
            from datetime import date
            promo = get_promo_code(promo_code_str)
            if promo and promo["is_active"]:
                if not promo["expires_at"] or date.fromisoformat(promo["expires_at"]) >= date.today():
                    discount_amount = calculate_discount(promo, total)
            total = max(0, total - discount_amount)
```

- [ ] **Step 5: Обновить `order_data` и текст уведомления**

Найти `order_data = {` и обновить:

```python
        order_data = {
            "status": status,
            "customer": {
                "comment": comment,
            },
            "telegram_user": tg_user,
            "items": normalized_items,
            "total": total,
            "promo_code": promo_code_str or None,
            "discount_amount": discount_amount,
        }
```

Найти `comment_block = ...` и добавить после него:

```python
        promo_block = f"\n<b>Промокод:</b> {promo_code_str} (−{rub(discount_amount)})" if discount_amount else ""
```

Найти строку `f"<b>Итого:</b> {rub(total)}"` и изменить на:

```python
            f"<b>Итого:</b> {rub(total)}"
            f"{promo_block}"
            f"{comment_block}"
```

(убрать `f"{comment_block}"` из предыдущей строки если там было)

- [ ] **Step 6: Обновить `add_order()` в `db.py` — сохранять promo_code и discount_amount**

В функции `add_order()` в `db.py` найти INSERT и обновить:

```python
        cur = conn.execute(
            """
            INSERT INTO orders (
                order_number,
                status,
                customer_comment,
                telegram_user_id,
                telegram_username,
                telegram_first_name,
                total,
                promo_code,
                discount_amount,
                created_at
            )
            SELECT COALESCE(MAX(order_number), 0) + 1, ?, ?, ?, ?, ?, ?, ?, ?, ?
            FROM orders
            """,
            (
                order_data["status"],
                order_data["customer"]["comment"],
                str(order_data["telegram_user"].get("id", "")),
                order_data["telegram_user"].get("username", ""),
                order_data["telegram_user"].get("first_name", ""),
                order_data["total"],
                order_data.get("promo_code"),
                order_data.get("discount_amount", 0),
                now_iso(),
            ),
        )
```

- [ ] **Step 7: Commit**

```bash
git add aioapp.py db.py
git commit -m "feat: add promo code API endpoints and order integration"
```

---

## Task 3: Клиент — UI промокода в корзине

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Добавить `promoCode` и `promoDiscount` в `state`**

Найти в `state`:
```javascript
      prevScrollY: 0,
```
Добавить после:
```javascript
      promoCode: '',
      promoDiscount: 0,
```

- [ ] **Step 2: Обновить `getTotal()` — учитывать скидку**

Найти:
```javascript
    function getTotal() {
      return getItems().reduce((sum, item) => sum + item.price * item.quantity, 0);
    }
```
Заменить на:
```javascript
    function getTotal() {
      const raw = getItems().reduce((sum, item) => sum + item.price * item.quantity, 0);
      return Math.max(0, raw - state.promoDiscount);
    }

    function getRawTotal() {
      return getItems().reduce((sum, item) => sum + item.price * item.quantity, 0);
    }
```

- [ ] **Step 3: Добавить HTML блока промокода в корзину**

Найти в `templates/index.html`:
```html
      <div id="cartSummary" class="hidden glass rounded-[28px] p-5 mt-4">
```
Заменить на:
```html
      <!-- Промокод -->
      <div id="promoBlock" class="hidden glass rounded-[28px] p-4 mt-4">
        <div style="display:flex;gap:8px;align-items:center;">
          <input id="promoInput" type="text" placeholder="ПРОМОКОД"
            style="flex:1;font-family:var(--fn-body);font-size:13px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;background:rgba(255,255,255,0.06);border:1px solid rgba(140,141,143,0.22);border-radius:3px;padding:10px 12px;color:var(--silver);outline:none;" />
          <button id="promoApplyBtn"
            style="font-family:var(--fn-body);font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;padding:10px 14px;border-radius:3px;cursor:pointer;background:linear-gradient(180deg,#1e1e1e 0%,#181818 100%);border:1px solid rgba(140,141,143,0.22);border-top-color:rgba(192,195,198,0.26);color:var(--silver-dim);white-space:nowrap;">
            Применить
          </button>
          <button id="promoClearBtn" style="display:none;font-family:var(--fn-body);font-size:18px;line-height:1;color:var(--silver-faint);background:none;border:none;cursor:pointer;padding:4px;">✕</button>
        </div>
        <div id="promoMsg" style="margin-top:8px;font-family:var(--fn-body);font-size:12px;letter-spacing:0.04em;display:none;"></div>
      </div>

      <div id="cartSummary" class="hidden glass rounded-[28px] p-5 mt-4">
```

- [ ] **Step 4: Обновить блок `cartSummary` — показывать скидку**

Найти:
```html
      <div id="cartSummary" class="hidden glass rounded-[28px] p-5 mt-4">
        <div class="flex items-center justify-between">
          <span style="font-family:var(--fn-display);font-size:26px;font-weight:800;letter-spacing:0.20em;text-transform:uppercase;color:var(--silver-faint);">Итого</span>
          <span id="cartSummaryTotal" style="font-family:var(--fn-display);font-size:36px;font-weight:800;letter-spacing:0.04em;color:var(--silver);line-height:1;">0 ₽</span>
        </div>
        <button id="goCheckoutFromCart" class="btn-cta" style="margin-top:16px;">Перейти к оформлению</button>
      </div>
```
Заменить на:
```html
      <div id="cartSummary" class="hidden glass rounded-[28px] p-5 mt-4">
        <div id="cartDiscountRow" style="display:none;justify-content:space-between;align-items:center;margin-bottom:6px;">
          <span style="font-family:var(--fn-body);font-size:12px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--silver-faint);">Скидка</span>
          <span id="cartDiscountAmt" style="font-family:var(--fn-body);font-size:14px;font-weight:700;color:#6fcf6f;">−0 ₽</span>
        </div>
        <div class="flex items-center justify-between">
          <span style="font-family:var(--fn-display);font-size:26px;font-weight:800;letter-spacing:0.20em;text-transform:uppercase;color:var(--silver-faint);">Итого</span>
          <span id="cartSummaryTotal" style="font-family:var(--fn-display);font-size:36px;font-weight:800;letter-spacing:0.04em;color:var(--silver);line-height:1;">0 ₽</span>
        </div>
        <button id="goCheckoutFromCart" class="btn-cta" style="margin-top:16px;">Перейти к оформлению</button>
      </div>
```

- [ ] **Step 5: Обновить `renderCart()` — показывать promoBlock и скидку**

Найти в `renderCart()`:
```javascript
      cartEmpty.classList.toggle('hidden', items.length > 0);
      cartSummary.classList.toggle('hidden', items.length === 0);
```
Заменить на:
```javascript
      cartEmpty.classList.toggle('hidden', items.length > 0);
      cartSummary.classList.toggle('hidden', items.length === 0);
      document.getElementById('promoBlock').classList.toggle('hidden', items.length === 0);

      const discountRow = document.getElementById('cartDiscountRow');
      const discountAmt = document.getElementById('cartDiscountAmt');
      if (state.promoDiscount > 0) {
        discountRow.style.display = 'flex';
        discountAmt.textContent = '−' + formatPrice(state.promoDiscount);
      } else {
        discountRow.style.display = 'none';
      }
```

- [ ] **Step 6: Найти строку обновления `cartSummaryTotal` и обновить**

Найти:
```javascript
      cartSummaryTotal.textContent = formatPrice(getTotal());
```
Это уже будет работать корректно т.к. `getTotal()` теперь учитывает скидку. Ничего менять не нужно.

- [ ] **Step 7: Добавить JS логику промокода после инициализации DOM-констант**

Найти блок где объявляются DOM-константы (около `const cartSummary = ...`) и добавить функцию и обработчики:

```javascript
    async function applyPromo() {
      const input = document.getElementById('promoInput');
      const msg = document.getElementById('promoMsg');
      const code = input.value.trim().toUpperCase();
      if (!code) return;

      msg.style.display = 'none';
      const rawTotal = getRawTotal();

      try {
        const res = await fetch('/api/promo/validate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, cart_total: rawTotal }),
        });
        const data = await res.json();

        if (data.ok) {
          state.promoCode = code;
          state.promoDiscount = data.discount_amount;
          msg.textContent = '✓ ' + data.message;
          msg.style.display = 'block';
          msg.style.color = '#6fcf6f';
          document.getElementById('promoClearBtn').style.display = 'block';
          document.getElementById('promoApplyBtn').style.display = 'none';
          input.disabled = true;
        } else {
          state.promoCode = '';
          state.promoDiscount = 0;
          msg.textContent = '✕ ' + data.error;
          msg.style.display = 'block';
          msg.style.color = '#e07070';
        }
      } catch {
        msg.textContent = '✕ Ошибка соединения';
        msg.style.display = 'block';
        msg.style.color = '#e07070';
      }

      renderCart();
    }

    function clearPromo() {
      state.promoCode = '';
      state.promoDiscount = 0;
      const input = document.getElementById('promoInput');
      const msg = document.getElementById('promoMsg');
      input.value = '';
      input.disabled = false;
      msg.style.display = 'none';
      document.getElementById('promoClearBtn').style.display = 'none';
      document.getElementById('promoApplyBtn').style.display = 'block';
      renderCart();
    }

    document.getElementById('promoApplyBtn').addEventListener('click', applyPromo);
    document.getElementById('promoClearBtn').addEventListener('click', clearPromo);
    document.getElementById('promoInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') applyPromo();
    });
```

- [ ] **Step 8: Обновить `renderCheckoutPreview()` — показывать скидку в превью**

Найти в `renderCheckoutPreview()` строку с итогом:
```javascript
          <span style="font-family:var(--fn-display);font-weight:800;font-size:22px;letter-spacing:0.04em;color:var(--silver);">${formatPrice(getTotal())}</span>
```
Заменить блок итога (весь `<div style="border-top...">`) на:
```javascript
          <div style="border-top:1px solid rgba(155,157,160,0.18);padding-top:8px;margin-top:4px;">
            ${state.promoDiscount > 0 ? `
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
                <span style="font-family:var(--fn-body);font-size:11px;font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:var(--silver-faint);">Скидка (${esc(state.promoCode)})</span>
                <span style="font-family:var(--fn-body);font-size:13px;font-weight:700;color:#6fcf6f;">−${formatPrice(state.promoDiscount)}</span>
              </div>` : ''}
            <div style="display:flex;align-items:center;justify-content:space-between;">
              <span style="font-family:var(--fn-body);font-size:11px;font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:var(--silver-faint);">Итого</span>
              <span style="font-family:var(--fn-display);font-weight:800;font-size:22px;letter-spacing:0.04em;color:var(--silver);">${formatPrice(getTotal())}</span>
            </div>
          </div>
```

- [ ] **Step 9: Обновить `sendOrder` — передавать promo_code в payload**

Найти в обработчике `sendOrder`:
```javascript
        const payload = {
          customer: {
            comment: document.getElementById('comment').value.trim(),
          },
          items,
        };
```
Заменить на:
```javascript
        const payload = {
          customer: {
            comment: document.getElementById('comment').value.trim(),
          },
          promo_code: state.promoCode || undefined,
          items,
        };
```

- [ ] **Step 10: Сбросить промокод после успешного заказа**

После `state.cart = {};` в обработчике успешного заказа добавить:
```javascript
      state.promoCode = '';
      state.promoDiscount = 0;
      clearPromo();
```

- [ ] **Step 11: Commit**

```bash
git add templates/index.html
git commit -m "feat: add promo code UI in cart"
```

---

## Task 4: Админка — вкладка «Утилиты» с аккордеоном

**Files:**
- Modify: `templates/admin.html`

- [ ] **Step 1: Переименовать drop → utils везде**

Заменить все вхождения (используй replace_all):
- `id="dropScreen"` → `id="utilsScreen"`
- `id="navDrop"` → `id="navUtils"`
- `dropScreen` → `utilsScreen` (в JS)
- `navDrop` → `navUtils` (в JS)
- `tab === 'drop'` → `tab === 'utils'`
- `tab !== 'drop'` → `tab !== 'utils'`
- `'drop'` → `'utils'` (только в контексте setTab/навигации)
- Лейбл `>Пост<` у кнопки навбара → `>Утилиты<`

- [ ] **Step 2: Добавить CSS аккордеона**

В блоке `<style>` `admin.html` добавить:

```css
  .accordion-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    cursor: pointer;
    padding: 14px 16px;
    border-radius: 4px;
    background: var(--scratch-a), var(--scratch-b), linear-gradient(160deg,#1e1e1e 0%,#181818 100%);
    border: 1px solid var(--m-border);
    border-top-color: var(--m-top);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
    user-select: none;
    -webkit-tap-highlight-color: transparent;
  }
  .accordion-header:active { filter: brightness(0.9); }
  .accordion-title {
    font-family: var(--fn-body);
    font-size: 11px; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--silver-dim);
  }
  .accordion-arrow {
    transition: transform 0.25s ease;
    color: var(--silver-faint);
  }
  .accordion-header.open .accordion-arrow { transform: rotate(90deg); }
  .accordion-body {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s ease;
  }
  .accordion-body.open { max-height: 2000px; }
```

- [ ] **Step 3: Заменить содержимое `utilsScreen` на два аккордеона**

Найти весь блок:
```html
    <section id="dropScreen" class="hidden">
      <div class="form-panel">
        <div class="form-title">Новый пост</div>
        ...
      </div>
    </section>
```
Заменить на:
```html
    <section id="utilsScreen" class="hidden">

      <!-- Аккордеон: Промокоды -->
      <div style="margin-bottom:10px;">
        <button class="accordion-header" id="accordionPromoHeader">
          <span class="accordion-title">Промокоды</span>
          <svg class="accordion-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg>
        </button>
        <div class="accordion-body" id="accordionPromoBody">
          <div class="form-panel" style="border-top:none;border-radius:0 0 4px 4px;">
            <!-- Форма создания -->
            <div class="form-title" style="margin-bottom:12px;">Новый промокод</div>
            <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px;">
              <input id="promoCodeInput" class="w-full rounded-2xl bg-white/10 border border-white/10 px-4 py-3 outline-none" placeholder="Код (например SALE20)" style="text-transform:uppercase;" />
              <div style="display:flex;gap:8px;">
                <select id="promoTypeSelect" class="rounded-2xl bg-white/10 border border-white/10 px-3 py-3 outline-none" style="flex:1;">
                  <option value="percent">Процент (%)</option>
                  <option value="fixed">Фикс. сумма (₽)</option>
                </select>
                <input id="promoValueInput" type="number" min="1" class="rounded-2xl bg-white/10 border border-white/10 px-4 py-3 outline-none" placeholder="Значение" style="flex:1;" />
              </div>
              <input id="promoExpiresInput" type="date" class="w-full rounded-2xl bg-white/10 border border-white/10 px-4 py-3 outline-none" />
            </div>
            <button id="promoCreateBtn" class="rounded-2xl bg-white text-slate-900 px-5 py-3 font-semibold">Создать промокод</button>
            <div id="promoCreateResult" style="display:none;margin-top:10px;padding:10px 14px;border-radius:3px;font-family:var(--fn-body);font-size:13px;"></div>

            <!-- Список промокодов -->
            <div id="promoList" style="margin-top:20px;display:flex;flex-direction:column;gap:8px;"></div>
          </div>
        </div>
      </div>

      <!-- Аккордеон: Пост -->
      <div style="margin-bottom:10px;">
        <button class="accordion-header" id="accordionPostHeader">
          <span class="accordion-title">Пост</span>
          <svg class="accordion-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg>
        </button>
        <div class="accordion-body" id="accordionPostBody">
          <div class="form-panel" style="border-top:none;border-radius:0 0 4px 4px;">
            <div class="form-sub" style="margin-bottom:12px;">Сообщение получат все пользователи, которые запускали бота.</div>
            <textarea id="dropText" class="w-full rounded-2xl bg-white/10 border border-white/10 px-4 py-3 outline-none min-h-[120px]" placeholder="Текст поста… Переносы строк сохраняются. Форматирование: &lt;b&gt;жирный&lt;/b&gt;, &lt;i&gt;курсив&lt;/i&gt;, &lt;code&gt;код&lt;/code&gt;" style="margin-bottom:10px;resize:vertical;"></textarea>
            <div style="margin-bottom:12px;">
              <input id="dropPhotoFile" type="file" accept="image/*" class="hidden" />
              <button id="dropPhotoBtn" style="font-family:var(--fn-body);font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--silver-dim);background:linear-gradient(180deg,#1e1e1e,#181818);border:1px solid rgba(140,141,143,0.22);border-top-color:rgba(192,195,198,0.26);box-shadow:inset 0 1px 0 rgba(255,255,255,0.05);border-radius:3px;cursor:pointer;padding:8px 14px;display:flex;align-items:center;gap:6px;">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
                Прикрепить фото
              </button>
              <div id="dropPhotoPreview" style="margin-top:8px;display:none;">
                <img id="dropPhotoImg" style="max-height:120px;border-radius:3px;border:1px solid rgba(140,141,143,0.22);" />
                <button id="dropPhotoClear" style="display:block;margin-top:4px;font-family:var(--fn-body);font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:var(--silver-faint);background:none;border:none;cursor:pointer;padding:0;">✕ убрать фото</button>
              </div>
            </div>
            <button id="dropSendBtn" class="rounded-2xl bg-white text-slate-900 px-5 py-3 font-semibold">Отправить пост</button>
            <div id="dropResult" style="display:none;margin-top:14px;padding:10px 14px;border-radius:3px;font-family:var(--fn-body);font-size:13px;letter-spacing:0.03em;"></div>
          </div>
        </div>
      </div>

    </section>
```

- [ ] **Step 4: Добавить JS аккордеона и управления промокодами**

В JS блоке `admin.html` (после существующих `navDrop`→`navUtils` обработчиков) добавить:

```javascript
    // Аккордеон
    function initAccordion(headerId, bodyId) {
      const header = document.getElementById(headerId);
      const body = document.getElementById(bodyId);
      header.addEventListener('click', () => {
        const isOpen = body.classList.contains('open');
        body.classList.toggle('open', !isOpen);
        header.classList.toggle('open', !isOpen);
      });
    }
    initAccordion('accordionPromoHeader', 'accordionPromoBody');
    initAccordion('accordionPostHeader', 'accordionPostBody');

    // Промокоды
    async function loadPromos() {
      const list = document.getElementById('promoList');
      try {
        const res = await apiGet('/api/admin/promos');
        if (!res.length) {
          list.innerHTML = '<div style="font-family:var(--fn-body);font-size:13px;color:var(--silver-faint);">Промокодов пока нет.</div>';
          return;
        }
        list.innerHTML = res.map(p => {
          const typeLabel = p.type === 'percent' ? `${p.value}%` : `${p.value} ₽`;
          const expires = p.expires_at ? `до ${p.expires_at}` : 'бессрочно';
          const activeLabel = p.is_active ? 'Активен' : 'Неактивен';
          const activeColor = p.is_active ? '#6fcf6f' : 'var(--silver-faint)';
          return `
            <div style="background:var(--scratch-a),var(--scratch-b),linear-gradient(160deg,#1e1e1e 0%,#181818 100%);border:1px solid var(--m-border);border-top-color:var(--m-top);border-radius:4px;padding:12px 14px;">
              <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap;">
                <div>
                  <div style="font-family:var(--fn-display);font-size:20px;font-weight:800;letter-spacing:0.08em;color:var(--silver);">${esc(p.code)}</div>
                  <div style="font-family:var(--fn-body);font-size:12px;color:var(--silver-dim);margin-top:3px;letter-spacing:0.04em;">
                    Скидка: <b>${typeLabel}</b> · ${expires}
                  </div>
                  <div style="font-family:var(--fn-body);font-size:11px;font-weight:700;letter-spacing:0.10em;text-transform:uppercase;color:${activeColor};margin-top:3px;">${activeLabel}</div>
                </div>
                <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;">
                  <button onclick="togglePromo(${p.id}, ${p.is_active ? 0 : 1})"
                    style="font-family:var(--fn-body);font-size:10px;font-weight:700;letter-spacing:0.10em;text-transform:uppercase;padding:6px 10px;border-radius:3px;cursor:pointer;background:linear-gradient(180deg,#2c2c2c,#1e1e1e);border:1px solid rgba(140,141,143,0.30);color:var(--silver-dim);">
                    ${p.is_active ? 'Деактивировать' : 'Активировать'}
                  </button>
                  <button onclick="removePromo(${p.id})"
                    style="font-family:var(--fn-body);font-size:10px;font-weight:700;letter-spacing:0.10em;text-transform:uppercase;padding:6px 10px;border-radius:3px;cursor:pointer;background:none;border:1px solid rgba(210,70,70,0.32);color:rgba(210,100,100,0.80);">
                    Удалить
                  </button>
                </div>
              </div>
            </div>
          `;
        }).join('');
      } catch {
        list.innerHTML = '<div style="color:var(--silver-faint);font-size:13px;">Ошибка загрузки.</div>';
      }
    }

    async function togglePromo(id, newActive) {
      await apiPost(`/api/admin/promos/${id}/status`, { is_active: newActive });
      loadPromos();
    }

    async function removePromo(id) {
      await apiPost(`/api/admin/promos/${id}/delete`, {});
      loadPromos();
    }

    window.togglePromo = togglePromo;
    window.removePromo = removePromo;

    document.getElementById('promoCreateBtn').addEventListener('click', async () => {
      const code = document.getElementById('promoCodeInput').value.trim().toUpperCase();
      const type = document.getElementById('promoTypeSelect').value;
      const value = parseFloat(document.getElementById('promoValueInput').value);
      const expires_at = document.getElementById('promoExpiresInput').value || null;
      const result = document.getElementById('promoCreateResult');

      result.style.display = 'none';
      try {
        const res = await apiPost('/api/admin/promos', { code, type, value, expires_at });
        if (res.ok) {
          result.textContent = '✓ Промокод создан';
          result.style.color = '#6fcf6f';
          result.style.background = 'rgba(111,207,111,0.08)';
          document.getElementById('promoCodeInput').value = '';
          document.getElementById('promoValueInput').value = '';
          document.getElementById('promoExpiresInput').value = '';
          loadPromos();
        } else {
          result.textContent = '✕ ' + (res.error || 'Ошибка');
          result.style.color = '#e07070';
          result.style.background = 'rgba(210,70,70,0.08)';
        }
      } catch {
        result.textContent = '✕ Ошибка соединения';
        result.style.color = '#e07070';
        result.style.background = 'rgba(210,70,70,0.08)';
      }
      result.style.display = 'block';
    });
```

- [ ] **Step 5: Загружать промокоды при переходе на вкладку utils**

В функции `setTab(tab)` найти блок обработки `'utils'` (бывший `'drop'`) и добавить:

```javascript
      if (tab === 'utils') loadPromos();
```

- [ ] **Step 6: Проверить что `apiGet` и `apiPost` существуют в admin.html**

```bash
grep -n "function apiGet\|function apiPost\|async function apiPost\|async function apiGet" templates/admin.html
```

Если не найдены — добавить перед `loadPromos`:

```javascript
    async function apiGet(url) {
      const res = await fetch(url, { headers: telegramHeaders() });
      return res.json();
    }
    async function apiPost(url, body) {
      const res = await fetch(url, {
        method: 'POST',
        headers: { ...telegramHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      return res.json();
    }
```

- [ ] **Step 7: Commit**

```bash
git add templates/admin.html
git commit -m "feat: add utils tab with promo codes management and post accordion"
```

---

## Self-Review Checklist

- [x] DB: `promo_codes` table ✓, `orders` migration ✓
- [x] API: validate ✓, list ✓, create ✓, status ✓, delete ✓
- [x] Order flow: повторная валидация ✓, сохранение promo_code/discount_amount ✓, уведомление ✓
- [x] Client: state ✓, getTotal() ✓, UI ✓, applyPromo ✓, clearPromo ✓, checkout payload ✓, сброс после заказа ✓
- [x] Admin: переименование ✓, аккордеон ✓, форма создания ✓, список ✓, toggle/delete ✓, loadPromos при переходе ✓
