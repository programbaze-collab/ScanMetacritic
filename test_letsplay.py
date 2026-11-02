import json
import unittest

from letsplay import choose_letsplay, load_transcript, parse_search_html


def video(video_id, title, views):
    return {
        "video_id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "views": views,
    }


class ChooseLetsplayTest(unittest.TestCase):
    def test_prefers_lets_play_then_views_and_requires_title_words(self):
        chosen = choose_letsplay(
            "Baldur's Gate 3",
            [
                video("trailer", "Baldur's Gate 3 Official Trailer", 9_000_000),
                video("mine", "Minecraft Let's Play", 20_000_000),
                video("old", "Baldur's Gate 2 Let's Play", 8_000_000),
                video("play", "Baldur's Gate 3 Gameplay", 4_000_000),
                video("low", "Baldur's Gate 3 Let's Play part 2", 1_000),
                video("high", "Baldur's Gate 3 - Let's Play Part 1", 2_000_000),
            ],
        )
        self.assertEqual(chosen["video_id"], "high")
        self.assertEqual(chosen["kind"], "let's play")
        self.assertEqual(chosen["words"], ["baldurs", "gate", "3"])
        self.assertEqual(chosen["views"], 2_000_000)

    def test_no_video_when_titles_do_not_contain_the_game(self):
        self.assertIsNone(
            choose_letsplay(
                "Zzxqplorn",
                [video("mine", "Minecraft Let's Play", 20_000_000)],
            )
        )

    def test_search_html_reads_view_counts(self):
        payload = {
            "contents": {
                "twoColumnSearchResultsRenderer": {
                    "primaryContents": {
                        "sectionListRenderer": {
                            "contents": [
                                {
                                    "itemSectionRenderer": {
                                        "contents": [
                                            {
                                                "videoRenderer": {
                                                    "videoId": "abc",
                                                    "title": {"runs": [{"text": "Baldur's Gate 3 Let's Play"}]},
                                                    "viewCountText": {"simpleText": "2,515,278 views"},
                                                }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }
        html = "var ytInitialData = " + json.dumps(payload) + ";"
        found = parse_search_html(html)
        self.assertEqual(found[0]["views"], 2_515_278)
        self.assertEqual(found[0]["title"], "Baldur's Gate 3 Let's Play")

    def test_transcript_uses_caption_track(self):
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
        self.assertEqual(load_transcript("abc", pages.get), "Hello there")


if __name__ == "__main__":
    unittest.main()
