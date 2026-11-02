"""Пересказ отзывов: внешняя модель при наличии ключа, иначе цитаты страницы."""

from __future__ import annotations

import os

QUOTE_LIMIT = 3


def summarize_quotes(quotes: list[dict], kind: str = "отзывов") -> str:
    texts = [item["quote"] for item in quotes if isinstance(item.get("quote"), str) and item["quote"] != ""]
    key = os.environ.get("SUMMARY_API_KEY", "").strip()
    if not key:
        return "\n\n".join(texts[:QUOTE_LIMIT])
    if not texts:
        return ""
    return _model_summary(
        texts,
        key,
        f"Кратко перескажи отзывы {kind} только по приведённым цитатам. "
        "Не добавляй оценок, имён и фактов, которых нет в цитатах.",
    )


def summarize_transcript(text: str) -> str:
    if not isinstance(text, str) or text.strip() == "":
        return ""
    key = os.environ.get("SUMMARY_API_KEY", "").strip()
    if not key:
        return ""
    return _model_summary(
        [text],
        key,
        "Сделай короткий вывод по расшифровке летсплея. "
        "Не добавляй фактов, которых нет в тексте.",
    )


def _model_summary(texts: list[str], key: str, instruction: str) -> str:
    url = os.environ.get("SUMMARY_API_URL", "").strip()
    if not url:
        raise RuntimeError("Задан SUMMARY_API_KEY, но не задан SUMMARY_API_URL")
    import httpx

    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.environ.get("SUMMARY_MODEL", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": "\n\n".join(texts)},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise RuntimeError("Модель не вернула текст пересказа")
    return content
