from __future__ import annotations

import struct
import unittest

from tools.static_rva_xrefs import RuntimeFunction, find_rva_xrefs


class _OptionalHeader:
    ImageBase = 0x180000000


class _FileHeader:
    Machine = 0x8664


class _Section:
    VirtualAddress = 0x1000
    Characteristics = 0x20000000

    def __init__(self, data: bytes) -> None:
        self._data = data

    def get_data(self) -> bytes:
        return self._data


class _FakePE:
    OPTIONAL_HEADER = _OptionalHeader()
    FILE_HEADER = _FileHeader()

    def __init__(self, data: bytes) -> None:
        self.sections = [_Section(data)]


class StaticRvaXrefTests(unittest.TestCase):
    def test_finds_rip_relative_and_immediate_references(self) -> None:
        image_base = _OptionalHeader.ImageBase
        target_rva = 0x3000
        lea_next_rva = 0x1007
        displacement = target_rva - lea_next_rva
        data = (
            b"\x48\x8D\x05" + struct.pack("<i", displacement)
            + b"\x48\xB8" + struct.pack("<Q", image_base + target_rva)
            + b"\xC3"
        )
        pe = _FakePE(data)
        functions = [RuntimeFunction(0x1000, 0x1020, 0)]

        matches = find_rva_xrefs(pe, {target_rva}, functions, [0x1000])[
            "0x3000"
        ]

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["site_rva"], "0x1000")
        self.assertEqual(matches[0]["reference_kind"], "rip_relative_memory")
        self.assertEqual(matches[0]["function"]["begin_rva"], "0x1000")
        self.assertEqual(matches[1]["site_rva"], "0x1007")
        self.assertEqual(matches[1]["reference_kind"], "immediate")

    def test_ignores_unselected_targets(self) -> None:
        image_base = _OptionalHeader.ImageBase
        data = b"\x48\xB8" + struct.pack("<Q", image_base + 0x3000) + b"\xC3"
        pe = _FakePE(data)

        matches = find_rva_xrefs(pe, {0x4000}, [], [])

        self.assertEqual(matches["0x4000"], [])

    def test_rejects_non_amd64_pe(self) -> None:
        pe = _FakePE(b"\xC3")
        pe.FILE_HEADER = type("FileHeader", (), {"Machine": 0xAA64})()

        with self.assertRaisesRegex(ValueError, "expected AMD64"):
            find_rva_xrefs(pe, {0x3000}, [], [])


if __name__ == "__main__":
    unittest.main()
