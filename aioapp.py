import asyncio
import html
import io
import json
import os
import queue as _queue
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qsl, urlencode

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.web_app import check_webapp_signature
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from db import (
    activate_product,
    add_category,
    add_order,
    add_product,
    calculate_discount,
    create_promo_code,
    deactivate_product,
    delete_category,
    delete_order,
    delete_product,
    delete_promo_code,
    get_product_image_paths,
    get_active_products,
    get_all_products,
    get_all_user_ids,
    get_categories,
    count_orders,
    get_order,
    get_order_status_keys,
    get_product_map,
    get_promo_code,
    get_status_label,
    get_user_orders,
    init_db,
    list_orders,
    list_promo_codes,
    set_promo_active,
    update_order_status,
    update_product,
    upsert_user,
    get_currency_rates,
    set_currency_rates,
)

# =========================================================
# Paths and environment
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8080").strip()
MANAGER_LINK = os.getenv("MANAGER_LINK", "https://t.me/").strip()
PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Put it in the .env file.")

# Поддержка нескольких администраторов через ADMIN_CHAT_IDS (через запятую).
# Для обратной совместимости также читается старый ADMIN_CHAT_ID.
def _parse_admin_ids() -> set[int]:
    ids: set[int] = set()
    for raw in (
        os.getenv("ADMIN_CHAT_IDS", ""),
        os.getenv("ADMIN_CHAT_ID", ""),
    ):
        for part in raw.split(","):
            part = part.strip()
            if part:
                try:
                    ids.add(int(part))
                except ValueError:
                    print(f"WARN: cannot parse admin id '{part}', skipping")
    return ids

ADMIN_CHAT_IDS: set[int] = _parse_admin_ids()

DB_PATH = BASE_DIR / "data" / "shop.db"
BACKUP_HOUR_MSK = 22  # час отправки бэкапа по МСК (UTC+3)

# =========================================================
# Bot, dispatcher, router, Flask app
# =========================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB
init_db()

BOT_LOOP = None
_admin_msg_queue: _queue.Queue[str] = _queue.Queue()


# =========================================================
# Utility functions
# =========================================================


async def log_bot_info() -> None:
    """Print basic bot metadata at startup."""
    me = await bot.get_me()
    print("BOT USERNAME:", me.username)
    print("BOT ID:", me.id)
    print("BOT TOKEN PREFIX:", BOT_TOKEN[:15])
    print("ADMIN CHAT IDS:", sorted(ADMIN_CHAT_IDS) if ADMIN_CHAT_IDS else "not set")


def is_admin_chat(chat_id: int) -> bool:
    """Return True if the given Telegram chat belongs to any configured admin."""
    return chat_id in ADMIN_CHAT_IDS


def rub(amount: int) -> str:
    """Format integer price to Russian ruble string."""
    return f"{amount:,}".replace(",", " ") + " ₽"


def json_error(message: str, status: int):
    """Return a standard JSON error response."""
    return jsonify({"ok": False, "error": message}), status


def send_admin_message(text: str) -> None:
    """Отправить сообщение всем администраторам. Буферизует, если event loop ещё не запущен."""
    if not ADMIN_CHAT_IDS:
        return
    if BOT_LOOP is None or not BOT_LOOP.is_running():
        _admin_msg_queue.put(text)
        return

    async def _send():
        await asyncio.gather(
            *[bot.send_message(admin_id, text) for admin_id in ADMIN_CHAT_IDS],
            return_exceptions=True,
        )

    asyncio.run_coroutine_threadsafe(_send(), BOT_LOOP)


async def _drain_admin_queue() -> None:
    """Отправить все сообщения, накопленные до запуска event loop, всем администраторам."""
    while not _admin_msg_queue.empty():
        try:
            text = _admin_msg_queue.get_nowait()
            await asyncio.gather(
                *[bot.send_message(admin_id, text) for admin_id in ADMIN_CHAT_IDS],
                return_exceptions=True,
            )
        except Exception as exc:
            print(f"WARN: failed to send buffered admin message: {exc}")


# =========================================================
# Telegram keyboard builders
# =========================================================


