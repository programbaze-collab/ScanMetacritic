"""Летсплей: ролик годится, только если в заголовке есть слова названия игры.

Среди подходящих сначала let's play, затем gameplay, затем playthrough,
и только потом большее число просмотров.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urlencode, urlparse, urlunparse, parse_qsl

_STOPWORDS = {"the", "a", "an", "of", "and", "for", "to", "in", "on", "at", "with", "from"}
_KIND_ORDER = ("let's play", "gameplay", "playthrough")


def title_words(title: str) -> list[str]:
    folded = _fold(title)
    words = []
    for token in re.findall(r"[^\W_]+", folded, flags=re.UNICODE):
        if token in _STOPWORDS:
            continue
        if token.isdigit() or len(token) >= 3:
            words.append(token)
    return words


def choose_letsplay(game_title: str, videos: list[dict]) -> dict | None:
    words = title_words(game_title)
    if not words:
        return None
    accepted = [video for video in videos if _title_has_words(video.get("title") or "", words)]
    if not accepted:
        return None
    accepted.sort(key=lambda video: (_kind_rank(video.get("title") or ""), -int(video.get("views") or 0), video.get("title") or ""))
    best = accepted[0]
    kind = _kind_name(best.get("title") or "") or "other"
    return {
        "video_id": best["video_id"],
        "title": best["title"],
        "url": best.get("url") or f"https://www.youtube.com/watch?v={best['video_id']}",
        "views": int(best.get("views") or 0),
        "kind": kind,
        "words": words,
    }


def search_letsplay(game_title: str, fetch) -> dict | None:
    found: dict[str, dict] = {}
    for query in (
        f"{game_title} let's play",
        f"{game_title} gameplay",
        f"{game_title} playthrough",
    ):
        for video in parse_search_html(fetch(_search_url(query))):
            previous = found.get(video["video_id"])
            if previous is None or video["views"] > previous["views"]:
                found[video["video_id"]] = video
    return choose_letsplay(game_title, list(found.values()))


def parse_search_html(html: str) -> list[dict]:
    if "ytInitialData" not in html:
        raise RuntimeError("YouTube не отдал список роликов")
    start = re.search(r"ytInitialData\s*=\s*", html)
    if start is None:
        raise RuntimeError("YouTube не отдал список роликов")
    data, _ = json.JSONDecoder().raw_decode(html[start.end():])
    videos = []
    for renderer in _video_renderers(data):
        video_id = renderer.get("videoId")
        title = _node_text(renderer.get("title"))
        if not isinstance(video_id, str) or video_id == "" or title == "":
            continue
        videos.append(
            {
                "video_id": video_id,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "views": _views(_node_text(renderer.get("viewCountText"))),
            }
        )
    return videos


def load_transcript(video_id: str, fetch) -> str:
    html = fetch(f"https://www.youtube.com/watch?v={video_id}")
    track = _caption_url(html)
    if track == "":
        return ""
    payload = fetch(_json3_url(track))
    return _transcript_text(payload)


def _search_url(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(query)


def _fold(text: str) -> str:
    return text.casefold().replace("'", "").replace("’", "").replace("`", "")


def _title_has_words(video_title: str, words: list[str]) -> bool:
    haystack = _fold(video_title)
    return all(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", haystack) for word in words)


def _kind_rank(title: str) -> int:
    return _KIND_ORDER.index(_kind_name(title)) if _kind_name(title) in _KIND_ORDER else len(_KIND_ORDER)


def _kind_name(title: str) -> str:
    folded = _fold(title)
    if re.search(r"lets\s*play", folded):
        return "let's play"
    if "gameplay" in folded:
        return "gameplay"
    if "playthrough" in folded:
        return "playthrough"
    return ""


def _node_text(node: object) -> str:
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("simpleText"), str):
        return node["simpleText"]
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(part.get("text", "") for part in runs if isinstance(part, dict))
    return ""


def _views(text: str) -> int:
    match = re.search(r"([\d]+(?:[.,]\d+)?)\s*([kmb])?", text.casefold().replace(",", ""))
    if match is None:
        return 0
    number = float(match.group(1).replace(",", "."))
    factor = {None: 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[match.group(2)]
    return int(number * factor)


def _video_renderers(node: object):
    if isinstance(node, dict):
        renderer = node.get("videoRenderer")
        if isinstance(renderer, dict):
            yield renderer
        for value in node.values():
            yield from _video_renderers(value)
    elif isinstance(node, list):
        for value in node:
            yield from _video_renderers(value)


def _caption_url(html: str) -> str:
    match = re.search(r"ytInitialPlayerResponse\s*=\s*", html)
    if match is None:
        return ""
    try:
        data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except json.JSONDecodeError:
        return ""
    tracks = (
        data.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    if not isinstance(tracks, list) or not tracks:
        return ""
    chosen = tracks[0]
    for track in tracks:
        language = track.get("languageCode")
        if language in ("en", "ru"):
            chosen = track
            if language == "ru":
                break
    base = chosen.get("baseUrl") if isinstance(chosen, dict) else ""
    return base if isinstance(base, str) else ""


def _json3_url(url: str) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["fmt"] = "json3"
    return urlunparse(parts._replace(query=urlencode(query)))


def _transcript_text(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    pieces = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        for segment in event.get("segs") or []:
            if isinstance(segment, dict) and isinstance(segment.get("utf8"), str):
                pieces.append(segment["utf8"])
    return "".join(pieces).strip()
