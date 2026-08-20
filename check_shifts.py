"""
TuksGolf Range shift watcher.

Logs into SuperSaaS with your own employee account, reads the schedule
page, and sends a free push notification (via ntfy.sh) to your phone
whenever a NEW shift shows up that wasn't there last time this ran.

This only uses the login/viewing access you already have as an employee.
It does not touch anyone else's account or any admin settings.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# ---- Config (comes from environment variables / GitHub Secrets) ----
SUPERSAAS_EMAIL = os.environ["SUPERSAAS_EMAIL"]
SUPERSAAS_PASSWORD = os.environ["SUPERSAAS_PASSWORD"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]  # e.g. tuksgolf-shifts-jm4829

SCHEDULE_URL = "https://www.supersaas.com/schedule/TuksGolf/TuksGolf_Range_Shifts?view=month"
STATE_FILE = Path(__file__).parent / "seen_shifts.json"

TIME_RANGE_RE = re.compile(r"^\d{1,2}:\d{2}\s*[\u2013-]\s*\d{1,2}:\d{2}$")
DAY_NUM_RE = re.compile(r"^\d{1,2}$")


def send_notification(title: str, message: str):
    """Push a free notification to the phone via ntfy.sh"""
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "high",
                "Tags": "golf",
            },
            timeout=15,
        )
    except Exception as e:
        print(f"Warning: failed to send notification: {e}")


def scrape_current_month_shifts(page, month_label: str):
    calendar_text = page.locator("body").inner_text()
    lines = [l.strip() for l in calendar_text.split("\n") if l.strip()]

    shifts = []
    current_day = None
    pending_time = None

    for line in lines:
        if DAY_NUM_RE.match(line) and 1 <= int(line) <= 31:
            current_day = int(line)
            pending_time = None
            continue

        if TIME_RANGE_RE.match(line):
            pending_time = line
            continue

        if pending_time is not None and current_day is not None:
            shifts.append(
                {
                    "month_label": month_label,
                    "day": current_day,
                    "time": pending_time,
                    "title": line,
                }
            )
            pending_time = None

    return shifts


def shift_key(shift: dict) -> str:
    return f'{shift["month_label"]}|{shift["day"]}|{shift["time"]}|{shift["title"]}'


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        page.goto(SCHEDULE_URL, wait_until="networkidle")

        if "login" in page.url:
            page.locator("#name").fill(SUPERSAAS_EMAIL)
            page.locator('input[type="password"]').fill(SUPERSAAS_PASSWORD)
            page.get_by_role("button", name=re.compile("log in", re.I)).click()
            page.wait_for_load_state("networkidle")

        if "schedule" not in page.url or "login" in page.url:
            page.goto(SCHEDULE_URL, wait_until="networkidle")

        month_label = "unknown"
        try:
            month_label = page.locator("text=/^[A-Z][a-z]+ \\d{4}$/").first.inner_text()
        except Exception:
            pass

        current_shifts = scrape_current_month_shifts(page, month_label)

        # Keep debug info in case we found nothing
        debug_url = page.url
        debug_text = page.locator("body").inner_text()

        browser.close()

    if not current_shifts:
        print("No shifts found on page — check login worked / page structure hasn't changed.")
        print(f"DEBUG final URL: {debug_url}")
        print("DEBUG first 1500 chars of page text:")
        print(debug_text[:1500])
        sys.exit(0)

    if STATE_FILE.exists():
        seen = set(json.loads(STATE_FILE.read_text()))
    else:
        seen = set()

    current_keys = {shift_key(s): s for s in current_shifts}
    new_keys = set(current_keys.keys()) - seen

    if new_keys:
        lines = []
        for k in sorted(new_keys):
            s = current_keys[k]
            lines.append(f'{s["month_label"]} {s["day"]}: {s["time"]} — {s["title"]}')
        message = "\n".join(lines)
        print("New shift(s) found:\n" + message)
        send_notification("New TuksGolf shift posted!", message)
    else:
        print(f"No new shifts. ({len(current_shifts)} shifts currently visible)")

    STATE_FILE.write_text(json.dumps(sorted(current_keys.keys())))


if __name__ == "__main__":
    main()
