"""Разбор одной сохранённой страницы игры Metacritic.

Берёт поля из __NUXT_DATA__ и не подменяет текст страницы.
"""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlparse

_NUXT_RE = re.compile(
    r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>',
    re.S,
)
_UNWRAP = {
    "ShallowReactive",
    "Reactive",
    "Ref",
    "ShallowRef",
    "EmptyRef",
    "EmptyShallowRef",
    "NuxtError",
}
_QUOTE_BUCKETS = ("default", "positive", "neutral", "negative")


def display_platform(name: str, slug: str = "") -> str:
    """iOS (iPhone/iPad) и slug ios-iphoneipad показываются как iOS."""
    folded = {
        re.sub(r"[^a-z0-9]+", "", (value or "").lower())
        for value in (name, slug)
    }
    if "iosiphoneipad" in folded:
        return "iOS"
    text = (name or "").strip()
    return text or (slug or "").strip()


def load_nuxt_root(html: str):
    return _hydrate(_nuxt_payload(html))


def parse_game_page(html: str) -> dict:
    payload = _nuxt_payload(html)
    root = _hydrate(payload)
    page = _game_page(root)
    product = _component_data(page, "product").get("item") or {}
    user_summary = _component_data(page, "user-score-summary").get("item") or {}
    critic_item = _component_data(page, "critic-reviews").get("item") or {}
    user_item = _component_data(page, "user-reviews").get("item") or {}

    return {
        "title": _text(product.get("title")),
        "cover": _cover_url(product.get("images")),
        "developer": _developers(product.get("production")),
        "description": _text(product.get("description")),
        "video": _video_url(product.get("video")),
        "platforms": _platforms(product, user_summary),
        "critic_quotes": _quotes(critic_item, "publicationName"),
        "user_quotes": _quotes(user_item, "author"),
    }


def _nuxt_payload(html: str) -> list:
    match = _NUXT_RE.search(html)
    if match is None:
        raise ValueError("На странице нет __NUXT_DATA__")
    payload = json.loads(unescape(match.group(1)))
    if not isinstance(payload, list):
        raise ValueError("__NUXT_DATA__ не является массивом")
    return payload


def _hydrate(payload: list):
    memo: dict[int, object] = {}

    def hydrate(index: int):
        if index in memo:
            return memo[index]
        if index < 0 or index >= len(payload):
            return None
        value = payload[index]
        if isinstance(value, list) and value and isinstance(value[0], str):
            tag = value[0]
            if tag == "Set":
                memo[index] = []
                return memo[index]
            if tag in _UNWRAP and len(value) > 1 and isinstance(value[1], int):
                memo[index] = None
                inner = hydrate(value[1])
                memo[index] = inner
                return inner
            boxed: dict = {"__tag": tag, "args": []}
            memo[index] = boxed
            boxed["args"] = [
                hydrate(item) if isinstance(item, int) else item for item in value[1:]
            ]
            return boxed
        if isinstance(value, list):
            items: list = []
            memo[index] = items
            for item in value:
                items.append(hydrate(item) if isinstance(item, int) else item)
            return items
        if isinstance(value, dict):
            obj: dict = {}
            memo[index] = obj
            for key, item in value.items():
                obj[key] = hydrate(item) if isinstance(item, int) else item
            return obj
        memo[index] = value
        return value

    return hydrate(0)


def _game_page(root: dict) -> dict:
    data = root.get("data") if isinstance(root, dict) else None
    if not isinstance(data, dict):
        raise ValueError("В данных страницы нет блока игры")
    path = root.get("path") if isinstance(root.get("path"), str) else ""
    slug = path.strip("/").split("/")[-1]
    direct = data.get(f"loadPage:games:{slug}:")
    if isinstance(direct, dict):
        return direct
    for key, value in data.items():
        if isinstance(key, str) and key.startswith("loadPage:games:") and isinstance(value, dict):
            return value
    raise ValueError("В данных страницы нет блока игры")


def _component_data(page: dict, name: str) -> dict:
    for component in page.get("components") or []:
        if not isinstance(component, dict):
            continue
        meta = component.get("meta") or {}
        if isinstance(meta, dict) and meta.get("componentName") == name:
            data = component.get("data")
            return data if isinstance(data, dict) else {}
    return {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _score(value: object):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _cover_url(images: object) -> str:
    if not isinstance(images, list):
        return ""
    for image in images:
        if not isinstance(image, dict) or image.get("typeName") != "cardImage":
            continue
        direct = image.get("imageUrl")
        if isinstance(direct, str) and direct.startswith(("http://", "https://")):
            return direct
        bucket = image.get("bucketType")
        bucket_path = image.get("bucketPath")
        if isinstance(bucket, str) and isinstance(bucket_path, str) and bucket and bucket_path:
            return f"https://www.metacritic.com/a/img/{bucket}{bucket_path}"
    return ""


def _developers(production: object) -> list[str]:
    if not isinstance(production, dict):
        return []
    names = []
    for company in production.get("companies") or []:
        if not isinstance(company, dict):
            continue
        role = company.get("typeName")
        name = company.get("name")
        if isinstance(role, str) and role.casefold() == "developer" and isinstance(name, str) and name:
            names.append(name)
    return names


def _video_url(video: object) -> str:
    if not isinstance(video, dict):
        return ""
    url = video.get("embedUrl")
    return url if isinstance(url, str) else ""


def _userscore_slug(summary: dict, product: dict) -> str:
    url = summary.get("url")
    if isinstance(url, str) and url:
        values = parse_qs(urlparse(url).query).get("platform") or []
        if values and isinstance(values[0], str):
            return values[0]
    platform = product.get("platform")
    platforms = product.get("platforms") or []
    if isinstance(platform, str):
        for item in platforms:
            if isinstance(item, dict) and item.get("name") == platform and isinstance(item.get("slug"), str):
                return item["slug"]
    return ""


def _platforms(product: dict, user_summary: dict) -> list[dict]:
    userscore = _score(user_summary.get("score")) if user_summary else None
    slug = _userscore_slug(user_summary, product) if user_summary else ""
    rows = []
    for item in product.get("platforms") or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("criticScoreSummary") if isinstance(item.get("criticScoreSummary"), dict) else {}
        item_slug = item.get("slug") if isinstance(item.get("slug"), str) else ""
        rows.append(
            {
                "name": display_platform(_text(item.get("name")), item_slug),
                "metascore": _score(summary.get("score")),
                "userscore": userscore if slug and item_slug == slug else None,
            }
        )
    return rows


def _review_platform(value: object) -> str:
    if isinstance(value, dict):
        name = value.get("name") if isinstance(value.get("name"), str) else ""
        slug = value.get("slug") if isinstance(value.get("slug"), str) else ""
        return display_platform(name, slug)
    if isinstance(value, str):
        return display_platform(value)
    return ""


def _quotes(item: object, source_key: str) -> list[dict]:
    if not isinstance(item, dict):
        return []
    seen: set[str] = set()
    quotes = []
    for bucket in _QUOTE_BUCKETS:
        reviews = item.get(bucket) or []
        if not isinstance(reviews, list):
            continue
        for review in reviews:
            if not isinstance(review, dict):
                continue
            quote = review.get("quote")
            if not isinstance(quote, str) or quote == "":
                continue
            if quote in seen:
                continue
            seen.add(quote)
            source = review.get(source_key)
            quotes.append(
                {
                    "quote": quote,
                    "score": _score(review.get("score")),
                    "source": source if isinstance(source, str) else "",
                    "platform": _review_platform(review.get("platform")),
                }
            )
    return quotes
