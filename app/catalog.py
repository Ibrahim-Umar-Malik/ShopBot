"""Parse and validate a seller's catalog CSV. Pure functions, no database, so they are easy to test.

Columns: sku, name, category, description, price, stock, colors, tags, pairs_with
  stock      "S:4|M:6|L:0"  or  "free:25" for one-size items
  colors     "black;white"
  pairs_with "NT-301;NT-402"  (SKUs the bot may upsell with this item)
"""
import csv
import io
from dataclasses import dataclass, field

REQUIRED = ["sku", "name", "category", "price", "stock"]


@dataclass
class ProductRow:
    sku: str
    name: str
    category: str
    description: str
    price: int
    stock: dict[str, int]
    colors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    pairs_with: list[str] = field(default_factory=list)


def _split(value: str, sep: str = ";") -> list[str]:
    return [v.strip() for v in (value or "").split(sep) if v.strip()]


def parse_stock(value: str) -> dict[str, int]:
    stock = {}
    for part in _split(value, "|"):
        size, _, qty = part.partition(":")
        if not size.strip() or not qty.strip().isdigit():
            raise ValueError(f"bad stock entry {part!r}, expected SIZE:QTY")
        stock[size.strip()] = int(qty)
    if not stock:
        raise ValueError("stock is empty")
    return stock


def parse_catalog(text: str) -> tuple[list[ProductRow], list[str]]:
    """Returns (rows, errors). Any error means the upload should be rejected as a whole."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        return [], [f"missing column(s): {', '.join(missing)}"]

    rows, errors, seen = [], [], set()
    for line, raw in enumerate(reader, start=2):
        try:
            sku = (raw.get("sku") or "").strip()
            if not sku:
                raise ValueError("sku is empty")
            if sku in seen:
                raise ValueError(f"duplicate sku {sku}")
            seen.add(sku)
            name = (raw.get("name") or "").strip()
            if not name:
                raise ValueError("name is empty")
            price_text = (raw.get("price") or "").strip()
            if not price_text.isdigit() or int(price_text) <= 0:
                raise ValueError(f"price must be a positive whole number, got {price_text!r}")
            rows.append(ProductRow(
                sku=sku, name=name,
                category=(raw.get("category") or "").strip().lower(),
                description=(raw.get("description") or "").strip(),
                price=int(price_text),
                stock=parse_stock(raw.get("stock") or ""),
                colors=_split(raw.get("colors") or ""),
                tags=_split(raw.get("tags") or "", " "),
                pairs_with=_split(raw.get("pairs_with") or ""),
            ))
        except ValueError as e:
            errors.append(f"row {line}: {e}")

    skus = {r.sku for r in rows}
    for r in rows:
        for other in r.pairs_with:
            if other not in skus:
                errors.append(f"{r.sku}: pairs_with points to unknown sku {other}")
    return rows, errors
