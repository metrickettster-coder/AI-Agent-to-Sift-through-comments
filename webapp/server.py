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

import base64
import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from titlesift.fetch import YouTubeAPISource, video_id  # noqa: E402
from titlesift.finder import (_answer_dict, _db_note, confirm, default_caches,  # noqa: E402
                              extractor, find, interactive)
from titlesift.gazetteer import fold  # noqa: E402

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
        fresh = hit[1] if hit and now - hit[0] < CACHE_SECONDS else None
    if fresh:
        return 200, with_names(fresh)
    with _lock:
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
    return 200, with_names(result)


# ------------------------------------------------------------- feedback
#
# Render's free disk is wiped on every deploy, so feedback cannot live in a
# file. Each message is emailed through Formspree. With GITHUB_TOKEN set (a fine-grained token that may only write
# issues on FEEDBACK_REPO) each message becomes an issue, where it lasts and
# can be read and acted on. Without it, messages still reach the service log.

FEEDBACK_REPO = os.environ.get("FEEDBACK_REPO", "metrickettster-coder/AI-Agent-to-Sift-through-comments")
# Chi's Formspree form: each message arrives as an email. The form id is
# public by design (it normally sits in a page's HTML), so it is not a secret.
FORMSPREE_URL = os.environ.get("FORMSPREE_URL", "https://formspree.io/f/mkjgbkgb")
KINDS = {"right": "Answer was right", "wrong": "Wrong or missing name",
         "bug": "Something broke", "idea": "Idea"}
_sent: dict[str, list[float]] = {}


def _clip(v, n: int) -> str:
    return str(v or "").replace("\r", "")[:n].strip()


def feedback(data: dict, visitor: str) -> tuple[int, dict]:
    kind = data.get("kind") if data.get("kind") in KINDS else "idea"
    message = _clip(data.get("message"), 2000)
    video = _clip(data.get("video"), 20)
    answer = _clip(data.get("answer"), 200)
    if kind in ("bug", "idea") and not message:
        return 400, {"error": "Write a few words so we know what to fix."}
    now = time.time()
    with _lock:
        recent = [t for t in _sent.get(visitor, []) if now - t < 3600]
        if len(recent) >= 10:
            return 429, {"error": "Thanks, we have plenty from you for this hour."}
        _sent[visitor] = recent + [now]

    title = f"[feedback] {KINDS[kind]}" + (f": {answer}" if answer else "") + \
            (f" ({video})" if video else "")
    lines = [f"**{KINDS[kind]}**", ""]
    if message:
        lines += ["> " + l for l in message.splitlines()] + [""]
    if video:
        lines.append(f"Video: https://youtu.be/{video}")
    if answer:
        lines.append(f"TitleSift said: {answer}" + (f" [{_clip(data.get('confidence'), 10)}]"
                                                    if data.get("confidence") else ""))
    lines += ["", "_Sent from the TitleSift feedback form._"]
    body = "\n".join(lines)
    print("FEEDBACK " + json.dumps({"title": title, "body": body}, ensure_ascii=False),
          file=sys.stderr, flush=True)

    # A thumbs-up is worth counting, not an email or an issue each, and the
    # free Formspree plan allows 50 messages a month.
    if kind != "right" and FORMSPREE_URL:
        req = urllib.request.Request(
            FORMSPREE_URL,
            data=json.dumps({"_subject": title[:150], "kind": KINDS[kind],
                             "message": message or "(no message)",
                             "video": f"https://youtu.be/{video}" if video else "",
                             "titlesift_said": answer}).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "User-Agent": "titlesift"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            print(f"FEEDBACK email not sent: {e}", file=sys.stderr, flush=True)

    token = os.environ.get("GITHUB_TOKEN")
    if token and kind != "right":
        req = urllib.request.Request(
            f"https://api.github.com/repos/{FEEDBACK_REPO}/issues",
            data=json.dumps({"title": title[:200], "body": body}).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "User-Agent": "titlesift"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:           # the log line above still has it
            print(f"FEEDBACK issue not created: {e}", file=sys.stderr, flush=True)
    return 200, {"ok": True}


# ------------------------------------------------------------- price votes
#
# "Would you pay?" is asked once per browser after a right answer. Counts
# live in memory; with GITHUB_TOKEN set they are also kept in the body of one
# issue ("[price-votes]") so a redeploy doesn't wipe them. /votes shows them.

PRICES = {"free": "Only if it's free", "0.99": "$0.99 a month",
          "1.99": "$1.99 a month", "2.99": "$2.99 a month"}
VOTES_TITLE = "[price-votes] Would you pay for TitleSift?"
_votes: dict[str, int] = {k: 0 for k in PRICES}
_voted: dict[str, float] = {}           # visitor -> time of their vote
_votes_issue: dict[str, int] = {}       # {"number": n} once found or made
_votes_dirty = threading.Event()


def _github(method: str, path: str, body: dict | None = None):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{FEEDBACK_REPO}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "titlesift"}, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode() or "null")


