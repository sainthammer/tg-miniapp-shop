import hashlib
import hmac
import os
import uuid
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qsl

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
import telebot
from telebot.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from db import (
    add_category,
    add_order,
    add_product,
    activate_product,
    deactivate_product,
    get_active_products,
    get_all_products,
    get_categories,
    get_category_by_name,
    get_currency_rates,
    get_next_order_number,
    get_order,
    get_order_status_keys,
    get_product_by_id,
    get_product_map,
    get_status_label,
    get_user_orders,
    init_db,
    list_orders,
    set_currency_rates,
    update_order_status,
    update_product,
    delete_product,
    delete_category,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8080").strip()
ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID", "").strip()
MANAGER_LINK = os.getenv("MANAGER_LINK", "https://t.me/").strip()
PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Put it in the .env file.")

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW) if ADMIN_CHAT_ID_RAW else None

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
me = bot.get_me()
print("BOT USERNAME:", me.username)
print("BOT ID:", me.id)
print("BOT TOKEN PREFIX:", BOT_TOKEN[:15])
print("ADMIN CHAT ID:", ADMIN_CHAT_ID)
app = Flask(__name__, template_folder="templates", static_folder="static")
init_db()


def is_admin_chat(chat_id: int) -> bool:
    return ADMIN_CHAT_ID is not None and chat_id == ADMIN_CHAT_ID


def build_user_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🛍 Открыть магазин", web_app=WebAppInfo(APP_URL)),
    )
    markup.add(
        KeyboardButton("👨‍💼 Менеджер"),
    )
    return markup


def build_admin_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🛍 Открыть магазин", web_app=WebAppInfo(APP_URL)),
        KeyboardButton(
            "🛠 Открыть админку", web_app=WebAppInfo(f"{APP_URL.rstrip('/')}?mode=admin")
        ),
    )
    markup.add(
        KeyboardButton("👨‍💼 Менеджер"),
        KeyboardButton("🆔 Мой ID"),
    )
    return markup


def build_inline_links(is_admin: bool):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Открыть магазин", web_app=WebAppInfo(APP_URL)))
    if is_admin:
        markup.add(
            InlineKeyboardButton(
                "Открыть админку", web_app=WebAppInfo(APP_URL.rstrip("/") + "/admin")
            )
        )
    markup.add(InlineKeyboardButton("Менеджер", url=MANAGER_LINK))
    return markup


