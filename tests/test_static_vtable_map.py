from __future__ import annotations

import struct
import unittest

from tools.static_vtable_map import RuntimeFunction, map_vtable


class _OptionalHeader:
    ImageBase = 0x180000000
    SizeOfImage = 0x10000


class _Section:
    VirtualAddress = 0x5000
    PointerToRawData = 0

    def __init__(self, raw_size: int) -> None:
        self.SizeOfRawData = raw_size


class _FakePE:
    OPTIONAL_HEADER = _OptionalHeader()

    def __init__(self, values: list[int], raw_size: int | None = None) -> None:
        self.__data__ = b"".join(struct.pack("<Q", value) for value in values)
        self.sections = [
            _Section(len(self.__data__) if raw_size is None else raw_size)
        ]


class StaticVtableMapTests(unittest.TestCase):
    def test_resolves_image_pointer_and_function_bounds(self) -> None:
        image_base = _OptionalHeader.ImageBase
        pe = _FakePE([image_base + 0x1010, 0, image_base + 0x3000])
        functions = [RuntimeFunction(0x1000, 0x1100, 0x7000)]

        entries = map_vtable(pe, 0x5000, 3, functions, [0x1000])

        self.assertEqual(entries[0]["slot_offset"], "0x0")
        self.assertEqual(entries[0]["target_rva"], "0x1010")
        self.assertEqual(entries[0]["function"]["begin_rva"], "0x1000")
        self.assertIsNone(entries[1]["target_rva"])
        self.assertEqual(entries[2]["target_rva"], "0x3000")
        self.assertIsNone(entries[2]["function"])

    def test_rejects_range_beyond_raw_section(self) -> None:
        pe = _FakePE([_OptionalHeader.ImageBase + 0x1010])

        with self.assertRaisesRegex(ValueError, "not fully backed"):
            map_vtable(pe, 0x5000, 2, [], [])

    def test_rejects_virtual_padding_despite_following_file_bytes(self) -> None:
        image_base = _OptionalHeader.ImageBase
        pe = _FakePE([image_base + 0x1010, image_base + 0x1020], raw_size=8)

        with self.assertRaisesRegex(ValueError, "not fully backed"):
            map_vtable(pe, 0x5000, 2, [], [])

    def test_rejects_unmapped_rva(self) -> None:
        pe = _FakePE([_OptionalHeader.ImageBase + 0x1010])

        with self.assertRaisesRegex(ValueError, "not fully backed"):
            map_vtable(pe, 0x6000, 1, [], [])


if __name__ == "__main__":
    unittest.main()
