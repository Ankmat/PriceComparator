"""
Coles-specific search scraper.

Uses curl_cffi to impersonate Chrome's TLS fingerprint, bypassing Akamai/Imperva.
Strategy:
  1. GET the Coles homepage to establish a valid session.
  2. GET the search page — the Next.js app embeds all results in <script id="__NEXT_DATA__">.
  3. Parse that embedded JSON (no separate API call needed).
"""

import json
import re
from urllib.parse import quote_plus

from models.product import Product
from scrapers.base import cffi_get

_HOMEPAGE   = "https://www.coles.com.au"
_SEARCH_URL = "https://www.coles.com.au/search?q={query}"

_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)


async def scrape_coles(
    query: str, max_results: int = 10
) -> tuple[list[Product], list[str]]:
    """
    Search Coles for `query` and return up to `max_results` products
    plus any spelling suggestions the API offers.
    """
    html = await cffi_get(
        homepage_url=_HOMEPAGE,
        target_url=_SEARCH_URL.format(query=quote_plus(query)),
    )

    if not html:
        return [], []

    data = _extract_next_data(html)
    if data is None:
        return [], []

    return _parse(data, max_results)


def _extract_next_data(html: str) -> dict | None:
    """Extract the __NEXT_DATA__ JSON blob embedded in the Next.js page HTML."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


# ─── JSON parsing ────────────────────────────────────────────────────────────────

def _parse(data: dict, max_results: int) -> tuple[list[Product], list[str]]:
    """
    Coles Next.js data structure (embedded in __NEXT_DATA__):
    {
      "props": {
        "pageProps": {
          "searchResults": {
            "results": [
              {
                "_type": "PRODUCT",
                "name": "Full Cream Milk 2L",
                "slug": "coles-full-cream-milk-2l-...",
                "imageUris": [{"uri": "https://..."}],
                "pricing": {
                  "now": 3.50,
                  "was": null,
                  "unit": {
                    "price": 1.75,
                    "quantity": 100,
                    "ofMeasureType": "g"
                  },
                  "comparable": "$1.75 per 100g"
                }
              }
            ],
            "correctedQuery": "milk",
            "didYouMean": ["oat milk"]
          }
        }
      }
    }

    Unit price normalisation: some versions report price per N units.
    We normalise to per 100 to match Woolworths' CupPrice convention.
    """
    products: list[Product] = []
    suggestions: list[str] = []

    # Navigate to searchResults
    try:
        search = (
            data.get("props", {})
                .get("pageProps", {})
                .get("searchResults", {})
        ) or {}
    except AttributeError:
        return [], []

    results: list = search.get("results") or []

    # Spelling suggestions
    corrected = (search.get("correctedQuery") or "").strip()
    if corrected:
        suggestions.append(corrected)
    for term in search.get("didYouMean") or []:
        t = (term or "").strip()
        if t and t not in suggestions:
            suggestions.append(t)

    for item in results:
        if item.get("_type") == "BANNER":
            continue

        pricing = item.get("pricing") or {}
        price = pricing.get("now")
        if price is None:
            continue

        name = item.get("name", "Unknown Product")
        slug = item.get("slug", "")

        image_list = item.get("imageUris") or item.get("images") or []
        image_url: str | None = image_list[0].get("uri") if image_list else None

        # Unit price — normalise to per 100g or per 100ml
        unit_info = pricing.get("unit") or {}
        raw_price = unit_info.get("price")
        quantity = unit_info.get("quantity") or unit_info.get("ofMeasureUnits")
        measure_type = (unit_info.get("ofMeasureType") or "").lower().strip()

        unit_price: float | None = None
        unit_price_display: str | None = None
        unit_measure: str | None = None

        if raw_price and quantity and measure_type in ("g", "ml", "kg", "l"):
            if measure_type == "kg":
                measure_type = "g"
                quantity = float(quantity) * 1000
            elif measure_type == "l":
                measure_type = "ml"
                quantity = float(quantity) * 1000

            unit_measure = measure_type
            unit_price = round((float(raw_price) / float(quantity)) * 100, 4)
            unit_price_display = f"${unit_price:.2f} / 100{measure_type}"

        # Fallback: parse "comparable" string, e.g. "$1.75 per 100g"
        if unit_price is None:
            comparable = (pricing.get("comparable") or "").strip()
            m = re.search(r"\$?([\d.]+)\s*(?:per|/)\s*100\s*(g|ml)", comparable, re.I)
            if m:
                unit_price = float(m.group(1))
                unit_measure = m.group(2).lower()
                unit_price_display = f"${unit_price:.2f} / 100{unit_measure}"

        was_raw = pricing.get("was")
        on_sale = was_raw is not None and float(was_raw) > float(price)
        was_price: float | None = float(was_raw) if on_sale else None

        products.append(
            Product(
                name=name,
                price=float(price),
                display_price=f"${price:.2f}",
                unit_price=unit_price,
                unit_price_display=unit_price_display,
                unit_measure=unit_measure,
                image_url=image_url,
                product_url=f"https://www.coles.com.au/product/{slug}",
                store="coles",
                on_sale=on_sale,
                was_price=was_price,
            )
        )

        if len(products) >= max_results:
            return products, suggestions

    return products, suggestions