def rub(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


def _parse_telegram_init_data(init_data: str) -> dict:
    import json

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = parsed.pop("hash", None)
    parsed.pop("signature", None)

    if not received_hash:
        raise ValueError("Missing hash in initData")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(parsed.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    print("DATA CHECK STRING:", data_check_string)
    print("RECEIVED HASH:", received_hash)
    print("CALCULATED HASH:", calculated_hash)

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Invalid Telegram signature")

    user_raw = parsed.get("user")
    if user_raw:
        parsed["user"] = json.loads(user_raw)

    return parsed


DEV_MODE = os.getenv("DEV_MODE", "0").strip() == "1"


from aiogram.utils.web_app import check_webapp_signature
from urllib.parse import parse_qsl
import json


def get_telegram_context_from_request():
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


def require_admin_context():
    context = get_telegram_context_from_request()
    user = context.get("user") or {}

    if str(user.get("id")) != str(ADMIN_CHAT_ID):
        raise PermissionError("Admin access required")

    return context


def json_error(message: str, status: int):
    return jsonify({"ok": False, "error": message}), status


def save_telegram_photo(message) -> str:
    if not message.photo:
        raise ValueError("Фото не найдено в сообщении.")
    photo = message.photo[-1]
    file_info = bot.get_file(photo.file_id)
    downloaded = bot.download_file(file_info.file_path)
    ext = Path(file_info.file_path).suffix or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = UPLOAD_DIR / filename
    with save_path.open("wb") as f:
        f.write(downloaded)
    return f"/static/uploads/{filename}"


from flask import redirect, request, url_for


@app.route("/")
def index():
    mode = (request.args.get("mode") or "").strip().lower()
    if mode == "admin":
        return render_template("admin.html")
    return render_template("index.html", manager_link=MANAGER_LINK)


@app.route("/api/admin/debug-auth")
def api_admin_debug_auth():
    try:
        context = get_telegram_context_from_request()
        user = context.get("user") or {}
        return jsonify(
            {
                "ok": True,
                "user_id": user.get("id"),
                "admin_chat_id": ADMIN_CHAT_ID,
                "is_admin": ADMIN_CHAT_ID is not None
                and int(user.get("id", 0)) == ADMIN_CHAT_ID,
            }
        )
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return json_error(str(exc), 400)


@app.route("/api/debug-telegram")
def api_debug_telegram():
    try:
        context = get_telegram_context_from_request()
        return jsonify({"ok": True, "context": context})
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


@app.route("/api/products")
def api_products():
    return jsonify(get_active_products())


@app.route("/api/categories")
def api_categories():
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
        import xml.etree.ElementTree as ET

        with urllib.request.urlopen(
            "https://www.cbr.ru/scripts/XML_daily.asp", timeout=5
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


@app.route("/api/my-orders")
def api_my_orders():
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

        order_number = get_next_order_number()
        status = "new"

        telegram_link = customer.get("telegram_link", "").strip() or "Не указано"
        comment = customer.get("comment", "").strip() or "—"

        username = tg_user.get("username") or "—"
        user_id = tg_user.get("id") or "—"
        first_name = tg_user.get("first_name") or "—"

        text = (
            f"<b>Новый заказ #{order_number}</b>\n\n"
            f"<b>Статус:</b> {get_status_label(status)}\n"
            f"<b>Telegram link:</b> {telegram_link}\n"
            f"<b>Mini App user:</b> {first_name} | @{username if username != '—' else 'unknown'} | ID: {user_id}\n\n"
            f"<b>Состав заказа:</b>\n"
            + "\n".join(lines)
            + f"\n\n<b>Комментарий:</b> {comment}"
            f"\n<b>Итого:</b> {rub(total)}"
        )

        order_data = {
            "order_number": order_number,
            "status": status,
            "customer": {
                "telegram_link": telegram_link,
                "comment": comment,
            },
            "telegram_user": tg_user,
            "items": normalized_items,
            "total": total,
        }
        add_order(order_data)

        if ADMIN_CHAT_ID:
            bot.send_message(ADMIN_CHAT_ID, text)

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


@app.route("/api/admin/orders")
def api_admin_orders():
    try:
        require_admin_context()
        return jsonify({"ok": True, "items": list_orders(100)})
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print("ADMIN API ERROR:", repr(exc))
        return json_error(str(exc), 400)


@app.route("/api/admin/orders/<int:order_number>")
def api_admin_order_detail(order_number: int):
    try:
        require_admin_context()
        order = get_order(order_number)
        if not order:
            return json_error("Order not found", 404)
        return jsonify({"ok": True, "item": order})
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print("ADMIN API ERROR:", repr(exc))
        return json_error(str(exc), 400)


@app.route("/api/admin/orders/<int:order_number>/status", methods=["POST"])
def api_admin_order_status(order_number: int):
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
    try:
        require_admin_context()
        return jsonify({"ok": True, "items": get_all_products()})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/products", methods=["POST"])
def api_admin_create_product():
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}
        title = (payload.get("title") or "").strip()
        category = (payload.get("category") or "").strip()
        image = (payload.get("image") or "").strip()
        description = (payload.get("description") or "").strip()
        sizes = payload.get("sizes") or []
        extra_images = [img for img in (payload.get("extra_images") or []) if isinstance(img, str) and img.strip()]
        try:
            price = int(payload.get("price", 0))
        except Exception:
            return json_error("Invalid price", 400)

        if not title or not category or not image or not description or price <= 0:
            return json_error("All product fields are required", 400)

        product = add_product(title, price, category, image, description, sizes, extra_images)
        if not product:
            return json_error("Invalid category", 400)
        return jsonify({"ok": True, "item": product})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/products/<int:product_id>", methods=["POST"])
def api_admin_update_product(product_id: int):
    try:
        require_admin_context()
        payload = request.get_json(force=True, silent=True) or {}
        updates = {}
        for key in ("title", "price", "category", "image", "description", "sizes", "extra_images"):
            if key in payload:
                updates[key] = payload[key]
        if "price" in updates:
            try:
                updates["price"] = int(updates["price"])
            except Exception:
                return json_error("Invalid price", 400)
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
    try:
        require_admin_context()
        ok = delete_product(product_id)
        if not ok:
            return json_error("Product not found", 404)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/categories/<int:category_id>/delete", methods=["POST"])
def api_admin_delete_category(category_id: int):
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


@app.route("/api/admin/categories")
def api_admin_categories():
    try:
        require_admin_context()
        return jsonify({"ok": True, "items": get_categories()})
    except PermissionError as exc:
        return json_error(str(exc), 403)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.route("/api/admin/categories", methods=["POST"])
def api_admin_add_category():
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


@app.route("/api/admin/upload-image", methods=["POST"])
def api_admin_upload_image():
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


@bot.message_handler(commands=["start"])
def start(message):
    admin = is_admin_chat(message.chat.id)
    reply_markup = build_admin_keyboard() if admin else build_user_keyboard()
    inline_markup = build_inline_links(admin)

    text = (
        "Добро пожаловать в магазин.\n\n"
        "Используй кнопки ниже, чтобы быстро открыть каталог"
    )
    if admin:
        text += " или перейти в админку."

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=reply_markup,
    )

    bot.send_message(
        message.chat.id,
        "Быстрые действия:",
        reply_markup=inline_markup,
    )


@bot.message_handler(commands=["admin_app"])
def admin_app(message):
    if not is_admin_chat(message.chat.id):
        bot.send_message(message.chat.id, "Нет доступа.")
        return

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "Открыть админку",
            web_app=WebAppInfo(f"{APP_URL.rstrip('/')}?mode=admin"),
        )
    )
    bot.send_message(message.chat.id, "Открыть админку:", reply_markup=markup)


