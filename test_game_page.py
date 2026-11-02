import unittest
from pathlib import Path

from game_page import display_platform, parse_game_page

FIXTURE = Path(__file__).with_name("fixtures") / "baldurs-gate-3.html"

DESCRIPTION = (
    "An ancient evil has returned to Baldur's Gate, intent on devouring it from the inside out. "
    "The fate of Faerun lies in your hands. Alone, you may resist. But together, you can overcome. "
    "Gather your party and return to the Forgotten Realms in a tale of fellowship and betrayal, "
    "sacrifice and survival, and the lure of absolute power. Mysterious abilities are awakening "
    "inside you, drawn from a mind flayer parasite planted in your brain. Resist, and turn darkness "
    "against itself. Or embrace corruption, and become ultimate evil."
)


class SavedGamePageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = parse_game_page(FIXTURE.read_text(encoding="utf-8"))

    def test_main_fields(self):
        game = self.game
        self.assertEqual(game["title"], "Baldur's Gate 3")
        self.assertEqual(
            game["cover"],
            "https://www.metacritic.com/a/img/catalog/provider/7/2/7-1695438055.jpg",
        )
        self.assertEqual(game["developer"], ["Larian Studios Games"])
        self.assertEqual(game["description"], DESCRIPTION)
        self.assertEqual(game["video"], "https://cdn.jwplayer.com/players/PimKzh3M.html")

    def test_platforms_and_scores(self):
        self.assertEqual(
            self.game["platforms"],
            [
                {"name": "PC", "metascore": 96, "userscore": 9.1},
                {"name": "PlayStation 5", "metascore": 96, "userscore": None},
                {"name": "Xbox Series X", "metascore": 99, "userscore": None},
            ],
        )

    def test_quotes_stay_separate_and_verbatim(self):
        critics = self.game["critic_quotes"]
        users = self.game["user_quotes"]
        self.assertEqual(len(critics), 13)
        self.assertEqual(len(users), 21)
        critic_text = {item["quote"] for item in critics}
        user_text = {item["quote"] for item in users}
        self.assertTrue(critic_text.isdisjoint(user_text))
        self.assertIn(
            {
                "quote": (
                    "Larian Studios, the creators of the latest installment, have gone to great "
                    "lengths to make sure they didn't disgrace any of the franchise's building blocks. "
                    "The love and care is evident in Baldur's Gate 3, though needless to pretend, "
                    "there aren't a few bricks crumbling here and there in this monument. It's far from "
                    "a perfect title, but on the whole Baldur's Gate 3 is a brilliant computer RPG that "
                    "is undoubtedly not to be overlooked. [Issue#329]"
                ),
                "score": 100,
                "source": "LEVEL (Czech Republic)",
                "platform": "PC",
            },
            critics,
        )
        self.assertIn(
            {
                "quote": "Astarion. \nBut seriously I have 1600 hours and I haven't stopped playing since November 2023",
                "score": 10,
                "source": "ScorpyDerpyVR",
                "platform": "PC",
            },
            users,
        )

    def test_ios_slug_is_labeled_ios(self):
        self.assertEqual(display_platform("iOS (iPhone/iPad)", "ios-iphoneipad"), "iOS")
        self.assertEqual(display_platform("ios-iphoneipad", "ios-iphoneipad"), "iOS")
        self.assertEqual(display_platform("PC", "pc"), "PC")
        self.assertEqual(display_platform("PlayStation 5", "playstation-5"), "PlayStation 5")


if __name__ == "__main__":
    unittest.main()
