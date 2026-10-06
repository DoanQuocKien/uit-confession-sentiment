"""Fill in confessions older than crawl.py can reach, using Facebook search filtered to the page.

Searches "uit confession #ID" with the author filter (UIT Confession page), saves every
confession in the result posts (a post bundles 2-3), and logs IDs it can't find to missing.txt.
Needs Chrome on the debug port with a logged-in profile (see crawl.py).
Usage: python crawl_search.py [--cdp http://localhost:9222] [--start 2016]
"""
import argparse, json, random, time
from pathlib import Path

from crawl import EXPAND_JS, READ_JS, OUT, load_seen, split_confessions

# Author filter = UIT Confession page id 100088809545601 (base64 of the rp_author filter).
FILTER = "eyJycF9hdXRob3I6MCI6IntcIm5hbWVcIjpcImF1dGhvclwiLFwiYXJnc1wiOlwiMTAwMDg4ODA5NTQ1NjAxXCJ9In0%3D"
URL = "https://www.facebook.com/search/top?q=uit%20confession%20%23{cid}&filters=" + FILTER
# Numbering jumps in the page itself (nothing to find): #1484->#1985, #1306->#1356, #836->#887.
GAPS = [range(1485, 1985), range(1307, 1356), range(837, 887), range(760, 780)]
MISSING = Path("missing.txt")
MAX_SCROLLS = 6
MAX_MISS_STREAK = 15  # consecutive not-found IDs before we assume throttling and stop
MAX_ERRORS = 5
STEP = 5  # gap probing stride


def in_gap(cid):
    return any(cid in g for g in GAPS)


def search(page, cid):
    """Return {id: body} for every confession in the results, scrolling until cid shows up."""
    page.goto(URL.format(cid=cid), wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    got = {}
    for _ in range(MAX_SCROLLS + 1):
        if page.evaluate(EXPAND_JS):
            time.sleep(1)
        for post in page.evaluate(READ_JS):
            got.update(split_confessions(post))
        if str(cid) in got:
            break
        page.mouse.wheel(0, 1500)
        time.sleep(2)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://localhost:9222")
    ap.add_argument("--start", type=int, default=2016)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    seen = load_seen()
    if not MISSING.exists():
        MISSING.write_text("1485-1984 known numbering gap (page jumped from #1484 to #1985)\n"
                           "1307-1355 known numbering gap (page jumped from #1306 to #1356)\n", encoding="utf-8")
    missing_logged = {l.split()[0] for l in MISSING.read_text(encoding="utf-8").splitlines() if l}
    streak = errors = 0
    with sync_playwright() as p:
        def new_page():
            return p.chromium.connect_over_cdp(args.cdp).contexts[0].new_page()

        page = new_page()
        def save(got):
            n = 0
            for c, body in got.items():
                if c in seen or not body or int(c) > args.start or in_gap(int(c)):
                    continue  # skip dupes, empties, newer ids (already crawled), stray typos
                seen.add(c)
                with OUT.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"confession_id": c, "post_url": None, "text": body}, ensure_ascii=False) + "\n")
                n += 1
            return n

        cid = args.start + 1
        while cid > 1:
            cid -= 1
            if str(cid) in seen or in_gap(cid) or str(cid) in missing_logged:
                continue
            try:
                got = search(page, cid)
                if str(cid) not in got:  # one retry; search can miss
                    time.sleep(5)
                    got = search(page, cid)
                errors = 0
            except Exception as e:
                errors += 1
                print(f"#{cid} error {errors}/{MAX_ERRORS}: {str(e)[:120]}", flush=True)
                if errors >= MAX_ERRORS:
                    break
                time.sleep(30)
                try:  # tab/browser may have been closed: reconnect to the debug port
                    page = new_page()
                except Exception as e2:
                    print(f"reconnect failed: {str(e2)[:100]}", flush=True)
                cid += 1  # retry this id
                continue
            new = save(got)
            if str(cid) in got:
                streak = 0
            else:
                streak += 1
                with MISSING.open("a", encoding="utf-8") as f:
                    f.write(f"{cid} not found by search\n")
                missing_logged.add(str(cid))
            print(f"#{cid}: +{new} (total saved {len(seen)}), miss streak {streak}", flush=True)
            if streak >= MAX_MISS_STREAK:
                # Probably a numbering gap: step down until results return, then rescan the edge.
                hi, probe = cid + streak - 1, cid
                while probe > STEP:
                    probe -= STEP
                    try:
                        got = search(page, probe)
                    except Exception as e:
                        print(f"gap probe #{probe} error: {str(e)[:100]}", flush=True)
                        got = {}
                    if got:
                        save(got)
                        break
                    time.sleep(random.uniform(2, 4))
                else:
                    print("no results anywhere below, stopping (end of history, or throttled)")
                    break
                with MISSING.open("a", encoding="utf-8") as f:
                    f.write(f"{probe + STEP}-{hi} probable numbering gap (auto-detected, edges approximate)\n")
                print(f"gap #{probe + STEP}-#{hi} skipped; resuming from #{probe + STEP - 1}", flush=True)
                cid, streak = probe + STEP, 0
            # ponytail: fixed pacing; add backoff if Facebook starts rate limiting
            time.sleep(random.uniform(2, 4))
        page.close()


if __name__ == "__main__":
    main()
