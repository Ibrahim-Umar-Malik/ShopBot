"""App database (SQLite). Every product query takes store_id and filters by it: tenant isolation lives here."""
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app import config
from app.catalog import ProductRow, parse_catalog

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id        INTEGER PRIMARY KEY,
    slug      TEXT UNIQUE NOT NULL,
    name      TEXT NOT NULL,
    tagline   TEXT NOT NULL DEFAULT '',
    city      TEXT NOT NULL DEFAULT '',
    currency  TEXT NOT NULL DEFAULT 'PKR'
);
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY,
    store_id    INTEGER NOT NULL REFERENCES stores(id),
    sku         TEXT NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    description TEXT NOT NULL,
    price       INTEGER NOT NULL,
    stock       TEXT NOT NULL,      -- JSON {"S": 4, "M": 0}
    colors      TEXT NOT NULL,      -- JSON list
    tags        TEXT NOT NULL,      -- JSON list
    pairs_with  TEXT NOT NULL,      -- JSON list of SKUs
    UNIQUE (store_id, sku)
);
CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
    name, category, description, colors, tags,
    product_id UNINDEXED, store_id UNINDEXED
);
"""

DEMO_STORES = [
    {"slug": "noor-threads", "name": "Noor Threads", "city": "Islamabad",
     "tagline": "Lawn, kurtas and festive wear, delivered across Pakistan"},
    {"slug": "kicks-corner", "name": "Kicks Corner", "city": "Rawalpindi",
     "tagline": "Sneakers and everyday shoes for the twin cities"},
]

STOPWORDS = {"a", "an", "the", "for", "and", "or", "of", "to", "in", "on", "with", "me", "my", "i",
             "need", "want", "show", "some", "something", "any", "do", "you", "have", "is", "are",
             "under", "below", "less", "than", "rs", "pkr", "please", "looking", "can", "get"}


@contextmanager
def connect(path: Path | None = None):
    conn = sqlite3.connect(path or config.APP_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path | None = None, seed: bool = True):
    path = path or config.APP_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        if seed:
            for store in DEMO_STORES:
                if not conn.execute("SELECT 1 FROM stores WHERE slug = ?", (store["slug"],)).fetchone():
                    store_id = create_store(conn, **store)
                    rows, errors = parse_catalog((config.SEED_DIR / f"{store['slug']}.csv").read_text("utf-8"))
                    if errors:
                        raise ValueError(f"demo catalog {store['slug']}: {errors}")
                    replace_catalog(conn, store_id, rows)


def create_store(conn, slug, name, tagline="", city="", currency="PKR") -> int:
    cur = conn.execute("INSERT INTO stores (slug, name, tagline, city, currency) VALUES (?, ?, ?, ?, ?)",
                       (slug, name, tagline, city, currency))
    return cur.lastrowid


def replace_catalog(conn, store_id: int, rows: list[ProductRow]):
    """Swap a store's whole catalog and rebuild its search index. Touches only this store's rows."""
    conn.execute("DELETE FROM products_fts WHERE store_id = ?", (store_id,))
    conn.execute("DELETE FROM products WHERE store_id = ?", (store_id,))
    for r in rows:
        cur = conn.execute(
            "INSERT INTO products (store_id, sku, name, category, description, price, stock, colors, tags, pairs_with)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (store_id, r.sku, r.name, r.category, r.description, r.price,
             json.dumps(r.stock), json.dumps(r.colors), json.dumps(r.tags), json.dumps(r.pairs_with)))
        conn.execute(
            "INSERT INTO products_fts (name, category, description, colors, tags, product_id, store_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r.name, r.category, r.description, " ".join(r.colors), " ".join(r.tags), cur.lastrowid, store_id))


# ---------- reads (all scoped by store_id) ----------

def get_store_by_slug(conn, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM stores WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def list_stores(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT s.*, COUNT(p.id) AS product_count FROM stores s LEFT JOIN products p ON p.store_id = s.id"
        " GROUP BY s.id ORDER BY s.id")]


def _product(row) -> dict:
    p = dict(row)
    for key in ("stock", "colors", "tags", "pairs_with"):
        p[key] = json.loads(p[key])
    p["in_stock"] = any(q > 0 for q in p["stock"].values())
    return p


def list_products(conn, store_id: int) -> list[dict]:
    return [_product(r) for r in conn.execute(
        "SELECT * FROM products WHERE store_id = ? ORDER BY category, price", (store_id,))]


def get_product(conn, store_id: int, product_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM products WHERE id = ? AND store_id = ?", (product_id, store_id)).fetchone()
    return _product(row) if row else None


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: words only, OR-ed, prefix-matched. Never passes raw user syntax."""
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 1]
    return " OR ".join(f'"{w}"*' for w in words[:8])


def search_products(conn, store_id: int, query: str = "", max_price: int | None = None,
                    category: str | None = None, limit: int = 5) -> list[dict]:
    where, params = ["p.store_id = ?"], [store_id]
    if max_price:
        where.append("p.price <= ?")
        params.append(max_price)
    if category:
        where.append("p.category LIKE ?")
        params.append(f"%{category.lower()}%")
    match = fts_query(query)
    if match:
        sql = ("SELECT p.* FROM products_fts f JOIN products p ON p.id = f.product_id"
               f" WHERE products_fts MATCH ? AND f.store_id = ? AND {' AND '.join(where)}"
               " ORDER BY bm25(products_fts) LIMIT ?")
        rows = conn.execute(sql, [match, store_id, *params, limit]).fetchall()
        if rows:
            return [_product(r) for r in rows]
    # No keywords, or nothing matched: fall back to the filters alone, cheapest first.
    sql = f"SELECT p.* FROM products p WHERE {' AND '.join(where)} ORDER BY p.price LIMIT ?"
    return [_product(r) for r in conn.execute(sql, [*params, limit])]


def get_addons(conn, store_id: int, product_id: int) -> list[dict]:
    product = get_product(conn, store_id, product_id)
    if not product or not product["pairs_with"]:
        return []
    marks = ",".join("?" * len(product["pairs_with"]))
    return [_product(r) for r in conn.execute(
        f"SELECT * FROM products WHERE store_id = ? AND sku IN ({marks})", [store_id, *product["pairs_with"]])]
