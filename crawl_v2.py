"""Re-scroll the page feed keeping EVERY post, keyed by (id, start of text), because ids repeat.

Writes posts_v2.jsonl (append-only; a post seen truncated is appended again when later seen full).
Resumable: it reloads posts_v2.jsonl and skips what it already has. Merge with merge_v2.py.
Needs Chrome on the debug port with the logged-in profile (see crawl.py).
Usage: python crawl_v2.py [--cdp http://localhost:9222]
Exit code 0 = reached the end of the feed; non-zero = crashed (the supervisor restarts it).
"""
import argparse, json, random, re, time
from pathlib import Path

from crawl import EXPAND_JS, PAGE_URL, READ_JS, clean_text, is_truncated, split_confessions

OUT2 = Path("posts_v2.jsonl")
END_ID = 2017            # the scroll feed ends here; ids below are covered by crawl_search_v2.py
STALE_LIMIT = 20


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://localhost:9222")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    best = load()
    print(f"resuming with {len(best)} posts already saved", flush=True)
    with sync_playwright() as p:
        page = p.chromium.connect_over_cdp(args.cdp).contexts[0].new_page()
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
            if low <= END_ID and stale >= 3:
                print("reached the end of the feed", flush=True)
                break
            if stale in (4, 8, 12):                 # feed stopped loading: nudge it, then cool down
                page.mouse.wheel(0, -2500)
                time.sleep(60 if stale == 8 else 6)
            page.mouse.wheel(0, 1800)
            time.sleep(random.uniform(2.5, 4.5) if new else random.uniform(0.8, 1.5))
        page.close()
    print(f"done: {len(best)} distinct posts (id+text); lowest id seen #{low}")


if __name__ == "__main__":
    main()
