"""Check the page for posts newer than the ones we have, and classify them.

  python update.py check     # look at the top of the feed, say how many new posts there are, change nothing
  python update.py run       # fetch new posts, add them to posts.jsonl, label them, update results.csv + report.html

Needs Chrome on the debug port with the Facebook login (started for you if it is not running) and, for `run`,
Ollama with the model (also started if it is not running). `run` also labels any post that is in posts.jsonl
but not yet in results.csv, so a run that was interrupted is finished by the next one.
Environment overrides: CHROME_EXE, CHROME_PROFILE, OLLAMA_EXE (OLLAMA_MODELS is inherited).
"""
import argparse, csv, json, os, random, subprocess, sys, time, urllib.request
from pathlib import Path

from crawl import EXPAND_JS, PAGE_URL, READ_JS, clean_text, is_truncated, split_confessions
from crawl_v2 import key

POSTS = Path("posts.jsonl")
CDP = "http://localhost:9222"
OLLAMA = "http://127.0.0.1:11434"
STOP_AFTER_KNOWN = 15     # this many already-known posts in a row means we have reached what we already had


def find_new(feed, known, stop_after=STOP_AFTER_KNOWN):
    """feed: [(id, text)] in page order, newest first. Returns (new posts in feed order, reached_known)."""
    new, seen, run = [], set(), 0
    for cid, body in feed:
        k = key(cid, body)
        if k in seen:
            continue
        seen.add(k)
        if k in known:
            run += 1
            if run >= stop_after:
                return new, True
        else:
            run = 0
            new.append((cid, body))
    return new, False


def known_keys():
    return {key(r["confession_id"], r["text"]) for r in map(json.loads, POSTS.read_text(encoding="utf-8").splitlines()) if r["text"]}


def up(url):
    try:
        urllib.request.urlopen(url, timeout=4).read()
        return True
    except Exception:
        return False


