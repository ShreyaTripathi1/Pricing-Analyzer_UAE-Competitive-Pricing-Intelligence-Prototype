"""
Talabat scraper — UAE market.
Extraction strategy (confirmed from Phase 0 recon):
  - Menu data is embedded as JSON inside __NEXT_DATA__ script tag in the HTML
  - No separate API call needed — one GET per branch page gets everything
  - Branch discovery uses hardcoded seed URLs from settings.py
    (Talabat has no public branch-list API — seed URLs extracted from site manually)

Confirmed __NEXT_DATA__ structure:
    props.pageProps.pageData.restaurant
        .branchId           → raw outlet ID (e.g. 780202)
        .branchName         → outlet name (e.g. "Puranmal Restaurant, Meadows")
        .restaurantId       → parent brand ID (707584 for all Puranmal branches)
        .areaName           → area label (e.g. "Meadows")
        .deliveryFee        → AED (string, e.g. "5")
        .minimumOrderAmount → AED
        .avgDeliveryTime    → string (e.g. "15-30 mins")
        .latitude / .longitude
        .isVatInclusive     → false for UAE
        .discountText       → platform-level discount description (empty if none)

    props.pageProps.pageData.menuData.items[]
        .id                 → raw item ID (integer)
        .name               → item name
        .price              → current price AED (already discounted if active)
        .oldPrice           → original price (-1 means no discount)
        .isItemDiscount     → bool
        .hasChoices         → bool (True = has size/add-on variants)
        .sectionName        → menu category
        .description        → item description
        .image              → image URL
"""

import re
import json
import logging
from datetime import datetime
from typing import Optional

from scrapers.base_scraper import BaseScraper
from config.settings import TARGET_BRANDS

logger = logging.getLogger(__name__)

NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


