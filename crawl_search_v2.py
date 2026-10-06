"""Variant-aware search crawl for ids below the feed's reach: keeps EVERY post found for an id.

crawl_search.py stored results by id (a dict), so a second post sharing an id was lost. This keeps
every (id, text) pair, expands truncated posts, and appends to posts_v2.jsonl (same format and key
as crawl_v2.py). Merge with merge_v2.py. Resumable via search_v2_done.txt (ids already searched).
Usage: python crawl_search_v2.py [--start 2045] [--stop 1] [--limit N]
"""
import argparse, json, random, time
from pathlib import Path

from crawl import EXPAND_JS, READ_JS, is_truncated, split_confessions
from crawl_search import GAPS, URL, in_gap
from crawl_v2 import OUT2, key, load

DONE = Path("search_v2_done.txt")
SCROLLS = 4


def search_all(page, cid):
    """All (id, body) pairs in the results for '#cid'. Returns (pairs, found_cid)."""
    page.goto(URL.format(cid=cid), wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    pairs = {}
    for scroll in range(SCROLLS + 1):
        before = len(pairs)
        for _ in range(6):                           # wait for render + expand until nothing is left
            n = page.evaluate(EXPAND_JS)
            time.sleep(2 if n else 1)
            posts = page.evaluate(READ_JS)
            for post in posts:
                for c, body in split_confessions(post):
                    k = key(c, body)
                    if body and (k not in pairs or (is_truncated(pairs[k][1]) and not is_truncated(body))):
                        pairs[k] = (c, body)
            if posts and not n:
                break
        if not pairs:
            return pairs, False                      # no results at all: do not scroll an empty page
        if scroll > 0 and len(pairs) == before:
            break                                    # the last scroll added nothing: every result has been seen
        page.mouse.wheel(0, 1500)
        time.sleep(2)
    return pairs, any(c == str(cid) for c, _ in pairs.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://localhost:9222")
    ap.add_argument("--start", type=int, default=2045)  # overlap: the scroll crawl died near the bottom
    ap.add_argument("--stop", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="stop after N searches (pilot)")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    best = load()
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    # skip the page's numbering gaps (crawl_search.GAPS) and ids already known to be missing (missing.txt)
    missing = {l.split()[0] for l in Path("missing.txt").read_text(encoding="utf-8").splitlines() if l and l.split()[0].isdigit()}
    todo = [c for c in range(args.start, args.stop - 1, -1) if not in_gap(c) and str(c) not in missing and str(c) not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} ids to search, {len(best)} posts already in posts_v2.jsonl", flush=True)
    errors = searched = new_total = 0
    with sync_playwright() as p:
        def new_page():
            return p.chromium.connect_over_cdp(args.cdp).contexts[0].new_page()
        page = new_page()
        i = 0
        while i < len(todo):
            cid = todo[i]
            try:
                pairs, found = search_all(page, cid)
                for _ in range(2):                    # empty can mean Chrome/Facebook was not ready: retry patiently
                    if pairs:
                        break
                    time.sleep(10)
                    pairs, found = search_all(page, cid)
                errors = 0
            except Exception as e:
                errors += 1
                print(f"#{cid} error {errors}/6: {str(e)[:90]}", flush=True)
                if errors >= 6:
                    raise SystemExit(1)
                time.sleep(30)
                try: page = new_page()
                except Exception: pass
                continue
            new = 0
            for k, (c, body) in pairs.items():
                if int(c) > args.start + 3:      # newer ids are the scroll crawl's job
                    continue
                if k not in best or (is_truncated(best[k]) and not is_truncated(body)):
                    best[k] = body
                    with OUT2.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"confession_id": c, "text": body}, ensure_ascii=False) + "\n")
                    new += 1
            if not pairs:                             # still nothing after the retries: list it for review
                with Path("search_v2_empty.txt").open("a") as f:
                    f.write(f"{cid}\n")
            with DONE.open("a") as f:
                f.write(f"{cid}\n")
            i += 1; searched += 1; new_total += new
            if searched % 80 == 0:               # fresh tab: a long-lived Facebook tab runs out of memory
                try:
                    page.close(); page = new_page()
                except Exception:
                    pass
            print(f"#{cid}: {len(pairs)} posts seen, +{new} new ({'found' if found else 'id not in results'}) [{searched}/{len(todo)}]", flush=True)
            time.sleep(random.uniform(2, 4))
        page.close()
    print(f"done: searched {searched} ids, {new_total} new/updated posts saved")


if __name__ == "__main__":
    main()
