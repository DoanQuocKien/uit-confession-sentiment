r"""Crawl the UIT Confessions Facebook page into posts.jsonl (resumable).

First run: log in by hand in the opened browser, then press Enter here.
Usage: python crawl.py [--max-posts N] [--cdp http://localhost:9222]

--cdp attaches to a normal Chrome you started yourself, so Facebook's login / two-step
check sees an ordinary browser:
  chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\chrome_fb
Log in there once (finish 2FA), then run this with --cdp.
"""
import argparse, json, random, re, time
from pathlib import Path

PAGE_URL = "https://www.facebook.com/UITconfess"
OUT = Path("posts.jsonl")
PROFILE = "fb_profile"
STALE_LIMIT = 15  # scrolls without a new post before we stop

# Confession number such as "#UIT12345" or "#12345", near the start of the post.
ID_RE = re.compile(r"#\s*(?:UIT\s*)?(\d{1,7})\b", re.I)
# ponytail: permalink formats vary; extend if posts come back without an id.
PERMA_RE = re.compile(r"(?:/posts/|story_fbid=|/permalink/)(pfbid\w+|\d+)")


def extract_id(text, url=""):
    """Confession number from the text, else the Facebook post id from the URL."""
    m = ID_RE.search(text[:200])
    if m:
        return m.group(1)
    m = PERMA_RE.search(url)
    return m.group(1) if m else None


# "#NNNN" alone on a line (current format) or "#N: text" on one line (the oldest posts, #1-#2)
SPLIT_RE = re.compile(r"(?m)^#\s*(?:UIT\s*)?(\d{1,7})(?:\s*$|[ \t]*:[ \t]*)")


def split_confessions(text):
    """One Facebook post can bundle several confessions, each led by a '#NNNN' line."""
    parts = SPLIT_RE.split(text)  # [pre, id1, body1, id2, body2, ...]
    return [(parts[i], parts[i + 1].strip().strip("-").strip()) for i in range(1, len(parts) - 1, 2)]


# Page furniture mixed into the post text (found by counting repeated lines/phrases over all posts).
_NOISE = [
    re.compile(r"#UITconfessions?\s*:?\s*(?:https?://)?bit\.ly/\S*", re.I),  # hashtag + link (+ typos)
    re.compile(r"#UITconfessions?\b", re.I),                                   # hashtag alone
    re.compile(r"\bẨn bớt\b"),                                                 # button shown after "Xem thêm"
    re.compile(r"[ \t]*-{3,}[ \t]*$", re.M),                                   # dash separators (alone or at line end)
    re.compile(r"^[ \t]*\.[ \t]*$", re.M),                                     # lone-dot lines
    re.compile(r"(?:…|\.\.\.)?[ \t]*\b(?:Xem thêm|See more)[ \t]*$"),          # truncated tail
]


def clean_text(t):
    """Raw post text without page furniture. posts.jsonl keeps the raw text; clean at load time."""
    for rx in _NOISE:
        t = rx.sub("", t)
    return re.sub(r"\n{2,}", "\n", re.sub(r"[ \t]+\n", "\n", t)).strip()


def is_truncated(t):
    return bool(re.search(r"(?:Xem thêm|See more)[ \t]*$", t.strip()))


def load_seen():
    if not OUT.exists():
        return set()
    return {json.loads(l)["confession_id"] for l in OUT.read_text(encoding="utf-8").splitlines() if l}


# Confession posts are NOT role=article on this page; their text sits in story_message nodes.
# ponytail: Facebook markup changes break these selectors; upgrade path is parsing the
# GraphQL responses via page.on("response"). Dates and per-post links are not captured
# (Facebook renders no per-post permalink in the DOM here).
EXPAND_JS = r"""() => [...document.querySelectorAll('[data-ad-rendering-role="story_message"] [role=button]')]
  .filter(b => /(Xem thêm|See more)$/.test(b.innerText.trim())).map(b => b.click()).length"""
READ_JS = r"""() => [...document.querySelectorAll('[data-ad-rendering-role="story_message"]')]
  .map(m => m.innerText.trim())"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-posts", type=int, default=0)
    ap.add_argument("--cdp", help="attach to a running Chrome, e.g. http://localhost:9222")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    seen = load_seen()
    total = len(seen)
    with sync_playwright() as p:
        if args.cdp:
            ctx = p.chromium.connect_over_cdp(args.cdp).contexts[0]
            page = ctx.new_page()
        else:  # real Chrome and no automation banner/flag look less like a bot
            ctx = p.chromium.launch_persistent_context(
                PROFILE, channel="chrome", headless=False,
                ignore_default_args=["--enable-automation"],
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PAGE_URL)
        input("Log in in the browser if needed, wait for the page feed, then press Enter...")

        stale, low = 0, float("inf")
        while stale < STALE_LIMIT and not (args.max_posts and total >= args.max_posts):
            new, seen_ids = 0, []
            if page.evaluate(EXPAND_JS):
                time.sleep(1)  # let expanded text render
            for post in page.evaluate(READ_JS):
                for cid, body in split_confessions(post):
                    seen_ids.append(int(cid))
                    if cid in seen or not body:
                        continue
                    seen.add(cid)
                    rec = {"confession_id": cid, "post_url": None, "text": body}
                    with OUT.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    new += 1
                    total += 1
            # progress = new posts, or reaching older ids (fast-forward over already-saved ones)
            vis_low = min(seen_ids, default=low)
            progressed = new > 0 or vis_low < low
            low = min(low, vis_low)
            stale = 0 if progressed else stale + 1
            print(f"{total} posts (+{new}), lowest visible #{low}, stalled {stale}")
            if stale in (4, 8, 12):  # feed stopped loading: nudge it, then cool down
                page.mouse.wheel(0, -2500)
                time.sleep(60 if stale == 8 else 6)
            page.mouse.wheel(0, 1800)  # small steps: big jumps skip virtualized posts
            time.sleep(random.uniform(2.5, 4.5) if new else random.uniform(0.8, 1.5))
        page.close() if args.cdp else ctx.close()


if __name__ == "__main__":
    main()
