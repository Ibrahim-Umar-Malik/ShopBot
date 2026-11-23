"""The sales agent's tools. Each reads store_id from the runtime context: the model cannot pick a store.

Tools return (text for the model, artifact for the UI). The artifact carries product cards to the page.
"""
from langchain.tools import ToolRuntime, tool

from app import db
from app.agent.context import ShopContext


def _money(amount: int, currency: str) -> str:
    return f"{'Rs' if currency == 'PKR' else currency} {amount:,}"


def _sizes(stock: dict[str, int]) -> str:
    if list(stock) == ["free"]:
        return f"one size ({stock['free']} left)" if stock["free"] else "one size (sold out)"
    if list(stock) == ["unstitched"]:
        return f"unstitched ({stock['unstitched']} left)" if stock["unstitched"] else "unstitched (sold out)"
    return ", ".join(f"{s}:{q}" if q else f"{s}:sold out" for s, q in stock.items())


def _line(p: dict, currency: str) -> str:
    colors = "/".join(p["colors"]) or "n/a"
    return f"#{p['id']} {p['name']} | {p['category']} | {_money(p['price'], currency)} | colors {colors} | stock {_sizes(p['stock'])}"


def _card(p: dict) -> dict:
    return {k: p[k] for k in ("id", "name", "category", "price", "colors", "in_stock")}


@tool(response_format="content_and_artifact")
def search_products(query: str, runtime: ToolRuntime[ShopContext], max_price: int | None = None,
                    category: str | None = None):
    """Search this store's catalog.

    Args:
        query: what the customer wants in plain words, e.g. "eid embroidered lawn" or "white sneakers".
        max_price: optional budget cap in the store currency (whole number).
        category: optional category filter, e.g. "kurta", "lawn suit", "shoes", "sneakers".
    """
    ctx = runtime.context
    with db.connect() as conn:
        found = db.search_products(conn, ctx.store_id, query, max_price, category, limit=5)
    if not found:
        return "No matching products in this store.", []
    return "\n".join(_line(p, ctx.currency) for p in found), [_card(p) for p in found]


@tool(response_format="content_and_artifact")
def get_product(product_id: int, runtime: ToolRuntime[ShopContext]):
    """Full details of one product in this store: description, price, colours and stock per size."""
    ctx = runtime.context
    with db.connect() as conn:
        p = db.get_product(conn, ctx.store_id, product_id)
    if not p:
        return f"Product #{product_id} is not in this store.", []
    return f"{_line(p, ctx.currency)}\n{p['description']}", [_card(p)]


@tool
def check_stock(product_id: int, size: str, runtime: ToolRuntime[ShopContext]) -> str:
    """How many pieces of a product are left in one size (use "free" for one-size items)."""
    with db.connect() as conn:
        p = db.get_product(conn, runtime.context.store_id, product_id)
    if not p:
        return f"Product #{product_id} is not in this store."
    sizes = {s.lower(): (s, q) for s, q in p["stock"].items()}
    key = size.strip().lower()
    if key not in sizes and len(sizes) == 1:   # one-size items: any size question maps to the only size
        key = next(iter(sizes))
    if key not in sizes:
        return f"{p['name']} does not come in size {size}. Available sizes: {', '.join(p['stock'])}."
    label, qty = sizes[key]
    return f"{p['name']}, size {label}: {qty} in stock." if qty else f"{p['name']}, size {label}: sold out."


@tool(response_format="content_and_artifact")
def suggest_addons(product_id: int, runtime: ToolRuntime[ShopContext]):
    """Items the seller says go well with this product. Use to suggest one add-on (upsell)."""
    ctx = runtime.context
    with db.connect() as conn:
        addons = [p for p in db.get_addons(conn, ctx.store_id, product_id) if p["in_stock"]]
    if not addons:
        return "No add-ons listed for this product.", []
    return "\n".join(_line(p, ctx.currency) for p in addons), [_card(p) for p in addons]


SALES_TOOLS = [search_products, get_product, check_stock, suggest_addons]
