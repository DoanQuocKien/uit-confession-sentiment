"""Local viewer for the posts and their classifications:  python ui.py  (opens http://127.0.0.1:8765)

Serves ui.html and the rows of results.csv as JSON, re-reading the file whenever it changes, so posts added by
`python update.py run` show up after a page reload. Listens on this machine only: the data contains personal information.
"""
import argparse, csv, json, threading, webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RESULTS = Path("results.csv")
PAGE = Path(__file__).with_name("ui.html")
_cache = {"mtime": None, "body": b"[]"}


def load_rows():
    """results.csv as JSON bytes, cached until the file changes. Adds `shared`: how many posts use the same id."""
    mtime = RESULTS.stat().st_mtime if RESULTS.exists() else None
    if mtime != _cache["mtime"]:
        rows = list(csv.DictReader(open(RESULTS, encoding="utf-8-sig"))) if mtime else []
        ids = Counter(r["confession_id"] for r in rows)
        for r in rows:
            r["shared"] = ids[r["confession_id"]]
            r["agree"] = r["agree"] == "True"
            r["truncated"] = r["truncated"] == "True"
            del r["post_url"]
        _cache.update(mtime=mtime, body=json.dumps(rows, ensure_ascii=False).encode("utf-8"))
    return _cache["body"]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/api/data":
            body, kind = load_rows(), "application/json; charset=utf-8"
        elif self.path.split("?")[0] in ("/", "/index.html"):
            body, kind = PAGE.read_bytes(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):   # keep the console quiet
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)      # 127.0.0.1: not reachable from other machines
    url = f"http://127.0.0.1:{args.port}/"
    print(f"serving {len(json.loads(load_rows()))} posts at {url}   (Ctrl+C to stop)", flush=True)
    if not args.no_open:
        threading.Timer(0.7, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
