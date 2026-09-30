# Chelsea ticket watcher

Checks one or more Chelsea FC ticketing pages every 60 seconds in headless Chromium and
emails you when tickets look available, or when a Queue-it waiting room opens.
It runs on GitHub Actions (`.github/workflows/chelsea-tickets.yml`).

## Setup

1. **Gmail app password**: at <https://myaccount.google.com/apppasswords>
   (requires 2-Step Verification), create an app password.
2. **Repo secrets** (Settings → Secrets and variables → Actions → Secrets):
   - `GMAIL_USER`: the Gmail address that sends the alert
   - `GMAIL_APP_PASSWORD`: the app password from step 1
   - `ALERT_TO` (optional): the recipient, if it's not `GMAIL_USER`
   - `CHELSEA_USERNAME` / `CHELSEA_PASSWORD`: your Chelsea account login, used
     only when a page shows a sign-in form
3. **Repo variables** (same page → Variables):
   - `CHELSEA_EVENTS` (required): one match per line as `Name | URL`, e.g.

     ```
     Chelsea v Bournemouth | https://…
     Chelsea v Spurs | https://…
     ```

     The watcher does nothing until this is set. For a single match you can
     instead set `CHELSEA_EVENT_URL`, plus `CHELSEA_EVENT_NAME` for its label.
   - `CHELSEA_LOGIN_URL` (optional): a sign-in page to open first, if the
     match pages don't redirect to one
   - `CHELSEA_LOGIN_USER_SELECTOR`, `CHELSEA_LOGIN_PASSWORD_SELECTOR`,
     `CHELSEA_LOGIN_SUBMIT_SELECTOR` (optional): CSS selectors, if the
     generic ones don't find the sign-in form
   - `CHELSEA_AVAILABLE_PATTERNS` / `CHELSEA_SOLD_OUT_PATTERNS` (optional):
     case-insensitive regexes separated by `|`, to override the defaults in `check.py`
4. The workflow must be on the default branch for its schedule to run. Start
   it straight away from Actions → *Chelsea ticket watcher* → *Run workflow*.

## How it works

- Each page load is classified as `available`, `sold_out`, `queue`, `unknown`
  or `error`. Sold-out wording takes priority over buy wording, because
  fixture pages often keep a greyed-out "Buy" button.
- An email is sent when the state changes to `available` or `queue`. It is not
  repeated while the state stays the same.
- If a page shows a password field, the watcher signs in once and reuses
  the session for every match. It handles both one-page forms and
  email-then-password forms. After 3 failed sign-ins it stops trying for the
  rest of that run, so a wrong password can't lock the account, and emails
  you that the checker is failing.
- After 15 failed checks in a row (the site may be blocking the runner), you
  get one "checker failing" email so you aren't left without coverage.
- Each Actions run loops for about 5h40m. A cron every 15 minutes keeps the
  next run queued behind it, so checks continue around the clock. A new run
  starts with no memory of the last one, so if tickets are still available
  you'll get one reminder email roughly every 6 hours.
- To stop it: Actions → *Chelsea ticket watcher* → ⋯ → *Disable workflow*.

## Test locally

```sh
pip install -r tools/chelsea-tickets/requirements.txt
python -m playwright install chromium
ONCE=1 EVENT_URL='https://…' CHELSEA_USERNAME=… CHELSEA_PASSWORD=… python tools/chelsea-tickets/check.py
```

This prints the state it detects. Use it to adjust the patterns for your
fixture's page before you rely on the alerts.
