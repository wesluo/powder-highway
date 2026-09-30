#!/usr/bin/env python3
"""Watch Chelsea FC ticketing pages and email when tickets become available.

Loads each page in headless Chromium once per interval, classifies it as
available / sold_out / queue / unknown / error, and emails on transitions
into an interesting state. Everything is configured through environment
variables so the GitHub Actions workflow can pass repo secrets/variables.

Required (one of):
  EVENTS               One match per line as "Name | URL", e.g.
                       "Chelsea v Bournemouth | https://...".
  EVENT_URL            A single fixture page (named by EVENT_NAME).

Email (skipped with a log line if unset, handy for local dry runs):
  GMAIL_USER           Gmail address that sends the alert.
  GMAIL_APP_PASSWORD   Gmail app password (not the account password).
  ALERT_TO             Recipient; defaults to GMAIL_USER.

Optional:
  EVENT_NAME           Label for EVENT_URL (default: "Chelsea FC tickets").
  AVAILABLE_PATTERNS   Regexes, separated by "|", that mean tickets can be bought.
  SOLD_OUT_PATTERNS    Regexes, separated by "|", that mean nothing is on sale.
  INTERVAL_SECONDS     Seconds between checks (default 60).
  RUN_MINUTES          Stop after this many minutes; 0 = forever (default 0).
  ERROR_ALERT_AFTER    Consecutive failed checks before a "checker is blocked"
                       email (default 15).
  ONCE                 If "1", check once, print the state and exit.

Login (only used when the page shows a login form):
  CHELSEA_USERNAME     Chelsea account email.
  CHELSEA_PASSWORD     Chelsea account password.
  LOGIN_URL            Sign-in page to open first, if the event page doesn't
                       redirect to it.
  LOGIN_USER_SELECTOR, LOGIN_PASSWORD_SELECTOR, LOGIN_SUBMIT_SELECTOR
                       CSS selectors overriding the generic defaults.
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
DEFAULT_USER_SELECTOR = ", ".join([
    'input[type="email"]',
    'input[autocomplete="username"]',
    'input[name*="email" i]',
    'input[name*="user" i]',
    'input[id*="email" i]',
    'input[id*="user" i]',
])
DEFAULT_PASSWORD_SELECTOR = 'input[type="password"]'
DEFAULT_SUBMIT_SELECTOR = 'button[type="submit"], input[type="submit"]'
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


class LoginFailed(Exception):
    pass


class Login:
    """Signs in when the page shows a password field; the session cookie is
    then reused across checks. Stops trying after MAX_FAILURES failures in a
    row so a wrong password can't get the account locked."""

    MAX_FAILURES = 3

    def __init__(self) -> None:
        self.username = os.environ.get("CHELSEA_USERNAME", "")
        self.password = os.environ.get("CHELSEA_PASSWORD", "")
        self.login_url = os.environ.get("LOGIN_URL", "").strip()
        self.user_sel = os.environ.get("LOGIN_USER_SELECTOR") or DEFAULT_USER_SELECTOR
        self.pass_sel = os.environ.get("LOGIN_PASSWORD_SELECTOR") or DEFAULT_PASSWORD_SELECTOR
        self.submit_sel = os.environ.get("LOGIN_SUBMIT_SELECTOR") or DEFAULT_SUBMIT_SELECTOR
        self.failures = 0

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)

    @property
    def gave_up(self) -> bool:
        return self.failures >= self.MAX_FAILURES

    def needed(self, page) -> bool:
        return page.locator(self.pass_sel).first.is_visible() or page.locator(
            self.user_sel).first.is_visible() and "login" in page.url.lower()

    def sign_in(self, page) -> None:
        if self.login_url:
            load(page, self.login_url)
        page.locator(self.user_sel).first.fill(self.username, timeout=15_000)
        # Some identity providers ask for the email first, then the password.
        if not page.locator(self.pass_sel).first.is_visible():
            page.locator(self.submit_sel).first.click(timeout=10_000)
        page.locator(self.pass_sel).first.fill(self.password, timeout=15_000)
        page.locator(self.submit_sel).first.click(timeout=10_000)
        settle(page)
        log("submitted login form")