def _votes_body() -> str:
    total = sum(_votes.values())
    lines = ["Answers to \"Would you pay to keep TitleSift going?\" (one per visitor a day).", "",
             "| Answer | Votes |", "|---|---|"]
    lines += [f"| {PRICES[k]} | {_votes[k]} |" for k in PRICES]
    lines += [f"| **Total** | **{total}** |", "",
              "Kept up to date by the TitleSift server. Don't edit the line below.", "",
              "<!-- votes " + json.dumps(_votes) + " -->"]
    return "\n".join(lines)


def load_votes():
    """Pick up the counts saved before the last restart, if any."""
    try:
        issues = _github("GET", "/issues?state=all&per_page=100&sort=created&direction=asc") or []
        for it in issues:
            if it.get("title") == VOTES_TITLE:
                _votes_issue["number"] = it["number"]
                m = re.search(r"<!-- votes (\{.*?\}) -->", it.get("body") or "")
                if m:
                    saved = json.loads(m.group(1))
                    with _lock:
                        for k in PRICES:
                            _votes[k] = max(_votes[k], int(saved.get(k, 0)))
                break
    except Exception as e:
        print(f"PRICEVOTE could not load saved counts: {e}", file=sys.stderr, flush=True)


def _save_votes_forever():
    while True:
        _votes_dirty.wait()
        time.sleep(30)                   # one write for a burst of votes
        _votes_dirty.clear()
        try:
            with _lock:
                body = _votes_body()
            if "number" in _votes_issue:
                _github("PATCH", f"/issues/{_votes_issue['number']}", {"body": body})
            else:
                made = _github("POST", "/issues", {"title": VOTES_TITLE, "body": body})
                if made:
                    _votes_issue["number"] = made["number"]
        except Exception as e:
            print(f"PRICEVOTE counts not saved: {e}", file=sys.stderr, flush=True)


def vote(data: dict, visitor: str) -> tuple[int, dict]:
    choice = str(data.get("price") or "")
    if choice not in PRICES:
        return 400, {"error": "Pick one of the answers."}
    now = time.time()
    with _lock:
        if now - _voted.get(visitor, 0) < 86400:
            return 200, {"ok": True, "counted": False}
        _voted[visitor] = now
        _votes[choice] += 1
    print("PRICEVOTE " + json.dumps({"price": choice}), file=sys.stderr, flush=True)
    if os.environ.get("GITHUB_TOKEN"):
        _votes_dirty.set()
    return 200, {"ok": True, "counted": True}


