#!/usr/bin/env python3
"""Watch a Chelsea FC ticketing page and email when tickets become available.

Loads the page in headless Chromium once per interval, classifies it as
available / sold_out / queue / unknown / error, and emails on transitions
into an interesting state. Everything is configured through environment
variables so the GitHub Actions workflow can pass repo secrets/variables.

Required:
  EVENT_URL            Fixture page on the ticketing site.

Email (skipped with a log line if unset, handy for local dry runs):
  GMAIL_USER           Gmail address that sends the alert.
  GMAIL_APP_PASSWORD   Gmail app password (not the account password).
  ALERT_TO             Recipient; defaults to GMAIL_USER.

Optional:
  EVENT_NAME           Label used in emails (default: "Chelsea FC tickets").
  AVAILABLE_PATTERNS   Regexes, separated by "|", that mean tickets can be bought.
  SOLD_OUT_PATTERNS    Regexes, separated by "|", that mean nothing is on sale.
  INTERVAL_SECONDS     Seconds between checks (default 60).
  RUN_MINUTES          Stop after this many minutes; 0 = forever (default 0).
  ERROR_ALERT_AFTER    Consecutive failed checks before a "checker is blocked"
                       email (default 15).
  ONCE                 If "1", check once, print the state and exit.
"""

from __future__ import annotations

import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

DEFAULT_AVAILABLE = "|".join([
    r"buy tickets?",
    r"add to basket",
    r"select (your )?seats?",
    r"choose (your )?seats?",
    r"book now",
    r"tickets? available",
])
DEFAULT_SOLD_OUT = "|".join([
    r"sold out",
    r"no tickets? (are )?(currently )?available",
    r"not (currently )?on sale",
    r"currently unavailable",
    r"no availability",
    r"sale (has )?(ended|closed)",
])
# Queue-it hosts the virtual waiting room used for busy sales.
QUEUE_MARKERS = re.compile(r"queue-it\.net|you are (now )?in (the )?(line|queue)", re.I)

ALERT_STATES = {"available", "queue"}


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


def compile_patterns(raw: str) -> re.Pattern[str]:
    return re.compile(raw, re.I)


def classify(url: str, text: str, available: re.Pattern[str], sold_out: re.Pattern[str]) -> tuple[str, str]:
    """Return (state, matched snippet) for a loaded page."""
    if QUEUE_MARKERS.search(url) or QUEUE_MARKERS.search(text):
        return "queue", "waiting room"
    # Sold-out wording wins: fixture pages often keep a greyed-out "Buy" button.
    if m := sold_out.search(text):
        return "sold_out", m.group(0)
    if m := available.search(text):
        return "available", m.group(0)
    return "unknown", ""


def send_email(subject: str, body: str) -> None:
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        log(f"email not configured, would have sent: {subject}")
        return
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = os.environ.get("ALERT_TO") or user
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    log(f"email sent: {subject}")


def check(page, url: str, available: re.Pattern[str], sold_out: re.Pattern[str]) -> tuple[str, str]:
    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    # Ticketing pages render availability client-side; give scripts a moment.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass
    text = page.inner_text("body", timeout=10_000)
    return classify(page.url, text, available, sold_out)


def main() -> int:
    url = os.environ.get("EVENT_URL", "").strip()
    if not url:
        log("EVENT_URL is not set")
        return 2
    name = os.environ.get("EVENT_NAME") or "Chelsea FC tickets"
    available = compile_patterns(os.environ.get("AVAILABLE_PATTERNS") or DEFAULT_AVAILABLE)
    sold_out = compile_patterns(os.environ.get("SOLD_OUT_PATTERNS") or DEFAULT_SOLD_OUT)
    interval = int(os.environ.get("INTERVAL_SECONDS") or 60)
    run_minutes = float(os.environ.get("RUN_MINUTES") or 0)
    error_alert_after = int(os.environ.get("ERROR_ALERT_AFTER") or 15)
    once = os.environ.get("ONCE") == "1"
    deadline = time.monotonic() + run_minutes * 60 if run_minutes else None

    previous = None
    errors = 0
    error_alerted = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
        )
        page = context.new_page()
        while True:
            started = time.monotonic()
            try:
                state, detail = check(page, url, available, sold_out)
                errors = 0
                error_alerted = False
            except Exception as exc:  # network errors, timeouts, bot walls
                state, detail = "error", f"{type(exc).__name__}: {exc}".splitlines()[0]
                errors += 1

            log(f"state={state} {detail!r}")
            if once:
                return 0 if state != "error" else 1

            if state in ALERT_STATES and state != previous:
                headline = "AVAILABLE" if state == "available" else "sale queue is open"
                send_email(
                    f"[Chelsea tickets] {name}: {headline}",
                    f"{name}\nState: {state} (matched: {detail})\n\nGo now: {url}\n",
                )
            if state == "error" and errors >= error_alert_after and not error_alerted:
                send_email(
                    f"[Chelsea tickets] checker failing for {name}",
                    f"The last {errors} checks failed; the site may be blocking the runner.\n"
                    f"Last error: {detail}\n\nPage: {url}\n",
                )
                error_alerted = True
            if state != "error":
                previous = state

            if deadline and time.monotonic() >= deadline:
                log("run window finished")
                return 0
            time.sleep(max(0.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
