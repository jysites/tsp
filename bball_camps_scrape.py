#!/usr/bin/env python3
"""
Scrapes the BondSports basketball camps page.

Extracts:
- title
- dates
- registration_starts
- signup_url

The existing JSON file is preserved when:
- the scrape fails;
- required page content cannot be found;
- the newly scraped sports data is unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import List

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from json_utils import write_json_if_changed


DEFAULT_OUT = "data/bball_camps.json"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
PAGE_TIMEOUT_MS = 30_000
WAIT_FOR_CARD_TIMEOUT_MS = 20_000

CAMPS_URL = (
    "https://bondsports.co/activity/programs/"
    "CO_ED-youth-BASKETBALL/12166"
)


@dataclass
class Camp:
    title: str
    dates: str
    registration_starts: str
    signup_url: str


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("bball_camps_scraper")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    return logger


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def extract_camps(page, logger: logging.Logger) -> List[Camp]:
    camps: List[Camp] = []

    try:
        page.wait_for_selector(
            'h3[data-testid="SeasonDetails-EF514D"]',
            timeout=WAIT_FOR_CARD_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "No basketball camp season cards were found before timeout. "
            "The existing JSON file will not be replaced."
        ) from exc

    headings = page.locator(
        'h3[data-testid="SeasonDetails-EF514D"]'
    )
    total = headings.count()
    logger.info("Found %s season card headings.", total)

    if total == 0:
        raise RuntimeError(
            "The basketball camps page returned zero season card headings."
        )

    for index in range(total):
        heading = headings.nth(index)

        # Kept exactly as requested.
        card = heading.locator("xpath=../..")

        try:
            title = _normalize_space(heading.inner_text())
        except Exception as exc:
            logger.warning(
                "Could not read title for card #%s: %s",
                index,
                exc,
            )
            continue

        if not title:
            logger.warning("Skipping card #%s because its title is empty.", index)
            continue

        dates = ""
        registration_starts = ""

        try:
            items = card.locator("li")

            for item_index in range(items.count()):
                item = items.nth(item_index)

                try:
                    label = _normalize_space(
                        item.locator("span").inner_text()
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not read label from card #%s item #%s: %s",
                        index,
                        item_index,
                        exc,
                    )
                    continue

                try:
                    value = _normalize_space(
                        item.locator("p").inner_text()
                    )
                except Exception:
                    value = ""

                if label == "Dates":
                    dates = value
                elif label == "Registration Starts":
                    registration_starts = value

        except Exception as exc:
            logger.warning(
                "Could not read detail items from card #%s: %s",
                index,
                exc,
            )

        logger.info(
            "Card #%s: %r | dates=%r | registration=%r",
            index,
            title,
            dates,
            registration_starts,
        )

        camps.append(
            Camp(
                title=title,
                dates=dates,
                registration_starts=registration_starts,
                signup_url=CAMPS_URL,
            )
        )

    if not camps:
        raise RuntimeError(
            "Basketball camps extraction produced zero valid camps. "
            "The existing JSON file will not be replaced."
        )

    return camps


def run(out_path: str = DEFAULT_OUT) -> dict:
    logger = setup_logger()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            logger.info("Loading %s", CAMPS_URL)
            page.goto(
                CAMPS_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )

            camps = extract_camps(page, logger)

        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                "Basketball camps page navigation timed out. "
                "The existing JSON file will not be replaced."
            ) from exc

        finally:
            page.close()
            context.close()
            browser.close()

    payload = {
        "signup_url": CAMPS_URL,
        "camps": [asdict(camp) for camp in camps],
    }

    changed = write_json_if_changed(out_path, payload)

    if changed:
        logger.info("Sports data changed. Updated %s", out_path)
    else:
        logger.info("No sports data changes. Left %s untouched.", out_path)

    return payload


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    arguments = parser.parse_args()

    run(out_path=arguments.out)