def votes_page() -> bytes:
    with _lock:
        counts = dict(_votes)
    total = sum(counts.values()) or 1
    rows = "".join(
        f"<tr><td>{html.escape(PRICES[k])}</td><td>{counts[k]}</td><td>{round(100 * counts[k] / total)}%</td></tr>"
        for k in PRICES)
    kept = ("Saved on GitHub, so they survive updates." if os.environ.get("GITHUB_TOKEN")
            else "Not saved anywhere yet: an update or restart resets them. Add GITHUB_TOKEN on Render to keep them.")
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>TitleSift price votes</title><body style='font:16px/1.5 system-ui;margin:24px'>"
            f"<h1 style='font-size:22px'>Would you pay to keep TitleSift going?</h1>"
            f"<table cellpadding=6 style='border-collapse:collapse'><tr><th align=left>Answer</th><th>Votes</th><th></th></tr>"
            f"{rows}<tr><td><b>Total</b></td><td><b>{sum(counts.values())}</b></td><td></td></tr></table>"
            f"<p style='color:#666'>{kept}</p>").encode()


def config() -> dict:
    kofi = os.environ.get("KOFI_URL", "").strip()
    return {"kofi": kofi if re.match(r"^https://ko-fi\.com/[A-Za-z0-9_]+/?$", kofi) else ""}


# ------------------------------------------------------------- names from visitors
#
# When the comments don't name a show, the next best source is the person
# who just searched for it and later found out. "I know the name" saves what
# they type against the video, and "Yes, this was right" counts as agreeing
# with the answer shown. A name reaches other visitors only once AniList,
# Wikipedia or MangaDex knows it, or two different people gave it, so one
# person can't put made-up text on the page.
#
# Names live in memory. With GITHUB_TOKEN set they are also kept in
# community.json on the titlesift-data branch of FEEDBACK_REPO (Render only
# redeploys from main, so these writes never trigger a deploy). Only the
# video id, the name, how many people gave it and when are saved: nothing
# about who gave it.

NAMES_BRANCH = os.environ.get("NAMES_BRANCH", "titlesift-data")
NAMES_FILE = "community.json"
NAMES_KEEP = 20000                      # videos; the oldest drop off after that
_names: dict[str, dict[str, dict]] = {}  # video -> fold(name) -> entry
_gave: dict[tuple[str, str], set] = {}  # (visitor, video) -> names they gave
_name_posts: dict[str, list[float]] = {}
_names_dirty = threading.Event()
_URLISH = re.compile(r"https?:|www\.|\.(com|net|org|io|gg|ly|me)\b", re.I)


def _video_meta(vid: str) -> dict:
    """What confirm() uses to tell a manhwa from a film of the same name."""
    hit = _answers.get(vid)
    title = ((hit[1].get("video") or {}).get("title") or "") if hit else ""
    return {"title": title, "tags": [], "description": ""}


def _shown(e: dict) -> bool:
    return e["count"] >= 2 or (e["count"] >= 1 and bool(e.get("db")))


def with_names(result: dict) -> dict:
    """The looked-up answer plus what TitleSift visitors have said about it."""
    vid = (result.get("video") or {}).get("id")
    with _lock:
        entries = [dict(e) for e in (_names.get(vid) or {}).values()]
    if not entries:
        return result
    entries.sort(key=lambda e: (-e["count"], e["first"]))
    r = dict(result)
    a = r.get("answer")
    if a:
        known = {fold(a["title"])} | {fold(n) for n in a.get("also_called") or []}
        if a.get("database"):
            known.add(fold(a["database"].get("name") or ""))
        match = next((e for e in entries if fold(e["name"]) in known), None)
        if match:
            r["answer"] = dict(a, community=match["count"])
        rest = [e for e in entries if e is not match and _shown(e)]
    else:
        shown = [e for e in entries if _shown(e)]
        if not shown:
            return result
        top, rest = shown[0], shown[1:]
        n, db = top["count"], top.get("db")
        people = "1 TitleSift user" if n == 1 else f"{n} TitleSift users"
        text = f"Named by {people}, not in the comments." + (
            " I" + _db_note(db)[1:] + "." if db else "")
        conf = "high" if n >= 2 and db else "medium"
        r["answer"] = dict(_answer_dict(top["name"], conf, text, db, {"title": (r.get("video") or {}).get("title", "")},
                                        [], []), community=n, from_community=True)
    if rest:
        r["community_others"] = [{"title": e["name"], "count": e["count"]} for e in rest[:3]]
    return r


