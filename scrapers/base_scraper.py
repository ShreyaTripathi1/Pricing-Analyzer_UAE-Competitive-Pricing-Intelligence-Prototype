"""
Base scraper class.

All platform-specific scrapers (Talabat, Noon, future) inherit from this.
Provides:
  - Shared HTTP session with retry logic and rate limiting
  - Standard logging interface
  - Abstract methods that each platform scraper must implement
  - Branch discovery and menu extraction contracts

Design: "Template Method" pattern — base class defines the algorithm skeleton,
subclasses fill in the platform-specific steps.
"""

import time
import random
import logging
import requests
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from config.settings import (
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    USER_AGENTS,
)

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """
    Abstract base for all platform scrapers.

    Subclasses must implement:
        discover_branches()  — find all outlets of a brand on this platform
        scrape_menu()        — extract menu + price data for one branch
    """

    def __init__(self, platform_id: str, brand_id: str):
        self.platform_id = platform_id
        self.brand_id    = brand_id
        self.session     = self._build_session()
        self.scraped_at  = datetime.utcnow().isoformat()

    # ── HTTP layer ────────────────────────────────────────────────────────────

    def _build_session(self) -> requests.Session:
        """Build a persistent HTTP session with default headers."""
        session = requests.Session()
        session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection":      "keep-alive",
        })
        return session

    def _rotate_user_agent(self) -> None:
        """Swap to a different User-Agent string (called on 403/429 responses)."""
        self.session.headers["User-Agent"] = random.choice(USER_AGENTS)

    def get(self, url: str, params: Optional[dict] = None,
            extra_headers: Optional[dict] = None) -> Optional[requests.Response]:
        """
        GET a URL with retry/backoff logic.

        Returns the Response on success, None after all retries exhausted.
        Handles:
          - Connection errors (network blip)
          - Timeout
          - 429 Too Many Requests (backs off longer)
          - 403 Forbidden (rotates User-Agent then retries)
          - 5xx server errors
        """
        if extra_headers:
            self.session.headers.update(extra_headers)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._polite_delay()
                logger.debug(f"GET {url} (attempt {attempt}/{MAX_RETRIES})")
                response = self.session.get(
                    url, params=params, timeout=REQUEST_TIMEOUT_SECONDS
                )

                if response.status_code == 200:
                    return response

                elif response.status_code == 429:
                    wait = RETRY_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(f"Rate limited (429). Waiting {wait}s before retry.")
                    time.sleep(wait)

                elif response.status_code == 403:
                    logger.warning(f"403 Forbidden on {url}. Rotating User-Agent.")
                    self._rotate_user_agent()
                    time.sleep(RETRY_BACKOFF_SECONDS)

                elif response.status_code >= 500:
                    wait = RETRY_BACKOFF_SECONDS * attempt
                    logger.warning(f"Server error {response.status_code}. Waiting {wait}s.")
                    time.sleep(wait)

                else:
                    logger.error(f"Unexpected status {response.status_code} for {url}")
                    return None

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on {url} (attempt {attempt})")
                time.sleep(RETRY_BACKOFF_SECONDS)

            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Connection error on {url}: {e} (attempt {attempt})")
                time.sleep(RETRY_BACKOFF_SECONDS)

        logger.error(f"All {MAX_RETRIES} attempts failed for {url}")
        return None

    def _polite_delay(self) -> None:
        """
        Sleep between requests to avoid hammering the server.
        Adds a small random jitter so requests don't fire at identical intervals.
        """
        jitter = random.uniform(0, 0.5)
        time.sleep(REQUEST_DELAY_SECONDS + jitter)

    # ── Abstract interface — subclasses implement these ───────────────────────

    @abstractmethod
    def discover_branches(self) -> list[dict]:
        """
        Find all UAE outlets for self.brand_id on this platform.

        Returns a list of branch dicts, each containing at minimum:
            {
                "branch_id":       str,   # composite key for DB
                "brand_id":        str,
                "platform_id":     str,
                "raw_id":          str,   # platform-native outlet ID
                "display_name":    str,
                "area_name":       str,
                "latitude":        float,
                "longitude":       float,
                "delivery_fee":    float,
                "min_order_amount": float,
                "avg_delivery_time": str,
                "outlet_url":      str,
                "last_scraped_at": str,   # ISO datetime
            }
        """
        raise NotImplementedError

    @abstractmethod
    def scrape_menu(self, branch: dict) -> dict:
        """
        Extract the full menu and prices for a single branch.

        branch: one of the dicts returned by discover_branches()

        Returns a dict:
            {
                "branch":    dict,          # the branch dict (pass-through)
                "items":     list[dict],    # menu item rows for DB
                "snapshots": list[dict],    # price snapshot rows for DB
            }
        """
        raise NotImplementedError

    # ── Orchestration — shared by all subclasses ──────────────────────────────

    def run(self) -> list[dict]:
        """
        Full pipeline for one brand on one platform:
          1. Discover all branches
          2. Scrape menu for each branch
          3. Return structured results

        The main orchestrator calls this and handles DB writes.
        """
        logger.info(f"[{self.platform_id.upper()}] Starting scrape for brand '{self.brand_id}'")

        branches = self.discover_branches()
        if not branches:
            logger.warning(f"No branches found for {self.brand_id} on {self.platform_id}")
            return []

        logger.info(f"[{self.platform_id.upper()}] Found {len(branches)} branches")

        results = []
        for i, branch in enumerate(branches, 1):
            logger.info(f"  [{i}/{len(branches)}] Scraping: {branch.get('display_name', branch['raw_id'])}")
            try:
                result = self.scrape_menu(branch)
                results.append(result)
            except Exception as e:
                logger.error(f"  Failed scraping branch {branch['raw_id']}: {e}", exc_info=True)
                continue

        logger.info(f"[{self.platform_id.upper()}] Done. {len(results)}/{len(branches)} branches scraped.")
        return results
