"""Каталог игр: список, карточка и страница обхода."""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from crawl import DEFAULT_DB, http_fetch, run_once
from letsplay import search_letsplay
from listing import game_url
from similar import catalog_slug, similar_games
from store import (
    STALE_LOCK,
    ZONE,
    connect,
    crawl_snapshot,
    load_catalog,
    load_letsplay,
    load_translation,
    local_day,
    save_translation,
)
from translate import to_russian

ROOT = Path(__file__).resolve().parent
SERVICE = "Каталог игр с Metacritic"
SORTS = (
    ("metascore_desc", "Оценка критиков по убыванию"),
    ("metascore_asc", "Оценка критиков по возрастанию"),
    ("userscore_desc", "Оценка игроков по убыванию"),
    ("userscore_asc", "Оценка игроков по возрастанию"),
)
_STATUS = {
    "ok": "Готово",
    "partial": "Готово, часть шагов не удалась",
    "error": "Ошибка",
    "empty": "Пустой список",
    "running": "Идёт обход",
}
_KIND = {
    "let's play": "летсплей",
    "gameplay": "геймплей",
    "playthrough": "прохождение",
    "other": "ролик",
}

templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.auto_reload = True
templates.env.globals["service"] = SERVICE
templates.env.globals["slug"] = catalog_slug
templates.env.filters["score"] = lambda value: _format_score(value)


def create_app(db_path: Path | None = None, start_crawl=None, translate=None) -> FastAPI:
    path = db_path or Path(os.environ.get("CATALOG_DB", str(DEFAULT_DB)))
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    def launch() -> None:
        if start_crawl is not None:
            start_crawl()
            return

        def job() -> None:
            conn = connect(path)
            try:
                run_once(
                    conn,
                    fetch=http_fetch,
                    trigger="manual",
                    find_letsplay=search_letsplay,
                )
            except Exception as exc:
                print(exc, file=sys.stderr)
            finally:
                conn.close()

        threading.Thread(target=job, name="catalog-crawl", daemon=True).start()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = "", platform: str = "", sort: str = "metascore_desc"):
        conn = connect(path)
        try:
            games = load_catalog(conn)
        finally:
            conn.close()
        names = sorted(
            {item["name"] for game in games for item in game["platforms"]},
            key=str.casefold,
        )
        chosen_sort = sort if any(value == sort for value, _label in SORTS) else "metascore_desc"
        visible = _filter_games(games, q, platform)
        visible.sort(key=lambda game: _sort_key(game, chosen_sort, platform))
        return templates.TemplateResponse(
            request,
            "list.html",
            {
                "games": visible,
                "q": q,
                "platform": platform,
                "platforms": names,
                "sort": chosen_sort,
                "sorts": SORTS,
            },
        )

    @app.get("/games/{slug}", response_class=HTMLResponse)
    def card(request: Request, slug: str):
        conn = connect(path)
        try:
            games = load_catalog(conn)
            game = next((item for item in games if catalog_slug(item["path"]) == slug), None)
            letsplay = load_letsplay(conn, game["path"]) if game else None
            if letsplay is not None:
                letsplay = {
                    **letsplay,
                    "kind_label": _KIND.get(letsplay["kind"], letsplay["kind"]),
                    "url": _http_url(letsplay["url"]),
                }
            copy = _copy(conn, game, letsplay, translate or to_russian) if game else None
        finally:
            conn.close()
        if game is None:
            return templates.TemplateResponse(
                request, "missing.html", {}, status_code=404
            )
        return templates.TemplateResponse(
            request,
            "card.html",
            {
                "game": game,
                "metacritic": game_url(game["path"]),
                "video": _http_url(game["video"]),
                "letsplay": letsplay,
                "similar": similar_games(games, game["path"]),
                "copy": copy,
            },
        )

    @app.get("/process", response_class=HTMLResponse)
    def process(request: Request):
        return templates.TemplateResponse(request, "process.html", _process(path))

    @app.get("/process/status")
    def process_status():
        return _process(path)

    @app.post("/process/run")
    def run_now():
        if not _process(path)["running"]:
            launch()
        return RedirectResponse("/process", status_code=303)

    return app


