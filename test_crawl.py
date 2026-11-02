import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import crawl
from crawl import EmptyListing, run_once, run_schedule
from listing import NEW_PAGE_ROOT, NEW_RELEASES_URL, new_page_url
from store import ZONE, AlreadyRunning, acquire_lock, connect, read_state, release_lock
from summary import summarize_quotes

FIXTURE = Path(__file__).with_name("fixtures") / "baldurs-gate-3.html"
MOSCOW = ZoneInfo("Europe/Moscow")


def flatten(value):
    nodes = []

    def add(item):
        if isinstance(item, dict):
            index = len(nodes)
            nodes.append(None)
            obj = {}
            nodes[index] = obj
            for key, child in item.items():
                obj[key] = add(child)
            return index
        if isinstance(item, list):
            index = len(nodes)
            nodes.append(None)
            seq = []
            nodes[index] = seq
            for child in item:
                seq.append(add(child))
            return index
        index = len(nodes)
        nodes.append(item)
        return index

    add(value)
    return nodes


def nuxt_html(root: dict) -> str:
    payload = json.dumps(flatten(root), ensure_ascii=False)
    return f'<script id="__NUXT_DATA__">{payload}</script>'


def releases_html(slugs, distractor=None) -> str:
    components = []
    if distractor:
        components.append(
            {
                "meta": {"componentName": "best-on-carousel"},
                "data": {"items": [{"slug": distractor, "type": "game-title"}]},
            }
        )
    components.append(
        {
            "meta": {"componentName": "new-releases-carousel"},
            "data": {"items": [{"slug": slug, "type": "game-title"} for slug in slugs]},
        }
    )
    return nuxt_html(
        {
            "path": "/game/",
            "data": {"loadPage:door:games-door-global:": {"components": components}},
        }
    )


def browse_html(slugs) -> str:
    return nuxt_html(
        {
            "path": "/browse/game/all/all/all-time/new/",
            "data": {
                "browse-game-/browse/game/all/all/all-time/new/": {
                    "items": [{"slug": slug, "type": "game-title"} for slug in slugs],
                    "total": len(slugs),
                }
            },
        }
    )


def fake_game(html: str) -> dict:
    slug = html.rstrip("/").split("/")[-1]
    return {
        "title": slug,
        "cover": "",
        "developer": [slug],
        "description": "",
        "video": "",
        "platforms": [{"name": "PC", "metascore": 1, "userscore": None}],
        "critic_quotes": [{"quote": f"quote {slug}", "score": 1, "source": "s", "platform": "PC"}],
        "user_quotes": [],
    }


class CrawlCasesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "catalog.db")
        self.pages = {}
        self.fetched = []

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def fetch(self, url):
        self.fetched.append(url)
        if url in self.pages:
            return self.pages[url]
        return url

    def launch(self, now, trigger="manual"):
        self.fetched.clear()
        return run_once(
            self.conn,
            fetch=self.fetch,
            now=now,
            trigger=trigger,
            parse_game=fake_game,
            find_letsplay=lambda title, fetch: None,
        )

    def test_three_cases_and_same_day_skip(self):
        morning = datetime(2026, 11, 2, 9, 0, tzinfo=ZONE)
        later = datetime(2026, 11, 2, 10, 0, tzinfo=ZONE)
        self.pages[NEW_RELEASES_URL] = releases_html(["alpha", "beta"], distractor="elden-ring")

        first = self.launch(morning)
        self.assertEqual(first["source"], "new_releases")
        self.assertEqual(first["url"], NEW_RELEASES_URL)
        self.assertEqual(first["taken"], ["/game/alpha/", "/game/beta/"])
        self.assertEqual(first["next_phase"], "new")
        self.assertEqual(first["next_page"], 1)
        self.assertNotIn("https://www.metacritic.com/game/elden-ring/", self.fetched)
        state = read_state(self.conn)
        self.assertEqual(state["day"], "2026-11-02")
        self.assertEqual(state["phase"], "new")
        self.assertEqual(state["new_page"], 1)
        summary = self.conn.execute(
            "SELECT critic_summary FROM games WHERE path = '/game/alpha/'"
        ).fetchone()
        self.assertEqual(summary["critic_summary"], "quote alpha")

        self.pages[NEW_PAGE_ROOT] = browse_html(["alpha", "delta"])
        second = self.launch(later, trigger="schedule")
        self.assertEqual(second["source"], "new")
        self.assertEqual(second["page"], 1)
        self.assertEqual(second["url"], NEW_PAGE_ROOT)
        self.assertEqual(second["skipped"], ["/game/alpha/"])
        self.assertEqual(second["taken"], ["/game/delta/"])
        self.assertNotIn("https://www.metacritic.com/game/alpha/", self.fetched)
        self.assertEqual(second["next_page"], 2)
        self.assertEqual(read_state(self.conn)["new_page"], 2)

        self.pages[new_page_url(2)] = browse_html([])
        with self.assertRaises(EmptyListing) as caught:
            self.launch(later)
        self.assertIn(new_page_url(2), str(caught.exception))
        self.assertEqual(self.fetched, [new_page_url(2)])
        state = read_state(self.conn)
        self.assertEqual(state["day"], "2026-11-02")
        self.assertEqual(state["phase"], "new")
        self.assertEqual(state["new_page"], 2)
        saved = {
            row["path"]
            for row in self.conn.execute("SELECT path FROM games ORDER BY path")
        }
        self.assertEqual(saved, {"/game/alpha/", "/game/beta/", "/game/delta/"})
        statuses = [row["status"] for row in self.conn.execute("SELECT status FROM crawl_runs ORDER BY id")]
        self.assertEqual(statuses, ["ok", "ok", "empty"])

    def test_new_novosibirsk_day_starts_at_new_releases(self):
        first_day = datetime(2026, 11, 2, 15, 0, tzinfo=ZONE)
        self.pages[NEW_RELEASES_URL] = releases_html(["alpha"])
        self.launch(first_day)
        self.pages[NEW_PAGE_ROOT] = browse_html(["beta"])
        self.launch(first_day + timedelta(hours=1))
        self.assertEqual(read_state(self.conn)["new_page"], 2)

        # 20:30 в Москве — уже 00:30 следующего дня в Новосибирске.
        next_morning = datetime(2026, 11, 2, 20, 30, tzinfo=MOSCOW)
        self.pages[NEW_RELEASES_URL] = releases_html(["alpha"])
        again = self.launch(next_morning)
        self.assertEqual(again["source"], "new_releases")
        self.assertEqual(again["taken"], ["/game/alpha/"])
        self.assertEqual(again["next_page"], 1)
        self.assertEqual(read_state(self.conn)["day"], "2026-11-03")

    def test_batch_stops_at_twenty(self):
        slugs = [f"game-{number}" for number in range(25)]
        self.pages[NEW_RELEASES_URL] = releases_html(slugs)
        result = self.launch(datetime(2026, 11, 2, 9, 0, tzinfo=ZONE))
        self.assertEqual(len(result["taken"]), 20)
        self.assertEqual(result["taken"][0], "/game/game-0/")
        self.assertEqual(result["taken"][-1], "/game/game-19/")
        self.assertIsNone(
            self.conn.execute("SELECT path FROM games WHERE path = '/game/game-20/'").fetchone()
        )

    def test_page_of_already_seen_games_still_advances(self):
        moment = datetime(2026, 11, 2, 9, 0, tzinfo=ZONE)
        self.pages[NEW_RELEASES_URL] = releases_html(["alpha"])
        self.launch(moment)
        self.pages[NEW_PAGE_ROOT] = browse_html(["alpha"])
        second = self.launch(moment + timedelta(hours=1))
        self.assertEqual(second["taken"], [])
        self.assertEqual(second["skipped"], ["/game/alpha/"])
        self.assertEqual(second["next_page"], 2)

    def test_total_failure_does_not_advance(self):
        self.pages[NEW_RELEASES_URL] = releases_html(["alpha"])

        def broken(_html):
            raise RuntimeError("сеть")

        with self.assertRaises(crawl.CrawlFailed):
            run_once(
                self.conn,
                fetch=self.fetch,
                now=datetime(2026, 11, 2, 9, 0, tzinfo=ZONE),
                parse_game=broken,
            )
        self.assertIsNone(read_state(self.conn))

    def test_failed_lock_is_not_released(self):
        now = datetime(2026, 11, 2, 9, 0, tzinfo=ZONE)
        acquire_lock(self.conn, now)
        self.pages[NEW_RELEASES_URL] = releases_html(["alpha"])
        with self.assertRaises(AlreadyRunning):
            self.launch(now)
        self.assertIsNotNone(self.conn.execute("SELECT id FROM crawl_lock").fetchone())
        release_lock(self.conn)

    def test_run_command_is_manual(self):
        db = Path(self.tmp.name) / "command.db"
        html = FIXTURE.read_text(encoding="utf-8")
        pages = {
            NEW_RELEASES_URL: releases_html(["baldurs-gate-3"]),
            "https://www.metacritic.com/game/baldurs-gate-3/": html,
        }
        original = crawl.http_fetch

        def fetch(url):
            return pages[url]

        crawl.http_fetch = fetch
        original_search = crawl.search_letsplay
        crawl.search_letsplay = lambda title, fetch: None
        try:
            code = crawl.main(["run", "--db", str(db)])
        finally:
            crawl.http_fetch = original
            crawl.search_letsplay = original_search
        self.assertEqual(code, 0)
        conn = connect(db)
        self.assertEqual(
            conn.execute("SELECT trigger FROM crawl_runs").fetchone()["trigger"],
            "manual",
        )
        self.assertEqual(
            conn.execute("SELECT title FROM games").fetchone()["title"],
            "Baldur's Gate 3",
        )
        conn.close()

    def test_schedule_runs_on_each_new_hour(self):
        moment = {"now": datetime(2026, 11, 2, 12, 10, tzinfo=ZONE)}
        calls = []

        def clock():
            return moment["now"]

        def sleep(seconds):
            moment["now"] = moment["now"] + timedelta(seconds=seconds)

        def runner(trigger):
            calls.append((trigger, moment["now"].hour))

        run_schedule(runner, clock=clock, sleep=sleep, stop_after=2)
        self.assertEqual(calls, [("schedule", 12), ("schedule", 13)])

    def test_summary_without_key_copies_quotes(self):
        import os

        os.environ.pop("SUMMARY_API_KEY", None)
        text = summarize_quotes(
            [
                {"quote": "for  Divinity [Issue#329]"},
                {"quote": "second"},
                {"quote": "third"},
                {"quote": "fourth"},
            ]
        )
        self.assertEqual(text, "for  Divinity [Issue#329]\n\nsecond\n\nthird")


