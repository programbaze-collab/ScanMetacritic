"""Списки игр: первый заход дня и страницы раздела New."""

from __future__ import annotations

from game_page import load_nuxt_root

NEW_RELEASES_URL = "https://www.metacritic.com/game/"
NEW_PAGE_ROOT = "https://www.metacritic.com/browse/game/all/all/all-time/new/"


def new_page_url(page: int) -> str:
    if page <= 1:
        return NEW_PAGE_ROOT
    return f"{NEW_PAGE_ROOT}?page={page}"


def game_url(path: str) -> str:
    return "https://www.metacritic.com" + path


def parse_listing(html: str, source: str) -> list[str]:
    root = load_nuxt_root(html)
    data = root.get("data") if isinstance(root, dict) else None
    if not isinstance(data, dict):
        return []
    if source == "new_releases":
        return _paths(_new_release_items(data))
    if source == "new":
        return _paths(_browse_items(data))
    raise ValueError(f"Неизвестный источник списка: {source}")


def _new_release_items(data: dict) -> list:
    for value in data.values():
        if not isinstance(value, dict):
            continue
        for component in value.get("components") or []:
            if not isinstance(component, dict):
                continue
            meta = component.get("meta") or {}
            if isinstance(meta, dict) and meta.get("componentName") == "new-releases-carousel":
                data_block = component.get("data")
                items = data_block.get("items") if isinstance(data_block, dict) else None
                return items if isinstance(items, list) else []
    return []


def _browse_items(data: dict) -> list:
    for key, value in data.items():
        if isinstance(key, str) and key.startswith("browse-game-") and isinstance(value, dict):
            items = value.get("items")
            return items if isinstance(items, list) else []
    return []


def _paths(items: list) -> list[str]:
    seen: set[str] = set()
    paths = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "game-title":
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or slug.strip() == "":
            continue
        path = f"/game/{slug}/"
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths
