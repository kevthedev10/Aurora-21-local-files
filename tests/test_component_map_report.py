from __future__ import annotations

import unittest

from tools.component_map_report import build_summary, find_hits


class ComponentMapReportTests(unittest.TestCase):
    def test_summary_prioritizes_high_signal_and_tracks_shared_identifiers(self) -> None:
        report = {
            "schema_version": 1,
            "source": {"file": "CardsDLL.dll", "size": 10, "sha256": "ABC"},
            "identifier_count": 3,
            "identifiers": [
                {"identifier": "CardsDLL", "category": "module"},
                {"identifier": "ut/auth", "category": "route"},
                {"identifier": ".?AVThing@@", "category": "rtti"},
            ],
            "target_count": 2,
            "targets_with_hits": [
                {
                    "file": "one.dll",
                    "hits": [
                        {"identifier": "CardsDLL", "category": "module"},
                        {"identifier": "ut/auth", "category": "route"},
                        {"identifier": "ut/auth", "category": "route"},
                    ],
                },
                {
                    "file": "two.dll",
                    "hits": [{"identifier": "CardsDLL", "category": "module"}],
                },
            ],
        }

        summary = build_summary(report, max_values=10)

        self.assertEqual(summary["identifier_category_counts"], {"module": 1, "route": 1, "rtti": 1})
        self.assertEqual(summary["targets_with_hits"][0]["unique_identifier_count"], 2)
        self.assertEqual(
            summary["targets_with_hits"][0]["high_signal_identifiers"],
            [
                {"category": "module", "identifier": "CardsDLL"},
                {"category": "route", "identifier": "ut/auth"},
            ],
        )
        self.assertEqual(summary["shared_identifiers"][0]["identifier"], "CardsDLL")
        self.assertEqual(summary["shared_identifiers"][0]["target_count"], 2)

    def test_rejects_non_positive_limit(self) -> None:
        with self.assertRaises(ValueError):
            build_summary({}, max_values=0)

    def test_find_hits_includes_target_file(self) -> None:
        report = {
            "targets_with_hits": [
                {
                    "file": "FIFA21.exe",
                    "hits": [
                        {"identifier": "CardsDLL", "file_offset": "0x10", "rva": "0x20"},
                        {"identifier": "ut/online", "file_offset": "0x30", "rva": "0x40"},
                    ],
                }
            ]
        }

        self.assertEqual(
            find_hits(report, {"CardsDLL"}),
            [
                {
                    "file": "FIFA21.exe",
                    "identifier": "CardsDLL",
                    "file_offset": "0x10",
                    "rva": "0x20",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
