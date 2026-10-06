"""Scroll the page feed keeping EVERY post, keyed by (id, start of text), because ids repeat.

Writes posts_v2.jsonl (append-only; a post seen truncated is appended again when later seen full).
Resumable: it reloads posts_v2.jsonl and skips what it already has. Merge with merge_v2.py.
Needs Chrome on the debug port with the logged-in profile (see crawl.py).

Usage:
  python crawl_v2.py                              # from the top of the feed (newest) down to about #2017
  python crawl_v2.py --prepare 2023-09            # opens the page, picks year/month in "Bộ lọc bài viết", then STOPS
  (you click "Xong" in that Chrome tab; the feed then jumps to the end of that month)
  python crawl_v2.py --use-open-tab --end-id 3    # scrolls that filtered tab down through everything older
Exit code 0 = reached the end of the feed; non-zero = crashed or stalled early (the supervisor restarts it).
"""
import argparse, json, random, re, sys, time
from pathlib import Path

from crawl import EXPAND_JS, PAGE_URL, READ_JS, clean_text, is_truncated, split_confessions

OUT2 = Path("posts_v2.jsonl")
PAGE_ID = "100088809545601"          # UIT Confession
END_ID = 2017            # default end of the plain feed; ids below are reached with --prepare / --use-open-tab
STALE_LIMIT = 20

# visible elements whose first line of text equals `want` (Facebook's Vietnamese labels need NFC matching)
FIND = r"""([sel, want]) => {
  const norm = s => (s || '').normalize('NFC').replace(/\s+/g, ' ').trim();
  return [...document.querySelectorAll(sel)]
    .filter(e => norm((e.innerText || '').split('\n')[0]) === want)
    .map(e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })
    .filter(r => r.w > 0 && r.h > 0); }"""
ANY = "span, div, a"
DIALOG = "[role=dialog] span, [role=dialog] div"
OPTION = "[role=option], [role=menuitem], [role=radio], [role=menuitemradio]"


def key(cid, body):
    return f"{cid}|{re.sub(r'[ \t]+', ' ', clean_text(body))[:50]}"


def load():
    best = {}
    if OUT2.exists():
        for l in OUT2.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            k = key(r["confession_id"], r["text"])
            if k not in best or (is_truncated(best[k]) and not is_truncated(r["text"])):
                best[k] = r["text"]
    return best


def click(page, sel, text, nth=0):
    for _ in range(30):                               # the dialog and its dropdowns appear slowly on a bad connection
        hits = page.evaluate(FIND, [sel, text])
        if len(hits) > nth:
            break
        time.sleep(1)
    if len(hits) <= nth:
        raise RuntimeError(f"cannot find {text!r} on the page ({len(hits)} matches)")
    h = hits[nth]
    page.mouse.click(h["x"] + h["w"] / 2, h["y"] + h["h"] / 2)
    time.sleep(2.5)


def pick_month(page, year, month):
    """Page timeline: 'Bộ lọc' > 'Đi đến: Năm / Tháng'. Leaves the dialog open on purpose: the feed only jumps
    after the user clicks 'Xong' (this script never clicks it), then --use-open-tab crawls that tab."""
    click(page, ANY, "Bộ lọc", nth=3)                 # the role=button one above the posts
    click(page, DIALOG, "Năm")
    click(page, OPTION, str(year))
    click(page, DIALOG, "Tháng")
    click(page, OPTION, f"Tháng {month}")


def find_open_tab(ctx):
    """The page tab the user filtered (UIT Confession timeline), or None."""
    for pg in reversed(ctx.pages):
        if "facebook.com/UITconfess" in pg.url or f"facebook.com/profile/{PAGE_ID}" in pg.url:
            return pg
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://localhost:9222")
    ap.add_argument("--prepare", help="YYYY-MM: open the page, choose that year and month in 'Bộ lọc bài viết', "
                                      "then stop and leave the tab open for the user to click 'Xong'")
    ap.add_argument("--use-open-tab", action="store_true", help="crawl the already-filtered UIT Confession tab")
    ap.add_argument("--end-id", type=int, default=END_ID, help="lowest id expected before the feed ends")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    best = load()
    print(f"resuming with {len(best)} posts already saved", flush=True)
    with sync_playwright() as p:
        ctx = p.chromium.connect_over_cdp(args.cdp).contexts[0]
        if args.prepare:
            page = ctx.new_page()
            page.set_viewport_size({"width": 1400, "height": 950})
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
            time.sleep(8)
            y, m = (int(x) for x in args.prepare.split("-"))
            pick_month(page, y, m)
            page.bring_to_front()
            print(f"prepared: year {y}, month {m} chosen. The tab is left open; click 'Xong' in it, then run with --use-open-tab", flush=True)
            return                                  # the tab stays open in Chrome
        if args.use_open_tab:
            page = find_open_tab(ctx)
            if page is None:
                raise SystemExit("no open UIT Confession tab found; run --prepare first")
            print(f"crawling the open tab: {page.url[:90]}", flush=True)
        else:
            page = ctx.new_page()
            page.set_viewport_size({"width": 1400, "height": 950})
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
            time.sleep(8)
        stale, low, passes = 0, float("inf"), 0
        while stale < STALE_LIMIT:
            for _ in range(3):                      # expand, wait, expand again until nothing is left
                n = page.evaluate(EXPAND_JS)
                time.sleep(1.5 if n else 0.3)
                if not n:
                    break
            new, ids = 0, []
            for post in page.evaluate(READ_JS):
                for cid, body in split_confessions(post):
                    ids.append(int(cid))
                    if not body:
                        continue
                    k = key(cid, body)
                    if k not in best or (is_truncated(best[k]) and not is_truncated(body)):
                        best[k] = body
                        with OUT2.open("a", encoding="utf-8") as f:
                            f.write(json.dumps({"confession_id": cid, "text": body}, ensure_ascii=False) + "\n")
                        new += 1
            vis_low = min(ids, default=low)
            progressed = new > 0 or vis_low < low
            low = min(low, vis_low)
            stale = 0 if progressed else stale + 1
            passes += 1
            if passes % 5 == 0 or new:
                print(f"{len(best)} posts (+{new}), lowest visible #{low}, stalled {stale}", flush=True)
            if low <= args.end_id and stale >= 3:
                print("reached the end of the feed", flush=True)
                break
            if stale in (4, 8, 12):                 # feed stopped loading: nudge it, then cool down
                page.mouse.wheel(0, -2500)
                time.sleep(60 if stale == 8 else 6)
            page.mouse.wheel(0, 1800)
            time.sleep(random.uniform(2.5, 4.5) if new else random.uniform(0.8, 1.5))
        page.close()
    print(f"done: {len(best)} distinct posts (id+text); lowest id seen #{low}")
    if args.use_open_tab and low > args.end_id + 40:         # stalled long before the expected end: not finished
        print(f"stopped early at #{low} (expected to reach about #{args.end_id}); exiting non-zero so it is retried", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