class TalabatScraper(BaseScraper):

    def __init__(self, brand_id: str):
        super().__init__(platform_id="talabat", brand_id=brand_id)
        self.brand_config = TARGET_BRANDS[brand_id]
        self.base_url     = "https://www.talabat.com"
        self.extra_headers = {
            "Referer":         "https://www.talabat.com/uae",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }

    # ── Branch discovery ──────────────────────────────────────────────────────

    def discover_branches(self) -> list[dict]:
        """
        Build the branch list from hardcoded seed URLs in settings.py.
        Each seed URL is fetched and its __NEXT_DATA__ is parsed to extract
        the full branch metadata (name, area, lat/lng, delivery fee, etc.).
        This way settings.py only needs the URL — all data comes from the page.
        """
        seed_branches = self.brand_config.get("talabat_branches", [])

        if not seed_branches:
            logger.error(
                f"No talabat_branches configured for '{self.brand_id}' in settings.py. "
                f"Add branch URLs to TARGET_BRANDS['{self.brand_id}']['talabat_branches']."
            )
            return []

        logger.info(f"Discovering {len(seed_branches)} Talabat branches from seed URLs")
        branches = []

        for raw_id, url in seed_branches:
            logger.debug(f"  Fetching branch metadata: {url}")
            response = self.get(url, extra_headers=self.extra_headers)

            if response is None:
                logger.warning(f"  Could not fetch branch {raw_id} — skipping")
                continue

            page_data = self._extract_next_data(response.text)
            if not page_data:
                # Fallback: build minimal branch from URL alone
                logger.warning(f"  No __NEXT_DATA__ for branch {raw_id} — using URL-only fallback")
                branches.append(self._minimal_branch(raw_id, url))
                continue

            branch = self._branch_from_page_data(page_data, raw_id, url)
            if branch:
                branches.append(branch)
                logger.info(f"  Found: {branch['display_name']} ({branch['area_name']})")

        return branches

    # ── Menu scraping ─────────────────────────────────────────────────────────

    def scrape_menu(self, branch: dict) -> dict:
        """
        Fetch the branch menu page and parse the embedded __NEXT_DATA__ JSON.
        Branch metadata was already extracted during discover_branches(),
        so this call only needs to parse the menuData section.
        """
        url      = branch["outlet_url"]
        response = self.get(url, extra_headers=self.extra_headers)

        if response is None:
            logger.error(f"Failed to fetch menu for branch {branch['raw_id']}")
            return {"branch": branch, "items": [], "snapshots": []}

        page_data = self._extract_next_data(response.text)
        if not page_data:
            logger.error(f"No __NEXT_DATA__ found at {url}")
            return {"branch": branch, "items": [], "snapshots": []}

        items, snapshots = self._parse_menu(branch, page_data)
        logger.info(f"  Extracted {len(items)} items from {branch['display_name']}")

        return {"branch": branch, "items": items, "snapshots": snapshots}

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _extract_next_data(self, html: str) -> Optional[dict]:
        """Extract and parse the __NEXT_DATA__ JSON blob from Talabat's HTML."""
        match = NEXT_DATA_PATTERN.search(html)
        if not match:
            logger.warning("__NEXT_DATA__ script tag not found in HTML response")
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse __NEXT_DATA__ JSON: {e}")
            return None

    def _branch_from_page_data(self, page_data: dict,
                                raw_id: str, url: str) -> Optional[dict]:
        """
        Extract branch-level metadata from a Talabat restaurant page's __NEXT_DATA__.
        Navigates: props -> pageProps -> pageData -> restaurant
        """
        try:
            page_props     = page_data.get("props", {}).get("pageProps", {})
            # Confirmed path from diagnostic: initialMenuState (not pageData)
            initial_state  = page_props.get("initialMenuState", {})
            restaurant     = initial_state.get("restaurant", {})

            if not restaurant:
                logger.warning(f"No restaurant in initialMenuState for branch {raw_id}")
                return self._minimal_branch(raw_id, url)

            # Use branchId from page data as the authoritative raw_id
            page_raw_id = str(restaurant.get("branchId") or raw_id)
            slug        = restaurant.get("branchSlug") or restaurant.get("restaurantSlug") or page_raw_id

            return {
                "branch_id":         f"talabat_{page_raw_id}",
                "brand_id":          self.brand_id,
                "platform_id":       "talabat",
                "raw_id":            page_raw_id,
                "display_name":      (
                    restaurant.get("branchName")
                    or restaurant.get("name")
                    or f"Branch {page_raw_id}"
                ),
                "area_name":         restaurant.get("areaName") or "",
                "latitude":          self._safe_float(restaurant.get("latitude")),
                "longitude":         self._safe_float(restaurant.get("longitude")),
                "delivery_fee":      self._safe_float(restaurant.get("deliveryFee")),
                "min_order_amount":  self._safe_float(restaurant.get("minimumOrderAmount")),
                "avg_delivery_time": restaurant.get("avgDeliveryTime") or "",
                "outlet_url":        url,
                "last_scraped_at":   datetime.utcnow().isoformat(),
            }

        except (KeyError, TypeError, AttributeError) as e:
            logger.error(f"Branch metadata parse error for {raw_id}: {e}")
            return self._minimal_branch(raw_id, url)

    def _minimal_branch(self, raw_id: str, url: str) -> dict:
        """Fallback branch dict when we can't parse page data."""
        return {
            "branch_id":         f"talabat_{raw_id}",
            "brand_id":          self.brand_id,
            "platform_id":       "talabat",
            "raw_id":            raw_id,
            "display_name":      f"{self.brand_config['display_name']} (Branch {raw_id})",
            "area_name":         "",
            "latitude":          None,
            "longitude":         None,
            "delivery_fee":      None,
            "min_order_amount":  None,
            "avg_delivery_time": "",
            "outlet_url":        url,
            "last_scraped_at":   datetime.utcnow().isoformat(),
        }

    def _parse_menu(self, branch: dict, page_data: dict) -> tuple[list, list]:
        """
        Navigate __NEXT_DATA__ to find menuData.items[] and parse each item.
        Returns (items_list, snapshots_list) ready for DB insertion.
        """
        items     = []
        snapshots = []

        try:
            page_props    = page_data.get("props", {}).get("pageProps", {})
            # Confirmed path from diagnostic: initialMenuState.menuData.items (162 items)
            initial_state = page_props.get("initialMenuState", {})
            menu_data     = initial_state.get("menuData", {})
            raw_items     = menu_data.get("items", [])

            if not raw_items:
                logger.warning(
                    f"No menuData.items found for {branch['display_name']}. "
                    f"Keys in initialMenuState: {list(initial_state.keys())}"
                )
                return [], []

            scraped_at = datetime.utcnow().isoformat()
            for raw in raw_items:
                item, snapshot = self._normalize_item(branch, raw, scraped_at)
                if item:
                    items.append(item)
                    snapshots.append(snapshot)

        except (KeyError, TypeError, AttributeError) as e:
            logger.error(
                f"Menu parse error for {branch['display_name']}: {e}", exc_info=True
            )

        return items, snapshots

    def _normalize_item(self, branch: dict, raw: dict,
                        scraped_at: str) -> tuple[Optional[dict], Optional[dict]]:
        """
        Map a raw Talabat menu item dict to our standard DB schemas.

        Discount logic confirmed from Phase 0 recon:
            price     = current price (already discounted if isItemDiscount=True)
            oldPrice  = original price (-1 means no active discount)
        """
        raw_item_id = str(raw.get("id") or "")
        if not raw_item_id:
            return None, None

        base_price     = self._safe_float(raw.get("price"))
        old_price_raw  = raw.get("oldPrice", -1)
        original_price = (
            self._safe_float(old_price_raw)
            if old_price_raw is not None and old_price_raw != -1
            else None
        )
        is_discounted  = raw.get("isItemDiscount", False)

        discount_type  = "NO_DISCOUNT"
        discount_value = None
        if is_discounted and original_price and original_price > 0 and base_price:
            discount_type  = "PERCENTAGE_OFF_ITEM"
            discount_value = round((1 - base_price / original_price) * 100, 2)

        branch_id = branch["branch_id"]
        item_id   = f"{branch_id}_{raw_item_id}"

        item = {
            "item_id":      item_id,
            "branch_id":    branch_id,
            "platform_id":  "talabat",
            "raw_item_id":  raw_item_id,
            "name":         raw.get("name") or "",
            "description":  raw.get("description") or "",
            "section_name": raw.get("sectionName") or "",
            "has_variants": 1 if raw.get("hasChoices") else 0,
            "image_url":    raw.get("image") or "",
            "is_available": 1,
        }

        snapshot = {
            "item_id":            item_id,
            "branch_id":          branch_id,
            "platform_id":        "talabat",
            "base_price":         base_price or 0.0,
            "original_price":     original_price,
            "discount_type":      discount_type,
            "discount_value":     discount_value,
            "effective_price":    base_price or 0.0,
            "delivery_fee_share": None,
            "is_vat_inclusive":   0,
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