"""The paste-a-link page and the one endpoint behind it.

Standard library only, so it runs on any free host that can run Python:

    YOUTUBE_API_KEY=... python3 webapp/server.py        # http://localhost:7860

The YouTube key stays on the server. The page never sees it.

Quota: the free YouTube allowance is 10,000 units a day and one lookup costs
about 8 (one video read plus a page of comments per hundred). Answers are
kept for six hours per video and each visitor gets a modest hourly allowance,
so one busy Short cannot spend the day's quota on its own.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from titlesift.fetch import YouTubeAPISource, video_id  # noqa: E402
from titlesift.finder import default_caches, extractor, find, interactive  # noqa: E402

STATIC = HERE / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
         ".json": "application/manifest+json", ".svg": "image/svg+xml",
         ".png": "image/png", ".css": "text/css"}
CACHE_SECONDS = 6 * 3600
PER_HOUR = int(os.environ.get("LOOKUPS_PER_HOUR", "30"))

_answers: dict[str, tuple[float, dict]] = {}
_visits: dict[str, list[float]] = {}
_lock = threading.Lock()
_caches = default_caches()


def _youtube_error(e: urllib.error.HTTPError) -> tuple[int, str]:
    try:
        reason = json.loads(e.read().decode())["error"]["errors"][0]["reason"]
    except Exception:
        reason = ""
    if reason == "commentsDisabled":
        return 200, "Comments are turned off on this video, so there is nothing to read."
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        return 503, "The tool has used up today's free YouTube allowance. It resets at midnight Pacific time."
    if e.code == 404 or reason == "videoNotFound":
        return 404, "That video could not be found. It may be private or deleted."
    return 502, "YouTube did not answer properly. Try again in a minute."


def lookup(url: str, visitor: str) -> tuple[int, dict]:
    try:
        vid = video_id(url.strip())
    except ValueError:
        return 400, {"error": "That doesn't look like a YouTube link. Copy it from the Share button and paste it here."}

    now = time.time()
    with _lock:
        hit = _answers.get(vid)
        if hit and now - hit[0] < CACHE_SECONDS:
            return 200, hit[1]
        recent = [t for t in _visits.get(visitor, []) if now - t < 3600]
        if len(recent) >= PER_HOUR:
            return 429, {"error": "That's a lot of lookups in one hour. Give it a little while and try again."}
        _visits[visitor] = recent + [now]

    try:
        result = find(vid, YouTubeAPISource(), _caches)
    except urllib.error.HTTPError as e:
        code, msg = _youtube_error(e)
        if code == 200:
            return 200, {"video": {"id": vid}, "answer": None, "asked": 0,
                         "others": [], "notice": msg}
        return code, {"error": msg}
    except RuntimeError as e:
        if "not found" in str(e):
            return 404, {"error": "That video could not be found. It may be private or deleted."}
        return 500, {"error": "Something went wrong reading that video."}
    except Exception:
        return 500, {"error": "Something went wrong reading that video."}

    with _lock:
        _answers[vid] = (now, result)
        if len(_answers) > 5000:
            for k in sorted(_answers, key=lambda k: _answers[k][0])[:1000]:
                _answers.pop(k, None)
    return 200, result


class Handler(BaseHTTPRequestHandler):
    server_version = "titlesift"

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/find":
            q = urllib.parse.parse_qs(u.query).get("url", [""])[0]
            visitor = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
            code, data = lookup(q, visitor)
            return self._send(code, json.dumps(data, ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")
        if u.path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            return self._send(404, b"Not found", "text/plain")
        return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"),
                          "no-cache")

    def log_message(self, fmt, *args):
        # Only the path, never the query: the link someone looked up is theirs.
        sys.stderr.write(f"{self.command} {self.path.split('?')[0]}\n")


def main():
    if not os.environ.get("YOUTUBE_API_KEY"):
        print("Set YOUTUBE_API_KEY first.", file=sys.stderr)
        raise SystemExit(2)
    interactive()
    extractor()          # build the catalogue before the first visitor waits on it
    port = int(os.environ.get("PORT", "7860"))
    print(f"Listening on http://0.0.0.0:{port}", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
