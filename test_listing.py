import unittest
from pathlib import Path

from listing import parse_listing

FIXTURES = Path(__file__).with_name("fixtures")


class ListingFixtureTest(unittest.TestCase):
    def test_new_releases_are_only_that_carousel(self):
        html = (FIXTURES / "game-hub.html").read_text(encoding="utf-8")
        paths = parse_listing(html, "new_releases")
        self.assertEqual(len(paths), 20)
        self.assertEqual(paths[0], "/game/where-the-light-sleeps/")
        self.assertNotIn("/game/elden-ring/", paths)
        self.assertNotIn("/game/baldurs-gate-3/", paths)

    def test_new_page_is_the_browse_list(self):
        html = (FIXTURES / "browse-new.html").read_text(encoding="utf-8")
        paths = parse_listing(html, "new")
        self.assertEqual(len(paths), 24)
        self.assertEqual(paths[0], "/game/traumata/")


if __name__ == "__main__":
    unittest.main()
