"""
Noon Food scraper — UAE market.

Extraction strategy (confirmed from Phase 0 recon):
  - food.noon.com is a Next.js SPA — menu NOT in HTML
  - All menu + price data arrives in a single XHR called 'guest' (61.2 kB confirmed)
  - Endpoint: hit via background fetch after page JS executes
  - We use Playwright to drive a headless browser and intercept that call

Confirmed field names from recon (Puranmal outlet, guest XHR):
    menu.items[]:
        itemCode        → raw item ID (e.g. "I446098410A")
        itemIdentifier  → longer hash ID (secondary)
        name            → item name (e.g. "Baked Vada Pav (1 Pcs)")
        price           → current/discounted price (float, e.g. 8.0)
        listingPrice    → original pre-discount price (same as price if no discount)
        categoryCode    → menu section/category code
        itemType        → "main" for regular items
        modifiers       → list of add-on options (variants)
        image           → relative image path

    outlet-level fields (from same guest response):
        menuCode        → outlet menu identifier
        isAcceptingOrders → bool

Noon URL pattern confirmed:
    food.noon.com/uae-en/outlet/{OUTLET_CODE}-{RestaurantName}/
    e.g. food.noon.com/uae-en/outlet/PRNML0KO1N-Puranmal/
"""

import json
import logging
import asyncio
from datetime import datetime
from typing import Optional

