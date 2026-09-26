"""Разбор Vimeo URL и выбор файла для расшифровки."""

from __future__ import annotations

import unittest

from course.video_hosts.vimeo_api import (
    parse_vimeo_ref,
    pick_audio_source,
    pick_texttrack,
    video_api_path,
)


class VimeoRefTests(unittest.TestCase):
    def test_plain_id(self):
        self.assertEqual(
            parse_vimeo_ref("https://vimeo.com/836305758?fl=tl&fe=ec"),
            ("836305758", ""),
        )

    def test_unlisted_hash(self):
        self.assertEqual(
            parse_vimeo_ref("https://vimeo.com/836305758/d40464c519"),
            ("836305758", "d40464c519"),
        )

    def test_player_h_param(self):
        self.assertEqual(
            parse_vimeo_ref("https://player.vimeo.com/video/836305758?h=d40464c519"),
            ("836305758", "d40464c519"),
        )

    def test_api_path(self):
        self.assertEqual(video_api_path("1", ""), "/videos/1")
        self.assertEqual(video_api_path("1", "abc"), "/videos/1:abc")


class VimeoPickTests(unittest.TestCase):
    def test_prefers_audio_then_smallest_mp4(self):
        payload = {
            "files": [
                {"link": "https://cdn/hd.mp4", "height": 1080, "size": 90_000_000, "type": "video/mp4"},
                {"link": "https://cdn/sd.mp4", "height": 360, "size": 20_000_000, "type": "video/mp4"},
                {"link": "https://cdn/a.m4a", "quality": "audio", "size": 5_000_000, "type": "audio/mp4"},
            ]
        }
        picked = pick_audio_source(payload)
        self.assertIsNotNone(picked)
        self.assertEqual(picked["link"], "https://cdn/a.m4a")

    def test_smallest_video_when_no_audio(self):
        payload = {
            "play": {
                "progressive": [
                    {"link": "https://cdn/hd.mp4", "height": 720, "size": 40, "type": "video/mp4"},
                    {"link": "https://cdn/low.mp4", "height": 240, "size": 10, "type": "video/mp4"},
                ]
            }
        }
        picked = pick_audio_source(payload)
        self.assertEqual(picked["link"], "https://cdn/low.mp4")

    def test_empty_without_files(self):
        self.assertIsNone(pick_audio_source({"name": "x"}))

    def test_prefers_russian_captions(self):
        track = pick_texttrack(
            [
                {"language": "en-x-autogen", "link": "en", "active": True, "name": "auto"},
                {"language": "ru", "link": "ru", "active": True, "name": "ru"},
            ]
        )
        self.assertEqual(track["link"], "ru")


if __name__ == "__main__":
    unittest.main()
