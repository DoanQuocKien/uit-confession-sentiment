"""Crawl the page's own search with a date-range filter, one window at a time (newest window first).

Facebook's page search (/profile/<page id>/search?q=...&filters=...) accepts a day range in its
creation_time filter even though the UI only offers years, so any window can be requested by URL.
Posts are keyed like crawl_v2.py (id + start of text) and appended to posts_v2.jsonl; merge_v2.py
folds them into posts.jsonl. Each saved post's date is kept in posts_dates.jsonl for checking.

Usage:
  python crawl_date.py --month 2023-09 --dry-run      # one month, saves nothing, prints coverage stats
  python crawl_date.py --from 2023-09 --to 2023-01    # months downward, saves, resumable
"""
import argparse, base64, calendar, json, random, re, time, urllib.parse
from pathlib import Path

from crawl import EXPAND_JS, clean_text, is_truncated, split_confessions
from crawl_v2 import OUT2, key, load

PAGE_ID = "100088809545601"          # UIT Confession
QUERY = "uit"
DATES_OUT = Path("posts_dates.jsonl")
DONE = Path("date_done.txt")        # windows already crawled

# message text + the post's date label ("19 Tháng 10, 2023"; the current year has no year part)
READ_DATED = r"""() => [...document.querySelectorAll('[data-ad-rendering-role="story_message"]')].map(m => {
  let n = m.parentElement, date = null;
  while (n && n.querySelectorAll('[data-ad-rendering-role="story_message"]').length === 1) {
    const e = [...n.querySelectorAll('a, span')].find(x => /^\d{1,2} Tháng \d{1,2}(, \d{4})?$/.test((x.innerText || '').normalize('NFC').trim()));
    if (e) { date = e.innerText.normalize('NFC').trim(); break; }
    n = n.parentElement;
  }
  return {date, text: m.innerText.trim()};
})"""


def date_filter(y1, m1, d1, y2, m2, d2):
    """The encoded `filters` value Facebook builds for a date range (verified against its own for a year)."""
    args = {"start_year": str(y1), "start_month": f"{y1}-{m1}", "end_year": str(y2), "end_month": f"{y2}-{m2}",
            "start_day": f"{y1}-{m1}-{d1}", "end_day": f"{y2}-{m2}-{d2}"}
    inner = {"name": "creation_time", "args": json.dumps(args, separators=(",", ":"))}
    outer = {"rp_creation_time:0": json.dumps(inner, separators=(",", ":"))}
    return base64.b64encode(json.dumps(outer, separators=(",", ":")).encode()).decode()


def window_url(y, m, d1=1, d2=None):
    d2 = d2 or calendar.monthrange(y, m)[1]
    f = urllib.parse.quote(date_filter(y, m, d1, y, m, d2), safe="")
    return f"https://www.facebook.com/profile/{PAGE_ID}/search?q={QUERY}&filters={f}"


def collect(page, url, max_scrolls=60):
    """Scroll one result list to its end; returns {key: (cid, body, date)}."""
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    time.sleep(7)
    found, idle = {}, 0
    for _ in range(max_scrolls):
        for _ in range(3):                           # expand and re-read until nothing is left to expand
            n = page.evaluate(EXPAND_JS)
            time.sleep(1.5 if n else 0.5)
            if not n:
                break
        before = len(found)
        for item in page.evaluate(READ_DATED):
            for cid, body in split_confessions(item["text"]):
                if not body:
                    continue
                k = key(cid, body)
                if k not in found or (is_truncated(found[k][1]) and not is_truncated(body)):
                    found[k] = (cid, body, item["date"])
        idle = idle + 1 if len(found) == before else 0
        if idle >= 4:                                # four scrolls with nothing new: end of the list
            break
        page.mouse.wheel(0, 2000)
        time.sleep(random.uniform(1.5, 2.5))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://localhost:9222")
    ap.add_argument("--month", help="one window, YYYY-MM")
    ap.add_argument("--from", dest="start", help="first month, YYYY-MM (newest)")
    ap.add_argument("--to", dest="stop", help="last month, YYYY-MM (oldest)")
    ap.add_argument("--dry-run", action="store_true", help="save nothing, print stats")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    if args.month:
        months = [tuple(map(int, args.month.split("-")))]
    else:
        (y, m), (y2, m2) = (tuple(map(int, s.split("-"))) for s in (args.start, args.stop))
        months = []
        while (y, m) >= (y2, m2):
            months.append((y, m)); y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    best = load()
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    with sync_playwright() as p:
        page = p.chromium.connect_over_cdp(args.cdp).contexts[0].new_page()
        for y, m in months:
            tag = f"{y}-{m:02d}"
            if tag in done and not args.dry_run:
                print(f"{tag}: already done", flush=True); continue
            found = collect(page, window_url(y, m))
            new = [(k, v) for k, v in found.items() if k not in best]
            days = sorted({v[2] for v in found.values() if v[2]})
            ids = sorted({int(v[0]) for v in found.values()})
            print(f"{tag}: {len(found)} posts | {len(found) - len(new)} already saved, {len(new)} NEW | "
                  f"ids #{ids[0] if ids else '-'}..#{ids[-1] if ids else '-'} | {len(days)} distinct dates", flush=True)
            if args.dry_run:
                print("   dates seen:", ", ".join(days[:40]), flush=True)
                continue
            with OUT2.open("a", encoding="utf-8") as f, DATES_OUT.open("a", encoding="utf-8") as g:
                for k, (cid, body, date) in found.items():
                    g.write(json.dumps({"confession_id": cid, "date": date, "month": tag}, ensure_ascii=False) + "\n")
                    if k not in best or (is_truncated(best[k]) and not is_truncated(body)):
                        best[k] = body
                        f.write(json.dumps({"confession_id": cid, "text": body}, ensure_ascii=False) + "\n")
            DONE.write_text("\n".join(sorted(done | {tag})) + "\n"); done.add(tag)
            time.sleep(random.uniform(2, 4))
        page.close()


if __name__ == "__main__":
    main()