def settle(page) -> None:
    # Ticketing pages render availability client-side; give scripts a moment.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def load(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    settle(page)


def check(page, url: str, login: Login, available: re.Pattern[str], sold_out: re.Pattern[str]) -> tuple[str, str]:
    load(page, url)
    if login.needed(page):
        if not login.configured:
            raise LoginFailed("page asks for a login but CHELSEA_USERNAME/CHELSEA_PASSWORD are not set")
        if login.gave_up:
            raise LoginFailed(f"not retrying login after {login.failures} failures")
        login.sign_in(page)
        load(page, url)
        if login.needed(page):
            login.failures += 1
            raise LoginFailed(f"still asked to log in after attempt {login.failures}")
        login.failures = 0
        log("logged in")
    text = page.inner_text("body", timeout=10_000)
    return classify(page.url, text, available, sold_out)


class Event:
    def __init__(self, name: str, url: str) -> None:
        self.name = name
        self.url = url
        self.previous: str | None = None
        self.errors = 0
        self.error_alerted = False


def parse_events() -> list[Event]:
    """EVENTS holds one "Name | URL" per line; EVENT_URL/EVENT_NAME is the
    single-match shorthand."""
    events = []
    for line in os.environ.get("EVENTS", "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, url = line.rpartition("|")
        if not sep:
            name, url = "", line
        events.append(Event(name.strip() or url.strip(), url.strip()))
    single = os.environ.get("EVENT_URL", "").strip()
    if single:
        events.append(Event(os.environ.get("EVENT_NAME") or "Chelsea FC tickets", single))
    return events


def watch(page, event: Event, login: Login, available, sold_out, error_alert_after: int) -> str:
    try:
        state, detail = check(page, event.url, login, available, sold_out)
        event.errors = 0
        event.error_alerted = False
    except Exception as exc:  # network errors, timeouts, bot walls, login
        state, detail = "error", f"{type(exc).__name__}: {exc}".splitlines()[0]
        event.errors += 1
    log(f"{event.name}: state={state} {detail!r}")

    if state in ALERT_STATES and state != event.previous:
        headline = "AVAILABLE" if state == "available" else "sale queue is open"
        send_email(
            f"[Chelsea tickets] {event.name}: {headline}",
            f"{event.name}\nState: {state} (matched: {detail})\n\nGo now: {event.url}\n",
        )
    failing = event.errors >= error_alert_after or login.gave_up
    if state == "error" and failing and not event.error_alerted:
        send_email(
            f"[Chelsea tickets] checker failing for {event.name}",
            f"The last {event.errors} checks failed; the site may be blocking the runner,\n"
            f"or the Chelsea login stopped working.\n"
            f"Last error: {detail}\n\nPage: {event.url}\n",
        )
        event.error_alerted = True
    if state != "error":
        event.previous = state
    return state


def main() -> int:
    events = parse_events()
    if not events:
        log("set EVENTS (one \"Name | URL\" per line) or EVENT_URL")
        return 2
    available = compile_patterns(os.environ.get("AVAILABLE_PATTERNS") or DEFAULT_AVAILABLE)
    sold_out = compile_patterns(os.environ.get("SOLD_OUT_PATTERNS") or DEFAULT_SOLD_OUT)
    interval = int(os.environ.get("INTERVAL_SECONDS") or 60)
    run_minutes = float(os.environ.get("RUN_MINUTES") or 0)
    error_alert_after = int(os.environ.get("ERROR_ALERT_AFTER") or 15)
    once = os.environ.get("ONCE") == "1"
    deadline = time.monotonic() + run_minutes * 60 if run_minutes else None

    login = Login()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # One context for all matches, so a single login covers every page.
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
            states = [watch(page, e, login, available, sold_out, error_alert_after) for e in events]
            if once:
                return 1 if "error" in states else 0
            if deadline and time.monotonic() >= deadline:
                log("run window finished")
                return 0
            time.sleep(max(0.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
