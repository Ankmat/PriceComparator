"""
Shared scraping infrastructure for CentSaver.

Woolworths and Coles are protected by Akamai Bot Manager, which blocks headless
browsers by inspecting TLS fingerprints (JA3/JA4). We bypass this using
`curl_cffi`, which uses libcurl with a patched BoringSSL stack that perfectly
impersonates real Chrome at the TLS layer.

Strategy for WW and Coles:
  1. Load the store homepage — Akamai sets session cookies after accepting the request.
  2. Use those cookies for the target API/page request.
  Both requests go through curl_cffi impersonating Chrome 124.

Aldi uses Playwright (DOM scraping) since their site requires JS rendering and
has less aggressive bot protection.
"""

import asyncio
from typing import Any

from curl_cffi.requests import AsyncSession

# ── Constants ───────────────────────────────────────────────────────────────────

IMPERSONATE = "chrome124"   # curl_cffi impersonation target

_BASE_HEADERS = {
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
]

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Kept for backward compatibility — aldi.py references these
_SPOOF_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
)


# ── curl_cffi helpers (Woolworths + Coles) ──────────────────────────────────────

_BLOCK_SIGNATURES = (
    "Pardon Our Interruption",
    "Access Denied",
    "Enable JavaScript and cookies to continue",
)

def _is_blocked(text: str) -> bool:
    """Return True if the response looks like a bot-block page."""
    return any(sig in text for sig in _BLOCK_SIGNATURES)


async def cffi_get(
    homepage_url: str,
    target_url: str,
    extra_headers: dict | None = None,
    settle_s: float = 0.8,
    timeout: int = 25,
) -> str | None:
    """
    Load `homepage_url` to establish session cookies, then GET `target_url`.
    Returns the response text, or None on failure/block.
    Retries once with a longer settle if the first attempt is bot-blocked.
    """
    headers = dict(_BASE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    for attempt, delay in enumerate([settle_s, 3.5]):
        try:
            async with AsyncSession() as session:
                await session.get(
                    homepage_url,
                    impersonate=IMPERSONATE,
                    headers={**headers, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
                    timeout=timeout,
                )
                await asyncio.sleep(delay)

                r = await session.get(
                    target_url,
                    impersonate=IMPERSONATE,
                    headers={
                        **headers,
                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                        "Referer": homepage_url,
                    },
                    timeout=timeout,
                )
                if r.status_code == 200 and not _is_blocked(r.text):
                    return r.text
                if attempt == 0 and r.status_code == 200:
                    # Bot-blocked on first attempt — retry with longer delay
                    continue
                return None
        except Exception:
            if attempt == 0:
                continue
            return None
    return None


async def cffi_post_json(
    homepage_url: str,
    api_url: str,
    payload: dict,
    referer: str | None = None,
    settle_s: float = 0.8,
    timeout: int = 25,
) -> dict | None:
    """
    Load `homepage_url` to establish session cookies, then POST JSON to `api_url`.
    Returns the parsed JSON dict, or None on failure.
    """
    nav_headers = {**_BASE_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    api_headers = {
        **_BASE_HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": homepage_url.rstrip("/"),
        "Referer": referer or homepage_url,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

    try:
        async with AsyncSession() as session:
            await session.get(
                homepage_url,
                impersonate=IMPERSONATE,
                headers=nav_headers,
                timeout=timeout,
            )
            await asyncio.sleep(settle_s)

            r = await session.post(
                api_url,
                impersonate=IMPERSONATE,
                headers=api_headers,
                json=payload,
                timeout=timeout,
            )
            if r.status_code == 200:
                return r.json()
            return None
    except Exception:
        return None


# ── Playwright helpers (Aldi) ───────────────────────────────────────────────────

try:
    from playwright_stealth import stealth_async as _stealth_async
    _HAS_STEALTH = True
except ImportError:
    _HAS_STEALTH = False


async def _make_context(p):
    """Launch headless Chromium with AU locale."""
    from playwright.async_api import Browser, BrowserContext
    browser = await p.chromium.launch(headless=True, args=_BROWSER_ARGS)
    context = await browser.new_context(
        user_agent=_USER_AGENT,
        viewport={"width": 1280, "height": 800},
        locale="en-AU",
        timezone_id="Australia/Sydney",
        extra_http_headers={"Accept-Language": "en-AU,en;q=0.9"},
    )
    return browser, context


async def _apply_stealth(page) -> None:
    if _HAS_STEALTH:
        await _stealth_async(page)
    else:
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            window.chrome = { runtime: {} };
        """)
