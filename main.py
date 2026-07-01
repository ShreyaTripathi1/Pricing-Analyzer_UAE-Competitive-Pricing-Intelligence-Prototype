"""
Main pipeline orchestrator for the UAE Competitive Pricing Analyzer.

Run this file to execute a full scrape + store cycle.

Usage:
    python main.py                         # scrape all brands, all platforms
    python main.py --brand puranmal        # single brand, all platforms
    python main.py --platform talabat      # all brands, single platform
    python main.py --brand puranmal --platform talabat  # targeted

Pipeline stages:
    1. Init DB (idempotent — safe to re-run)
    2. For each target brand × platform:
        a. Discover all UAE branches
        b. Scrape menu + prices for each branch
        c. Write to DB (branches, menu_items, price_snapshots)
    3. Log summary

Next phase (not in this file yet):
    - Comparator module reads from DB and writes comparison table
    - Report generator reads comparison table and produces output
"""

import argparse
import logging
import sys
import os

# Make sure project root is on the path regardless of where we run from
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import TARGET_BRANDS, PLATFORMS, LOG_LEVEL, LOG_FILE
from models.database import (
    init_db, seed_platforms, seed_brands,
    db_session, upsert_branch, upsert_menu_item, insert_price_snapshot
)
from scrapers.talabat_scraper import TalabatScraper
from scrapers.noon_scraper    import NoonScraper

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level    = getattr(logging, LOG_LEVEL, logging.INFO),
    format   = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)
logger = logging.getLogger("main")

# ── Scraper registry — maps platform_id → scraper class ──────────────────────
SCRAPER_REGISTRY = {
    "talabat": TalabatScraper,
    "noon":    NoonScraper,
}


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_pipeline(target_brands: list[str], target_platforms: list[str]) -> None:
    """
    Execute the full scrape pipeline for the given brands and platforms.
    Writes all results to the SQLite database.
    """
    logger.info("=" * 60)
    logger.info("UAE COMPETITIVE PRICING ANALYZER — PIPELINE START")
    logger.info(f"Brands:    {target_brands}")
    logger.info(f"Platforms: {target_platforms}")
    logger.info("=" * 60)

    total_branches = 0
    total_items    = 0
    total_snapshots = 0
    errors         = []

    for brand_id in target_brands:
        if brand_id not in TARGET_BRANDS:
            logger.warning(f"Unknown brand '{brand_id}' — skipping")
            continue

        for platform_id in target_platforms:
            if platform_id not in SCRAPER_REGISTRY:
                logger.warning(f"No scraper registered for '{platform_id}' — skipping")
                continue

            logger.info(f"\n[SCRAPING] {brand_id} on {platform_id}")

            try:
                ScraperClass = SCRAPER_REGISTRY[platform_id]
                scraper      = ScraperClass(brand_id=brand_id)
                results      = scraper.run()

                # Write results to DB
                with db_session() as conn:
                    for result in results:
                        branch    = result["branch"]
                        items     = result["items"]
                        snapshots = result["snapshots"]

                        upsert_branch(conn, branch)
                        total_branches += 1

                        for item in items:
                            upsert_menu_item(conn, item)
                        total_items += len(items)

                        for snapshot in snapshots:
                            insert_price_snapshot(conn, snapshot)
                        total_snapshots += len(snapshots)

                logger.info(
                    f"[DONE] {brand_id}/{platform_id} — "
                    f"{len(results)} branches, "
                    f"{sum(len(r['items']) for r in results)} items"
                )

            except Exception as e:
                err = f"{brand_id}/{platform_id}: {e}"
                logger.error(f"[FAILED] {err}", exc_info=True)
                errors.append(err)

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"  Branches scraped:    {total_branches}")
    logger.info(f"  Menu items stored:   {total_items}")
    logger.info(f"  Price snapshots:     {total_snapshots}")
    if errors:
        logger.warning(f"  Errors ({len(errors)}):")
        for err in errors:
            logger.warning(f"    - {err}")
    logger.info("=" * 60)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="UAE Competitive Pricing Analyzer — scrape and store menu prices"
    )
    parser.add_argument(
        "--brand", "-b",
        choices=list(TARGET_BRANDS.keys()) + ["all"],
        default="all",
        help="Brand to scrape (default: all)"
    )
    parser.add_argument(
        "--platform", "-p",
        choices=list(SCRAPER_REGISTRY.keys()) + ["all"],
        default="all",
        help="Platform to scrape (default: all)"
    )
    parser.add_argument(
        "--init-only",
        action="store_true",
        help="Only initialise the database, do not scrape"
    )

    args = parser.parse_args()

    # Always init first — idempotent
    logger.info("Initialising database...")
    init_db()
    seed_platforms()
    seed_brands()

    if args.init_only:
        logger.info("--init-only flag set. Database ready. Exiting.")
        return

    brands    = list(TARGET_BRANDS.keys())    if args.brand    == "all" else [args.brand]
    platforms = list(SCRAPER_REGISTRY.keys()) if args.platform == "all" else [args.platform]

    run_pipeline(brands, platforms)


if __name__ == "__main__":
    main()
