"""Сырая расшифровка урока в текст."""

from __future__ import annotations

import unittest

from course.disk_layout import is_ignored_name
from course.speech import SpeechSegment
from course.transcript_disk import format_raw_transcript, transcript_filename
from course.paths import extracted_plain_text


class FormatTranscriptTests(unittest.TestCase):
    def test_timestamps_and_header(self):
        text = format_raw_transcript(
            lesson_key="1.1",
            title="Взрослая позиция",
            url="https://vimeo.com/836305758",
            method="whisper",
            segments=[
                SpeechSegment(start_sec=0, end_sec=3, text="Привет"),
                SpeechSegment(start_sec=75, end_sec=80, text="Дальше"),
            ],
        )
        self.assertIn("Урок 1.1 Взрослая позиция", text)
        self.assertIn("Источник: https://vimeo.com/836305758", text)
        self.assertIn("[00:00] Привет", text)
        self.assertIn("[01:15] Дальше", text)

    def test_no_lesson_prefix_without_key(self):
        text = format_raw_transcript(
            title="Про МБТ.mp4",
            method="whisper",
            segments=[SpeechSegment(start_sec=0, end_sec=2, text="Про продукт")],
        )
        self.assertIn("Про МБТ.mp4", text)
        self.assertNotIn("Урок", text)


class TranscriptFilenameTests(unittest.TestCase):
    def test_from_disk_audio_stem(self) -> None:
        name = transcript_filename(
            {
                "disk_path": "/Аватар/МБТ/Практики/ Зум 4 с МБТ.m4a",
                "title": " Зум 4 с МБТ.m4a",
            }
        )
        self.assertEqual(name, "_Расшифровка Зум 4 с МБТ.txt")
        self.assertTrue(is_ignored_name(name))

    def test_unique_per_file_in_same_folder(self) -> None:
        a = transcript_filename({"disk_path": "/Аватар/МБТ/Практики/Зум 3.m4a"})
        b = transcript_filename({"disk_path": "/Аватар/МБТ/Практики/Зум 4.m4a"})
        self.assertEqual(a, "_Расшифровка Зум 3.txt")
        self.assertEqual(b, "_Расшифровка Зум 4.txt")
        self.assertNotEqual(a, b)

    def test_falls_back_to_title(self) -> None:
        name = transcript_filename({"title": "Zoom запись"}, title="")
        self.assertEqual(name, "_Расшифровка Zoom запись.txt")


class ExtractedPlainTextTests(unittest.TestCase):
    def test_prefers_pages_then_transcript(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw)
            (dest / "transcript.json").write_text(
                json.dumps({"segments": [{"text": "из видео"}]}),
                encoding="utf-8",
            )
            self.assertEqual(extracted_plain_text(dest), "из видео")
            (dest / "pages.json").write_text(
                json.dumps([{"text": "из документа"}]),
                encoding="utf-8",
            )
            self.assertEqual(extracted_plain_text(dest), "из документа")


if __name__ == "__main__":
    unittest.main()
