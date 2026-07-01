"""
Database layer for the UAE Competitive Pricing Analyzer.

Schema design principles:
  - Normalized: brands, branches and items are separate tables so we never repeat
    a restaurant name 200 times in the price table.
  - Audit-friendly: every price row carries a scraped_at timestamp so we can track
    price changes over time (the foundation of a time-series comparison later).
  - Platform-agnostic: same schema works for Talabat, Noon, and any future platform
    — platform_id is just a string foreign key.
  - Migration-ready: designed to drop into PostgreSQL with minimal changes
    (no SQLite-isms in the data model, just in the connection string).

Tables
------
  platforms        — Talabat, Noon Food, etc.
  brands           — McDonald's, Puranmal (parent brand, not a specific outlet)
  branches         — Individual physical outlets (one brand has many branches)
  menu_items       — Canonical item list per branch per platform
  price_snapshots  — Price + discount at a point in time (append-only)
  comparisons      — Computed deltas between platforms (written by comparator)
"""

import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager
from config.settings import DB_PATH

logger = logging.getLogger(__name__)


# ── Schema DDL ────────────────────────────────────────────────────────────────

SCHEMA = """
-- Delivery platforms we scrape
CREATE TABLE IF NOT EXISTS platforms (
    platform_id   TEXT PRIMARY KEY,          -- e.g. 'talabat', 'noon'
    display_name  TEXT NOT NULL,             -- e.g. 'Talabat', 'Noon Food'
    base_url      TEXT NOT NULL,
    currency      TEXT NOT NULL DEFAULT 'AED',
    vat_inclusive INTEGER NOT NULL DEFAULT 0, -- 0=False, 1=True
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Parent restaurant brands (not outlets)
CREATE TABLE IF NOT EXISTS brands (
    brand_id      TEXT PRIMARY KEY,          -- e.g. 'mcdonalds', 'puranmal'
    display_name  TEXT NOT NULL,
    cuisine_tags  TEXT,                      -- JSON array stored as text
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Individual physical outlets / branches
CREATE TABLE IF NOT EXISTS branches (
    branch_id         TEXT PRIMARY KEY,      -- composite: '{platform_id}_{raw_id}'
    brand_id          TEXT NOT NULL REFERENCES brands(brand_id),
    platform_id       TEXT NOT NULL REFERENCES platforms(platform_id),
    raw_id            TEXT NOT NULL,         -- platform-native ID (e.g. '780202')
    display_name      TEXT NOT NULL,         -- e.g. 'Puranmal Restaurant, Meadows'
    area_name         TEXT,                  -- e.g. 'Meadows', 'Al Barsha'
    latitude          REAL,
    longitude         REAL,
    delivery_fee      REAL,
    min_order_amount  REAL,
    avg_delivery_time TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1,
    outlet_url        TEXT,                  -- full URL to this branch's menu page
    last_scraped_at   TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform_id, raw_id)              -- prevent duplicate branch inserts
);

-- Menu items as seen on a specific branch/platform
-- An item row is the "catalogue entry" — price history is in price_snapshots
CREATE TABLE IF NOT EXISTS menu_items (
    item_id       TEXT PRIMARY KEY,          -- composite: '{branch_id}_{raw_item_id}'
    branch_id     TEXT NOT NULL REFERENCES branches(branch_id),
    platform_id   TEXT NOT NULL REFERENCES platforms(platform_id),
    raw_item_id   TEXT NOT NULL,             -- platform-native item ID
    name          TEXT NOT NULL,
    description   TEXT,
    section_name  TEXT,                      -- menu category (e.g. 'Starters', 'Mains')
    has_variants  INTEGER NOT NULL DEFAULT 0, -- 1 if item has size/add-on choices
    image_url     TEXT,
    is_available  INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(branch_id, raw_item_id)
);

-- Append-only price log — one row per scrape per item
-- This is the core of the time-series capability
CREATE TABLE IF NOT EXISTS price_snapshots (
    snapshot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id           TEXT NOT NULL REFERENCES menu_items(item_id),
    branch_id         TEXT NOT NULL REFERENCES branches(branch_id),
    platform_id       TEXT NOT NULL REFERENCES platforms(platform_id),
    base_price        REAL NOT NULL,         -- listed price (may be post-discount)
    original_price    REAL,                  -- pre-discount price (NULL if no discount)
    discount_type     TEXT,                  -- from DISCOUNT_TYPES in settings
    discount_value    REAL,                  -- numeric value of discount (% or flat AED)
    effective_price   REAL NOT NULL,         -- computed: true cost to consumer
    delivery_fee_share REAL,                 -- allocated portion of delivery fee
    is_vat_inclusive  INTEGER NOT NULL DEFAULT 0,
    scraped_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Comparison results written by the comparator module
CREATE TABLE IF NOT EXISTS comparisons (
    comparison_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id            TEXT NOT NULL REFERENCES brands(brand_id),
    item_name_normalized TEXT NOT NULL,      -- fuzzy-matched canonical name
    platform_a          TEXT NOT NULL,
    platform_b          TEXT NOT NULL,
    branch_a_id         TEXT,
    branch_b_id         TEXT,
    price_a             REAL,
    price_b             REAL,
    delta_abs           REAL,               -- price_b - price_a
    delta_pct           REAL,               -- (delta_abs / price_a) * 100
    cheaper_platform    TEXT,               -- 'platform_a', 'platform_b', or 'equal'
    match_score         REAL,               -- fuzzy match confidence (0-100)
    compared_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_snapshots_item    ON price_snapshots(item_id, scraped_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_branch  ON price_snapshots(branch_id, scraped_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_platform ON price_snapshots(platform_id, scraped_at);
CREATE INDEX IF NOT EXISTS idx_comparisons_brand ON comparisons(brand_id, compared_at);
CREATE INDEX IF NOT EXISTS idx_branches_brand    ON branches(brand_id, platform_id);
"""


