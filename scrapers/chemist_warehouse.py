"""
Chemist Warehouse scraper.

CW is a Next.js SPA — products load via XHR after page hydration.
Strategy:
  1. Navigate to the search page with Playwright.
  2. Intercept all JSON responses to find the product search API call.
  3. Parse products from the captured response (tries multiple field patterns).
  4. Falls back to DOM scraping if no API response is captured.
"""

import asyncio
import re
import urllib.parse
from playwright.async_api import async_playwright

from models.product import Product
from scrapers.base import _make_context, _apply_stealth

_BASE_URL   = "https://www.chemistwarehouse.com.au"
_SEARCH_URL = "https://www.chemistwarehouse.com.au/search?search_query={query}"

# Known API path fragments for CW's product search XHR
_API_HINTS = ("search", "product", "catalogue", "_next/data")

# ─── Price regex ────────────────────────────────────────────────────────────────

_UNIT_RE = re.compile(
    r"\$?([\d.]+)\s*(?:per|/)\s*([\d.]+)\s*(g|ml|kg|l)",
    re.IGNORECASE,
)


async def scrape_chemist_warehouse(
    query: str, max_results: int = 10
) -> tuple[list[Product], list[str]]:
    async with async_playwright() as p:
        browser, context = await _make_context(p)
        page = await context.new_page()
        await _apply_stealth(page)

        captured: list[dict] = []

        async def on_response(response):
            ct = response.headers.get("content-type", "")
            if "json" not in ct or response.status != 200:
                return
            url = response.url.lower()
            if not any(h in url for h in _API_HINTS):
                return
            try:
                data = await response.json()
                if isinstance(data, dict):
                    captured.append(data)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            encoded = urllib.parse.quote(query)
            await page.goto(
                _SEARCH_URL.format(query=encoded),
                wait_until="networkidle",
                timeout=35_000,
            )
            await asyncio.sleep(3)

            # ── Try to parse products from captured API responses ──
            for data in captured:
                products = _parse_response(data, max_results)
                if products:
                    return products, []

            # ── Fallback: DOM scraping ──
            products = await _scrape_dom(page, max_results)
            return products, []

        except Exception:
            return [], []
        finally:
            await browser.close()


def _parse_response(data: dict, max_results: int) -> list[Product]:
    """
    Try multiple field-name patterns to extract products from a JSON response.
    CW may use different field names depending on their platform version.
    """
    # Find a product list in the response (try common container keys)
    product_list = None
    for key in ("Products", "products", "Results", "results", "items", "Items",
                "hits", "Hits", "productList", "ProductList"):
        val = data.get(key)
        if isinstance(val, list) and val:
            product_list = val
            break

    # Also try nested: data["pageProps"]["searchResults"]["products"] etc.
    if product_list is None:
        page_props = data.get("pageProps", {})
        for nested_key in ("searchResults", "products", "catalogue"):
            nested = page_props.get(nested_key, {})
            if isinstance(nested, dict):
                for key in ("Products", "products", "results", "items", "hits"):
                    val = nested.get(key)
                    if isinstance(val, list) and val:
                        product_list = val
                        break
            elif isinstance(nested, list) and nested:
                product_list = nested
                break

    if not product_list:
        return []

    products: list[Product] = []
    for item in product_list[:max_results]:
        if not isinstance(item, dict):
            continue
        p = _extract_product(item)
        if p:
            products.append(p)

    return products