def ensure(url, cmd, what, wait=90):
    """Start `cmd` (hidden) unless `url` already answers."""
    if up(url):
        return
    print(f"starting {what} ...", flush=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(cmd, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        var = "CHROME_EXE" if what == "Chrome" else "OLLAMA_EXE"
        raise SystemExit(f"cannot start {what}: {cmd[0]!r} was not found. Start it yourself, or set {var} to its full path.")
    for _ in range(wait // 3):
        time.sleep(3)
        if up(url):
            return
    raise SystemExit(f"could not start {what}")


def ensure_chrome():
    exe = os.environ.get("CHROME_EXE", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    profile = os.environ.get("CHROME_PROFILE", r"C:\chrome_fb")
    ensure(CDP + "/json/version", [exe, "--remote-debugging-port=9222", f"--user-data-dir={profile}", "--no-first-run", PAGE_URL], "Chrome")


def ensure_ollama():
    ensure(OLLAMA + "/api/version", [os.environ.get("OLLAMA_EXE", "ollama"), "serve"], "Ollama")


def expand_all(page):
    """Click every "Xem thêm", then wait until no visible post is still cut off (a slow connection needs the wait)."""
    for _ in range(8):
        n = page.evaluate(EXPAND_JS)
        time.sleep(2 if n else 1)
        if not any(is_truncated(b) for post in page.evaluate(READ_JS) for _, b in split_confessions(post)):
            return


def scan_feed(page, max_scrolls=20):
    """Complete, expanded posts from the top of the feed: {key: (id, text)}."""
    page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
    time.sleep(8)
    out = {}
    for _ in range(max_scrolls):
        expand_all(page)
        for post in page.evaluate(READ_JS):
            for cid, body in split_confessions(post):
                if body and not is_truncated(body):
                    out[key(cid, body)] = (cid, body)
        page.mouse.wheel(0, 1800)
        time.sleep(random.uniform(1.5, 3))
    return out


def fetch_new(known, max_scrolls=60):
    """Scroll the feed from the newest post down until we reach posts we already have."""
    from playwright.sync_api import sync_playwright
    ensure_chrome()
    feed, seen = [], set()
    with sync_playwright() as p:
        page = p.chromium.connect_over_cdp(CDP).contexts[0].new_page()
        page.set_viewport_size({"width": 1400, "height": 950})
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
        time.sleep(8)
        idle = 0
        for _ in range(max_scrolls):
            expand_all(page)
            grew = False
            for post in page.evaluate(READ_JS):
                for cid, body in split_confessions(post):
                    k = key(cid, body)
                    if body and k not in seen:
                        seen.add(k); feed.append((cid, body)); grew = True
            new, reached = find_new(feed, known)
            if reached:
                break
            idle = 0 if grew else idle + 1
            if idle >= 5:                                # the feed stopped growing
                break
            page.mouse.wheel(0, 1800)
            time.sleep(random.uniform(1.5, 3))
        page.close()
    return new, reached


def pick_full(cid, truncated_text, pairs):
    """The complete version of a truncated post among search results `pairs` ({key: (id, body)}), or None.
    Several different posts can share an id, so the one whose start matches the truncated text is chosen."""
    from merge_v2 import same_post, stem
    s = stem(truncated_text)
    for c, body in pairs.values():
        if c == cid and not is_truncated(body) and len(stem(body)) >= len(s) and same_post(s, stem(body)):
            return body
    return None


def truncated_count():
    return sum(is_truncated(r["text"]) for r in map(json.loads, POSTS.read_text(encoding="utf-8").splitlines()) if r["text"])


def repair_truncated():
    """Look up every stored post that ended at "Xem thêm" by id in the page's search and swap in the full text."""
    rows = [json.loads(l) for l in POSTS.read_text(encoding="utf-8").splitlines() if l]
    bad = [r for r in rows if is_truncated(r["text"])]
    if not bad:
        return 0
    from playwright.sync_api import sync_playwright
    from crawl_search_v2 import search_all
    ensure_chrome()
    fixed = 0
    with sync_playwright() as p:
        page = p.chromium.connect_over_cdp(CDP).contexts[0].new_page()
        left = []
        for r in bad:
            for attempt in range(3):                     # an empty result is often just Facebook being slow
                pairs, _ = search_all(page, r["confession_id"])
                if pairs:
                    break
                time.sleep(10)
            full = pick_full(r["confession_id"], r["text"], pairs)
            if full:
                r["text"], fixed = full, fixed + 1
            else:
                left.append(r)
        if left:                                         # the newest posts may not be in the search index yet: use the feed
            feed = scan_feed(page)
            for r in left:
                full = pick_full(r["confession_id"], r["text"], feed)
                if full:
                    r["text"], fixed = full, fixed + 1
        page.close()
    if fixed:
        tmp = POSTS.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(POSTS)
    print(f"truncated posts: {len(bad)} found, {fixed} repaired" + ("" if fixed == len(bad) else f", {len(bad) - fixed} still cut off"), flush=True)
    return fixed


def process(new):
    """Add `new` to posts.jsonl, repair truncated posts, then label every post that is not in results.csv yet."""
    if new:
        with POSTS.open("a", encoding="utf-8") as f:
            for cid, body in reversed(new):              # oldest first, so the file stays roughly in id order
                f.write(json.dumps({"confession_id": cid, "post_url": None, "text": body}, ensure_ascii=False) + "\n")
    repair_truncated()
    import classify
    posts = classify.load_posts()
    results = Path("results.csv")
    old = list(csv.DictReader(open(results, encoding="utf-8-sig"))) if results.exists() else []
    old = [r for r in old if r["truncated"] != "True"]   # a label made from cut-off text is redone (cache hit if it still is)
    have = {key(r["confession_id"], r["text"]) for r in old}
    todo = [p for p in posts if key(p["confession_id"], p["text"]) not in have]
    if not todo:
        print("nothing to label: results.csv is up to date")
        return
    print(f"labeling {len(todo)} post(s): " + ", ".join("#" + p["confession_id"] for p in todo[:12]) + (" ..." if len(todo) > 12 else ""), flush=True)
    classify.load_env()
    ensure_ollama()
    cl = classify.ollama_labels(todo, Path("llm_cache.jsonl"))
    ph = classify.phobert_labels(todo)
    rows = old + [classify.result_row(p, cl[i], ph[i]) for i, p in enumerate(todo)]
    classify.write_outputs(rows)
    print(f"results.csv now has {len(rows)} rows (+{len(todo)})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "run"])
    ap.add_argument("--max-scrolls", type=int, default=60)
    args = ap.parse_args()
    new, reached = fetch_new(known_keys(), args.max_scrolls)
    ids = ", ".join("#" + c for c, _ in new[:15]) + (" ..." if len(new) > 15 else "")
    print(f"{len(new)} new post(s) on the page" + (f": {ids}" if new else "") +
          ("" if reached else "  (warning: did not reach posts we already have; try a higher --max-scrolls)"))
    if args.command == "run":
        process(new)
    else:
        cut = truncated_count()
        if cut:
            print(f"{cut} stored post(s) are cut off at \"Xem thêm\"; `run` repairs them")
        if new:
            print("run `python update.py run` to add and label them")


if __name__ == "__main__":
    main()
