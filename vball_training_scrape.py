#!/usr/bin/env python3
"""
vball_training_scrape.py
Playwright sync scraper for BondSports React-rendered pages.
Writes a single JSON file with categories -> events.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ----------------------------
# Configuration
# ----------------------------
DEFAULT_OUT = "data/vball_training.json"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
PAGE_TIMEOUT_MS = 30000
WAIT_FOR_UL_TIMEOUT_MS = 20000

CATEGORIES: Dict[str, Dict[str, str]] = {
    "beginner": {
        "label": "Training: Intermediate 12/under",
        "url": "https://bondsports.co/activity/programs/CO_ED-adult-VOLLEYBALL/13547/season/training%3A-intermediate-12%2Funder/105882",
    },
    "intermediate": {
        "label": "Training: Advanced 14U+",
        "url": "https://bondsports.co/activity/programs/CO_ED-adult-VOLLEYBALL/13547/season/training%3A-advanced-14u%2B/105883",
    },
}


# ----------------------------
# Models
# ----------------------------
@dataclass
class Event:
    title: str
    date: str
    time: str
    signup_url: str


# ----------------------------
# Helpers
# ----------------------------
def setup_logger() -> logging.Logger:
    logger = logging.getLogger("vball_scraper")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(ch)
    return logger


def _normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# ----------------------------
# Extraction logic
# ----------------------------
def extract_events_from_page(page, category_url: str, logger: logging.Logger) -> List[Event]:
    events: List[Event] = []

    try:
        page.wait_for_selector('ul[data-testid="events-session"]', timeout=WAIT_FOR_UL_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.info("No ul[data-testid='events-session'] found (timed out). Returning empty list.")
        return events

    uls = page.locator('ul[data-testid="events-session"]')
    total = uls.count()
    logger.info(f"Found {total} event blocks.")

    for i in range(total):
        ul = uls.nth(i)

        title = ""
        try:
            title_locator = ul.locator("li:has(span:has-text('Event Name')) p").first
            if title_locator.count():
                title = _normalize_space(title_locator.inner_text())
        except Exception:
            title = ""

        date = ""
        try:
            date_locator = ul.locator("li:has(span:has-text('Dates')) p").first
            if date_locator.count():
                date = _normalize_space(date_locator.inner_text())
        except Exception:
            date = ""

        time_text = ""
        try:
            time_locator = ul.locator("li:has(span:has-text('Days & Time')) p").first
            if not time_locator.count():
                time_locator = ul.locator("li:has(span:has-text('Days')) p").first
            if not time_locator.count():
                time_locator = ul.locator("li:has(span):nth-last-child(1) p").first
            if time_locator.count():
                time_text = _normalize_space(time_locator.inner_text())
        except Exception:
            time_text = ""

        if not (title and (date or time_text)):
            try:
                raw = _normalize_space(ul.inner_text())
                lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
                for idx, ln in enumerate(lines):
                    if ln.upper().endswith("TRAINING") or re.search(r"\bTRAINING\b", ln, re.I):
                        if not title:
                            title = ln
                    if re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b", ln, re.I):
                        if not date:
                            date = ln
                    if re.search(r"\d{1,2}:\d{2}\s*(AM|PM)", ln, re.I):
                        if not time_text:
                            time_text = ln
                title = _normalize_space(title)
                date = _normalize_space(date)
                time_text = _normalize_space(time_text)
            except Exception:
                pass

        if not title:
            logger.debug(f"Skipping event block #{i} — title missing.")
            continue

        events.append(Event(title=title, date=date, time=time_text, signup_url=category_url))

    return events

def extract_events_from_season_cards(page, signup_url: str, filter_word: str, logger: logging.Logger) -> List[Event]:
    """
    Extracts events from SeasonDetails card layout (same structure as camps page).
    Only includes cards whose title contains filter_word (case-insensitive).
    """
    events: List[Event] = []

    try:
        page.wait_for_selector('h3[data-testid="SeasonDetails-EF514D"]', timeout=WAIT_FOR_UL_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.info("No season cards found (timed out). Returning empty list.")
        return events

    cards = page.locator('div.css-1y8xm4p-SeasonDetails-boxItemCss')
    total = cards.count()
    logger.info(f"Found {total} season cards.")

    for i in range(total):
        card = cards.nth(i)

        # Get title
        title = ""
        try:
            title = _normalize_space(card.locator('h3[data-testid="SeasonDetails-EF514D"]').inner_text())
        except Exception:
            title = ""

        # Filter — skip if title doesn't contain the filter word
        if not title or filter_word.lower() not in title.lower():
            logger.debug(f"Skipping card #{i} '{title}' — does not match filter '{filter_word}'.")
            continue

        # Get dates and registration starts
        date = ""
        try:
            items = card.locator("li")
            for j in range(items.count()):
                item = items.nth(j)
                label = _normalize_space(item.locator("span").inner_text())
                if label == "Dates":
                    try:
                        date = _normalize_space(item.locator("p").inner_text())
                    except Exception:
                        pass
        except Exception:
            pass

        events.append(Event(title=title, date=date, time="", signup_url=signup_url))

    return events

# ----------------------------
# Runner
# ----------------------------
def run(categories: Dict[str, Dict[str, str]], out_path: str = DEFAULT_OUT) -> Dict:
    logger = setup_logger()
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "categories": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        for key, meta in categories.items():
            label = meta.get("label", key)
            url = meta.get("url", "")
            logger.info(f"=== Scraping '{key}' -> {url}")

            try:
                page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                logger.warning(f"[{key}] page.goto timed out.")
                payload["categories"][key] = {"label": label, "url": url, "events": []}
                continue
            except Exception as e:
                logger.exception(f"[{key}] error loading page: {e}")
                payload["categories"][key] = {"label": label, "url": url, "events": []}
                continue

            try:
                scrape_mode = meta.get("scrape_mode", "event_sessions")
                signup_url = meta.get("signup_url", url)
                filter_word = meta.get("filter", "")

                if scrape_mode == "season_cards":
                    events = extract_events_from_season_cards(page, signup_url, filter_word, logger)
                else:
                    events = extract_events_from_page(page, url, logger)

                payload["categories"][key] = {
                    "label": label,
                    "url": url,
                    "events": [asdict(e) for e in events],
                }
                logger.info(f"[{key}] found {len(events)} events.")
            except Exception as e:
                logger.exception(f"[{key}] extraction error: {e}")
                payload["categories"][key] = {"label": label, "url": url, "events": []}

        page.close()
        context.close()
        browser.close()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"Wrote output to {out_path}")
    return payload


# ----------------------------
# CLI
# ----------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    run(CATEGORIES, out_path=args.out)