# ── Connection management ─────────────────────────────────────────────────────

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Return a sqlite3 connection with row_factory set for dict-like access."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row   # rows accessible as dicts: row['column_name']
    conn.execute("PRAGMA journal_mode=WAL")   # Write-Ahead Logging: better concurrency
    conn.execute("PRAGMA foreign_keys=ON")    # enforce FK constraints
    return conn


@contextmanager
def db_session(db_path: str = DB_PATH):
    """
    Context manager for safe DB sessions with auto-commit and rollback.

    Usage:
        with db_session() as conn:
            conn.execute("INSERT INTO ...")
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"DB session rolled back due to: {e}")
        raise
    finally:
        conn.close()


# ── Initialisation ────────────────────────────────────────────────────────────

def init_db(db_path: str = DB_PATH) -> None:
    """
    Create all tables and indexes if they don't already exist.
    Safe to call on every startup — idempotent.
    """
    import os
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    with db_session(db_path) as conn:
        conn.executescript(SCHEMA)
    logger.info(f"Database initialised at {db_path}")


# ── Seed helpers ──────────────────────────────────────────────────────────────

def seed_platforms(db_path: str = DB_PATH) -> None:
    """Insert platform records from config. Skips if already exists."""
    import json
    from config.settings import PLATFORMS

    with db_session(db_path) as conn:
        for pid, p in PLATFORMS.items():
            conn.execute("""
                INSERT OR IGNORE INTO platforms
                    (platform_id, display_name, base_url, currency, vat_inclusive)
                VALUES (?, ?, ?, ?, ?)
            """, (pid, p["name"], p["base_url"], p["currency"],
                  1 if p.get("vat_inclusive") else 0))
    logger.info("Platforms seeded")


def seed_brands(db_path: str = DB_PATH) -> None:
    """Insert brand records from config. Skips if already exists."""
    import json
    from config.settings import TARGET_BRANDS

    with db_session(db_path) as conn:
        for bid, b in TARGET_BRANDS.items():
            conn.execute("""
                INSERT OR IGNORE INTO brands
                    (brand_id, display_name, cuisine_tags)
                VALUES (?, ?, ?)
            """, (bid, b["display_name"], json.dumps(b.get("cuisines", []))))
    logger.info("Brands seeded")


# ── Query helpers ─────────────────────────────────────────────────────────────

def upsert_branch(conn: sqlite3.Connection, branch: dict) -> None:
    """
    Insert a branch if new, update delivery_fee/last_scraped_at if exists.
    branch dict keys must match the branches table columns.
    """
    conn.execute("""
        INSERT INTO branches
            (branch_id, brand_id, platform_id, raw_id, display_name,
             area_name, latitude, longitude, delivery_fee, min_order_amount,
             avg_delivery_time, outlet_url, last_scraped_at)
        VALUES
            (:branch_id, :brand_id, :platform_id, :raw_id, :display_name,
             :area_name, :latitude, :longitude, :delivery_fee, :min_order_amount,
             :avg_delivery_time, :outlet_url, :last_scraped_at)
        ON CONFLICT(platform_id, raw_id) DO UPDATE SET
            delivery_fee      = excluded.delivery_fee,
            last_scraped_at   = excluded.last_scraped_at,
            is_active         = 1
    """, branch)


def upsert_menu_item(conn: sqlite3.Connection, item: dict) -> None:
    """Insert a menu item if new, update name/description/availability if changed."""
    conn.execute("""
        INSERT INTO menu_items
            (item_id, branch_id, platform_id, raw_item_id, name,
             description, section_name, has_variants, image_url, is_available)
        VALUES
            (:item_id, :branch_id, :platform_id, :raw_item_id, :name,
             :description, :section_name, :has_variants, :image_url, :is_available)
        ON CONFLICT(branch_id, raw_item_id) DO UPDATE SET
            name         = excluded.name,
            description  = excluded.description,
            is_available = excluded.is_available
    """, item)


def insert_price_snapshot(conn: sqlite3.Connection, snapshot: dict) -> None:
    """Append a price snapshot row. Always inserts — never updates."""
    conn.execute("""
        INSERT INTO price_snapshots
            (item_id, branch_id, platform_id, base_price, original_price,
             discount_type, discount_value, effective_price,
             delivery_fee_share, is_vat_inclusive, scraped_at)
        VALUES
            (:item_id, :branch_id, :platform_id, :base_price, :original_price,
             :discount_type, :discount_value, :effective_price,
             :delivery_fee_share, :is_vat_inclusive, :scraped_at)
    """, snapshot)


if __name__ == "__main__":
    # Run directly to initialise and seed the DB
    logging.basicConfig(level="INFO")
    init_db()
    seed_platforms()
    seed_brands()
    print("Database ready.")
