import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

import db
from scrapers.woolworths import scrape_woolworths
from scrapers.coles import scrape_coles
from scrapers.aldi import scrape_aldi
from models.product import Product, SearchResponse


# ─── App startup ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()   # create tables if they don't exist
    yield

app = FastAPI(title="CentSaver", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    """Railway health check endpoint."""
    return {"status": "ok", "app": "CentSaver"}


@app.get("/")
async def root() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/search", response_model=SearchResponse)
async def search(q: str = Query(..., min_length=1, max_length=100)) -> SearchResponse:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Check Woolworths + Coles cache ──
    ww_cached = db.get_cached("woolworths", query)
    coles_cached = db.get_cached("coles", query)
    aldi_cached = db.get_aldi_cached(query)

    woolworths: list[Product]
    coles: list[Product]
    aldi: list[Product]
    ww_suggestions: list[str]
    coles_suggestions: list[str]
    served_from_cache = False

    if ww_cached and coles_cached and aldi_cached is not None:
        # Full cache hit — no scraping needed
        woolworths, ww_suggestions = ww_cached
        coles, coles_suggestions = coles_cached
        aldi = aldi_cached
        served_from_cache = True
    else:
        # At least one store needs live scraping — run all three in parallel.
        # Stores with a valid cache entry skip their scraper.
        tasks = [
            _get_woolworths(query, ww_cached),
            _get_coles(query, coles_cached),
            _get_aldi(query, aldi_cached),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        ww_result, coles_result, aldi_result = results

        if isinstance(ww_result, Exception):
            woolworths, ww_suggestions = [], []
        else:
            woolworths, ww_suggestions = ww_result

        if isinstance(coles_result, Exception):
            coles, coles_suggestions = [], []
        else:
            coles, coles_suggestions = coles_result

        if isinstance(aldi_result, Exception):
            aldi = []
        else:
            aldi = aldi_result

        # ── Persist results ──
        db.save_cache("woolworths", query, woolworths, ww_suggestions)
        db.save_cache("coles", query, coles, coles_suggestions)
        if aldi:
            db.save_aldi_products(query, aldi)

        # ── Record prices for trend tracking (fire-and-forget) ──
        all_products = woolworths + coles + aldi
        if all_products:
            db.record_prices(all_products)

    # ── Merge spelling suggestions (deduplicated) ──
    seen: set[str] = set()
    suggestions: list[str] = []
    for term in ww_suggestions + coles_suggestions:
        key = term.lower().strip()
        if key and key != query.lower() and key not in seen:
            seen.add(key)
            suggestions.append(term)

    # ── Best unit price across all three stores ──
    best = _find_best_unit_price(woolworths + coles + aldi)

    return SearchResponse(
        query=query,
        woolworths=woolworths,
        coles=coles,
        aldi=aldi,
        best_unit_price_store=best.store if best else None,
        best_unit_price_product=best,
        suggestions=suggestions,
        cached=served_from_cache,
        fetched_at=now_iso,
    )


# ─── Per-store helpers (return from cache or scrape) ────────────────────────────

async def _get_woolworths(
    query: str,
    cached: tuple[list[Product], list[str]] | None,
) -> tuple[list[Product], list[str]]:
    if cached is not None:
        return cached
    return await scrape_woolworths(query)


async def _get_coles(
    query: str,
    cached: tuple[list[Product], list[str]] | None,
) -> tuple[list[Product], list[str]]:
    if cached is not None:
        return cached
    return await scrape_coles(query)


async def _get_aldi(
    query: str,
    cached: list[Product] | None,
) -> list[Product]:
    if cached is not None:
        return cached
    products, _ = await scrape_aldi(query)
    return products


# ─── Best unit price logic ───────────────────────────────────────────────────────

def _find_best_unit_price(products: list[Product]) -> Product | None:
    """
    Return the product with the lowest unit price (per 100g or per 100ml).
    When both weight and volume products exist, prefer the dominant measure type
    (whichever has more results — likely the more relevant category for this search).
    """
    with_unit = [p for p in products if p.unit_price is not None and p.unit_measure]
    if not with_unit:
        return None

    by_measure: dict[str, list[Product]] = {}
    for p in with_unit:
        by_measure.setdefault(p.unit_measure, []).append(p)  # type: ignore[arg-type]

    dominant = max(by_measure, key=lambda m: len(by_measure[m]))
    return min(by_measure[dominant], key=lambda p: p.unit_price)  # type: ignore[return-value]


# ─── Cache management ────────────────────────────────────────────────────────────

@app.delete("/api/cache")
async def clear_cache(q: str | None = None) -> dict:
    """
    Clear the search cache.
    - DELETE /api/cache        → wipes everything
    - DELETE /api/cache?q=eggs → wipes only that query
    """
    count = db.clear_cache(q.strip() if q else None)
    return {"cleared": count, "query": q}


# ─── Price trend API ─────────────────────────────────────────────────────────────

@app.get("/api/trend")
async def price_trend(store: str, product: str) -> dict:
    trend = db.get_price_trend(store, product)
    if trend is None:
        return {"available": False}
    return {"available": True, **trend}


# ─── Shopping basket comparison ──────────────────────────────────────────────────

@app.post("/api/basket")
async def basket_compare(items: list[str]) -> dict:
    """
    Given a list of search terms (the user's shopping list), search each item
    across all three stores and return the total basket cost per store.
    """
    if not items or len(items) > 30:
        raise HTTPException(status_code=400, detail="Provide 1–30 items")

    # Run all item searches concurrently
    tasks = [search(q=item) for item in items]
    results: list[SearchResponse] = await asyncio.gather(*tasks, return_exceptions=True)

    basket: dict[str, dict] = {
        "woolworths": {"total": 0.0, "items": [], "missing": []},
        "coles":      {"total": 0.0, "items": [], "missing": []},
        "aldi":       {"total": 0.0, "items": [], "missing": []},
    }

    for item, result in zip(items, results):
        if isinstance(result, Exception):
            for store in basket:
                basket[store]["missing"].append(item)
            continue

        for store_key, products in [
            ("woolworths", result.woolworths),
            ("coles",      result.coles),
            ("aldi",       result.aldi),
        ]:
            if products:
                cheapest = min(products, key=lambda p: p.price)
                basket[store_key]["total"] = round(basket[store_key]["total"] + cheapest.price, 2)
                basket[store_key]["items"].append({
                    "query": item,
                    "name": cheapest.name,
                    "price": cheapest.price,
                    "display_price": cheapest.display_price,
                    "on_sale": cheapest.on_sale,
                    "product_url": cheapest.product_url,
                })
            else:
                basket[store_key]["missing"].append(item)

    # Determine cheapest store (only for stores with no missing items)
    complete_stores = {
        s: d for s, d in basket.items() if not d["missing"]
    }
    cheapest_store = (
        min(complete_stores, key=lambda s: complete_stores[s]["total"])
        if complete_stores else None
    )

    return {
        "basket": basket,
        "cheapest_store": cheapest_store,
        "item_count": len(items),
    }