def _extract_product(item: dict) -> Product | None:
    """Extract a Product from a CW API product dict (tries multiple field names)."""
    # ── Name ──
    name = (
        item.get("Name") or item.get("name") or
        item.get("DisplayName") or item.get("displayName") or
        item.get("title") or item.get("Title") or ""
    ).strip()
    brand = (item.get("BrandName") or item.get("brand") or item.get("Brand") or "").strip()
    if brand and not name.startswith(brand):
        name = f"{brand} {name}".strip()
    if not name:
        return None

    # ── Price ──
    raw_price = (
        item.get("SellPrice") or item.get("Price") or item.get("price") or
        item.get("RegularPrice") or item.get("regularPrice") or
        item.get("NowPrice") or item.get("specialPrice") or 0
    )
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    was_raw = item.get("WasPrice") or item.get("wasPrice") or item.get("normalPrice")
    was_price: float | None = None
    on_sale = False
    if was_raw:
        try:
            was_price = float(was_raw)
            on_sale = was_price > price
        except (TypeError, ValueError):
            pass

    # ── Unit price ──
    unit_price: float | None = None
    unit_price_display: str | None = None
    unit_measure: str | None = None

    unit_raw = (
        item.get("CupPrice") or item.get("cupPrice") or
        item.get("unitPrice") or item.get("UnitPrice") or
        item.get("pricePerUnit") or ""
    )
    unit_meas_raw = (
        item.get("CupMeasure") or item.get("cupMeasure") or
        item.get("unitMeasure") or item.get("UnitMeasure") or ""
    )
    if unit_raw and unit_meas_raw:
        try:
            unit_price = float(unit_raw)
            meas = unit_meas_raw.lower().strip()
            if "100g" in meas or "100ml" in meas:
                unit_measure = "g" if "g" in meas else "ml"
                unit_price_display = f"${unit_price:.2f} / 100{unit_measure}"
        except (TypeError, ValueError):
            pass

    # ── Image ──
    image_url = (
        item.get("SmallImageFile") or item.get("ImageUrl") or item.get("imageUrl") or
        item.get("image") or item.get("thumbnail") or None
    )
    if isinstance(image_url, dict):
        image_url = image_url.get("url") or image_url.get("src")

    # ── Product URL ──
    url_slug = (
        item.get("UrlName") or item.get("urlName") or item.get("slug") or
        item.get("ProductId") or item.get("id") or ""
    )
    if url_slug:
        product_url = f"{_BASE_URL}/buy/{url_slug}"
    else:
        product_url = _BASE_URL

    return Product(
        name=name,
        price=price,
        display_price=f"${price:.2f}",
        unit_price=unit_price,
        unit_price_display=unit_price_display,
        unit_measure=unit_measure,
        image_url=image_url,
        product_url=product_url,
        store="chemist_warehouse",
        on_sale=on_sale,
        was_price=was_price,
    )


async def _scrape_dom(page, max_results: int) -> list[Product]:
    """DOM fallback: try common product card selectors."""
    selectors_to_try = [
        ".product-grid-item",
        ".product-tile",
        ".search-result-item",
        '[data-testid="product-card"]',
        ".product-item",
    ]

    for sel in selectors_to_try:
        try:
            count = await page.locator(sel).count()
            if count > 0:
                return await _extract_dom_products(page, sel, max_results)
        except Exception:
            continue
    return []


async def _extract_dom_products(page, card_sel: str, max_results: int) -> list[Product]:
    cards = page.locator(card_sel)
    n = min(await cards.count(), max_results)
    products: list[Product] = []

    for i in range(n):
        card = cards.nth(i)

        # Name
        name = ""
        for name_sel in (".product-title", ".product-name", "h3", "h4", "[class*='name']"):
            el = card.locator(name_sel).first
            if await el.count() > 0:
                name = (await el.inner_text()).strip()
                if name:
                    break
        if not name:
            continue

        # Price
        price_text = ""
        for price_sel in (".Price", ".product-price", "[class*='price']", ".sell-price"):
            el = card.locator(price_sel).first
            if await el.count() > 0:
                price_text = (await el.inner_text()).strip()
                if price_text:
                    break
        m = re.search(r"\$?([\d.]+)", price_text)
        if not m:
            continue
        price = float(m.group(1))

        # Image
        image_url = None
        img = card.locator("img").first
        if await img.count() > 0:
            image_url = await img.get_attribute("src")

        # Product URL
        link = card.locator("a").first
        product_url = _BASE_URL
        if await link.count() > 0:
            href = await link.get_attribute("href") or ""
            product_url = href if href.startswith("http") else _BASE_URL + href

        products.append(Product(
            name=name,
            price=price,
            display_price=f"${price:.2f}",
            unit_price=None,
            unit_price_display=None,
            unit_measure=None,
            image_url=image_url,
            product_url=product_url,
            store="chemist_warehouse",
        ))

    return products
