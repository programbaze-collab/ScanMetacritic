"""Один запуск обхода: вручную и раз в час по Asia/Novosibirsk.

Первый заход суток читает New Releases и после успеха ставит раздел New, страница 1.
Следующий заход читает текущую страницу New и увеличивает номер на 1.
Пустой список на странице — ошибка, фаза и страница не меняются.
За сутки каждая игра берётся не больше одного раза.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from game_page import parse_game_page
from letsplay import load_transcript, search_letsplay
from listing import NEW_RELEASES_URL, game_url, new_page_url, parse_listing
from store import (
    ZONE,
    AlreadyRunning,
    acquire_lock,
    clear_letsplay,
    connect,
    finish_run,
    local_day,
    processed_today,
    read_state,
    release_lock,
    save_game,
    save_letsplay,
    save_summary,
    start_run,
    write_state,
)
from summary import summarize_quotes, summarize_transcript

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "catalog.db"
BATCH = 20

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


class EmptyListing(Exception):
    def __init__(self, url: str):
        super().__init__(f"Пустой список игр: {url}")
        self.url = url


class CrawlFailed(Exception):
    pass


def http_fetch(url: str) -> str:
    with httpx.Client(timeout=40, follow_redirects=True, headers=_HEADERS) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def run_once(
    conn,
    *,
    fetch,
    now: datetime | None = None,
    trigger: str = "manual",
    limit: int = BATCH,
    parse_game=parse_game_page,
    summarize=summarize_quotes,
    find_letsplay=search_letsplay,
) -> dict:
    now = now or datetime.now(ZONE)
    day = local_day(now)
    held = False
    acquire_lock(conn, now)
    held = True
    run_id = None
    try:
        state = read_state(conn)
        first_of_day = state is None or state["day"] != day
        if first_of_day:
            source = "new_releases"
            page = None
            url = NEW_RELEASES_URL
        else:
            source = "new"
            page = int(state["new_page"])
            url = new_page_url(page)
        run_id = start_run(conn, trigger, now.isoformat(), url)
        try:
            paths = parse_listing(fetch(url), source)
        except Exception as exc:
            finish_run(
                conn,
                run_id,
                status="error",
                finished_at=now.isoformat(),
                taken=0,
                page=page,
                message=str(exc),
            )
            raise
        if not paths:
            finish_run(
                conn,
                run_id,
                status="empty",
                finished_at=now.isoformat(),
                taken=0,
                page=page,
                message=f"Пустой список игр: {url}",
            )
            raise EmptyListing(url)

        skipped = [path for path in paths if processed_today(conn, path, day)]
        chosen = [path for path in paths if path not in skipped][:limit]
        taken = []
        errors = []
        for path in chosen:
            try:
                game = parse_game(fetch(game_url(path)))
                save_game(conn, path, game, day, now.isoformat())
            except Exception as exc:
                errors.append({"path": path, "step": "карточка", "message": str(exc)})
                continue
            taken.append(path)
            _write_summaries(conn, path, game, summarize, now, errors)
            _write_letsplay(conn, path, game, fetch, find_letsplay, now, errors)

        if chosen and not taken:
            finish_run(
                conn,
                run_id,
                status="error",
                finished_at=now.isoformat(),
                taken=0,
                page=page,
                message="Не удалось сохранить ни одной игры",
            )
            raise CrawlFailed("Не удалось сохранить ни одной игры")

        next_page = 1 if first_of_day else page + 1
        write_state(conn, day, "new", next_page)
        message = _message(source, page, taken, skipped, errors, next_page)
        finish_run(
            conn,
            run_id,
            status="ok" if not errors else "partial",
            finished_at=now.isoformat(),
            taken=len(taken),
            page=page,
            message=message,
        )
        return {
            "trigger": trigger,
            "source": source,
            "url": url,
            "page": page,
            "taken": taken,
            "skipped": skipped,
            "errors": errors,
            "next_phase": "new",
            "next_page": next_page,
            "message": message,
        }
    finally:
        if held:
            release_lock(conn)


def run_schedule(runner, *, clock, sleep, stop_after: int | None = None) -> None:
    last_hour = None
    completed = 0
    while True:
        now = clock()
        hour = now.astimezone(ZONE).strftime("%Y-%m-%dT%H")
        if hour != last_hour:
            try:
                runner(trigger="schedule")
            except Exception as exc:
                print(exc, file=sys.stderr)
            last_hour = hour
            completed += 1
            if stop_after is not None and completed >= stop_after:
                return
        pause = _seconds_until_next_hour(clock())
        sleep(pause)


def _seconds_until_next_hour(now: datetime) -> float:
    local = now.astimezone(ZONE)
    upcoming = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (upcoming - local).total_seconds()


def _write_summaries(conn, path, game, summarize, now, errors) -> None:
    for column, quotes, kind, step in (
        ("critic_summary", game["critic_quotes"], "критиков", "пересказ критиков"),
        ("user_summary", game["user_quotes"], "игроков", "пересказ игроков"),
    ):
        try:
            save_summary(conn, path, column, summarize(quotes, kind), now.isoformat())
        except Exception as exc:
            errors.append({"path": path, "step": step, "message": str(exc)})


def _write_letsplay(conn, path, game, fetch, find_letsplay, now, errors) -> None:
    try:
        chosen = find_letsplay(game["title"], fetch)
    except Exception as exc:
        errors.append({"path": path, "step": "летсплей", "message": str(exc)})
        return
    if chosen is None:
        clear_letsplay(conn, path)
        return
    transcript = ""
    try:
        transcript = load_transcript(chosen["video_id"], fetch)
    except Exception as exc:
        errors.append({"path": path, "step": "расшифровка", "message": str(exc)})
    conclusion = ""
    if transcript:
        try:
            conclusion = summarize_transcript(transcript)
        except Exception as exc:
            errors.append({"path": path, "step": "вывод", "message": str(exc)})
    save_letsplay(
        conn,
        path,
        {**chosen, "transcript": transcript, "conclusion": conclusion},
        now.isoformat(),
    )


def _message(source, page, taken, skipped, errors, next_page) -> str:
    if source == "new_releases":
        where = "New Releases"
    else:
        where = f"New, страница {page}"
    text = (
        f"{where}: сохранено {len(taken)}, "
        f"пропущено уже обработанных сегодня {len(skipped)}. "
        f"Дальше раздел New, страница {next_page}."
    )
    missed = [item["path"] for item in errors if item.get("step") == "карточка"]
    if missed:
        text += f" Не сохранились: {', '.join(missed)}."
    later = [item for item in errors if item.get("step") != "карточка"]
    if later:
        text += " Ошибки после сохранения: " + "; ".join(
            f"{item['step']} {item['path']}" for item in later
        ) + "."
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Почасовой обход Metacritic")
    parser.add_argument("command", choices=("run", "schedule"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    conn = connect(args.db)
    try:
        if args.command == "run":
            try:
                result = run_once(
                    conn,
                    fetch=http_fetch,
                    trigger="manual",
                    find_letsplay=search_letsplay,
                )
            except EmptyListing as exc:
                print(exc, file=sys.stderr)
                return 2
            except CrawlFailed as exc:
                print(exc, file=sys.stderr)
                return 1
            print(result["message"])
            return 0

        def runner(trigger: str):
            result = run_once(
                conn,
                fetch=http_fetch,
                trigger=trigger,
                find_letsplay=search_letsplay,
            )
            print(result["message"])
            return result

        run_schedule(runner, clock=lambda: datetime.now(ZONE), sleep=time.sleep)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
