"""Перевод текстов карточки на русский. Оригинал в базе не меняется."""

from __future__ import annotations

import os
import re

_MARKER = re.compile(r"\[[^\]\n]+\]")
_BREAKS = re.compile(r"(\n\s*\n)")
_CHUNK = 4000


def to_russian(text: str, *, block=None) -> str:
    if not isinstance(text, str) or text.strip() == "":
        return ""
    if _already_russian(text):
        return text
    translate = block or _translate_block
    parts = []
    for piece in _BREAKS.split(text):
        if piece.strip() == "":
            parts.append(piece)
            continue
        masked, tokens = _mask(piece)
        translated = translate(masked)
        if not isinstance(translated, str) or translated.strip() == "":
            raise RuntimeError("Перевод пустой")
        parts.append(_unmask(translated, tokens))
    return "".join(parts)


def _already_russian(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return True
    cyrillic = sum(1 for char in letters if "\u0400" <= char <= "\u04ff")
    return cyrillic / len(letters) >= 0.5


def _mask(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def replace(match: re.Match) -> str:
        tokens.append(match.group(0))
        return f"ZXQ{len(tokens) - 1}ZQ"

    return _MARKER.sub(replace, text), tokens


def _unmask(text: str, tokens: list[str]) -> str:
    for index, token in enumerate(tokens):
        text = text.replace(f"ZXQ{index}ZQ", token)
    return text


def _translate_block(text: str) -> str:
    key = os.environ.get("SUMMARY_API_KEY", "").strip()
    url = os.environ.get("SUMMARY_API_URL", "").strip()
    if key and url:
        try:
            return _model(text, key, url)
        except Exception:
            pass
    return _google(text)


def _model(text: str, key: str, url: str) -> str:
    import httpx

    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.environ.get("SUMMARY_MODEL", "gpt-4o-mini"),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Переведи текст на русский язык. Не пересказывай и не сокращай. "
                        "Сохрани имена, названия, числа и оценки. "
                        "Последовательности ZXQ и ZQ оставь без изменений. "
                        "Не добавляй фактов. Верни только перевод."
                    ),
                },
                {"role": "user", "content": text},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str) or content.strip() == "":
        raise RuntimeError("Модель не вернула перевод")
    return content


def _google(text: str) -> str:
    import httpx

    chunks = _chunks(text)
    translated = []
    with httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for chunk in chunks:
            response = client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "ru", "dt": "t", "q": chunk},
            )
            response.raise_for_status()
            payload = response.json()
            translated.append(
                "".join(item[0] for item in payload[0] if item and item[0])
            )
    return "".join(translated)


def _chunks(text: str) -> list[str]:
    if len(text) <= _CHUNK:
        return [text]
    pieces = []
    rest = text
    while len(rest) > _CHUNK:
        cut = rest.rfind(" ", 0, _CHUNK)
        if cut < _CHUNK // 2:
            cut = _CHUNK
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces
