import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app import create_app
from game_page import parse_game_page
from similar import similar_games
from store import ZONE, connect, list_games, save_game

FIXTURES = Path(__file__).with_name("fixtures")


def game(path, title, developer, description):
    return {
        "path": path,
        "title": title,
        "developer": developer,
        "description": description,
    }


class SimilarGamesTest(unittest.TestCase):
    def test_two_saved_games_share_developer_words(self):
        with TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "catalog.db")
            now = datetime(2026, 11, 2, 9, tzinfo=ZONE).isoformat()
            for name, path in (
                ("baldurs-gate-3.html", "/game/baldurs-gate-3/"),
                ("divinity-original-sin-ii.html", "/game/divinity-original-sin-ii/"),
            ):
                parsed = parse_game_page((FIXTURES / name).read_text(encoding="utf-8"))
                parsed["related"] = ["/game/elden-ring/"]
                save_game(conn, path, parsed, "2026-11-02", now)
            games = list_games(conn)
            conn.close()
        found = similar_games(games, "/game/baldurs-gate-3/")
        self.assertEqual([item["path"] for item in found], ["/game/divinity-original-sin-ii/"])
        self.assertEqual(
            [(item["word"], item["left"], item["right"]) for item in found[0]["words"]],
            [
                ("Games", ["developer"], ["developer"]),
                ("Larian", ["developer"], ["developer"]),
                ("Studios", ["developer"], ["developer"]),
                ("become", ["description"], ["description"]),
            ],
        )

    def test_weak_overlap_still_returns_the_nearest_card(self):
        games = [
            game("/game/a/", "Purple Lantern", ["Northwind"], "moss"),
            game("/game/b/", "Qzxqv", ["Mmpf"], "blip"),
        ]
        found = similar_games(games, "/game/a/")
        self.assertEqual([item["path"] for item in found], ["/game/b/"])
        self.assertEqual(found[0]["words"], [])

    def test_at_most_four_titles(self):
        source = game("/game/source/", "Quest Source", [], "mmm mmm")
        others = [
            game(f"/game/quest-{name}/", f"Quest {name}", [], "mmm mmm")
            for name in ("One", "Two", "Three", "Four")
        ]
        distant = game("/game/quest-five/", "Quest Five", [], "zzz zzz")
        found = similar_games([source, *others, distant], "/game/source/")
        self.assertEqual(len(found), 4)
        self.assertNotIn("/game/quest-five/", [item["path"] for item in found])

    def test_card_link_opens_the_other_saved_game(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.db"
            conn = connect(db)
            now = datetime(2026, 11, 2, 9, tzinfo=ZoneInfo("Asia/Novosibirsk")).isoformat()
            for name, path in (
                ("baldurs-gate-3.html", "/game/baldurs-gate-3/"),
                ("divinity-original-sin-ii.html", "/game/divinity-original-sin-ii/"),
            ):
                save_game(
                    conn,
                    path,
                    parse_game_page((FIXTURES / name).read_text(encoding="utf-8")),
                    "2026-11-02",
                    now,
                )
            conn.close()
            client = TestClient(create_app(db))
            page = client.get("/games/baldurs-gate-3")
            self.assertEqual(page.status_code, 200)
            self.assertIn('href="/games/divinity-original-sin-ii"', page.text)
            self.assertIn("Divinity: Original Sin II", page.text)
            opened = client.get("/games/divinity-original-sin-ii")
            self.assertEqual(opened.status_code, 200)
            self.assertIn("<h1 class=\"game-title\">Divinity: Original Sin II</h1>", opened.text)
            self.assertIn('href="/games/baldurs-gate-3"', opened.text)


if __name__ == "__main__":
    unittest.main()