def _process(path: Path) -> dict:
    now = datetime.now(ZONE)
    conn = connect(path)
    try:
        snapshot = crawl_snapshot(conn)
    finally:
        conn.close()
    running = _lock_active(snapshot["lock"], now)
    run = snapshot["run"]
    state = snapshot["state"]
    if running:
        status = "Идёт обход"
    elif run is None:
        status = "Обход не запускался"
    elif run["status"] == "running":
        status = "Прерван"
    else:
        status = _STATUS.get(run["status"], run["status"])
    if state is None or state["day"] != local_day(now):
        nxt = "Следующий запуск читает New Releases"
    else:
        nxt = f"Следующий запуск читает раздел New, страница {state['new_page']}"
    started = ""
    if run and run.get("started_at"):
        started = _clock(datetime.fromisoformat(run["started_at"]))
    message = ""
    if run and run.get("message") and not running:
        message = run["message"]
    return {
        "time": _clock(now),
        "status": status,
        "running": running,
        "next": nxt,
        "message": message,
        "started": started,
    }


def _copy(conn, game: dict, letsplay: dict | None, translate) -> dict:
    sources = {
        "description": game.get("description") or "",
        "critic_summary": game.get("critic_summary") or "",
        "user_summary": game.get("user_summary") or "",
        "conclusion": (letsplay or {}).get("conclusion") or "",
        "transcript": (letsplay or {}).get("transcript") or "",
    }
    ready = {}
    pending = []
    for field, source in sources.items():
        if source == "":
            ready[field] = {"orig": "", "ru": ""}
            continue
        cached = load_translation(conn, game["path"], field, source)
        if cached is None:
            pending.append(field)
        else:
            ready[field] = {"orig": source, "ru": cached}
    rendered = _translate_sources(translate, [sources[field] for field in pending])
    for field, text in zip(pending, rendered):
        source = sources[field]
        if text is None:
            ready[field] = {"orig": source, "ru": source}
            continue
        save_translation(conn, game["path"], field, source, text)
        ready[field] = {"orig": source, "ru": text}
    ready["has_prose"] = any(item["orig"] for item in ready.values() if isinstance(item, dict))
    return ready


def _translate_sources(translate, sources: list[str]) -> list[str | None]:
    if not sources:
        return []

    def one(source: str) -> str | None:
        try:
            text = translate(source)
        except Exception:
            return None
        if not isinstance(text, str) or text.strip() == "":
            return None
        return text

    if len(sources) == 1:
        return [one(sources[0])]
    with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
        return list(pool.map(one, sources))


def _lock_active(started_at: str | None, now: datetime) -> bool:
    if not started_at:
        return False
    started = datetime.fromisoformat(started_at)
    if started.tzinfo is None:
        started = started.replace(tzinfo=ZONE)
    return now - started < STALE_LOCK


def _clock(moment: datetime) -> str:
    return moment.astimezone(ZONE).strftime("%d.%m.%Y %H:%M:%S") + " (Asia/Novosibirsk)"


def _filter_games(games: list[dict], q: str, platform: str) -> list[dict]:
    needle = q.casefold().strip()
    visible = []
    for game in games:
        if needle and needle not in game["title"].casefold():
            continue
        if platform and not any(item["name"] == platform for item in game["platforms"]):
            continue
        visible.append(game)
    return visible


def _sort_key(game: dict, sort: str, platform: str):
    field, descending = {
        "metascore_desc": ("metascore", True),
        "metascore_asc": ("metascore", False),
        "userscore_desc": ("userscore", True),
        "userscore_asc": ("userscore", False),
    }[sort]
    rows = game["platforms"]
    if platform:
        rows = [item for item in rows if item["name"] == platform]
    values = [item[field] for item in rows if item[field] is not None]
    value = max(values) if values else None
    rank = 0 if value is None else (-value if descending else value)
    return (value is None, rank, game["title"].casefold(), game["path"])


def _format_score(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def _http_url(url: str) -> str:
    if isinstance(url, str) and (url.startswith("https://") or url.startswith("http://")):
        return url
    return ""


app = create_app()
