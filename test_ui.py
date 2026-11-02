import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app import create_app
from store import ZONE, acquire_lock, connect, local_day, save_game, start_run, write_state


def save(conn, path, title, platforms):
    save_game(
        conn,
        path,
        {
            "title": title,
            "cover": "https://example.test/cover.jpg",
            "developer": ["Studio"],
            "description": "Описание " + title,
            "video": "https://example.test/video.html",
            "platforms": platforms,
            "critic_quotes": [{"quote": "Цитата критика", "score": 80, "source": "A", "platform": "PC"}],
            "user_quotes": [{"quote": "Цитата игрока", "score": 7, "source": "B", "platform": "PC"}],
        },
        "2026-11-02",
        datetime(2026, 11, 2, 9, tzinfo=ZONE).isoformat(),
    )


class CatalogInterfaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "catalog.db"
        self.conn = connect(self.db)
        save(
            self.conn,
            "/game/alpha/",
            "Alpha Quest",
            [
                {"name": "PC", "metascore": 10, "userscore": 9},
                {"name": "PlayStation 5", "metascore": 90, "userscore": None},
            ],
        )
        save(
            self.conn,
            "/game/beta/",
            "Beta Quest",
            [{"name": "PC", "metascore": 80, "userscore": 4}],
        )
        save(
            self.conn,
            "/game/gamma/",
            "Gamma",
            [{"name": "PC", "metascore": None, "userscore": None}],
        )
        self.conn.close()
        self.started = []
        self.client = TestClient(create_app(self.db, start_crawl=lambda: self.started.append("run")))

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_filters_search_and_sort(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Каталог игр с Metacritic", page.text)
        self.assertIn("cover-slot", page.text)
        self.assertIn("title-slot", page.text)
        self.assertIn("platforms-slot", page.text)
        self.assertIn("right 1rem center", self.client.get("/static/catalog.css").text)
        self.assertLess(page.text.index("Alpha Quest"), page.text.index("Beta Quest"))
        self.assertLess(page.text.index("Beta Quest"), page.text.index(">Gamma<"))

        by_name = self.client.get("/", params={"q": "beta"})
        self.assertIn("Beta Quest", by_name.text)
        self.assertNotIn("Alpha Quest", by_name.text)

        platform = self.client.get("/", params={"platform": "PlayStation 5"})
        self.assertIn("Alpha Quest", platform.text)
        self.assertNotIn("Beta Quest", platform.text)

        ascending = self.client.get("/", params={"sort": "metascore_asc"})
        self.assertLess(ascending.text.index("Beta Quest"), ascending.text.index("Alpha Quest"))
        self.assertLess(ascending.text.index("Alpha Quest"), ascending.text.index(">Gamma<"))

        players = self.client.get("/", params={"sort": "userscore_desc"})
        self.assertLess(players.text.index("Alpha Quest"), players.text.index("Beta Quest"))

    def test_card_has_catalog_sections_and_similar_link(self):
        page = self.client.get("/games/alpha")
        self.assertEqual(page.status_code, 200)
        self.assertIn("<h1 class=\"game-title\">Alpha Quest</h1>", page.text)
        self.assertIn("https://www.metacritic.com/game/alpha/", page.text)
        self.assertIn(">90<", page.text)
        self.assertIn("Описание Alpha Quest", page.text)
        self.assertIn("Пересказ критиков", page.text)
        self.assertIn("Пересказ игроков", page.text)
        self.assertIn("https://example.test/video.html", page.text)
        self.assertIn("Летсплей", page.text)
        self.assertIn('data-set-lang="ru" class="is-current"', page.text)
        self.assertIn('data-set-lang="orig"', page.text)
        self.assertIn('data-lang="orig" hidden', page.text)
        self.assertIn('href="/games/beta"', page.text)
        self.assertIn("Каталог игр с Metacritic", page.text)
        self.assertEqual(self.client.get("/games/missing").status_code, 404)

    def test_card_defaults_to_russian_and_keeps_original(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.db"
            conn = connect(db)
            save(
                conn,
                "/game/alpha/",
                "Alpha Quest",
                [{"name": "PC", "metascore": 90, "userscore": 8}],
            )
            conn.execute(
                """
                UPDATE games
                SET description = ?, critic_summary = ?, user_summary = ?
                WHERE path = '/game/alpha/'
                """,
                ("Ancient evil [Issue#329]", "Critics liked it", "Players liked it"),
            )
            conn.commit()
            conn.close()
            self._assert_russian_card(db)

    def _assert_russian_card(self, db):
        calls = []

        def translate(text):
            calls.append(text)
            return {
                "Ancient evil [Issue#329]": "Древнее зло [Issue#329]",
                "Critics liked it": "Критикам понравилось",
                "Players liked it": "Игрокам понравилось",
            }[text]

        client = TestClient(create_app(db, start_crawl=lambda: None, translate=translate))
        page = client.get("/games/alpha")
        self.assertLess(page.text.index("Древнее зло [Issue#329]"), page.text.index("Ancient evil [Issue#329]"))
        self.assertIn('data-lang="ru">Древнее зло [Issue#329]', page.text)
        self.assertIn('data-lang="orig" hidden>Ancient evil [Issue#329]', page.text)
        self.assertIn("Критикам понравилось", page.text)
        self.assertIn("Игрокам понравилось", page.text)
        self.assertEqual(len(calls), 3)

        again = client.get("/games/alpha")
        self.assertIn("Древнее зло [Issue#329]", again.text)
        self.assertEqual(len(calls), 3)

    def test_process_hides_progress_until_a_run_starts(self):
        page = self.client.get("/process")
        self.assertIn("Обход не запускался", page.text)
        self.assertIn("(Asia/Novosibirsk)", page.text)
        self.assertIn("Запустить сейчас", page.text)
        self.assertIn('id="crawl-progress" class="d-none" hidden', page.text)
        self.assertIn('id="run-now" type="submit">', page.text)

        started = self.client.post("/process/run", follow_redirects=False)
        self.assertEqual(started.status_code, 303)
        self.assertEqual(self.started, ["run"])

        conn = connect(self.db)
        acquire_lock(conn, datetime.now(ZONE))
        write_state(conn, local_day(datetime.now(ZONE)), "new", 2)
        conn.close()
        running = self.client.get("/process")
        self.assertIn("Идёт обход", running.text)
        self.assertNotIn('id="crawl-progress" class="d-none"', running.text)
        self.assertIn("раздел New, страница 2", running.text)
        self.assertIn('id="run-now" type="submit" disabled', running.text)

        conn = connect(self.db)
        conn.execute("DELETE FROM crawl_lock")
        start_run(conn, "manual", datetime(2026, 11, 2, 12, tzinfo=ZONE).isoformat(), "https://example.test/")
        conn.execute(
            """
            UPDATE crawl_runs
            SET status = 'ok', finished_at = started_at, message = 'New Releases: сохранено 1'
            """
        )
        conn.commit()
        conn.close()
        done = self.client.get("/process")
        self.assertIn("Готово", done.text)
        self.assertIn("New Releases: сохранено 1", done.text)
        self.assertIn('id="crawl-progress" class="d-none" hidden', done.text)


if __name__ == "__main__":
    unittest.main()
