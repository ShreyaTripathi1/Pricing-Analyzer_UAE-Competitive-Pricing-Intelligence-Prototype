"""
Central configuration for the UAE Competitive Pricing Analyzer.
All platform URLs, target restaurants, scraping behavior, and DB paths live here.
To add a new platform or restaurant, this is the only file you touch.
"""

import os

# ── Project root ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH  = os.path.join(DATA_DIR, "pricing.db")

# ── Scraping behavior ─────────────────────────────────────────────────────────
REQUEST_DELAY_SECONDS   = 2.0
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES             = 3
RETRY_BACKOFF_SECONDS   = 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# ── Platform definitions ───────────────────────────────────────────────────────
PLATFORMS = {
    "talabat": {
        "name":          "Talabat",
        "base_url":      "https://www.talabat.com",
        "country_slug":  "uae",
        "method":        "html_embedded",
        "currency":      "AED",
        "vat_inclusive": False,
    },
    "noon": {
        "name":          "Noon Food",
        "base_url":      "https://food.noon.com",
        "country_slug":  "uae-en",
        "method":        "xhr_intercept",
        "currency":      "AED",
        "vat_inclusive": None,
    },
}

# ── Target restaurant brands ───────────────────────────────────────────────────
#
# talabat_branches: list of known branch URLs to seed scraping with.
#   Format: (branch_id_suffix, url)
#   We confirmed from recon that each Talabat branch has its own URL:
#     /uae/restaurant/{branchId}/{slug}
#   Branch metadata (name, area, lat/lng) is extracted from __NEXT_DATA__ on each page.
#   Add more branches here as you discover them — no code changes needed.
#
TARGET_BRANDS = {
    "mcdonalds": {
        "display_name":          "McDonald's",
        "talabat_restaurant_id": None,      # TODO: find from a McDonald's branch page
        "noon_outlet_prefix":    None,      # TODO: find from Noon Food URL
        "cuisines":              ["Fast Food", "Burgers"],
        "talabat_branches": [
            # Add McDonald's branch URLs here once identified
            # ("600001", "https://www.talabat.com/uae/restaurant/600001/mcdonalds-deira"),
        ],
        "noon_branches": [],
    },
    "puranmal": {
        "display_name":          "Puranmal Restaurant",
        "talabat_restaurant_id": 707584,    # confirmed from Phase 0 recon
        "noon_outlet_prefix":    "PRNML0KO1N",
        "noon_outlet_slug":      "PRNML0KO1N-Puranmal",  # full slug confirmed from recon URL
        "cuisines":              ["Indian", "Sweets"],
        # Confirmed branch URLs from Phase 0 recon + Talabat search results
        # Format: (raw_branch_id, full_url)
        "talabat_branches": [
            # aid = Talabat area/delivery-zone ID — required for menu data to load
            ("780202", "https://www.talabat.com/uae/restaurant/780202/puranmal-restaurant?aid=1291"),
            ("600747", "https://www.talabat.com/uae/restaurant/600747/puranmal-restaurant-dip-dubai-investments-park-1?aid=9376"),
            ("600737", "https://www.talabat.com/uae/restaurant/600737/puranmal-restaurant-al-barsha-1?aid=1276"),
            ("600748", "https://www.talabat.com/uae/restaurant/600748/puranmal-restaurant-sharjah?aid=1587"),
        ],
        "noon_branches": [
            # Noon branch outlet codes discovered from guest XHR
            # Will be auto-discovered by NoonScraper on first run
        ],
    },
}

# ── Discount taxonomy ──────────────────────────────────────────────────────────
DISCOUNT_TYPES = {
    "PERCENTAGE_OFF_ITEM":  "percentage",
    "FLAT_OFF_ITEM":        "flat",
    "BOGO":                 "bogo",
    "PLATFORM_PERCENTAGE":  "platform_pct",
    "FREE_DELIVERY":        "free_delivery",
    "NO_DISCOUNT":          "none",
}

# ── Fuzzy matching threshold ───────────────────────────────────────────────────
ITEM_MATCH_THRESHOLD = 85

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE  = os.path.join(BASE_DIR, "scraper.log")