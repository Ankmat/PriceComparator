"""
Big W scraper.

BigW is a Woolworths Group company with a Next.js SPA frontend.
Strategy:
  1. Navigate to search page with Playwright.
  2. Intercept JSON responses to capture their search API call.
  3. Try multiple field patterns to extract products.
  4. Falls back to DOM scraping if no API data captured.
"""

import asyncio
import re
import urllib.parse
from playwright.async_api import async_playwright

from models.product import Product
from scrapers.base import _make_context, _apply_stealth

_BASE_URL   = "https://www.bigw.com.au"
_SEARCH_URL = "https://www.bigw.com.au/search?q={query}"

_API_HINTS = ("search", "product", "catalogue", "_next/data", "api", "graphql")

_UNIT_RE = re.compile(
    r"\$?([\d.]+)\s*(?:per|/)\s*([\d.]+)\s*(g|ml|kg|l)",
    re.IGNORECASE,
)


async def scrape_bigw(
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

            # ── Try API responses first ──
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
    """Try multiple field patterns to find and parse a product list."""
    product_list = _find_product_list(data)
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


def _find_product_list(data: dict) -> list | None:
    """Recursively search common keys for a product list."""
    # Direct keys
    for key in ("products", "Products", "results", "Results", "items", "Items",
                "hits", "searchResults", "productList"):
        val = data.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val
        if isinstance(val, dict):
            inner = _find_product_list(val)
            if inner:
                return inner

    # pageProps nesting (Next.js)
    page_props = data.get("pageProps", {})
    if isinstance(page_props, dict):
        return _find_product_list(page_props)

    return None


def _extract_product(item: dict) -> Product | None:
    # ── Name ──
    name = (
        item.get("name") or item.get("Name") or
        item.get("displayName") or item.get("title") or ""
    ).strip()
    if not name:
        return None

    # ── Price ──
    raw_price = (
        item.get("price") or item.get("Price") or
        item.get("sellPrice") or item.get("SellPrice") or
        item.get("currentPrice") or 0
    )
    # Price might be nested: {"price": {"current": {"value": 4.99}}}
    if isinstance(raw_price, dict):
        raw_price = (
            raw_price.get("current", {}).get("value") or
            raw_price.get("value") or
            raw_price.get("amount") or 0
        )
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    # ── Was price / on sale ──
    was_price: float | None = None
    on_sale = item.get("onSpecial") or item.get("isOnSale") or item.get("isSpecial") or False
    raw_was = item.get("wasPrice") or item.get("WasPrice") or item.get("previousPrice")
    if isinstance(raw_was, dict):
        raw_was = raw_was.get("value") or raw_was.get("amount")
    if raw_was:
        try:
            was_price = float(raw_was)
            on_sale = was_price > price
        except (TypeError, ValueError):
            pass

    # ── Unit price ──
    unit_price: float | None = None
    unit_price_display: str | None = None
    unit_measure: str | None = None

    cup_price = item.get("cupPrice") or item.get("CupPrice") or item.get("unitPrice")
    cup_meas  = item.get("cupMeasure") or item.get("CupMeasure") or item.get("unitMeasure") or ""
    if cup_price and cup_meas:
        try:
            unit_price = float(cup_price)
            meas = cup_meas.lower().strip()
            unit_measure = "g" if "g" in meas else ("ml" if "ml" in meas else None)
            if unit_measure:
                unit_price_display = f"${unit_price:.2f} / 100{unit_measure}"
        except (TypeError, ValueError):
            pass

    # ── Image ──
    image_url = (
        item.get("imageUrl") or item.get("ImageUrl") or item.get("image") or None
    )
    if isinstance(image_url, dict):
        image_url = image_url.get("url") or image_url.get("src")

    # ── Product URL ──
    slug = (
        item.get("urlKey") or item.get("slug") or item.get("productId") or
        item.get("sku") or item.get("id") or ""
    )
    product_url = (
        f"{_BASE_URL}/product/{slug}" if slug else
        item.get("productUrl") or item.get("url") or _BASE_URL
    )
    if not product_url.startswith("http"):
        product_url = _BASE_URL + product_url

    return Product(
        name=name,
        price=price,
        display_price=f"${price:.2f}",
        unit_price=unit_price,
        unit_price_display=unit_price_display,
        unit_measure=unit_measure,
        image_url=image_url,
        product_url=product_url,
        store="bigw",
        on_sale=bool(on_sale),
        was_price=was_price,
    )


async def _scrape_dom(page, max_results: int) -> list[Product]:
    """DOM fallback: try common BigW product card selectors."""
    for sel in (
        '[data-testid="product-tile"]',
        ".product-tile",
        ".search-result-item",
        ".product-card",
        '[class*="ProductCard"]',
    ):
        try:
            if await page.locator(sel).count() > 0:
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

        name = ""
        for name_sel in ("h3", "h4", "[class*='name']", "[class*='title']"):
            el = card.locator(name_sel).first
            if await el.count() > 0:
                name = (await el.inner_text()).strip()
                if name:
                    break
        if not name:
            continue

        price_text = ""
        for price_sel in ("[class*='price']", "[class*='Price']"):
            el = card.locator(price_sel).first
            if await el.count() > 0:
                price_text = (await el.inner_text()).strip()
                if price_text:
                    break
        m = re.search(r"\$?([\d.]+)", price_text)
        if not m:
            continue
        price = float(m.group(1))

        image_url = None
        img = card.locator("img").first
        if await img.count() > 0:
            image_url = await img.get_attribute("src")

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
            store="bigw",
        ))

    return products
