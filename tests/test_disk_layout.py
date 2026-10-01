"""Разбор путей Яндекс.Диска → продукт / урок / роль файла."""

from __future__ import annotations

import unittest

from course.disk_layout import (
    classify_disk_path,
    parse_lesson_folder,
    parse_lesson_key_from_text,
    parse_scope_reply,
)
from course.products import Product, ProductRegistry

_REG = ProductRegistry(
    products=[
        Product(id="mbt", name="МБТ", aliases=("мбт",), disk_folder="МБТ"),
        Product(
            id="magiya",
            name="Курс Магия",
            aliases=("курс магия", "магия"),
            disk_folder="Курс Магия",
        ),
        Product(
            id="materinstvo",
            name="Клуб Материнство",
            aliases=("клуб материнство",),
            disk_folder="Клуб Материнство",
        ),
    ]
)


def _role(path: str, *, active: str = "mbt"):
    return classify_disk_path(
        path,
        active_product_id=active,
        registry=_REG,
        course_disk_root="/Аватар",
    )


class DiskLayoutTests(unittest.TestCase):
    def test_parse_lesson_folder_module_dot(self) -> None:
        parsed = parse_lesson_folder("Урок 2.4. Деньги и ценность")
        self.assertIsNotNone(parsed)
        key, module_no, lesson_no, title = parsed  # type: ignore[misc]
        self.assertEqual(key, "2.4")
        self.assertEqual(module_no, 2)
        self.assertEqual(lesson_no, 4)
        self.assertIn("Деньги", title)

    def test_parse_lesson_key_from_text(self) -> None:
        self.assertEqual(parse_lesson_key_from_text("Урок 1.6 начинается"), "1.6")
        self.assertEqual(parse_lesson_key_from_text("Практика 2.4 Zoom"), "2.4")
        self.assertEqual(parse_lesson_key_from_text("Урок_1_4_конспект.pdf"), "1.4")

    def test_parse_scope_reply_module_or_lesson(self) -> None:
        self.assertEqual(parse_scope_reply("модуль 2"), ("", 2))
        self.assertEqual(parse_scope_reply("модуль: 2"), ("", 2))
        self.assertEqual(parse_scope_reply("2.4"), ("2.4", 2))
        self.assertEqual(parse_scope_reply("урок 2.4"), ("2.4", 2))
        self.assertEqual(parse_scope_reply("просто текст"), ("", None))

    def test_summary_in_lesson_folder(self) -> None:
        role = _role(
            "/Аватар/МБТ/Курс/Модуль 2/Урок 2.4. Деньги/конспект.pdf"
        )
        self.assertFalse(role.skip)
        self.assertEqual(role.product_id, "mbt")
        self.assertEqual(role.kind, "summary")
        self.assertEqual(role.lesson_key, "2.4")
        self.assertEqual(role.module_no, 2)

    def test_lesson_video(self) -> None:
        role = _role(
            "/Аватар/МБТ/Курс/Модуль 2/Урок 2.4. Деньги/запись.mp4"
        )
        self.assertEqual(role.kind, "lesson_video")
        self.assertEqual(role.lesson_key, "2.4")

    def test_practice_from_filename(self) -> None:
        role = _role("/Аватар/МБТ/Практики/2026-09-18 Практика 2.4.mp4")
        self.assertEqual(role.kind, "practice")
        self.assertEqual(role.lesson_key, "2.4")
        self.assertEqual(str(role.recorded_on), "2026-09-18")
        self.assertFalse(role.needs_lesson_confirm)

    def test_expert_info_and_posts(self) -> None:
        info = _role("/Аватар/00 Эксперт/Об эксперте.docx")
        self.assertEqual(info.product_id, "_expert")
        self.assertEqual(info.kind, "expert_info")
        post = _role("/Аватар/00 Эксперт/Посты/telegram/пост.txt")
        self.assertEqual(post.kind, "post")
        self.assertEqual(post.platform, "telegram")

    def test_product_info(self) -> None:
        role = _role("/Аватар/МБТ/О продукте.md")
        self.assertEqual(role.kind, "product_info")
        self.assertEqual(role.product_id, "mbt")

    def test_expert_folder_product_video(self) -> None:
        role = _role("/Аватар/00 Эксперт/Про МБТ.mp4")
        self.assertFalse(role.skip)
        self.assertEqual(role.kind, "product_info")
        self.assertEqual(role.product_id, "mbt")

    def test_expert_folder_unnamed_media(self) -> None:
        video = _role("/Аватар/00 Эксперт/интервью.mp4")
        self.assertFalse(video.skip)
        self.assertEqual(video.kind, "expert_info")
        self.assertEqual(video.product_id, "_expert")
        audio = _role("/Аватар/00 Эксперт/про эксперта.m4a")
        self.assertEqual(audio.kind, "expert_info")
        self.assertEqual(audio.product_id, "_expert")

    def test_product_folder_named_audio(self) -> None:
        role = _role("/Аватар/МБТ/О продукте.mp3")
        self.assertFalse(role.skip)
        self.assertEqual(role.kind, "product_info")
        self.assertEqual(role.product_id, "mbt")

    def test_inactive_product_skipped(self) -> None:
        role = _role("/Аватар/Курс Магия/Курс/Урок 1.1/конспект.pdf")
        self.assertTrue(role.skip)
        self.assertEqual(role.skip_reason, "inactive_product")
        self.assertEqual(role.product_id, "magiya")

    def test_link_file_in_lesson_folder(self) -> None:
        role = _role(
            "/Аватар/МБТ/Курс/Модуль 2/Урок 2.4. Деньги/ссылки.txt"
        )
        self.assertTrue(role.link_list)
        self.assertEqual(role.kind, "lesson_video")
        self.assertEqual(role.lesson_key, "2.4")
        self.assertEqual(role.module_no, 2)
        self.assertFalse(role.needs_lesson_confirm)

    def test_link_file_module_only(self) -> None:
        role = _role("/Аватар/МБТ/Курс/Модуль 2/запись.txt")
        self.assertTrue(role.link_list)
        self.assertEqual(role.kind, "lesson_video")
        self.assertEqual(role.lesson_key, "")
        self.assertEqual(role.module_no, 2)
        self.assertFalse(role.needs_lesson_confirm)

    def test_zoom_link_file_in_practices(self) -> None:
        role = _role("/Аватар/МБТ/Практики/zoom.txt")
        self.assertTrue(role.link_list)
        self.assertEqual(role.kind, "practice")
        self.assertTrue(role.needs_lesson_confirm)


if __name__ == "__main__":
    unittest.main()
