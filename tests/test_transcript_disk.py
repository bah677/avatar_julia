"""Сырая расшифровка урока в текст."""

from __future__ import annotations

import unittest

from course.speech import SpeechSegment
from course.transcript_disk import format_raw_transcript


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


if __name__ == "__main__":
    unittest.main()
