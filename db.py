"""
SQLite database layer for PriceComparator.

Tables:
  search_cache    — short-lived results for Woolworths and Coles (30-min TTL)
  aldi_products   — growing Aldi product catalogue (24-hr TTL, persists across searches)

The Aldi catalogue grows with every unique search. When a future user searches for
the same (or a cached) term, we serve from DB instantly without scraping.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models.product import Product

# On Railway, set DB_PATH=/data/centsaver.db (pointing at a persistent volume).
# Locally it defaults to the project directory.
DB_PATH = Path(os.getenv("DB_PATH", "centsaver.db"))

WW_COLES_TTL = timedelta(days=2)   # supermarket prices rarely change daily
ALDI_TTL = timedelta(days=2)       # same TTL for Aldi catalogue entries


# ─── Connection management ──────────────────────────────────────────────────────

@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # allow concurrent reads during writes
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Schema init ────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables on first run. Safe to call every startup (IF NOT EXISTS)."""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS search_cache (
                store           TEXT    NOT NULL,
                query           TEXT    NOT NULL,
                products_json   TEXT    NOT NULL,
                suggestions_json TEXT   NOT NULL DEFAULT '[]',
                fetched_at      TEXT    NOT NULL,
                PRIMARY KEY (store, query)
            );

            CREATE TABLE IF NOT EXISTS aldi_products (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                search_query        TEXT    NOT NULL,
                name                TEXT    NOT NULL,
                price               REAL    NOT NULL,
                display_price       TEXT    NOT NULL,
                unit_price          REAL,
                unit_price_display  TEXT,
                unit_measure        TEXT,
                image_url           TEXT,
                product_url         TEXT    NOT NULL,
                fetched_at          TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_aldi_query
                ON aldi_products(search_query);

            CREATE INDEX IF NOT EXISTS idx_cache_store_query
                ON search_cache(store, query);

            -- Price history: one row per product per day per store.
            -- Used to show price trends (up/down arrows, % change).
            CREATE TABLE IF NOT EXISTS price_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                store        TEXT    NOT NULL,
                product_name TEXT    NOT NULL,  -- normalised for fuzzy matching
                price        REAL    NOT NULL,
                recorded_at  TEXT    NOT NULL,  -- ISO date (YYYY-MM-DD)
                UNIQUE(store, product_name, recorded_at)
            );

            CREATE INDEX IF NOT EXISTS idx_history_store_name
                ON price_history(store, product_name);
        """)


# ─── Normalise query keys ───────────────────────────────────────────────────────

def _normalise(query: str) -> str:
    return query.lower().strip()


# ─── Woolworths / Coles cache ───────────────────────────────────────────────────

def get_cached(store: str, query: str) -> tuple[list[Product], list[str]] | None:
    """
    Return (products, suggestions) from cache if a fresh entry exists, else None.
    Fresh = fetched within the last 30 minutes.
    """
    key = _normalise(query)
    cutoff = (datetime.now(timezone.utc) - WW_COLES_TTL).isoformat()

    with _conn() as conn:
        row = conn.execute(
            "SELECT products_json, suggestions_json FROM search_cache "
            "WHERE store = ? AND query = ? AND fetched_at > ?",
            (store, key, cutoff),
        ).fetchone()

    if row is None:
        return None

    products = [Product(**p) for p in json.loads(row["products_json"])]
    suggestions: list[str] = json.loads(row["suggestions_json"])
    return products, suggestions


def save_cache(
    store: str,
    query: str,
    products: list[Product],
    suggestions: list[str],
) -> None:
    """Upsert cache entry for a Woolworths or Coles search."""
    key = _normalise(query)
    now = datetime.now(timezone.utc).isoformat()

    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO search_cache (store, query, products_json, suggestions_json, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(store, query) DO UPDATE SET
                products_json    = excluded.products_json,
                suggestions_json = excluded.suggestions_json,
                fetched_at       = excluded.fetched_at
            """,
            (store, key, json.dumps([p.model_dump() for p in products]),
             json.dumps(suggestions), now),
        )


# ─── Aldi catalogue ─────────────────────────────────────────────────────────────

