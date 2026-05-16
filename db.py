import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "shop.db"

ORDER_STATUSES = {
    "new": "Новый",
    "confirmed": "Подтвержден",
    "processing": "В обработке",
    "shipped": "Отправлен",
    "done": "Завершен",
    "cancelled": "Отменен",
}

ACTIVE_ORDER_STATUSES = ["new", "confirmed", "processing", "shipped"]


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                image TEXT NOT NULL,
                description TEXT NOT NULL,
                sizes_json TEXT NOT NULL DEFAULT '[]',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'new',
                customer_name TEXT,
                customer_phone TEXT,
                customer_telegram_link TEXT,
                customer_comment TEXT,
                telegram_user_id TEXT,
                telegram_username TEXT,
                telegram_first_name TEXT,
                total INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                product_title TEXT NOT NULL,
                product_price INTEGER NOT NULL,
                product_size TEXT,
                quantity INTEGER NOT NULL,
                subtotal INTEGER NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            );

            CREATE INDEX IF NOT EXISTS idx_orders_telegram_user_id
                ON orders (telegram_user_id);

            CREATE TABLE IF NOT EXISTS users (
                telegram_user_id TEXT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen TEXT NOT NULL
            );
            """)
        conn.commit()

    with get_connection() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        if "images_json" not in cols:
            conn.execute("ALTER TABLE products ADD COLUMN images_json TEXT NOT NULL DEFAULT '[]'")
            conn.commit()

    seed_defaults()


def seed_defaults() -> None:
    with get_connection() as conn:
        category_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM categories"
        ).fetchone()["cnt"]
        if category_count == 0:
            default_categories = ["Футболки", "Худи", "Брюки", "Куртки"]
            for name in default_categories:
                conn.execute(
                    "INSERT INTO categories (name, created_at) VALUES (?, ?)",
                    (name, now_iso()),
                )
            conn.commit()

        product_count = conn.execute("SELECT COUNT(*) AS cnt FROM products").fetchone()[
            "cnt"
        ]
        if product_count == 0:
            categories = {
                row["name"]: row["id"]
                for row in conn.execute("SELECT id, name FROM categories").fetchall()
            }
            default_products = [
                (
                    "Oversize T-Shirt",
                    2490,
                    categories["Футболки"],
                    "https://placehold.co/800x800/png?text=Oversize+T-Shirt",
                    "Свободная базовая футболка из плотного хлопка.",
                    ["S", "M", "L", "XL"],
                ),
                (
                    "Urban Hoodie",
                    5490,
                    categories["Худи"],
                    "https://placehold.co/800x800/png?text=Urban+Hoodie",
                    "Теплое худи с мягким начесом и современным кроем.",
                    ["M", "L", "XL"],
                ),
                (
                    "Cargo Pants",
                    6290,
                    categories["Брюки"],
                    "https://placehold.co/800x800/png?text=Cargo+Pants",
                    "Универсальные карго-брюки на каждый день.",
                    ["30", "32", "34"],
                ),
                (
                    "Light Jacket",
                    8990,
                    categories["Куртки"],
                    "https://placehold.co/800x800/png?text=Light+Jacket",
                    "Легкая городская куртка для прохладной погоды.",
                    ["S", "M", "L"],
                ),
            ]
            for (
                title,
                price,
                category_id,
                image,
                description,
                sizes,
            ) in default_products:
                conn.execute(
                    """
                    INSERT INTO products (title, price, category_id, image, description, sizes_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        price,
                        category_id,
                        image,
                        description,
                        json_dumps(sizes),
                        now_iso(),
                    ),
                )
            conn.commit()


def get_status_label(status: str) -> str:
    return ORDER_STATUSES.get(status, status)


def get_order_status_keys() -> list[str]:
    return list(ORDER_STATUSES.keys())


def add_category(name: str) -> bool:
    name = name.strip()
    if not name:
        return False
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO categories (name, created_at) VALUES (?, ?)",
                (name, now_iso()),
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def delete_category(category_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) as n FROM products WHERE category_id = ?", (category_id,)
        ).fetchone()["n"]
        if count > 0:
            return {"ok": False, "error": f"Нельзя удалить: в категории {count} товар(ов)"}
        cur = conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": "Категория не найдена"}
    return {"ok": True}