def add_name(data: dict, visitor: str) -> tuple[int, dict]:
    vid = str(data.get("video") or "")
    if not re.fullmatch(r"[\w-]{11}", vid):
        return 400, {"error": "Look up a video first."}
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip().strip("\"'“”")
    if len(name) < 2:
        return 400, {"error": "Type the name first."}
    if len(name) > 120 or _URLISH.search(name):
        return 400, {"error": "Just the name, please (no links)."}
    key = fold(name)
    if not key:
        return 400, {"error": "Type the name first."}
    now = time.time()
    with _lock:
        recent = [t for t in _name_posts.get(visitor, []) if now - t < 3600]
        if len(recent) >= 20:
            return 429, {"error": "Thanks, that's plenty for this hour."}
        _name_posts[visitor] = recent + [now]
        mine = _gave.setdefault((visitor, vid), set())
        if key in mine:
            e = (_names.get(vid) or {}).get(key) or {"count": 0}
            return 200, {"ok": True, "counted": False, "count": e["count"], "shown": _shown(e)}
        if len(mine) >= 3:
            return 200, {"ok": True, "counted": False, "count": 0, "shown": False}
        existing = (_names.get(vid) or {}).get(key)
        hit = _answers.get(vid)
        cached = (hit[1].get("answer") or {}) if hit else {}

    db = existing.get("db") if existing else None
    if not existing:
        if cached and fold(cached.get("title") or "") == key and cached.get("database"):
            db = cached["database"]          # the answer we already checked
        else:
            found = confirm(name, _video_meta(vid), _caches)
            db = found if found and found.get("status") == "confirmed" else None

    with _lock:
        per = _names.setdefault(vid, {})
        e = per.get(key)
        if e is None:
            e = per[key] = {"name": name, "count": 0, "db": db, "first": int(now)}
        e["count"] += 1
        e["last"] = int(now)
        mine.add(key)
        if len(_names) > NAMES_KEEP:
            for old in sorted(_names, key=lambda v: max(x.get("last", 0) for x in _names[v].values()))[:1000]:
                _names.pop(old, None)
        out = {"ok": True, "counted": True, "count": e["count"], "shown": _shown(e)}
    print("COMMUNITY " + json.dumps({"video": vid, "name": name, "count": e["count"],
                                     "db": bool(db), "yes": bool(data.get("confirm"))}, ensure_ascii=False),
          file=sys.stderr, flush=True)
    if os.environ.get("GITHUB_TOKEN"):
        _names_dirty.set()
    return 200, out