def build_user_keyboard() -> ReplyKeyboardMarkup:
    """Build reply keyboard for regular users."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Открыть магазин")],
            [KeyboardButton(text="👨‍💼 Менеджер")],
        ],
        resize_keyboard=True,
    )


def build_admin_keyboard() -> ReplyKeyboardMarkup:
    """Build reply keyboard for admin user."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🛍 Открыть магазин"),
                KeyboardButton(text="🛠 Открыть админку"),
            ],
            [
                KeyboardButton(text="👨‍💼 Менеджер"),
                KeyboardButton(text="🆔 Мой ID"),
            ],
        ],
        resize_keyboard=True,
    )


def _build_url(base: str, **params) -> str:
    """Build URL, auto-adding ngrok browser warning bypass when needed."""
    if "ngrok" in base:
        params["ngrok-skip-browser-warning"] = "true"
    if not params:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params)}"


def build_store_inline() -> InlineKeyboardMarkup:
    """Build inline button that opens the customer Mini App."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть магазин",
                    web_app=WebAppInfo(url=_build_url(APP_URL)),
                )
            ]
        ]
    )


def build_admin_inline() -> InlineKeyboardMarkup:
    """Build inline button that opens the admin Mini App."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть админку",
                    web_app=WebAppInfo(url=_build_url(APP_URL.rstrip("/"), mode="admin")),
                )
            ]
        ]
    )


# =========================================================
# Telegram Mini App auth helpers
# =========================================================


def get_telegram_context_from_request() -> dict:
    """
    Validate Telegram Mini App initData from request headers
    and return parsed Telegram context.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "").strip()

    if not init_data:
        raise ValueError("Missing Telegram init data")

    if not check_webapp_signature(BOT_TOKEN, init_data):
        raise ValueError("Invalid Telegram signature")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    parsed.pop("hash", None)
    parsed.pop("signature", None)

    if "user" in parsed:
        parsed["user"] = json.loads(parsed["user"])

    return parsed


def require_admin_context() -> dict:
    """
    Validate Telegram Mini App request and ensure that the current
    Telegram user is one of the configured admins.
    """
    context = get_telegram_context_from_request()
    user = context.get("user") or {}

    if str(user.get("id")) not in {str(i) for i in ADMIN_CHAT_IDS}:
        raise PermissionError("Admin access required")

    return context


# =========================================================
# Flask page routes
# =========================================================



@app.route("/")
def index():
    """Render customer app or admin app depending on query mode."""
    mode = (request.args.get("mode") or "").strip().lower()
    if mode == "admin":
        return render_template("admin.html")
    return render_template("index.html", manager_link=MANAGER_LINK, admin_ids=sorted(ADMIN_CHAT_IDS))


# =========================================================
# Public API routes
# =========================================================


@app.route("/api/products")
def api_products():
    """Return active products for the customer Mini App."""
    return jsonify(get_active_products())


@app.route("/api/categories")
def api_categories():
    """Return category names for the customer Mini App."""
    return jsonify([item["name"] for item in get_categories()])


@app.route("/api/currency-rates")
def api_currency_rates():
    return jsonify({"ok": True, **get_currency_rates()})


@app.route("/api/admin/currency-rates")
def api_admin_get_currency_rates():
    try:
        require_admin_context()
        return jsonify({"ok": True, **get_currency_rates()})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/currency-rates", methods=["POST"])
def api_admin_set_currency_rates():
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}
        try:
            usd = float(payload.get("usd", 0))
            byn = float(payload.get("byn", 0))
        except (ValueError, TypeError):
            return json_error("Invalid rate values", 400)
        import math
        if not (math.isfinite(usd) and math.isfinite(byn)):
            return json_error("Rates must be finite numbers", 400)
        if usd <= 0 or byn <= 0:
            return json_error("Rates must be positive", 400)
        set_currency_rates(usd, byn)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/fetch-currency-rates")
def api_admin_fetch_currency_rates():
    try:
        require_admin_context()
        import urllib.request
        import ssl
        import xml.etree.ElementTree as ET

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(
            "https://www.cbr.ru/scripts/XML_daily.asp", timeout=5, context=ctx
        ) as resp:
            xml_bytes = resp.read()

        root = ET.fromstring(xml_bytes.decode("windows-1251"))
        usd = None
        byn = None
        for valute in root.findall("Valute"):
            char_code = valute.findtext("CharCode", "")
            nominal = int(valute.findtext("Nominal", "1") or 1)
            value_str = (valute.findtext("Value", "0") or "0").replace(",", ".")
            rate = round(float(value_str) / nominal, 4)
            if char_code == "USD":
                usd = rate
            elif char_code in ("BYN", "BYR"):
                byn = rate

        if usd is None or byn is None:
            return json_error("Не удалось найти курсы в ответе ЦБ РФ", 502)
        return jsonify({"ok": True, "usd": usd, "byn": byn})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(f"Ошибка загрузки курсов: {exc}", 502)


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


@app.route("/api/my-orders")
def api_my_orders():
    """Return active orders for the current Telegram user."""
    try:
        context = get_telegram_context_from_request()
        user = context.get("user") or {}
        user_id = str(user.get("id") or "")
        if not user_id:
            return jsonify({"ok": True, "items": []})

        items = get_user_orders(user_id, active_only=True)
        return jsonify({"ok": True, "items": items})
    except Exception as exc:
        return json_error(str(exc), 403)


@app.route("/api/order", methods=["POST"])
def create_order():
    """Create a new order from the customer Mini App cart."""
    try:
        context = get_telegram_context_from_request()
        tg_user = context.get("user") or {}

        payload = request.get_json(force=True, silent=True) or {}
        items = payload.get("items", [])
        customer = payload.get("customer", {}) or {}

        if not items:
            return json_error("Корзина пуста", 400)

        product_map = get_product_map()
        lines = []
        normalized_items = []
        total = 0

        for item in items:
            product_id = int(item.get("id", 0))
            quantity = int(item.get("quantity", 0))
            size = (item.get("size") or "").strip()

            product = product_map.get(product_id)
            if not product or quantity <= 0:
                continue

            available_sizes = product.get("sizes") or []
            if available_sizes and size not in available_sizes:
                continue

            subtotal = product["price"] * quantity
            total += subtotal

            size_label = f" | Размер: {size}" if size else ""
            lines.append(
                f'• {product["title"]} × {quantity}{size_label} — {rub(subtotal)}'
            )
            normalized_items.append(
                {
                    "id": product_id,
                    "title": product["title"],
                    "price": product["price"],
                    "size": size,
                    "quantity": quantity,
                    "subtotal": subtotal,
                }
            )

        if not normalized_items:
            return json_error("Некорректные товары или размеры", 400)

        # promo validation
        promo_code_str = (payload.get("promo_code") or "").strip().upper()
        discount_amount = 0
        if promo_code_str:
            promo = get_promo_code(promo_code_str)
            if promo and promo["is_active"]:
                if not promo["expires_at"] or date.fromisoformat(promo["expires_at"]) >= date.today():
                    discount_amount = calculate_discount(promo, total)
            total = max(0, total - discount_amount)

        status = "new"

        comment = customer.get("comment", "").strip()

        username = tg_user.get("username") or ""
        user_id = tg_user.get("id") or "—"
        first_name = tg_user.get("first_name") or "—"

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
        saved_order = add_order(order_data)
        order_number = saved_order["order_number"]

        username_str = f"@{username}" if username else "—"
        tg_profile = f'<a href="tg://user?id={user_id}">{first_name}</a>'
        items_block = "\n".join(lines)
        comment_block = f"\n\n<b>Комментарий:</b> {comment}" if comment else ""
        promo_block = f"\n<b>Промокод:</b> {promo_code_str} (−{rub(discount_amount)})" if promo_code_str else ""

        text = (
            f"<b>Новый заказ #{order_number}</b>\n\n"
            f"<b>Покупатель:</b> {tg_profile}\n"
            f"<b>Telegram:</b> {username_str}\n"
            f"<b>User ID:</b> <code>{user_id}</code>\n\n"
            f"<b>Состав заказа:</b>\n"
            f"{items_block}\n\n"
            f"<b>Итого:</b> {rub(total)}"
            f"{promo_block}"
            f"{comment_block}"
        )

        send_admin_message(text)

        return jsonify(
            {
                "ok": True,
                "message": "Заказ отправлен",
                "order_number": order_number,
                "status": status,
                "total": total,
            }
        )
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc), 400)
    except Exception as exc:
        print("ORDER ERROR:", repr(exc))
        return json_error(str(exc), 500)


# =========================================================
# Admin API routes
# =========================================================


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
        if expires_at:
            try:
                date.fromisoformat(expires_at)
            except ValueError:
                return json_error("Неверный формат даты (ожидается YYYY-MM-DD)", 400)

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


@app.route("/api/admin/orders")
def api_admin_orders():
    """Return admin order list with pagination."""
    try:
        require_admin_context()
        per_page = 15
        page = max(1, int(request.args.get("page", 1)))
        search = request.args.get("search", "").strip()
        field = request.args.get("field", "number")
        offset = (page - 1) * per_page
        items = list_orders(per_page, offset, search, field)
        total = count_orders(search, field)
        return jsonify({
            "ok": True,
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, -(-total // per_page)),
        })
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/orders/<int:order_number>")
def api_admin_order_detail(order_number: int):
    """Return full details for one order."""
    try:
        require_admin_context()
        order = get_order(order_number)
        if not order:
            return json_error("Order not found", 404)
        return jsonify({"ok": True, "item": order})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/orders/<int:order_number>/status", methods=["POST"])
def api_admin_order_status(order_number: int):
    """Update order status from admin panel."""
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}
        new_status = (payload.get("status") or "").strip()

        if new_status not in get_order_status_keys():
            return json_error("Invalid status", 400)

        order = update_order_status(order_number, new_status)
        if not order:
            return json_error("Order not found", 404)

        return jsonify({"ok": True, "item": order})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/products")
def api_admin_products():
    """Return all products for admin panel."""
    try:
        require_admin_context()
        return jsonify({"ok": True, "items": get_all_products()})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/products", methods=["POST"])
def api_admin_create_product():
    """Create a new product from admin panel."""
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}

        title = (payload.get("title") or "").strip()
        category = (payload.get("category") or "").strip()
        image = (payload.get("image") or "").strip()
        description = (payload.get("description") or "").strip()
        sizes = payload.get("sizes") or []
        extra_images = [img for img in (payload.get("extra_images") or []) if isinstance(img, str) and img.strip()]
        measurements = (payload.get("measurements") or "").strip() or None

        try:
            price = int(payload.get("price", 0))
        except Exception:
            return json_error("Invalid price", 400)

        if not title or not category or not image or not description or price <= 0:
            return json_error("All product fields are required", 400)

        product = add_product(title, price, category, image, description, sizes, extra_images, measurements)
        if not product:
            return json_error("Invalid category", 400)

        return jsonify({"ok": True, "item": product})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/products/<int:product_id>", methods=["POST"])
def api_admin_update_product(product_id: int):
    """Update existing product from admin panel."""
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}
        updates = {}

        for key in ("title", "price", "category", "image", "description", "sizes", "extra_images", "measurements"):
            if key in payload:
                updates[key] = payload[key]

        if "price" in updates:
            try:
                updates["price"] = int(updates["price"])
            except Exception:
                return json_error("Invalid price", 400)

        if "image" in updates and not str(updates["image"]).strip():
            return json_error("image cannot be empty", 400)

        product = update_product(product_id, updates)
        if not product:
            return json_error("Product not found or invalid category", 404)

        return jsonify({"ok": True, "item": product})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/products/<int:product_id>/hide", methods=["POST"])
def api_admin_hide_product(product_id: int):
    """Hide product in admin panel."""
    try:
        require_admin_context()
        ok = deactivate_product(product_id)
        if not ok:
            return json_error("Product not found", 404)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/products/<int:product_id>/show", methods=["POST"])
def api_admin_show_product(product_id: int):
    """Show product in admin panel."""
    try:
        require_admin_context()
        ok = activate_product(product_id)
        if not ok:
            return json_error("Product not found", 404)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/products/<int:product_id>/delete", methods=["POST"])
def api_admin_delete_product(product_id: int):
    """Delete product and its uploaded images from disk."""
    try:
        require_admin_context()
        image_paths = get_product_image_paths(product_id)
        ok = delete_product(product_id)
        if not ok:
            return json_error("Product not found", 404)
        upload_dir = UPLOAD_DIR.resolve()
        for rel_path in image_paths:
            abs_path = (BASE_DIR / rel_path.lstrip("/")).resolve()
            if not abs_path.is_relative_to(upload_dir):
                continue
            try:
                abs_path.unlink(missing_ok=True)
            except OSError:
                pass
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/orders/<int:order_number>/delete", methods=["POST"])
def api_admin_delete_order(order_number: int):
    """Delete order and its items from the database."""
    try:
        require_admin_context()
        ok = delete_order(order_number)
        if not ok:
            return json_error("Order not found", 404)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/categories")
def api_admin_categories():
    """Return categories for admin panel."""
    try:
        require_admin_context()
        return jsonify({"ok": True, "items": get_categories()})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/categories", methods=["POST"])
def api_admin_add_category():
    """Create a new category from admin panel."""
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}
        name = (payload.get("name") or "").strip()

        if not name:
            return json_error("Category name is required", 400)

        ok = add_category(name)
        if not ok:
            return json_error("Category already exists or invalid", 400)

        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/categories/<int:category_id>/delete", methods=["POST"])
def api_admin_delete_category(category_id: int):
    """Delete category from admin panel."""
    try:
        require_admin_context()
        result = delete_category(category_id)
        if not result["ok"]:
            return json_error(result["error"], 400)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/upload-image", methods=["POST"])
def api_admin_upload_image():
    """Upload product image from admin panel."""
    try:
        require_admin_context()

        if "image" not in request.files:
            return json_error("Image file is required", 400)

        file = request.files["image"]
        if not file.filename:
            return json_error("Empty file", 400)

        ext = Path(file.filename).suffix.lower() or ".jpg"
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            return json_error("Unsupported file format", 400)

        filename = f"{uuid.uuid4().hex}{ext}"
        path = UPLOAD_DIR / filename
        file.save(path)

        return jsonify({"ok": True, "path": f"/static/uploads/{filename}"})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/broadcast", methods=["POST"])
def api_admin_broadcast():
    """Send a drop announcement to all registered users."""
    try:
        require_admin_context()

        text = (request.form.get("text") or "").strip()
        photo_file = request.files.get("photo")
        photo_bytes: bytes | None = None
        photo_name: str | None = None
        if photo_file and photo_file.filename:
            photo_bytes = photo_file.read()
            photo_name = photo_file.filename

        if not text and not photo_bytes:
            return json_error("text or photo required", 400)

        if BOT_LOOP is None or not BOT_LOOP.is_running():
            return json_error("Bot loop is not running", 503)

        future = asyncio.run_coroutine_threadsafe(
            _broadcast(text, photo_bytes, photo_name), BOT_LOOP
        )
        result = future.result(timeout=180)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return json_error(str(exc), 500)


async def _broadcast(text: str, photo_bytes: bytes | None, photo_name: str | None) -> dict:
    safe_text = html.escape(text) if text else ""
    user_ids = get_all_user_ids()
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            if photo_bytes:
                await bot.send_photo(
                    int(uid),
                    BufferedInputFile(photo_bytes, filename=photo_name or "photo.jpg"),
                    caption=safe_text or None,
                    parse_mode=ParseMode.HTML,
                )
            else:
                await bot.send_message(int(uid), safe_text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    return {"sent": sent, "failed": failed, "total": len(user_ids)}


# =========================================================
# Aiogram bot command handlers
# =========================================================


@router.message(Command("start"))
async def start_handler(message: Message):
    """Show welcome message and main reply keyboard."""
    admin = is_admin_chat(message.chat.id)
    reply_markup = build_admin_keyboard() if admin else build_user_keyboard()

    upsert_user(
        str(message.from_user.id),
        message.from_user.username,
        message.from_user.first_name,
    )

    text = "Добро пожаловать в магазин."
    if admin:
        text += "\nУ тебя также есть доступ к админке."

    await message.answer(text, reply_markup=reply_markup)


@router.message(Command("admin_app"))
async def admin_app_handler(message: Message):
    """Send inline button that opens admin Mini App."""
    if not is_admin_chat(message.chat.id):
        await message.answer("Нет доступа.")
        return

    await message.answer(
        "Нажми кнопку ниже, чтобы открыть админку.",
        reply_markup=build_admin_inline(),
    )


@router.message(Command("myid"))
async def myid_handler(message: Message):
    """Return current Telegram chat id."""
    await message.answer(f"Твой chat id: <code>{message.chat.id}</code>")


@router.message(Command("add_product"))
async def add_product_guide_handler(message: Message):
    """Show brief hint for admin product management."""
    await message.answer(
        "Для управления товарами используй кнопку «🛠 Открыть админку»."
    )


@router.message(Command("admin"))
async def admin_help_handler(message: Message):
    """Show admin help and admin keyboard."""
    if not is_admin_chat(message.chat.id):
        await message.answer("Нет доступа.")
        return

    await message.answer(
        (
            "<b>Админ-доступ</b>\n\n"
            "🛠 Открыть админку — панель управления\n"
            "🛍 Открыть магазин — клиентская витрина\n"
            "🆔 Мой ID — показать chat id"
        ),
        reply_markup=build_admin_keyboard(),
    )


# =========================================================
# Aiogram text button handlers
# =========================================================


@router.message(F.text == "🛍 Открыть магазин")
async def open_store_button_handler(message: Message):
    """Send inline button that opens customer Mini App."""
    await message.answer(
        "Нажми кнопку ниже, чтобы открыть магазин.",
        reply_markup=build_store_inline(),
    )


@router.message(F.text == "🛠 Открыть админку")
async def open_admin_button_handler(message: Message):
    """Send inline button that opens admin Mini App for admin only."""
    if not is_admin_chat(message.chat.id):
        await message.answer("Нет доступа.")
        return

    await message.answer(
        "Нажми кнопку ниже, чтобы открыть админку.",
        reply_markup=build_admin_inline(),
    )


@router.message(F.text == "👨‍💼 Менеджер")
async def open_manager_handler(message: Message):
    """Send inline link to manager chat."""
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Написать менеджеру", url=MANAGER_LINK)]
        ]
    )
    await message.answer("Открыть чат с менеджером:", reply_markup=markup)


@router.message(F.text == "🆔 Мой ID")
async def my_id_button_handler(message: Message):
    """Return current Telegram chat id from keyboard button."""
    await message.answer(f"Твой chat id: <code>{message.chat.id}</code>")


@router.message(Command("backup"))
async def backup_command(message: Message):
    """Send DB backup on demand (admin only)."""
    if not is_admin_chat(message.chat.id):
        return
    await message.answer("Создаю бэкап БД…")
    await send_db_backup()


# =========================================================
# Database backup
# =========================================================


async def send_db_backup() -> None:
    """Send a hot backup of shop.db to every admin."""
    if not ADMIN_CHAT_IDS or not DB_PATH.exists():
        return

    buf = io.BytesIO()
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(":memory:")
    try:
        src.backup(dst, pages=-1)
        dst.execute("VACUUM")
        buf.write("\n".join(dst.iterdump()).encode())
        buf.seek(0)
    finally:
        src.close()
        dst.close()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
    filename = f"shop_backup_{ts}.sql"
    doc = BufferedInputFile(buf.read(), filename=filename)
    caption = f"Бэкап БД · {ts} UTC"

    await asyncio.gather(
        *[bot.send_document(admin_id, doc, caption=caption) for admin_id in ADMIN_CHAT_IDS],
        return_exceptions=True,
    )


async def _backup_scheduler() -> None:
    """Wait until 22:00 MSK, send backup, then repeat daily."""
    msk_offset = 3 * 3600
    while True:
        now_utc = datetime.now(timezone.utc).timestamp()
        now_msk_sec = (now_utc + msk_offset) % 86400
        target_msk_sec = BACKUP_HOUR_MSK * 3600
        seconds_until = (target_msk_sec - now_msk_sec) % 86400
        if seconds_until == 0:
            seconds_until = 86400
        await asyncio.sleep(seconds_until)
        await send_db_backup()


# =========================================================
# Bot runner
# =========================================================


def run_bot() -> None:
    """Start aiogram polling in a dedicated event loop."""

    async def _main():
        try:
            await log_bot_info()
        except Exception as exc:
            print(f"WARN: could not fetch bot info (network issue?): {exc}")
        await bot.delete_webhook(drop_pending_updates=True)
        if ADMIN_CHAT_IDS:
            await _drain_admin_queue()
        asyncio.create_task(_backup_scheduler())
        await dp.start_polling(bot, handle_signals=False)

    global BOT_LOOP
    BOT_LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(BOT_LOOP)
    BOT_LOOP.run_until_complete(_main())


# =========================================================
# Application entrypoint
# =========================================================

if __name__ == "__main__":
    Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
