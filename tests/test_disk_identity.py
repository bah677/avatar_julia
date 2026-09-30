"""Отпечаток файла на Диске: не перечитывать из‑за смены одного etag."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from course.disk_identity import content_changed, file_fingerprint, normalize_etag


class DiskIdentityTests(unittest.TestCase):
    def test_normalize_quotes_and_weak(self):
        self.assertEqual(normalize_etag('"ABC"'), "abc")
        self.assertEqual(normalize_etag('W/"abc"'), "abc")

    def test_same_etag_not_changed(self):
        self.assertFalse(
            content_changed("abc", size=10, etag="ABC")
        )

    def test_etag_drift_without_size_is_not_a_change(self):
        self.assertFalse(
            content_changed("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", size=100, etag="bbbb")
        )

    def test_size_change_is_real(self):
        fp = file_fingerprint(size=100, modified=datetime(2026, 1, 1, tzinfo=timezone.utc), etag="aaa")
        self.assertTrue(
            content_changed(
                fp,
                size=200,
                modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
                etag="aaa",
            )
        )

    def test_same_size_and_mtime_ignores_etag(self):
        mt = datetime(2026, 9, 1, tzinfo=timezone.utc)
        fp = file_fingerprint(size=50, modified=mt, etag="old")
        self.assertFalse(
            content_changed(fp, size=50, modified=mt, etag="new")
        )

    def test_mtime_jump_same_size_is_not_a_change(self):
        fp = file_fingerprint(
            size=50,
            modified=datetime(2026, 9, 1, tzinfo=timezone.utc),
            etag="old",
        )
        self.assertFalse(
            content_changed(
                fp,
                size=50,
                modified=datetime(2026, 9, 2, tzinfo=timezone.utc),
                etag="new",
            )
        )


if __name__ == "__main__":
    unittest.main()