def _names_file() -> tuple[dict, str | None]:
    """The saved file and its version, or nothing when there isn't one yet."""
    try:
        got = _github("GET", f"/contents/{NAMES_FILE}?ref={NAMES_BRANCH}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}, None
        raise
    if not got:
        return {}, None
    return json.loads(base64.b64decode(got["content"]).decode() or "{}"), got["sha"]


def load_names():
    try:
        saved, _ = _names_file()
        with _lock:
            for vid, per in (saved.get("videos") or {}).items():
                for key, e in per.items():
                    cur = _names.setdefault(vid, {}).get(key)
                    if cur is None or e.get("count", 0) > cur["count"]:
                        _names[vid][key] = e
    except Exception as e:
        print(f"COMMUNITY could not load saved names: {e}", file=sys.stderr, flush=True)


def _ensure_branch():
    try:
        _github("GET", f"/git/ref/heads/{NAMES_BRANCH}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        base = _github("GET", "/git/ref/heads/main")
        _github("POST", "/git/refs", {"ref": f"refs/heads/{NAMES_BRANCH}", "sha": base["object"]["sha"]})


def _save_names_forever():
    while True:
        _names_dirty.wait()
        time.sleep(30)                   # one write for a burst of names
        _names_dirty.clear()
        try:
            _ensure_branch()
            _, sha = _names_file()
            with _lock:
                doc = {"about": "Names TitleSift visitors gave for videos. Written by the server.",
                       "videos": _names}
                text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True)
            body = {"message": "Save names from TitleSift visitors", "branch": NAMES_BRANCH,
                    "content": base64.b64encode(text.encode()).decode()}
            if sha:
                body["sha"] = sha
            _github("PUT", f"/contents/{NAMES_FILE}", body)
        except Exception as e:
            print(f"COMMUNITY names not saved: {e}", file=sys.stderr, flush=True)
            _names_dirty.set()           # try again after the next pause


def names_page() -> bytes:
    with _lock:
        rows = [(max(e.get("last", 0), e["first"]), vid, dict(e)) for vid, per in _names.items() for e in per.values()]
    rows.sort(key=lambda x: x[0], reverse=True)
    body = "".join(
        f"<tr><td>{time.strftime('%d %b %H:%M', time.gmtime(t))}</td>"
        f"<td><a href='https://www.youtube.com/watch?v={vid}'>{vid}</a></td>"
        f"<td>{html.escape(e['name'])}</td><td>{e['count']}</td>"
        f"<td>{html.escape((e.get('db') or {}).get('source', '')) or '–'}</td>"
        f"<td>{'Yes' if _shown(e) else 'Not yet'}</td></tr>"
        for t, vid, e in rows[:300])
    kept = ("Saved on GitHub, so they survive updates." if os.environ.get("GITHUB_TOKEN")
            else "Not saved anywhere yet: an update or restart resets them. Add GITHUB_TOKEN on Render to keep them.")
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<meta name=robots content=noindex><title>TitleSift names from visitors</title>"
            f"<body style='font:15px/1.5 system-ui;margin:16px'>"
            f"<h1 style='font-size:22px'>Names from visitors</h1>"
            f"<p style='color:#666'>Newest first (times in UTC). A name is shown to others once a database knows it "
            f"or 2 people gave it. {kept}</p><div style='overflow-x:auto'>"
            f"<table cellpadding=6 style='border-collapse:collapse'><tr><th align=left>When</th><th align=left>Video</th>"
            f"<th align=left>Name</th><th>People</th><th>Found on</th><th>Shown</th></tr>{body}</table></div>").encode()


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

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/api/feedback", "/api/vote", "/api/name"):
            return self._send(404, b"Not found", "text/plain")
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 8000)
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self._send(400, b'{"error": "Bad request"}', "application/json")
        visitor = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        code, out = {"/api/vote": vote, "/api/name": add_name}.get(path, feedback)(data, visitor)
        return self._send(code, json.dumps(out).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/find":
            q = urllib.parse.parse_qs(u.query).get("url", [""])[0]
            visitor = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
            code, data = lookup(q, visitor)
            return self._send(code, json.dumps(data, ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")
        if u.path == "/api/config":
            return self._send(200, json.dumps(config()).encode(), "application/json; charset=utf-8")
        if u.path == "/names":
            return self._send(200, names_page(), "text/html; charset=utf-8")
        if u.path == "/votes":
            return self._send(200, votes_page(), "text/html; charset=utf-8")
        if u.path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        if name in ("privacy", "terms"):
            name += ".html"
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
    load_votes()
    threading.Thread(target=_save_votes_forever, daemon=True).start()
    load_names()
    threading.Thread(target=_save_names_forever, daemon=True).start()
    port = int(os.environ.get("PORT", "7860"))
    print(f"Listening on http://0.0.0.0:{port}", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
