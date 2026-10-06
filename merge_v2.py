"""Merge posts_v2.jsonl (+ dup_candidates.jsonl) into posts.jsonl.

A new (id, text) is the SAME post as an existing row when the id matches and the starts of the
texts agree: then the fuller, non-truncated text wins. Otherwise it is a different post that
shares the id, and is appended as another row (ids repeat on this page).
Backs up posts.jsonl first. Run:  python merge_v2.py [--dry-run]
"""
import json, re, shutil, sys
from pathlib import Path

from crawl import clean_text, is_truncated


def stem(t):
    return re.sub(r"\s+", " ", clean_text(t)).rstrip(" …").strip()


def same_post(a, b):
    k = min(len(a), len(b), 40)
    return k >= 1 and a[:k] == b[:k]


def load(path):
    p = Path(path)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l] if p.exists() else []


def main(dry):
    rows = load("posts.jsonl")
    by_id = {}
    for i, r in enumerate(rows):
        by_id.setdefault(r["confession_id"], []).append(i)
    # best version of each incoming post (a later, non-truncated sighting beats a truncated one)
    incoming = {}
    for r in load("posts_v2.jsonl") + load("dup_candidates.jsonl"):
        k = (r["confession_id"], stem(r["text"])[:40])
        if k not in incoming or (is_truncated(incoming[k]["text"]) and not is_truncated(r["text"])):
            incoming[k] = r
    upgraded = added = unchanged = 0
    for (cid, _), r in incoming.items():
        hit = next((i for i in by_id.get(cid, []) if same_post(stem(rows[i]["text"]), stem(r["text"]))), None)
        if hit is None:
            rows.append({"confession_id": cid, "post_url": None, "text": r["text"]})
            by_id.setdefault(cid, []).append(len(rows) - 1)
            added += 1
        elif is_truncated(rows[hit]["text"]) and not is_truncated(r["text"]):
            rows[hit]["text"] = r["text"]
            upgraded += 1
        else:
            unchanged += 1
    ids = [r["confession_id"] for r in rows]
    repeated = len(ids) - len(set(ids))
    print(f"incoming distinct posts: {len(incoming)} | upgraded (truncated -> full): {upgraded} | "
          f"added as new rows: {added} | already had: {unchanged}")
    print(f"posts.jsonl would have {len(rows)} rows ({len(set(ids))} distinct ids, {repeated} extra rows for repeated ids); "
          f"still truncated: {sum(is_truncated(r['text']) for r in rows)}")
    if dry:
        print("dry run: nothing written")
        return
    shutil.copy("posts.jsonl", "posts.before_merge_v2.jsonl")
    Path("posts.jsonl.tmp").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    Path("posts.jsonl.tmp").replace("posts.jsonl")
    print("written (backup: posts.before_merge_v2.jsonl)")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
