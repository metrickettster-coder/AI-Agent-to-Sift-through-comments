"""Comment sources.

`YouTubeAPISource` uses the official YouTube Data API v3 (commentThreads.list),
which is the only terms-compliant way to read comments at scale and the right
foundation for a commercial product. It needs an API key:
  https://console.cloud.google.com -> enable "YouTube Data API v3" -> create key
Set it as YOUTUBE_API_KEY.

`JSONFileSource` reads a saved comment dump, for offline development.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

API_ROOT = "https://www.googleapis.com/youtube/v3"

_VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")


def video_id(url_or_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id):
        return url_or_id
    m = _VIDEO_ID.search(url_or_id)
    if not m:
        raise ValueError(f"could not read a video id out of {url_or_id!r}")
    return m.group(1)


class YouTubeAPISource:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "No YouTube API key. Set YOUTUBE_API_KEY, or pass api_key=."
            )

    def _get(self, path: str, **params) -> dict:
        params["key"] = self.api_key
        url = f"{API_ROOT}/{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def video_meta(self, vid: str) -> dict:
        data = self._get("videos", part="snippet,statistics", id=vid)
        items = data.get("items") or []
        if not items:
            raise RuntimeError(f"video {vid} not found or not public")
        s = items[0]["snippet"]
        return {
            "id": vid,
            "title": s.get("title", ""),
            "channel": s.get("channelTitle", ""),
            # description and tags carry the answer often enough to be worth
            # always fetching - they cost nothing extra on this call.
            "description": s.get("description", ""),
            "tags": s.get("tags", []),
            "comment_count": int(items[0].get("statistics", {}).get("commentCount", 0)),
        }

    def comments(self, vid: str, limit: int = 500, include_replies: bool = True):
        """Yield comment dicts: {id, author, text, likes}."""
        got, page = 0, None
        while got < limit:
            params = dict(
                part="snippet,replies",
                videoId=vid,
                maxResults=min(100, limit - got),
                textFormat="plainText",
                order="relevance",
            )
            if page:
                params["pageToken"] = page
            data = self._get("commentThreads", **params)

            for item in data.get("items", []):
                top = item["snippet"]["topLevelComment"]
                s = top["snippet"]
                yield {
                    "id": top["id"],
                    "author": s.get("authorDisplayName", ""),
                    "text": s.get("textDisplay", ""),
                    "likes": s.get("likeCount", 0),
                }
                got += 1
                if include_replies:
                    for rep in item.get("replies", {}).get("comments", []):
                        rs = rep["snippet"]
                        yield {
                            "id": rep["id"],
                            "author": rs.get("authorDisplayName", ""),
                            "text": rs.get("textDisplay", ""),
                            "likes": rs.get("likeCount", 0),
                        }
                        got += 1
            page = data.get("nextPageToken")
            if not page:
                break


class JSONFileSource:
    """Reads [{id, author, text}, ...] from a JSON file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def comments(self, *_args, **_kw):
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        for i, row in enumerate(rows):
            if isinstance(row, str):
                yield {"id": f"c{i}", "author": f"user{i}", "text": row}
            else:
                row.setdefault("id", f"c{i}")
                row.setdefault("author", f"user{i}")
                yield row