from scrapers.base_scraper import BaseScraper
from config.settings import TARGET_BRANDS, REQUEST_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class NoonScraper(BaseScraper):

    def __init__(self, brand_id: str):
        super().__init__(platform_id="noon", brand_id=brand_id)
        self.brand_config  = TARGET_BRANDS[brand_id]
        self.base_url      = "https://food.noon.com"
        self.outlet_prefix = self.brand_config.get("noon_outlet_prefix")
        # Full slug confirmed from recon: PRNML0KO1N-Puranmal
        # Falls back to prefix alone if slug not configured
        self.outlet_slug   = (
            self.brand_config.get("noon_outlet_slug")
            or self.outlet_prefix
        )

    # ── Branch discovery ──────────────────────────────────────────────────────

    def discover_branches(self) -> list[dict]:
        """
        Load the brand listing page with Playwright and intercept the
        guest XHR which contains outlet data alongside menu data.
        """
        if not self.outlet_prefix:
            logger.error(f"No noon_outlet_prefix configured for brand {self.brand_id}")
            return []

        try:
            return asyncio.run(self._async_discover_branches())
        except Exception as e:
            logger.error(f"Playwright branch discovery failed: {e}", exc_info=True)
            return self._fallback_single_branch()

    async def _async_discover_branches(self) -> list[dict]:
        """Intercept the guest XHR on the brand page to find all outlets."""
        from playwright.async_api import async_playwright

        captured = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            async def handle_response(response):
                url = response.url
                # Target: the 'guest' endpoint confirmed in recon
                if "guest" in url or "outlet" in url or "branch" in url:
                    try:
                        body = await response.json()
                        captured.append({"url": url, "body": body,
                                         "size": len(json.dumps(body))})
                        logger.debug(f"Captured {len(json.dumps(body))} bytes: {url}")
                    except Exception:
                        pass

            page.on("response", handle_response)

            brand_url = f"{self.base_url}/uae-en/outlet/{self.outlet_slug}/"
            logger.info(f"Loading Noon brand page: {brand_url}")
            await page.goto(
                brand_url,
                timeout=REQUEST_TIMEOUT_SECONDS * 1000,
                wait_until="domcontentloaded",   # less strict than networkidle, avoids HTTP2 errors
            )
            # Wait for XHR calls to fire after initial DOM load
            await page.wait_for_timeout(5000)
            await browser.close()

        return self._extract_branches_from_captures(captured)

    def _extract_branches_from_captures(self, captures: list[dict]) -> list[dict]:
        """
        Find the outlet/branch list in the captured responses.
        The guest response may contain an 'outlets' or 'branches' array.
        """
        for cap in sorted(captures, key=lambda x: x["size"], reverse=True):
            body = cap["body"]

            # Noon may wrap outlets at top level or nested under data/brand
            outlets = (
                body.get("outlets")
                or body.get("branches")
                or body.get("locations")
                or body.get("data", {}).get("outlets")
                or body.get("data", {}).get("branches")
            )

            if outlets and isinstance(outlets, list):
                logger.info(f"Found {len(outlets)} outlets in: {cap['url']}")
                return [self._normalize_branch(o) for o in outlets]

        logger.warning("Outlet list not found in captures — using fallback single branch")
        return self._fallback_single_branch()

    def _fallback_single_branch(self) -> list[dict]:
        """Return the single known outlet when auto-discovery fails."""
        brand_name = self.brand_config["display_name"]
        raw_id     = self.outlet_prefix or "UNKNOWN"
        return [{
            "branch_id":         f"noon_{raw_id}",
            "brand_id":          self.brand_id,
            "platform_id":       "noon",
            "raw_id":            raw_id,
            "display_name":      brand_name,
            "area_name":         "",
            "latitude":          None,
            "longitude":         None,
            "delivery_fee":      None,
            "min_order_amount":  None,
            "avg_delivery_time": "",
            "outlet_url":        (
                f"{self.base_url}/uae-en/outlet/{self.outlet_slug}/"
            ),
            "last_scraped_at":   datetime.utcnow().isoformat(),
        }]

    # ── Menu scraping ─────────────────────────────────────────────────────────

    def scrape_menu(self, branch: dict) -> dict:
        """
        Load the outlet page with Playwright and intercept the guest XHR
        which carries the full menu with confirmed fields:
            menu.items[].itemCode / .name / .price / .listingPrice / .categoryCode
        """
        try:
            items, snapshots = asyncio.run(self._async_scrape_menu(branch))
            logger.info(f"  Extracted {len(items)} items from {branch['display_name']}")
            return {"branch": branch, "items": items, "snapshots": snapshots}
        except Exception as e:
            logger.error(f"Playwright menu scrape failed for {branch['raw_id']}: {e}",
                         exc_info=True)
            return {"branch": branch, "items": [], "snapshots": []}

    async def _async_scrape_menu(self, branch: dict) -> tuple[list, list]:
        """Async Playwright session — intercept guest XHR and parse menu."""
        from playwright.async_api import async_playwright

        captured = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            async def handle_response(response):
                url = response.url
                # 'guest' confirmed as the XHR carrying full menu data
                if "guest" in url:
                    try:
                        body = await response.json()
                        size = len(json.dumps(body))
                        captured.append({"url": url, "body": body, "size": size})
                        logger.debug(f"Captured guest response: {size} bytes from {url}")
                    except Exception:
                        pass

            page.on("response", handle_response)

            logger.info(f"Loading Noon menu page: {branch['outlet_url']}")
            await page.goto(
                branch["outlet_url"],
                timeout=REQUEST_TIMEOUT_SECONDS * 1000,
                wait_until="domcontentloaded",   # avoids HTTP2 protocol errors
            )
            await page.wait_for_timeout(6000)    # give the guest XHR time to fire
            await browser.close()

        if not captured:
            logger.warning(f"No guest XHR captured for {branch['display_name']}")
            return [], []

        # Use the largest captured response (most data)
        best = max(captured, key=lambda x: x["size"])
        logger.debug(f"Using guest response: {best['size']} bytes from {best['url']}")

        scraped_at = datetime.utcnow().isoformat()
        return self._parse_menu(branch, best["body"], scraped_at)

    # ── Parsing helpers ───────────────────────────────────────────────────────

    def _parse_menu(self, branch: dict, data: dict,
                    scraped_at: str) -> tuple[list, list]:
        """
        Parse the confirmed Noon guest XHR structure:
            data.menu.items[]   (confirmed from Phase 0 recon)

        Each item has:
            itemCode        → ID
            name            → display name
            price           → current price (discounted if on sale)
            listingPrice    → original price (== price when no discount)
            categoryCode    → section identifier
            modifiers       → [] or list of add-on groups (variants)
        """
        items     = []
        snapshots = []

        # Confirmed path from recon: top-level 'menu' key -> 'items' array
        menu      = data.get("menu", {})
        raw_items = menu.get("items", [])

        # Also try direct 'items' in case structure varies by outlet
        if not raw_items:
            raw_items = data.get("items", [])

        if not raw_items:
            logger.warning(f"No items found in guest response for {branch['display_name']}")
            # Log the top-level keys to help diagnose
            logger.debug(f"Top-level keys in response: {list(data.keys())}")
            return [], []

        # Build a category code -> name map if categories exist
        categories = {
            c.get("categoryCode"): c.get("name", "")
            for c in data.get("categories", [])
            if c.get("categoryCode")
        }

        for raw in raw_items:
            # Skip non-main items (headers, separators, etc.)
            if raw.get("itemType") not in ("main", None, ""):
                continue

            item, snapshot = self._normalize_item(
                branch, raw, categories, scraped_at
            )
            if item:
                items.append(item)
                snapshots.append(snapshot)

        return items, snapshots

    def _normalize_branch(self, raw: dict) -> dict:
        """Map a raw Noon outlet dict to our standard branch schema."""
        raw_id     = str(
            raw.get("outletCode") or raw.get("id") or raw.get("code") or ""
        )
        brand_name = self.brand_config["display_name"]

        return {
            "branch_id":         f"noon_{raw_id}",
            "brand_id":          self.brand_id,
            "platform_id":       "noon",
            "raw_id":            raw_id,
            "display_name":      raw.get("name") or raw.get("outletName") or brand_name,
            "area_name":         raw.get("area") or raw.get("zone") or "",
            "latitude":          self._safe_float(raw.get("lat") or raw.get("latitude")),
            "longitude":         self._safe_float(raw.get("lng") or raw.get("longitude")),
            "delivery_fee":      self._safe_float(
                raw.get("deliveryFee") or raw.get("delivery_fee")
            ),
            "min_order_amount":  self._safe_float(
                raw.get("minimumOrder") or raw.get("min_order")
            ),
            "avg_delivery_time": str(raw.get("deliveryTime") or raw.get("eta") or ""),
            "outlet_url":        (
                raw.get("url")
                or f"{self.base_url}/uae-en/outlet/{raw_id}/"
            ),
            "last_scraped_at":   datetime.utcnow().isoformat(),
        }

    def _normalize_item(self, branch: dict, raw: dict,
                        categories: dict, scraped_at: str
                        ) -> tuple[Optional[dict], Optional[dict]]:
        """
        Map a raw Noon menu item to our DB schemas.

        Discount logic (confirmed field names from Phase 0 recon):
            price        = current price (already discounted if sale is active)
            listingPrice = original price (equals price when no discount)
        """
        raw_item_id = str(
            raw.get("itemCode") or raw.get("itemIdentifier") or ""
        )
        if not raw_item_id:
            return None, None

        price         = self._safe_float(raw.get("price"))
        listing_price = self._safe_float(raw.get("listingPrice"))

        # Discount: listingPrice > price means a discount is active
        is_discounted  = (
            listing_price is not None
            and price is not None
            and listing_price > price
        )
        original_price = listing_price if is_discounted else None
        discount_type  = "PERCENTAGE_OFF_ITEM" if is_discounted else "NO_DISCOUNT"
        discount_value = (
            round((1 - price / listing_price) * 100, 2)
            if is_discounted and listing_price and listing_price > 0 and price
            else None
        )

        # Resolve section name from category code
        cat_code     = raw.get("categoryCode") or ""
        section_name = categories.get(cat_code, cat_code)

        branch_id = branch["branch_id"]
        item_id   = f"{branch_id}_{raw_item_id}"

        item = {
            "item_id":      item_id,
            "branch_id":    branch_id,
            "platform_id":  "noon",
            "raw_item_id":  raw_item_id,
            "name":         raw.get("name") or "",
            "description":  raw.get("description") or "",
            "section_name": section_name,
            "has_variants": 1 if raw.get("modifiers") else 0,
            "image_url":    raw.get("image") or "",
            "is_available": 1 if raw.get("isAcceptingOrders", True) else 0,
        }

        snapshot = {
            "item_id":            item_id,
            "branch_id":          branch_id,
            "platform_id":        "noon",
            "base_price":         price or 0.0,
            "original_price":     original_price,
            "discount_type":      discount_type,
            "discount_value":     discount_value,
            "effective_price":    price or 0.0,
            "delivery_fee_share": None,
            "is_vat_inclusive":   0,   # TBD — confirm from real data
            "scraped_at":         scraped_at,
        }

        return item, snapshot

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
