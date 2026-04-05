import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel

import db
from scrapers.woolworths import scrape_woolworths
from scrapers.coles import scrape_coles
from scrapers.aldi import scrape_aldi
from scrapers.registry import STORE_REGISTRY
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


@app.get("/api/stores")
async def list_stores() -> list[dict]:
    """Return all available stores for the store selector UI."""
    return [
        {
            "key":      key,
            "label":    meta["label"],
            "color":    meta["color"],
            "category": meta["category"],
        }
        for key, meta in STORE_REGISTRY.items()
    ]


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
    Prefers the dominant measure type (weight vs volume).
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

class BasketRequest(BaseModel):
    items: list[str]
    stores: list[str] = list(STORE_REGISTRY.keys())   # default: all stores


@app.post("/api/basket")
async def basket_compare(req: BasketRequest) -> dict:
    """
    Given a shopping list and selected stores, find the cheapest option for
    each item at each store, then compute:
      - Single-store totals (cheapest one store for the full basket)
      - Optimal split (cheapest store per item, grouped by store)
      - Savings summary (cheapest recommended vs costliest available per item)
    All items are searched in parallel.
    """
    if not req.items or len(req.items) > 30:
        raise HTTPException(status_code=400, detail="Provide 1–30 items")

    # Validate and filter stores
    valid_stores = [s for s in req.stores if s in STORE_REGISTRY]
    if not valid_stores:
        raise HTTPException(status_code=400, detail="No valid stores selected")

    # ── Search all items in parallel across selected stores ──
    item_tasks = [_search_for_basket(item.strip(), valid_stores) for item in req.items]
    item_results = await asyncio.gather(*item_tasks, return_exceptions=True)

    # ── Initialise per-store basket accumulators ──
    basket: dict[str, dict] = {
        store: {"total": 0.0, "items": [], "missing": []}
        for store in valid_stores
    }

    # ── Per-item split tracking ──
    split_by_store: dict[str, list[dict]] = {store: [] for store in valid_stores}
    split_total = 0.0
    unavailable: list[str] = []
    savings_per_item: dict[str, dict] = {}

    for item, result in zip(req.items, item_results):
        item = item.strip()
        if isinstance(result, Exception):
            for store in valid_stores:
                basket[store]["missing"].append(item)
            unavailable.append(item)
            continue

        # result: dict[store_key -> list[Product]]
        # Find cheapest product per store for this item
        store_cheapest: dict[str, Product] = {}
        for store_key in valid_stores:
            products = result.get(store_key, [])
            if products:
                store_cheapest[store_key] = min(products, key=lambda p: p.price)

        # ── Update single-store totals ──
        for store_key in valid_stores:
            if store_key in store_cheapest:
                p = store_cheapest[store_key]
                basket[store_key]["total"] = round(basket[store_key]["total"] + p.price, 2)
                basket[store_key]["items"].append({
                    "query":         item,
                    "name":          p.name,
                    "price":         p.price,
                    "display_price": p.display_price,
                    "on_sale":       p.on_sale,
                    "product_url":   p.product_url,
                })
            else:
                basket[store_key]["missing"].append(item)

        if not store_cheapest:
            # Not found at any selected store
            unavailable.append(item)
            continue

        # ── Optimal split: globally cheapest store for this item ──
        best_store = min(store_cheapest, key=lambda s: store_cheapest[s].price)
        best_p = store_cheapest[best_store]
        worst_p = max(store_cheapest.values(), key=lambda p: p.price)

        split_total = round(split_total + best_p.price, 2)
        split_by_store[best_store].append({
            "query":         item,
            "name":          best_p.name,
            "price":         best_p.price,
            "display_price": best_p.display_price,
            "on_sale":       best_p.on_sale,
            "product_url":   best_p.product_url,
        })

        # ── Savings: cheapest vs costliest available ──
        saving = round(worst_p.price - best_p.price, 2)
        savings_per_item[item] = {
            "min_price":   best_p.price,
            "max_price":   worst_p.price,
            "saving":      saving,
            "best_store":  best_store,
        }

    # ── Determine cheapest single store (only stores with no missing items) ──
    complete_stores = {s: d for s, d in basket.items() if not d["missing"]}
    cheapest_store = (
        min(complete_stores, key=lambda s: complete_stores[s]["total"])
        if complete_stores else None
    )

    # ── Savings summary ──
    total_saving = round(sum(v["saving"] for v in savings_per_item.values()), 2)
    sum_of_max = round(sum(v["max_price"] for v in savings_per_item.values()), 2)
    saving_pct = round((total_saving / sum_of_max * 100), 1) if sum_of_max > 0 else 0.0

    # How much cheaper is split vs cheapest single store?
    savings_vs_single = 0.0
    if cheapest_store and split_total > 0:
        savings_vs_single = round(complete_stores[cheapest_store]["total"] - split_total, 2)

    # ── Build optimal_split subtotals ──
    optimal_split_stores = {}
    for store_key in valid_stores:
        items_list = split_by_store[store_key]
        if items_list:
            optimal_split_stores[store_key] = {
                "label":    STORE_REGISTRY[store_key]["label"],
                "items":    items_list,
                "subtotal": round(sum(i["price"] for i in items_list), 2),
            }

    return {
        "basket":          basket,
        "cheapest_store":  cheapest_store,
        "item_count":      len(req.items),
        "selected_stores": valid_stores,
        "optimal_split": {
            "split_total":       split_total,
            "savings_vs_single": savings_vs_single,
            "by_store":          optimal_split_stores,
            "unavailable":       unavailable,
        },
        "savings_summary": {
            "total_saving": total_saving,
            "saving_pct":   saving_pct,
            "per_item":     savings_per_item,
        },
    }


# ─── Basket search helper ────────────────────────────────────────────────────────

async def _search_for_basket(
    query: str,
    store_keys: list[str],
) -> dict[str, list[Product]]:
    """
    Search a single query across the given stores in parallel.
    Uses cache for each store where available.
    Returns dict[store_key -> list[Product]].
    """
    async def _one_store(store_key: str) -> tuple[str, list[Product]]:
        meta = STORE_REGISTRY[store_key]
        scraper = meta["scraper"]

        if meta["cache_type"] == "aldi":
            cached = db.get_aldi_cached(query)
            if cached is not None:
                return store_key, cached
            products, _ = await scraper(query)
            if products:
                db.save_aldi_products(query, products)
            return store_key, products
        else:
            cached = db.get_cached(store_key, query)
            if cached is not None:
                products, _ = cached
                return store_key, products
            products, suggestions = await scraper(query)
            db.save_cache(store_key, query, products, suggestions)
            return store_key, products

    tasks = [_one_store(sk) for sk in store_keys]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out: dict[str, list[Product]] = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        store_key, products = r
        out[store_key] = products

    return out
