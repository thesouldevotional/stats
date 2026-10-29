#!/usr/bin/env python3
"""Telegram reminder for posts whose verse still has no reflection on soul.

Runs every morning in the GitHub Action (and locally for testing):

    TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python3 remind.py [--dry-run]

plan.json lists the post dates (Mon/Wed/Fri) and verse slugs, copied from the "soul social plan" sheet.
For the posts going out in 2 days and in 1 day, the live verse file is checked: a reflection is the
block between two "---" lines. If it's missing, one message goes to @IDC_ToDo_bot.
"""
import html, json, os, sys, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SITE = "https://thesouldevotional.github.io/soul/"
DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def check(slug):
    """(reference, has reflection); has reflection is None if the file can't be read."""
    try:
        text = get(f"{SITE}bible/{slug}.txt")
    except Exception:
        return slug, None
    ref = text.splitlines()[0].split(" | ")[0].strip()
    return ref, sum(1 for l in text.splitlines() if l.strip() == "---") >= 2


def main():
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    lines = []
    for p in json.loads((HERE / "plan.json").read_text()):
        left = (date.fromisoformat(p["date"]) - today).days
        if left not in (1, 2):
            continue
        ref, ok = check(p["slug"])
        if ok:
            continue
        d = date.fromisoformat(p["date"])
        when = "domani" if left == 1 else "fra 2 giorni"
        link = f'<a href="{SITE}post.html?p=bible/{p["slug"]}.txt">{html.escape(ref)}</a>'
        note = " (non sono riuscito a leggere il file)" if ok is None else ""
        lines.append(f"• {link}, esce {when} ({DAYS[d.weekday()]} {d:%d/%m}){note}")
    if not lines:
        print("nothing to remind")
        return
    msg = "✍️ soul social: manca la riflessione\n" + "\n".join(lines)
    if "--dry-run" in sys.argv:
        print(msg)
        return
    data = urllib.parse.urlencode({"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": msg,
                                   "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
    with urllib.request.urlopen(url, data=data, timeout=30) as r:
        print("telegram", r.status)


if __name__ == "__main__":
    main()
