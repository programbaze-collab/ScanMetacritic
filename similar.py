"""Похожие игры по тексту уже сохранённых карточек.

Берутся название, разработчик и описание. Чужие подборки Metacritic не читаются.
Если общих слов мало, всё равно возвращаются ближайшие карточки, а не пустой список.
"""

from __future__ import annotations

import re

LIMIT = 4
_FIELD_WEIGHT = {"title": 3, "developer": 2, "description": 1}
_STOPWORDS = {
    "the", "a", "an", "of", "and", "for", "to", "in", "on", "at", "with", "from",
    "you", "your", "but", "that", "this", "are", "has", "have", "will", "only",
    "out", "its", "into", "can", "not", "one", "up", "by", "as", "or", "be", "is",
    "it", "we", "our", "their", "they", "them", "his", "her", "was", "were", "been",
    "than", "then", "also", "other", "about", "over", "after", "before", "all",
    "any", "who", "what", "when", "which", "while", "just", "more", "most", "some",
    "such", "no", "nor", "so", "if", "do", "did", "does", "own", "may", "might",
    "must", "too", "very", "via", "per", "across", "among", "between", "within",
    "without", "upon", "off", "again", "further", "once",
}


def similar_games(games: list[dict], path: str, limit: int = LIMIT) -> list[dict]:
    profiles = {game["path"]: _profile(game) for game in games if game.get("path")}
    source = profiles.get(path)
    if source is None:
        return []
    ranked = []
    for other_path, other in profiles.items():
        if other_path == path:
            continue
        words, score = _shared(source, other)
        ranked.append(
            {
                "path": other_path,
                "title": other["title"],
                "words": words,
                "score": score,
                "closeness": _jaccard(source["trigrams"], other["trigrams"]),
            }
        )
    ranked.sort(key=lambda item: (-item["score"], -item["closeness"], item["title"], item["path"]))
    return [
        {"path": item["path"], "title": item["title"], "words": item["words"]}
        for item in ranked[:limit]
    ]


def catalog_slug(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    return parts[-1] if parts else path


def _profile(game: dict) -> dict:
    developer = game.get("developer") or []
    if isinstance(developer, list):
        developer_text = " ".join(str(name) for name in developer)
    else:
        developer_text = str(developer)
    title = game.get("title") or ""
    description = game.get("description") or ""
    fields = {
        "title": _tokens(title),
        "developer": _tokens(developer_text),
        "description": _tokens(description),
    }
    return {
        "title": title,
        "fields": fields,
        "surfaces": _surfaces(f"{title} {developer_text} {description}"),
        "trigrams": _trigrams(f"{title} {developer_text} {description}"),
    }


def _shared(left: dict, right: dict) -> tuple[list[dict], int]:
    words = []
    score = 0
    vocabulary = set().union(*left["fields"].values())
    for folded in vocabulary:
        left_fields = [name for name, bag in left["fields"].items() if folded in bag]
        right_fields = [name for name, bag in right["fields"].items() if folded in bag]
        if not left_fields or not right_fields:
            continue
        weight = _FIELD_WEIGHT[max(left_fields, key=_FIELD_WEIGHT.get)]
        weight += _FIELD_WEIGHT[max(right_fields, key=_FIELD_WEIGHT.get)]
        score += weight
        words.append(
            {
                "word": left["surfaces"].get(folded) or right["surfaces"].get(folded) or folded,
                "left": left_fields,
                "right": right_fields,
                "weight": weight,
            }
        )
    words.sort(key=lambda item: (-item["weight"], item["word"].casefold()))
    return words, score


def _tokens(text: str) -> set[str]:
    return set(_surfaces(text))


def _surfaces(text: str) -> dict[str, str]:
    found = {}
    for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE):
        folded = token.casefold().replace("'", "").replace("’", "")
        if folded in _STOPWORDS:
            continue
        if not folded.isdigit() and len(folded) < 3:
            continue
        if folded.isdigit() and len(folded) < 2:
            continue
        found.setdefault(folded, token)
    return found


def _trigrams(text: str) -> set[str]:
    folded = re.sub(r"[^0-9a-zа-яё]+", " ", text.casefold().replace("ё", "е"))
    compact = folded.strip()
    if len(compact) < 3:
        return set()
    return {compact[index:index + 3] for index in range(len(compact) - 2)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)