@bot.message_handler(commands=["myid"])
def myid(message):
    bot.send_message(message.chat.id, f"Твой chat id: <code>{message.chat.id}</code>")


@bot.message_handler(commands=["add_product"])
def add_product_guide(message):
    bot.send_message(
        message.chat.id,
        "Для управления товарами используй /admin_app — там доступна отдельная админка Mini App.",
    )


@bot.message_handler(commands=["admin"])
def admin_help(message):
    if not is_admin_chat(message.chat.id):
        bot.send_message(message.chat.id, "Нет доступа.")
        return

    bot.send_message(
        message.chat.id,
        (
            "<b>Админ-доступ</b>\n\n"
            "🛠 Открыть админку — панель управления\n"
            "🛍 Открыть магазин — клиентская витрина\n"
            "🆔 Мой ID — показать chat id"
        ),
        reply_markup=build_admin_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "👨‍💼 Менеджер")
def open_manager(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Написать менеджеру", url=MANAGER_LINK))
    bot.send_message(
        message.chat.id,
        "Открыть чат с менеджером:",
        reply_markup=markup,
    )


@bot.message_handler(func=lambda m: m.text == "🆔 Мой ID")
def my_id_button(message):
    bot.send_message(message.chat.id, f"Твой chat id: <code>{message.chat.id}</code>")


@bot.message_handler(func=lambda m: m.text == "🛠 Открыть админку")
def open_admin_button(message):
    if not is_admin_chat(message.chat.id):
        bot.send_message(message.chat.id, "Нет доступа.")
        return

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "Открыть админку", web_app=WebAppInfo(APP_URL.rstrip("/") + "/admin")
        )
    )
    bot.send_message(
        message.chat.id,
        "Открыть админку:",
        reply_markup=markup,
    )


def run_bot():
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
