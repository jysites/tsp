#!/usr/bin/env python3
"""
vball_camps_scrape.py

Scrapes the BondSports volleyball camps page.
Extracts: title, dates, registration_starts, signup_url
Writes to a single JSON file.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ----------------------------
# Configuration
# ----------------------------
DEFAULT_OUT = "data/vball_camps.json"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
PAGE_TIMEOUT_MS = 30000
WAIT_FOR_CARD_TIMEOUT_MS = 20000

CAMPS_URL = "https://bondsports.co/activity/programs/CO_ED-youth-VOLLEYBALL/13552"


# ----------------------------
# Model
# ----------------------------
@dataclass
class Camp:
    title: str
    dates: str
    registration_starts: str
    signup_url: str


# ----------------------------
# Helpers
# ----------------------------
def setup_logger() -> logging.Logger:
    logger = logging.getLogger("vball_camps_scraper")
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
# Extraction
# ----------------------------
def extract_camps(page, logger: logging.Logger) -> List[Camp]:
    camps: List[Camp] = []

    try:
        page.wait_for_selector('h3[data-testid="SeasonDetails-EF514D"]', timeout=WAIT_FOR_CARD_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.info("No season cards found (timed out). Returning empty list.")
        return camps

    # Locate by the stable h3 testid, then go up to the MuiBox parent
    # which is always the direct grandparent: h3 -> MuiBox div -> grid child div
    headings = page.locator('h3[data-testid="SeasonDetails-EF514D"]')
    total = headings.count()
    logger.info(f"Found {total} season card headings.")

    for i in range(total):
        heading = headings.nth(i)

        # Walk up two levels to get the MuiBox card container
        card = heading.locator('xpath=../..')

        title = ""
        try:
            title = _normalize_space(heading.inner_text())
        except Exception:
            title = ""

        if not title:
            logger.debug(f"Skipping card #{i} — no title found.")
            continue

        dates = ""
        registration_starts = ""
        try:
            items = card.locator("li")
            for j in range(items.count()):
                item = items.nth(j)
                try:
                    label = _normalize_space(item.locator("span").inner_text())
                except Exception:
                    continue
                value = ""
                try:
                    value = _normalize_space(item.locator("p").inner_text())
                except Exception:
                    pass

                if label == "Dates":
                    dates = value
                elif label == "Registration Starts":
                    registration_starts = value
        except Exception:
            pass

        logger.info(f"  Card #{i}: '{title}' | dates='{dates}' | reg='{registration_starts}'")

        camps.append(Camp(
            title=title,
            dates=dates,
            registration_starts=registration_starts,
            signup_url=CAMPS_URL,
        ))

    return camps


# ----------------------------
# Runner
# ----------------------------
def run(out_path: str = DEFAULT_OUT) -> dict:
    logger = setup_logger()
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "signup_url": CAMPS_URL,
        "camps": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        logger.info(f"Loading {CAMPS_URL}")
        try:
            page.goto(CAMPS_URL, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.warning("Page load timed out.")
        except Exception as e:
            logger.exception(f"Error loading page: {e}")

        try:
            camps = extract_camps(page, logger)
            payload["camps"] = [asdict(c) for c in camps]
            logger.info(f"Found {len(camps)} camps total.")
        except Exception as e:
            logger.exception(f"Extraction error: {e}")

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
    run(out_path=args.out)
