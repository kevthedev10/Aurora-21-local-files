from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.component_map import Identifier, classify_string, extract_strings, scan_file


class ComponentMapTests(unittest.TestCase):
    def test_classification(self) -> None:
        self.assertEqual(classify_string("FUT/SINGLE_BASEURL_%s"), "config")
        self.assertEqual(classify_string("ut/auth"), "route")
        self.assertEqual(classify_string("StubFifaOnline"), "factory_or_service")
        self.assertEqual(classify_string("OnlineComponentFactory"), "factory_or_service")
        self.assertEqual(classify_string(".?AVOnlineComponent@@"), "rtti")
        self.assertIsNone(classify_string("GetModuleHandleW"))
        self.assertIsNone(classify_string("ordinary sentence with spaces"))

    def test_extracts_ascii_and_utf16(self) -> None:
        data = b"xxStubFifaOnline\x00" + "ut/auth".encode("utf-16le") + b"\x00\x00"
        values = {(item.value, item.encoding) for item in extract_strings(data)}
        self.assertIn(("xxStubFifaOnline", "ascii"), values)
        self.assertIn(("ut/auth", "utf-16le"), values)

    def test_scan_file_reports_exact_offsets(self) -> None:
        identifiers = [
            Identifier("StubFifaOnline", "factory_or_service", (), ()),
            Identifier("ut/auth", "route", (), ()),
        ]
        payload = b"AAAAStubFifaOnline\x00BBBB" + "ut/auth".encode("utf-16le")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(payload)
            hits = scan_file(path, identifiers, max_hits_per_identifier=4)

        by_value = {hit.identifier: hit for hit in hits}
        self.assertEqual(by_value["StubFifaOnline"].file_offset, 4)
        self.assertEqual(by_value["StubFifaOnline"].encoding, "ascii")
        self.assertEqual(by_value["ut/auth"].encoding, "utf-16le")


if __name__ == "__main__":
    unittest.main()