def get_categories() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, name
            FROM categories
            WHERE is_active = 1
            ORDER BY name
            """).fetchall()
        return [dict(row) for row in rows]


def list_categories_text() -> str:
    categories = get_categories()
    if not categories:
        return "Категорий пока нет."
    return "\n".join(f'• {item["name"]} (ID: {item["id"]})' for item in categories)


def get_category_by_name(name: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, name
            FROM categories
            WHERE name = ? AND is_active = 1
            """,
            (name.strip(),),
        ).fetchone()
        return dict(row) if row else None


def add_product(
    title: str,
    price: int,
    category_name: str,
    image: str,
    description: str,
    sizes: list[str],
    extra_images: list[str] | None = None,
):
    category = get_category_by_name(category_name)
    if not category:
        return None

    clean_sizes = [size.strip() for size in sizes if size.strip()]
    clean_extra = [img.strip() for img in (extra_images or []) if img.strip()]
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO products (title, price, category_id, image, description, sizes_json, images_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                price,
                category["id"],
                image,
                description,
                json_dumps(clean_sizes),
                json_dumps(clean_extra),
                now_iso(),
            ),
        )
        product_id = cur.lastrowid
        conn.commit()
    return get_product_by_id(product_id)


def _map_product_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["sizes"] = json_loads(item.pop("sizes_json", "[]"), [])
    extra = json_loads(item.pop("images_json", "[]"), [])
    item["images"] = [item["image"]] + extra
    item["is_active"] = bool(item.get("is_active", 1))
    return item


def get_active_products() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                p.id,
                p.title,
                p.price,
                c.name AS category,
                p.image,
                p.description,
                p.sizes_json,
                p.images_json,
                p.is_active
            FROM products p
            JOIN categories c ON c.id = p.category_id
            WHERE p.is_active = 1 AND c.is_active = 1
            ORDER BY p.id DESC
            """).fetchall()
    return [_map_product_row(row) for row in rows]


def get_all_products() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                p.id,
                p.title,
                p.price,
                c.name AS category,
                p.image,
                p.description,
                p.sizes_json,
                p.images_json,
                p.is_active
            FROM products p
            JOIN categories c ON c.id = p.category_id
            ORDER BY p.id DESC
            """).fetchall()
    return [_map_product_row(row) for row in rows]


def get_product_map() -> dict[int, dict[str, Any]]:
    return {item["id"]: item for item in get_active_products()}


def get_product_by_id(product_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                p.id,
                p.title,
                p.price,
                c.name AS category,
                p.image,
                p.description,
                p.sizes_json,
                p.images_json,
                p.is_active
            FROM products p
            JOIN categories c ON c.id = p.category_id
            WHERE p.id = ?
            """,
            (product_id,),
        ).fetchone()
    return _map_product_row(row) if row else None


def list_all_products_text() -> str:
    rows = get_all_products()
    if not rows:
        return "Товаров пока нет."
    lines = []
    for row in rows:
        status = "активен" if row["is_active"] else "скрыт"
        sizes = ", ".join(row["sizes"]) or "без размеров"
        lines.append(
            f'#{row["id"]} | {row["title"]} | {row["price"]} ₽ | {row["category"]} | {sizes} | {status}'
        )
    return "\n".join(lines)


def update_product(product_id: int, updates: dict[str, Any]):
    current = get_product_by_id(product_id)
    if not current:
        return None

    title = updates.get("title", current["title"])
    price = updates.get("price", current["price"])
    image = updates.get("image", current["image"])
    description = updates.get("description", current["description"])
    category_name = updates.get("category", current["category"])
    sizes = updates.get("sizes", current["sizes"])
    current_extra = current["images"][1:] if len(current.get("images", [])) > 1 else []
    extra_images = updates.get("extra_images", current_extra)

    category = get_category_by_name(category_name)
    if not category:
        return None

    clean_sizes = [size.strip() for size in sizes if size.strip()]
    clean_extra = [img.strip() for img in extra_images if img.strip()]
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE products
            SET title = ?, price = ?, category_id = ?, image = ?, description = ?, sizes_json = ?, images_json = ?
            WHERE id = ?
            """,
            (
                title,
                price,
                category["id"],
                image,
                description,
                json_dumps(clean_sizes),
                json_dumps(clean_extra),
                product_id,
            ),
        )
        conn.commit()
    return get_product_by_id(product_id)


