"""
Central registry of all supported store scrapers.

Each entry defines:
  label      — display name shown in UI
  scraper    — async function(query: str) -> tuple[list[Product], list[str]]
               (products, suggestions)
  color      — brand hex color for UI pills
  category   — "grocery" | "pharmacy" | "general"
  cache_type — "search"  → uses db.get_cached / db.save_cache
               "aldi"    → uses db.get_aldi_cached / db.save_aldi_products
"""

from scrapers.woolworths import scrape_woolworths
from scrapers.coles import scrape_coles
from scrapers.aldi import scrape_aldi
from scrapers.chemist_warehouse import scrape_chemist_warehouse
from scrapers.bigw import scrape_bigw

# Note: IGA removed — their online store is location-dependent (requires a
# specific local store to be selected), making national price comparison
# impossible without store selection infrastructure.

STORE_REGISTRY: dict[str, dict] = {
    "woolworths": {
        "label":      "Woolworths",
        "scraper":    scrape_woolworths,
        "color":      "#00a854",
        "category":   "grocery",
        "cache_type": "search",
    },
    "coles": {
        "label":      "Coles",
        "scraper":    scrape_coles,
        "color":      "#e2231a",
        "category":   "grocery",
        "cache_type": "search",
    },
    "aldi": {
        "label":      "Aldi",
        "scraper":    scrape_aldi,
        "color":      "#f4821f",
        "category":   "grocery",
        "cache_type": "aldi",
    },
    "chemist_warehouse": {
        "label":      "Chemist Warehouse",
        "scraper":    scrape_chemist_warehouse,
        "color":      "#d40000",
        "category":   "pharmacy",
        "cache_type": "search",
    },
    "bigw": {
        "label":      "Big W",
        "scraper":    scrape_bigw,
        "color":      "#0055a5",
        "category":   "general",
        "cache_type": "search",
    },
}