class EnrichmentStepTest(unittest.TestCase):
    def test_summaries_and_letsplay_follow_the_saved_card(self):
        with TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "catalog.db")
            seen = []

            def summarize(quotes, kind="отзывов"):
                row = conn.execute(
                    "SELECT title, critic_summary FROM games WHERE path = '/game/alpha/'"
                ).fetchone()
                seen.append((kind, row["title"], row["critic_summary"]))
                return f"пересказ {kind}"

            def find(title, fetch):
                row = conn.execute(
                    "SELECT critic_summary, user_summary FROM games WHERE path = '/game/alpha/'"
                ).fetchone()
                seen.append(("letsplay", row["critic_summary"], row["user_summary"]))
                return {
                    "video_id": "abc",
                    "title": "alpha Let's Play",
                    "url": "https://www.youtube.com/watch?v=abc",
                    "views": 15,
                    "kind": "let's play",
                    "words": ["alpha"],
                }

            pages = {
                "https://www.youtube.com/watch?v=abc": (
                    "ytInitialPlayerResponse = "
                    '{"captions":{"playerCaptionsTracklistRenderer":{"captionTracks":'
                    '[{"languageCode":"en","baseUrl":"https://example.test/caption"}]}}};'
                ),
                "https://example.test/caption?fmt=json3": json.dumps(
                    {"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "there"}]}]}
                ),
            }
            import os

            os.environ.pop("SUMMARY_API_KEY", None)
            listing = releases_html(["alpha"])

            def fetch(url):
                if url == NEW_RELEASES_URL:
                    return listing
                return pages.get(url, url)

            run_once(
                conn,
                fetch=fetch,
                now=datetime(2026, 11, 2, 9, 0, tzinfo=ZONE),
                parse_game=fake_game,
                summarize=summarize,
                find_letsplay=find,
            )
            self.assertEqual(seen[0], ("критиков", "alpha", ""))
            self.assertEqual(seen[1][0], "игроков")
            self.assertEqual(seen[2][1:], ("пересказ критиков", "пересказ игроков"))
            game = conn.execute(
                "SELECT critic_summary, user_summary FROM games"
            ).fetchone()
            self.assertEqual(game["critic_summary"], "пересказ критиков")
            self.assertEqual(game["user_summary"], "пересказ игроков")
            video = conn.execute("SELECT title, transcript, conclusion FROM lets_plays").fetchone()
            self.assertEqual(video["title"], "alpha Let's Play")
            self.assertEqual(video["transcript"], "Hello there")
            self.assertEqual(video["conclusion"], "")
            conn.close()

    def test_missing_letsplay_leaves_the_card(self):
        with TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "catalog.db")
            listing = releases_html(["alpha"])
            run_once(
                conn,
                fetch=lambda url: listing if url == NEW_RELEASES_URL else url,
                now=datetime(2026, 11, 2, 9, 0, tzinfo=ZONE),
                parse_game=fake_game,
                find_letsplay=lambda title, fetch: None,
            )
            self.assertEqual(
                conn.execute("SELECT title FROM games").fetchone()["title"],
                "alpha",
            )
            self.assertIsNone(conn.execute("SELECT path FROM lets_plays").fetchone())
            conn.close()


