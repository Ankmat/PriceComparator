"""
Aldi-specific search scraper.

Aldi's search page (https://www.aldi.com.au/search?q=…) returns category
navigation links rather than product results. Products are on category pages
that require JavaScript rendering. Strategy:

  1. Navigate to the Aldi search page.
  2. Extract category links from the nav tree.
  3. Navigate to the first matching category page.
  4. Wait for product tiles to render, then extract them.

Products are persisted in the Aldi catalogue DB so future searches are served
instantly without re-scraping.
"""

import asyncio
import re
from playwright.async_api import async_playwright

from models.product import Product
from scrapers.base import _make_context, _apply_stealth

_SEARCH_URL  = "https://www.aldi.com.au/search?q={query}"
_BASE_URL    = "https://www.aldi.com.au"

# ── Selectors for the category product listing page ──────────────────────────────

_TILE_SEL    = ".product-tile"
_NAME_SEL    = "[data-test='product-tile__name'] p"
_BRAND_SEL   = "[data-test='product-tile__brandname'] p"
_PRICE_SEL   = ".base-price__regular span"
_UNIT_SEL    = "[data-test='product-tile__comparison-price'] p"
_IMG_SEL     = "img.base-image"
_LINK_SEL    = "a.product-tile__link"

# Regex to extract numeric unit price from e.g. "($1.60 per 1 L)" or "$0.80 per 100g"
_UNIT_RE = re.compile(
    r"\$?([\d.]+)\s*(?:per|/)\s*([\d.]+)\s*(g|ml|kg|l|100g|100ml)",
    re.IGNORECASE,
)


