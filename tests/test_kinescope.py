"""Разбор ссылок Kinescope."""

from __future__ import annotations

import unittest

from course.video_hosts.kinescope import KinescopeAdapter, parse_kinescope_id
from course.video_hosts.ytdlp import extract_kinescope_urls, extract_video_urls


class KinescopeUrlTests(unittest.TestCase):
    def test_share_and_embed_ids(self) -> None:
        self.assertEqual(
            parse_kinescope_id("https://kinescope.io/wDv57H5iTcZtnnDNN767fz"),
            "wDv57H5iTcZtnnDNN767fz",
        )
        self.assertEqual(
            parse_kinescope_id(
                "https://kinescope.io/embed/f82e984b-b33a-4424-8903-8eb0ea9ba50d"
            ),
            "f82e984b-b33a-4424-8903-8eb0ea9ba50d",
        )

    def test_extract_from_text(self) -> None:
        urls = extract_kinescope_urls(
            "запись https://kinescope.io/wDv57H5iTcZtnnDNN767fz практика"
        )
        self.assertEqual(urls, ["https://kinescope.io/wDv57H5iTcZtnnDNN767fz"])
        pairs = extract_video_urls("https://kinescope.io/wDv57H5iTcZtnnDNN767fz")
        self.assertEqual(pairs, [("kinescope", "https://kinescope.io/wDv57H5iTcZtnnDNN767fz")])

    def test_timecode(self) -> None:
        url = KinescopeAdapter().timecode_url(
            "https://kinescope.io/wDv57H5iTcZtnnDNN767fz",
            75,
            video_id="wDv57H5iTcZtnnDNN767fz",
        )
        self.assertEqual(url, "https://kinescope.io/wDv57H5iTcZtnnDNN767fz?seek=75")


if __name__ == "__main__":
    unittest.main()