def get_aldi_cached(query: str) -> list[Product] | None:
    """
    Return Aldi products from the catalogue if a fresh entry exists (< 24 hrs).
    Returns None if not cached or stale (caller should scrape live).
    """
    key = _normalise(query)
    cutoff = (datetime.now(timezone.utc) - ALDI_TTL).isoformat()

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT name, price, display_price, unit_price, unit_price_display,
                   unit_measure, image_url, product_url
            FROM aldi_products
            WHERE search_query = ? AND fetched_at > ?
            ORDER BY price ASC
            """,
            (key, cutoff),
        ).fetchall()

    if not rows:
        return None

    return [
        Product(
            name=r["name"],
            price=r["price"],
            display_price=r["display_price"],
            unit_price=r["unit_price"],
            unit_price_display=r["unit_price_display"],
            unit_measure=r["unit_measure"],
            image_url=r["image_url"],
            product_url=r["product_url"],
            store="aldi",
        )
        for r in rows
    ]


def save_aldi_products(query: str, products: list[Product]) -> None:
    """
    Persist freshly-scraped Aldi products to the catalogue.
    Replaces any existing rows for this query so stale data is not mixed in.
    """
    if not products:
        return

    key = _normalise(query)
    now = datetime.now(timezone.utc).isoformat()

    with _conn() as conn:
        # Delete previous entries for this query, then insert fresh results
        conn.execute("DELETE FROM aldi_products WHERE search_query = ?", (key,))
        conn.executemany(
            """
            INSERT INTO aldi_products
                (search_query, name, price, display_price,
                 unit_price, unit_price_display, unit_measure,
                 image_url, product_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    key, p.name, p.price, p.display_price,
                    p.unit_price, p.unit_price_display, p.unit_measure,
                    p.image_url, p.product_url, now,
                )
                for p in products
            ],
        )


# ─── Price history ──────────────────────────────────────────────────────────────

def record_prices(products: list[Product]) -> None:
    """
    Record today's price for each product (one row per product per store per day).
    Duplicate entries for the same day are silently ignored (ON CONFLICT IGNORE).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [
        (p.store, _normalise(p.name), p.price, today)
        for p in products
    ]
    with _conn() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO price_history (store, product_name, price, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )


def get_price_trend(store: str, product_name: str) -> dict | None:
    """
    Return price trend for a product over the last 30 days.
    Returns {current, previous, change, change_pct, trend: 'up'|'down'|'same'}
    or None if fewer than 2 data points exist.
    """
    key = _normalise(product_name)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT price, recorded_at FROM price_history
            WHERE store = ? AND product_name = ?
            ORDER BY recorded_at DESC LIMIT 30
            """,
            (store, key),
        ).fetchall()

    if len(rows) < 2:
        return None

    current = rows[0]["price"]
    previous = rows[-1]["price"]
    change = round(current - previous, 2)
    change_pct = round((change / previous) * 100, 1) if previous else 0
    trend = "up" if change > 0 else ("down" if change < 0 else "same")

    return {
        "current": current,
        "previous": previous,
        "change": change,
        "change_pct": change_pct,
        "trend": trend,
    }


def clear_cache(query: str | None = None) -> int:
    """
    Delete cache entries.
    - query=None  → wipe everything (search_cache + aldi_products)
    - query="foo" → wipe only that normalised query
    Returns total rows deleted.
    """
    total = 0
    with _conn() as conn:
        if query is None:
            total += conn.execute("DELETE FROM search_cache").rowcount
            total += conn.execute("DELETE FROM aldi_products").rowcount
        else:
            key = _normalise(query)
            total += conn.execute(
                "DELETE FROM search_cache WHERE query = ?", (key,)
            ).rowcount
            total += conn.execute(
                "DELETE FROM aldi_products WHERE search_query = ?", (key,)
            ).rowcount
    return total


def get_catalogue_stats() -> dict:
    """Return summary stats about the Aldi catalogue (useful for debugging)."""
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM aldi_products").fetchone()[0]
        queries = conn.execute(
            "SELECT COUNT(DISTINCT search_query) FROM aldi_products"
        ).fetchone()[0]
    return {"total_products": total, "unique_queries": queries}
