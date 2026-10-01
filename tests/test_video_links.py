"""Файлы со ссылками, сопоставление урока/модуля, Zoom URL."""

from __future__ import annotations

import unittest

from course.classify import classify_by_rules
from course.disk_layout import is_video_link_filename
from course.match_scope import match_scope
from course.video_hosts.ytdlp import detect_host, extract_video_urls
from course.video_hosts.zoom import normalize_zoom_url, parse_zoom_id, password_from_url
from course.video_links import (
    extract_video_description,
    extract_video_password,
    is_password_followup,
    link_disk_key,
    parse_video_link_file,
)
from rag.material_index import format_chunk_heading


class ParseVideoLinkFileTests(unittest.TestCase):
    def test_headers_and_hosts(self) -> None:
        text = """
# комментарий
тип: практика
модуль: 2
урок: 4
пароль: secret
описание: разбор возражений про цену, живые примеры
https://kinescope.io/wDv57H5iTcZtnnDNN767fz
https://vimeo.com/123456789
https://youtu.be/abcdEFGHijk
https://us02web.zoom.us/rec/share/abc.def
"""
        entries = parse_video_link_file(text)
        self.assertEqual(len(entries), 4)
        hosts = [e.host for e in entries]
        self.assertEqual(hosts, ["kinescope", "vimeo", "youtube", "zoom"])
        self.assertEqual(entries[0].lesson_key, "2.4")
        self.assertEqual(entries[0].module_no, 2)
        self.assertEqual(entries[0].kind, "practice")
        self.assertEqual(entries[3].password, "secret")
        self.assertEqual(entries[0].password, "")
        self.assertEqual(entries[0].description, "разбор возражений про цену, живые примеры")
        self.assertEqual(entries[3].description, entries[0].description)

    def test_multiline_description(self) -> None:
        text = (
            "описание:\n"
            "Первый абзац про модуль.\n"
            "Второй абзац — акценты.\n"
            "https://kinescope.io/abc123XYZ\n"
        )
        entries = parse_video_link_file(text)
        self.assertEqual(len(entries), 1)
        self.assertIn("Первый абзац", entries[0].description)
        self.assertIn("Второй абзац", entries[0].description)

    def test_extract_description_from_dm(self) -> None:
        text = (
            "урок 2.4 практика\n"
            "описание: акцент на цене и ценности\n"
            "https://kinescope.io/wDv57H5iTcZtnnDNN767fz\n"
        )
        self.assertEqual(
            extract_video_description(text),
            "акцент на цене и ценности",
        )

    def test_module_only_without_lesson(self) -> None:
        text = "модуль: 3\nhttps://kinescope.io/abc123XYZ\n"
        entries = parse_video_link_file(text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].lesson_key, "")
        self.assertEqual(entries[0].module_no, 3)

    def test_password_on_same_line(self) -> None:
        text = "https://zoom.us/rec/play/xxxx пароль: 111\n"
        entries = parse_video_link_file(text)
        self.assertEqual(entries[0].host, "zoom")
        self.assertEqual(entries[0].password, "111")


class PasswordAndZoomTests(unittest.TestCase):
    def test_extract_video_password(self) -> None:
        self.assertEqual(extract_video_password("пароль: 12-ab"), "12-ab")
        self.assertEqual(
            extract_video_password("https://zoom.us/rec/share/x?pwd=QQQ"),
            "QQQ",
        )

    def test_zoom_id_and_pwd(self) -> None:
        url = "https://us02web.zoom.us/rec/share/AbC_12.3?pwd=zz"
        self.assertEqual(parse_zoom_id(url), "AbC_12.3")
        self.assertEqual(password_from_url(url), "zz")
        self.assertEqual(detect_host(url), "zoom")

    def test_normalize_play_embed_share(self) -> None:
        from urllib.parse import quote

        share = "https://us06web.zoom.us/rec/share/xxxxx.yyyy"
        play = (
            "https://us06web.zoom.us/rec/play/abc.def"
            f"?originRequestUrl={quote(share, safe='')}"
        )
        self.assertEqual(normalize_zoom_url(play), share)
        pairs = extract_video_urls(play + "\nпароль: 111")
        self.assertEqual(pairs[0], ("zoom", share))

    def test_password_followup(self) -> None:
        self.assertTrue(is_password_followup("пароль: w8M=6u=x"))
        self.assertFalse(is_password_followup("https://zoom.us/rec/share/x пароль: 1"))
        self.assertFalse(is_password_followup("напиши пост про паузу"))

    def test_extract_video_urls_order(self) -> None:
        pairs = extract_video_urls(
            "см. https://kinescope.io/wDv57H5iTcZtnnDNN767fz "
            "и https://zoom.us/rec/play/aabb"
        )
        self.assertEqual(
            [h for h, _ in pairs],
            ["kinescope", "zoom"],
        )


class MatchScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lessons = [
            {"lesson_key": "2.4", "module_no": 2, "title": "Деньги и ценность"},
            {"lesson_key": "1.1", "module_no": 1, "title": "Старт"},
        ]

    def test_lesson_key(self) -> None:
        m = match_scope("запись урока 2.4", self.lessons)
        self.assertEqual(m.lesson_key, "2.4")
        self.assertEqual(m.module_no, 2)

    def test_title_words(self) -> None:
        m = match_scope("это про деньги и ценность", self.lessons)
        self.assertEqual(m.lesson_key, "2.4")
        self.assertGreaterEqual(m.confidence, 0.4)

    def test_module_only(self) -> None:
        m = match_scope("зум модуля 3", self.lessons)
        self.assertEqual(m.lesson_key, "")
        self.assertEqual(m.module_no, 3)
        self.assertEqual(m.kind_hint, "practice")

    def test_classify_rules_module(self) -> None:
        r = classify_by_rules(title="Zoom 1", comment="модуль 2 практика")
        self.assertEqual(r.module_no, 2)
        self.assertEqual(r.kind, "practice")
        self.assertEqual(r.lesson_key, "")


class MiscTests(unittest.TestCase):
    def test_link_filename(self) -> None:
        self.assertTrue(is_video_link_filename("запись.txt"))
        self.assertTrue(is_video_link_filename("Ссылки.md"))
        self.assertTrue(is_video_link_filename("zoom.txt"))
        self.assertFalse(is_video_link_filename("конспект.txt"))
        self.assertFalse(is_video_link_filename("запись.mp4"))

    def test_link_disk_key(self) -> None:
        self.assertEqual(
            link_disk_key("/Аватар/МБТ/Курс/Модуль 2/запись.txt", 0),
            "/Аватар/МБТ/Курс/Модуль 2/запись.txt::0",
        )

    def test_heading_module(self) -> None:
        h = format_chunk_heading(
            product_name="МБТ",
            module_no=2,
            kind="practice",
        )
        self.assertIn("Модуль 2", h)
        self.assertNotIn("Урок", h)
        h2 = format_chunk_heading(
            product_name="МБТ",
            lesson_key="2.4",
            kind="practice",
            extra="описание",
        )
        self.assertIn("описание", h2)


if __name__ == "__main__":
    unittest.main()
