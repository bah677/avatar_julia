"""Нарезка расшифровки для майнинга: без обрезки реплик, квота участников."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from course.mining import (
    _merge_cards,
    duration_from_segments,
    mining_chunks_from_segments,
)
from course.speech import SpeechSegment, segments_from_dicts
from openai_client.content_prompts import mining_system


def _seg(start: float, text: str, *, dur: float = 2.0) -> SpeechSegment:
    return SpeechSegment(start_sec=start, end_sec=start + dur, text=text)


class MiningChunksTests(unittest.TestCase):
    def test_continuous_speech_keeps_almost_all_chars(self) -> None:
        segs = [
            _seg(i * 3.0, f"реплика номер {i} " + ("слово " * 20))
            for i in range(400)
        ]
        full = sum(len(s.text) for s in segs)
        chunks = mining_chunks_from_segments(segs)
        mine = sum(len(c) for c in chunks)
        self.assertGreater(len(chunks), 1)
        self.assertGreaterEqual(mine / full, 0.95)

    def test_window_splits_long_session(self) -> None:
        segs = [_seg(float(i * 30), "коротко") for i in range(80)]
        chunks = mining_chunks_from_segments(segs, window_sec=600.0, max_chars=50_000)
        self.assertGreaterEqual(len(chunks), 3)

    def test_duration_from_last_segment(self) -> None:
        segs = [_seg(0, "a", dur=10), _seg(7100, "b", dur=20)]
        self.assertEqual(duration_from_segments(segs), 7120)


class MergeParticipantQuotaTests(unittest.TestCase):
    def test_keeps_participant_cards_even_if_lower_score(self) -> None:
        pool = [
            {"title": f"эксперт тема {i:02d}", "text": "t", "speaker": "expert", "score": 90, "anchor_sec": i}
            for i in range(20)
        ] + [
            {
                "title": f"участница боль {i:02d}",
                "text": "боль",
                "speaker": "participant",
                "score": 40,
                "anchor_sec": 100 + i,
            }
            for i in range(10)
        ]
        out = _merge_cards(pool, top_n=24, min_participant=8)
        n_p = sum(1 for c in out if c["speaker"] == "participant")
        self.assertGreaterEqual(n_p, 8)
        self.assertEqual(len(out), 24)


class PracticePromptTests(unittest.TestCase):
    def test_practice_prompt_values_participants(self) -> None:
        text = mining_system("Юлия", "practice")
        self.assertIn("speaker=participant", text)
        self.assertIn("не менее ценны", text.casefold())


class Zoom4CoverageTests(unittest.TestCase):
    def test_zoom4_transcript_not_truncated(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "course"
            / "124dc585-369f-4cd7-838f-2b69c644be4b"
            / "transcript.json"
        )
        if not path.is_file():
            self.skipTest("нет расшифровки Зум 4")
        data = json.loads(path.read_text(encoding="utf-8"))
        segs = segments_from_dicts(data.get("segments") or [])
        full = sum(len(s.text) for s in segs)
        chunks = mining_chunks_from_segments(segs)
        mine = sum(len(c) for c in chunks)
        self.assertGreater(full, 50_000)
        self.assertGreaterEqual(mine / full, 0.95)
        self.assertGreaterEqual(len(chunks), 5)


if __name__ == "__main__":
    unittest.main()