def get_product_image_paths(product_id: int) -> list[str]:
    """Return all local image paths for a product (main + extra)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT image, images_json FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    if not row:
        return []
    paths = [row["image"]] + json_loads(row["images_json"] or "[]", [])
    return [p for p in paths if p and p.startswith("/static/uploads/")]


def delete_product(product_id: int) -> bool:
    with get_connection() as conn:
        conn.execute("DELETE FROM order_items WHERE product_id = ?", (product_id,))
        cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
    return cur.rowcount > 0


def deactivate_product(product_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE products SET is_active = 0 WHERE id = ?",
            (product_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def activate_product(product_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE products SET is_active = 1 WHERE id = ?",
            (product_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def get_next_order_number() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(order_number), 0) + 1 AS n FROM orders"
        ).fetchone()
        return row["n"]


def add_order(order_data: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders (
                order_number,
                status,
                customer_name,
                customer_phone,
                customer_telegram_link,
                customer_comment,
                telegram_user_id,
                telegram_username,
                telegram_first_name,
                total,
                created_at
            )
            SELECT COALESCE(MAX(order_number), 0) + 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            FROM orders
            """,
            (
                order_data["status"],
                order_data["customer"]["name"],
                order_data["customer"]["phone"],
                order_data["customer"]["telegram_link"],
                order_data["customer"]["comment"],
                str(order_data["telegram_user"].get("id", "")),
                order_data["telegram_user"].get("username", ""),
                order_data["telegram_user"].get("first_name", ""),
                order_data["total"],
                now_iso(),
            ),
        )
        order_id = cur.lastrowid

        assigned_number = conn.execute(
            "SELECT order_number FROM orders WHERE id = ?", (order_id,)
        ).fetchone()["order_number"]

        for item in order_data["items"]:
            conn.execute(
                """
                INSERT INTO order_items (
                    order_id,
                    product_id,
                    product_title,
                    product_price,
                    product_size,
                    quantity,
                    subtotal
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    item["id"],
                    item["title"],
                    item["price"],
                    item.get("size") or "",
                    item["quantity"],
                    item["subtotal"],
                ),
            )
        conn.commit()

    return get_order(assigned_number)


def list_orders(limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                order_number,
                status,
                customer_name,
                total,
                created_at,
                telegram_user_id
            FROM orders
            ORDER BY order_number DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_order(order_number: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE order_number = ?",
            (order_number,),
        ).fetchone()
        if not order:
            return None
        items = conn.execute(
            """
            SELECT
                product_id AS id,
                product_title AS title,
                product_price AS price,
                product_size AS size,
                quantity,
                subtotal
            FROM order_items
            WHERE order_id = ?
            ORDER BY id ASC
            """,
            (order["id"],),
        ).fetchall()
    result = dict(order)
    result["items"] = [dict(item) for item in items]
    return result


def update_order_status(order_number: int, status: str):
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE orders SET status = ? WHERE order_number = ?",
            (status, order_number),
        )
        conn.commit()
    if cur.rowcount == 0:
        return None
    return get_order(order_number)


def get_user_orders(
    telegram_user_id: str, active_only: bool = True
) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if active_only:
            placeholders = ",".join("?" * len(ACTIVE_ORDER_STATUSES))
            rows = conn.execute(
                f"""
                SELECT order_number
                FROM orders
                WHERE telegram_user_id = ? AND status IN ({placeholders})
                ORDER BY order_number DESC
                """,
                [telegram_user_id, *ACTIVE_ORDER_STATUSES],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT order_number
                FROM orders
                WHERE telegram_user_id = ?
                ORDER BY order_number DESC
                """,
                (telegram_user_id,),
            ).fetchall()

    return [get_order(int(row["order_number"])) for row in rows]


def upsert_user(user_id: str, username: str | None = None, first_name: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_user_id, username, first_name, first_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (str(user_id), username, first_name, now_iso()),
        )
        conn.commit()


def get_all_user_ids() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT telegram_user_id FROM users").fetchall()
    return [row["telegram_user_id"] for row in rows]
