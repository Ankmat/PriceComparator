"""
Woolworths-specific search scraper.

Uses curl_cffi to impersonate Chrome's TLS fingerprint, bypassing Akamai Bot Manager.
Strategy:
  1. GET the Woolworths homepage to establish a valid session (Akamai cookies).
  2. POST to the internal REST search API using those session cookies.
"""

from urllib.parse import quote_plus

from models.product import Product
from scrapers.base import cffi_post_json

_HOMEPAGE = "https://www.woolworths.com.au"
_API_URL   = "https://www.woolworths.com.au/apis/ui/Search/products"


async def scrape_woolworths(
    query: str, max_results: int = 10
) -> tuple[list[Product], list[str]]:
    """
    Search Woolworths for `query` and return up to `max_results` products
    plus any spelling suggestions the API offers.
    """
    payload = {
        "Filters": [],
        "IsSpecial": False,
        "Location": f"/shop/search/products?searchTerm={quote_plus(query)}",
        "PageNumber": 1,
        "PageSize": max(max_results + 5, 36),
        "SearchTerm": query,
        "SortType": "TraderRelevance",
        "token": "",
        "gpBoost": 0,
        "IsBundle": False,
        "isMobile": False,
    }

    data = await cffi_post_json(
        homepage_url=_HOMEPAGE,
        api_url=_API_URL,
        payload=payload,
        referer=f"https://www.woolworths.com.au/shop/search/products?searchTerm={quote_plus(query)}",
    )

    if data is None:
        return [], []

    return _parse(data, max_results)


# ─── JSON parsing ────────────────────────────────────────────────────────────────

def _parse(data: dict, max_results: int) -> tuple[list[Product], list[str]]:
    """
    Woolworths API response structure:
    {
      "Products": [
        {
          "Products": [
            {
              "Name": "...",
              "Price": 3.50,
              "CupPrice": 1.75,
              "CupMeasure": "100G",
              "SmallImageFile": "https://...",
              "Stockcode": 123456,
              "IsOnSpecial": false,
              "WasPrice": null
            }
          ]
        }
      ],
      "Corrections": [{"Term": "milk"}],
      "Suggestions": ["oat milk", "skim milk"]
    }
    """
    products: list[Product] = []

    # Spelling suggestions
    suggestions: list[str] = []
    for correction in data.get("Corrections") or []:
        term = (correction.get("Term") or "").strip()
        if term:
            suggestions.append(term)
    for s in data.get("Suggestions") or []:
        if isinstance(s, str) and s.strip() and s not in suggestions:
            suggestions.append(s.strip())

    # Products — outer list is brand groups, each containing individual SKUs
    for group in data.get("Products") or []:
        for item in group.get("Products") or []:
            price = item.get("Price")
            if price is None:
                continue

            name = item.get("Name", "Unknown Product")
            stockcode = item.get("Stockcode", "")
            cup_price = item.get("CupPrice")
            cup_measure_raw = (item.get("CupMeasure") or "").strip()
            image = item.get("SmallImageFile") or None

            unit_price: float | None = None
            unit_price_display: str | None = None
            unit_measure: str | None = None

            if cup_price and cup_measure_raw:
                import re as _re
                m_text = cup_measure_raw.strip().lower()
                # Parse quantity + unit from CupMeasure e.g. "100G", "1L", "1KG", "100ML"
                qty_match = _re.match(r"([\d.]+)\s*(g|ml|kg|l|kg)\b", m_text)
                if qty_match:
                    qty = float(qty_match.group(1))
                    unit = qty_match.group(2)
                    # Normalise kg→g, L→ml then scale to per-100
                    if unit == "kg":
                        unit = "g"
                        qty *= 1000
                    elif unit == "l":
                        unit = "ml"
                        qty *= 1000
                    if unit in ("g", "ml") and qty > 0:
                        unit_measure = unit
                        unit_price = round((float(cup_price) / qty) * 100, 4)
                        unit_price_display = f"${unit_price:.2f} / 100{unit}"

            on_sale = bool(item.get("IsOnSpecial"))
            was_price_raw = item.get("WasPrice")
            was_price: float | None = float(was_price_raw) if was_price_raw else None

            products.append(
                Product(
                    name=name,
                    price=float(price),
                    display_price=f"${price:.2f}",
                    unit_price=unit_price,
                    unit_price_display=unit_price_display,
                    unit_measure=unit_measure,
                    image_url=image,
                    product_url=f"https://www.woolworths.com.au/shop/productdetails/{stockcode}",
                    store="woolworths",
                    on_sale=on_sale,
                    was_price=was_price,
                )
            )

            if len(products) >= max_results:
                return products, suggestions

    return products, suggestions
