#!/usr/bin/env python3
"""
Playwright scraper for BondSports basketball training pages.

The existing JSON file is preserved when:
- any configured category cannot be loaded;
- required page content cannot be found;
- extraction produces no valid events;
- the newly scraped sports data is unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Dict, List

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from json_utils import write_json_if_changed


DEFAULT_OUT = "data/bball_training.json"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
PAGE_TIMEOUT_MS = 30_000
WAIT_FOR_UL_TIMEOUT_MS = 20_000

CATEGORIES: Dict[str, Dict[str, str]] = {
    "beginner": {
        "label": "Training: Beginner 2nd-4th",
        "url": "https://bondsports.co/activity/programs/CO_ED-adult-BASKETBALL/13110/season/training%3A-beginner-2nd-4th/104915",
    },
    "intermediate": {
        "label": "Training: Intermediate 5th-8th",
        "url": "https://bondsports.co/activity/programs/CO_ED-adult-BASKETBALL/13110/season/training%3A-intermediate-5th-8th/104916",
    },
    "advanced": {
        "label": "Training: Advanced 9th-12th",
        "url": "https://bondsports.co/activity/programs/CO_ED-adult-BASKETBALL/13110/season/training%3A-advanced-9th-12th/104908",
    },
    "group": {
        "label": "Group Training",
        "url": "https://bondsports.co/activity/programs/CO_ED-adult-BASKETBALL/13110",
        "filter": ["group", "west training"],
        "signup_url": "https://bondsports.co/activity/programs/CO_ED-adult-BASKETBALL/13110",
        "scrape_mode": "season_cards",
    },
    "individual": {
        "label": "Individual Training",
        "url": "https://bondsports.co/activity/programs/CO_ED-adult-BASKETBALL/13110/season/Individual%20Training%3A%20All%20Ages/130006",
    },
}


@dataclass
class Event:
    title: str
    date: str
    time: str
    signup_url: str


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("bball_scraper")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    return logger


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def extract_events_from_page(
    page,
    category_url: str,
    logger: logging.Logger,
) -> List[Event]:
    events: List[Event] = []

    try:
        page.wait_for_selector(
            'ul[data-testid="events-session"]',
            timeout=WAIT_FOR_UL_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "No ul[data-testid='events-session'] was found before timeout."
        ) from exc

    uls = page.locator('ul[data-testid="events-session"]')
    total = uls.count()
    logger.info("Found %s event blocks.", total)

    if total == 0:
        raise RuntimeError("The page returned zero event blocks.")

    for index in range(total):
        ul = uls.nth(index)

        title = ""
        date = ""
        time_text = ""

        try:
            title_locator = ul.locator(
                "li:has(span:has-text('Event Name')) p"
            ).first
            if title_locator.count():
                title = _normalize_space(title_locator.inner_text())
        except Exception as exc:
            logger.warning(
                "Could not read title from event block #%s: %s",
                index,
                exc,
            )

        try:
            date_locator = ul.locator(
                "li:has(span:has-text('Dates')) p"
            ).first
            if date_locator.count():
                date = _normalize_space(date_locator.inner_text())
        except Exception as exc:
            logger.warning(
                "Could not read date from event block #%s: %s",
                index,
                exc,
            )

        try:
            time_locator = ul.locator(
                "li:has(span:has-text('Days & Time')) p"
            ).first

            if not time_locator.count():
                time_locator = ul.locator(
                    "li:has(span:has-text('Days')) p"
                ).first

            if not time_locator.count():
                time_locator = ul.locator(
                    "li:has(span):nth-last-child(1) p"
                ).first

            if time_locator.count():
                time_text = _normalize_space(
                    time_locator.inner_text()
                )

        except Exception as exc:
            logger.warning(
                "Could not read time from event block #%s: %s",
                index,
                exc,
            )

        if not (title and (date or time_text)):
            try:
                raw = _normalize_space(ul.inner_text())
                lines = [
                    line.strip()
                    for line in raw.split("\n")
                    if line.strip()
                ]

                for line in lines:
                    if (
                        line.upper().endswith("TRAINING")
                        or re.search(r"\bTRAINING\b", line, re.I)
                    ):
                        if not title:
                            title = line

                    if re.search(
                        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|"
                        r"Sept|Oct|Nov|Dec)\b",
                        line,
                        re.I,
                    ):
                        if not date:
                            date = line

                    if re.search(
                        r"\d{1,2}:\d{2}\s*(AM|PM)",
                        line,
                        re.I,
                    ):
                        if not time_text:
                            time_text = line

                title = _normalize_space(title)
                date = _normalize_space(date)
                time_text = _normalize_space(time_text)

            except Exception as exc:
                logger.warning(
                    "Fallback extraction failed for event block #%s: %s",
                    index,
                    exc,
                )

        if not title:
            logger.warning(
                "Skipping event block #%s because its title is empty.",
                index,
            )
            continue

        events.append(
            Event(
                title=title,
                date=date,
                time=time_text,
                signup_url=category_url,
            )
        )

    if not events:
        raise RuntimeError(
            "Event-session extraction produced zero valid events."
        )

    return events


def extract_events_from_season_cards(
    page,
    signup_url: str,
    filter_word: str,
    logger: logging.Logger,
) -> List[Event]:
    events: List[Event] = []

    try:
        page.wait_for_selector(
            'h3[data-testid="SeasonDetails-EF514D"]',
            timeout=WAIT_FOR_UL_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "No season cards were found before timeout."
        ) from exc

    # Kept exactly as requested.
    cards = page.locator(
        "div.css-1y8xm4p-SeasonDetails-boxItemCss"
    )
    total = cards.count()
    logger.info("Found %s season cards.", total)

    if total == 0:
        raise RuntimeError("The page returned zero season cards.")

    for index in range(total):
        card = cards.nth(index)

        try:
            title = _normalize_space(
                card.locator(
                    'h3[data-testid="SeasonDetails-EF514D"]'
                ).inner_text()
            )
        except Exception as exc:
            logger.warning(
                "Could not read title from season card #%s: %s",
                index,
                exc,
            )
            continue

        if not title or filter_word.lower() not in title.lower():
            continue

        date = ""

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

                if label == "Dates":
                    try:
                        date = _normalize_space(
                            item.locator("p").inner_text()
                        )
                    except Exception as exc:
                        logger.warning(
                            "Could not read date from card #%s: %s",
                            index,
                            exc,
                        )
                    break

        except Exception as exc:
            logger.warning(
                "Could not read detail items from season card #%s: %s",
                index,
                exc,
            )

        events.append(
            Event(
                title=title,
                date=date,
                time="",
                signup_url=signup_url,
            )
        )

    if not events:
        raise RuntimeError(
            "Season-card extraction produced zero matching events."
        )

    return events


def run(
    categories: Dict[str, Dict[str, str]],
    out_path: str = DEFAULT_OUT,
) -> Dict:
    logger = setup_logger()
    scraped_categories: Dict[str, dict] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            for key, metadata in categories.items():
                label = metadata.get("label", key)
                url = metadata.get("url", "")

                if not url:
                    raise ValueError(
                        f"Category {key!r} does not have a URL."
                    )

                logger.info("Scraping %r -> %s", key, url)

                try:
                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=PAGE_TIMEOUT_MS,
                    )
                except PlaywrightTimeoutError as exc:
                    raise RuntimeError(
                        f"Navigation timed out for category {key!r}. "
                        "The existing JSON file will not be replaced."
                    ) from exc

                scrape_mode = metadata.get(
                    "scrape_mode",
                    "event_sessions",
                )
                signup_url = metadata.get("signup_url", url)
                filter_word = metadata.get("filter", "")

                if scrape_mode == "season_cards":
                    events = extract_events_from_season_cards(
                        page,
                        signup_url,
                        filter_word,
                        logger,
                    )
                else:
                    events = extract_events_from_page(
                        page,
                        url,
                        logger,
                    )

                scraped_categories[key] = {
                    "label": label,
                    "url": url,
                    "events": [asdict(event) for event in events],
                }

                logger.info(
                    "[%s] found %s events.",
                    key,
                    len(events),
                )

        finally:
            page.close()
            context.close()
            browser.close()

    missing_categories = set(categories) - set(scraped_categories)

    if missing_categories:
        raise RuntimeError(
            "Scrape did not complete all configured categories: "
            f"{sorted(missing_categories)}"
        )

    total_events = sum(
        len(category["events"])
        for category in scraped_categories.values()
    )

    if total_events == 0:
        raise RuntimeError(
            "Basketball training scrape produced zero total events. "
            "The existing JSON file will not be replaced."
        )

    payload = {
        "categories": scraped_categories,
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

    run(CATEGORIES, out_path=arguments.out)
