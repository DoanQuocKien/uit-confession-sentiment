"""Re-fetch posts whose saved text ends in "Xem thêm" (truncated), via the filtered Facebook search.

Fixes go to recrawl_fixes.jsonl as they are found (resumable); at the end they are merged into
posts.jsonl (text replaced, newly seen confessions appended). posts.jsonl keeps raw text.
Needs Chrome on the debug port with the logged-in profile (see crawl.py).
Usage: python recrawl_truncated.py [--cdp http://localhost:9222]
"""
import argparse, json, random, time
from pathlib import Path

from crawl import EXPAND_JS, OUT, READ_JS, is_truncated, split_confessions
from crawl_search import URL

FIXES = Path("recrawl_fixes.jsonl")
NEW = Path("recrawl_new.jsonl")
EXPAND_ROUNDS = 4   # expand clicks + re-reads per scroll position
SCROLLS = 6


def fetch(page, cid):
    """Return (bodies of all confessions seen, True if cid's body came back complete)."""
    page.goto(URL.format(cid=cid), wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    got = {}
    for _ in range(SCROLLS + 1):
        for _ in range(EXPAND_ROUNDS + 3):
            clicked = page.evaluate(EXPAND_JS)
            time.sleep(2 if clicked else 1)
            posts = page.evaluate(READ_JS)
            for post in posts:
                for c, body in split_confessions(post):
                    if c not in got or (is_truncated(got[c]) and not is_truncated(body)):
                        got[c] = body
            if str(cid) in got and not is_truncated(got[str(cid)]):
                return got, True
            if posts and not clicked:   # rendered and nothing left to expand: safe to scroll on
                break                   # (no posts yet = still rendering: keep waiting, don't scroll)
        if str(cid) in got and not is_truncated(got[str(cid)]):
            return got, True
        if not got:      # nothing rendered after waiting: the search has no results, don't scroll an empty page
            return got, False
        page.mouse.wheel(0, 1500)
        time.sleep(2)
    return got, False


def fetch_any(page, cid):
    """fetch(cid); if the search does not return it complete, try the neighbors that share its post."""
    merged = {}
    for cand in (cid, cid + 1, cid - 1):
        got, ok = fetch(page, cand)
        for c, b in got.items():
            if c not in merged or (is_truncated(merged[c]) and not is_truncated(b)):
                merged[c] = b
        if str(cid) in merged and not is_truncated(merged[str(cid)]):
            return merged, True
    return merged, False


def merge():
    fixes = {}
    if FIXES.exists():
        for l in FIXES.read_text(encoding="utf-8").splitlines():
            r = json.loads(l); fixes[r["confession_id"]] = r["text"]
    new = {}
    if NEW.exists():
        for l in NEW.read_text(encoding="utf-8").splitlines():
            r = json.loads(l); new[r["confession_id"]] = r["text"]
    rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l]
    have = {r["confession_id"] for r in rows}
    for r in rows:
        if r["confession_id"] in fixes:
            r["text"] = fixes[r["confession_id"]]
    rows += [{"confession_id": c, "post_url": None, "text": t} for c, t in new.items() if c not in have]
    tmp = Path("posts.jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(OUT)
    print(f"merged: {len(fixes)} texts replaced, {len([c for c in new if c not in have])} new confessions added")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://localhost:9222")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l]
    have = {r["confession_id"] for r in rows}
    done = {json.loads(l)["confession_id"] for l in FIXES.read_text(encoding="utf-8").splitlines()} if FIXES.exists() else set()
    todo = sorted((int(r["confession_id"]) for r in rows if is_truncated(r["text"]) and r["confession_id"] not in done), reverse=True)
    print(f"{len(todo)} truncated posts to re-fetch ({len(done)} already fixed)", flush=True)
    failed, errors = [], 0
    with sync_playwright() as p:
        def new_page():
            return p.chromium.connect_over_cdp(args.cdp).contexts[0].new_page()
        page = new_page()
        i = 0
        while i < len(todo):
            cid = todo[i]
            try:
                got, ok = fetch_any(page, cid)
                errors = 0
            except Exception as e:
                errors += 1
                print(f"#{cid} error {errors}/5: {str(e)[:100]}", flush=True)
                if errors >= 5:
                    break
                time.sleep(30)
                try: page = new_page()
                except Exception: pass
                continue                      # retry the same id
            i += 1
            if ok:
                with FIXES.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"confession_id": str(cid), "text": got[str(cid)]}, ensure_ascii=False) + "\n")
            else:
                failed.append(cid)
            for c, body in got.items():       # confessions we did not have at all
                if c not in have and not is_truncated(body) and body:
                    have.add(c)
                    with NEW.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"confession_id": c, "text": body}, ensure_ascii=False) + "\n")
                    print(f"  new confession #{c}", flush=True)
            print(f"#{cid}: {'fixed' if ok else 'STILL TRUNCATED'} ({i}/{len(todo)}, failed so far {len(failed)})", flush=True)
            time.sleep(random.uniform(2, 4))
        page.close()
    print("still truncated:", failed)
    merge()


if __name__ == "__main__":
    main()