async def scrape_aldi(
    query: str, max_results: int = 10
) -> tuple[list[Product], list[str]]:
    """
    Search Aldi for `query`. Returns (products, suggestions=[]).
    Follows the two-step approach: search → category → product list.
    """
    async with async_playwright() as p:
        browser, context = await _make_context(p)
        page = await context.new_page()
        await _apply_stealth(page)

        try:
            # ── Step 1: load search page ──────────────────────────────────────
            await page.goto(
                _SEARCH_URL.format(query=query),
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await asyncio.sleep(1.5)

            # ── Step 2: find search-relevant category links ───────────────────
            # Aldi search shows a sidebar with ALL categories but also adds links
            # relevant to the query (their text contains the search term).
            # Filter by links whose text matches the query.
            # Build a list of meaningful words from the query (skip short/generic ones)
            stop_words = {"a", "an", "the", "and", "or", "for", "of", "in", "2l", "3l", "1l", "500g", "1kg", "2kg"}
            query_words = [w for w in query.lower().split() if len(w) > 2 and w not in stop_words]

            cat_links: list[str] = await page.evaluate(
                """
                (queryWords) => {
                    const links = Array.from(document.querySelectorAll(
                        'a[href*="/products/"]'
                    ));

                    // Score each link: text matches score higher than href-only matches.
                    // Prefer the most specific category (e.g. "Eggs" over "Dairy, Eggs & Fridge").
                    // Score = (text_word_matches * 100) / text_word_count
                    // so "Eggs" (1 word, 1 match) scores 100, "Dairy Eggs & Fridge" (4 words, 1 match) scores 25.
                    const scored = links.map(a => {
                        const linkText = (a.textContent || '').trim().toLowerCase();
                        const linkHref = (a.getAttribute('href') || '').toLowerCase();
                        const textWords = linkText.split(/[\s,&]+/).filter(Boolean);
                        const textMatches = queryWords.filter(w => linkText.includes(w)).length;
                        const hrefMatches = queryWords.filter(w => linkHref.includes(w)).length;
                        if (textMatches === 0) {
                            // No text match — only href match, low priority
                            return { href: a.getAttribute('href'), score: hrefMatches };
                        }
                        // Text match: score by specificity (more of text = the query → higher)
                        const score = (textMatches * 100) / Math.max(textWords.length, 1);
                        return { href: a.getAttribute('href'), score };
                    }).filter(x => x.href && x.score >= 10);  // require at least one text match

                    // Sort by score desc — most specific category first
                    scored.sort((a, b) => b.score - a.score);
                    if (scored.length > 0) {
                        return scored.map(x => x.href);
                    }

                    // No text match found — return empty (caller will scrape current page)
                    return [];
                }
                """,
                query_words,
            )

            category_url: str | None = None
            if cat_links:
                # Use the first category link found
                href = cat_links[0]
                category_url = href if href.startswith("http") else _BASE_URL + href

            if not category_url:
                # No category found — try to scrape the current page directly
                products = await _scrape_tiles(page, max_results)
                return products, []

            # ── Step 3: navigate to category page ────────────────────────────
            await page.goto(category_url, wait_until="domcontentloaded", timeout=30_000)

            # Wait for product tiles to render
            try:
                await page.wait_for_selector(_TILE_SEL, timeout=12_000, state="visible")
            except Exception:
                return [], []  # no products on category page

            await asyncio.sleep(2)  # let lazy-loaded images settle

            products = await _scrape_tiles(page, max_results)
            return products, []

        except Exception:
            return [], []
        finally:
            await browser.close()


# ─── DOM extraction ──────────────────────────────────────────────────────────────

async def _scrape_tiles(page, max_results: int) -> list[Product]:
    """Extract product data from .product-tile elements on the current page."""
    tiles = page.locator(_TILE_SEL)
    count = min(await tiles.count(), max_results)
    if count == 0:
        return []

    products: list[Product] = []

    for i in range(count):
        tile = tiles.nth(i)

        # ── Name ──
        name_el = tile.locator(_NAME_SEL).first
        brand_el = tile.locator(_BRAND_SEL).first
        name = ""
        brand = ""
        if await name_el.count() > 0:
            name = (await name_el.inner_text()).strip()
        if await brand_el.count() > 0:
            brand = (await brand_el.inner_text()).strip().title()
        full_name = f"{brand} {name}".strip() if brand else name
        if not full_name:
            continue

        # ── Price ──
        price_el = tile.locator(_PRICE_SEL).first
        if await price_el.count() == 0:
            continue
        price_text = (await price_el.inner_text()).strip()
        price_match = re.search(r"\$?([\d.]+)", price_text)
        if not price_match:
            continue
        price = float(price_match.group(1))

        # ── Unit price ──
        unit_price: float | None = None
        unit_price_display: str | None = None
        unit_measure: str | None = None

        unit_el = tile.locator(_UNIT_SEL).first
        if await unit_el.count() > 0:
            unit_text = (await unit_el.inner_text()).strip()
            m = _UNIT_RE.search(unit_text)
            if m:
                unit_val = float(m.group(1))
                qty_str  = m.group(2)
                meas     = m.group(3).lower()

                # Normalise to per 100 units
                qty = float(qty_str)
                if meas in ("l", "kg"):
                    meas = "ml" if meas == "l" else "g"
                    qty *= 1000
                if meas in ("100g", "100ml"):
                    meas = meas.replace("100", "")
                    qty = 100

                if qty > 0 and meas in ("g", "ml"):
                    unit_measure = meas
                    unit_price = round((unit_val / qty) * 100, 4)
                    unit_price_display = f"${unit_price:.2f} / 100{meas}"

        # ── Image ──
        img_el = tile.locator(_IMG_SEL).first
        image_url: str | None = None
        if await img_el.count() > 0:
            src = await img_el.get_attribute("src")
            if src and src.startswith("http"):
                image_url = src

        # ── Product URL ──
        link_el = tile.locator(_LINK_SEL).first
        product_url = _BASE_URL
        if await link_el.count() > 0:
            href = await link_el.get_attribute("href")
            if href:
                product_url = href if href.startswith("http") else _BASE_URL + href

        products.append(
            Product(
                name=full_name,
                price=price,
                display_price=f"${price:.2f}",
                unit_price=unit_price,
                unit_price_display=unit_price_display,
                unit_measure=unit_measure,
                image_url=image_url,
                product_url=product_url,
                store="aldi",
            )
        )

    return products