class SavedGameCrawlTest(unittest.TestCase):
    def test_real_game_page_is_stored(self):
        html = FIXTURE.read_text(encoding="utf-8")
        with TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "catalog.db")
            pages = {
                NEW_RELEASES_URL: releases_html(["baldurs-gate-3"]),
                "https://www.metacritic.com/game/baldurs-gate-3/": html,
            }
            import os

            os.environ.pop("SUMMARY_API_KEY", None)
            result = run_once(
                conn,
                fetch=lambda url: pages[url],
                now=datetime(2026, 11, 2, 9, 0, tzinfo=ZONE),
                find_letsplay=lambda title, fetch: None,
            )
            self.assertEqual(result["taken"], ["/game/baldurs-gate-3/"])
            game = conn.execute("SELECT title, cover, developer, video FROM games").fetchone()
            self.assertEqual(game["title"], "Baldur's Gate 3")
            self.assertEqual(
                game["cover"],
                "https://www.metacritic.com/a/img/catalog/provider/7/2/7-1695438055.jpg",
            )
            self.assertEqual(json.loads(game["developer"]), ["Larian Studios Games"])
            self.assertEqual(game["video"], "https://cdn.jwplayer.com/players/PimKzh3M.html")
            scores = conn.execute(
                "SELECT name, metascore, userscore FROM platform_scores ORDER BY position"
            ).fetchall()
            self.assertEqual(
                [tuple(row) for row in scores],
                [("PC", 96, 9.1), ("PlayStation 5", 96, None), ("Xbox Series X", 99, None)],
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM critic_quotes").fetchone()["n"],
                13,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM user_quotes").fetchone()["n"],
                21,
            )
            texts = conn.execute(
                "SELECT critic_summary, user_summary FROM games"
            ).fetchone()
            self.assertTrue(texts["critic_summary"].startswith("Larian Studios"))
            self.assertTrue(texts["user_summary"].startswith("Astarion."))
            self.assertNotEqual(texts["critic_summary"], texts["user_summary"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
