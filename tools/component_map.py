from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pefile


ASCII_STRING = re.compile(rb"[\x20-\x7e]{4,}")
UTF16_STRING = re.compile(rb"(?<![\x20-\x7e])(?:[\x20-\x7e]\x00){4,}")
GUID = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)
CONFIG_KEY = re.compile(r"^(?:FUT|FIFA|ONLINE|UT)/[A-Za-z0-9_./%{}-]+$", re.I)
ROUTE = re.compile(r"^(?:ut|pow)/[A-Za-z0-9_./%{}?&=-]+$", re.I)
FACTORY_OR_SERVICE = re.compile(
    r"^(?:StubFifa[A-Za-z0-9_]*|FifaOnline[A-Za-z0-9_]*|"
    r"UltimateTeam[A-Za-z0-9_]*|OnlineService[A-Za-z0-9_]*|"
    r"[A-Za-z0-9_]*(?:Factory|Component|Registry|Plugin|Module|Controller)"
    r"(?:Impl|Interface|Base)?)$"
)
IDENTIFIER_SHAPE = re.compile(r"^[A-Za-z0-9_./:%{}?&=+<>@*~$-]{6,160}$")
RTTI_PREFIXES = (".?AV", ".?AU", ".?AW4", ".?AT")
TEXT_EXTENSIONS = {".cfg", ".ini", ".json", ".manifest", ".toml", ".xml", ".yaml", ".yml"}
PE_EXTENSIONS = {".dll", ".exe"}
SKIPPED_DIRECTORIES = {"__Installer"}
SYSTEM_IMPORT_PREFIXES = (
    "api-ms-win-",
    "ext-ms-win-",
    "kernel32",
    "user32",
    "advapi32",
    "ole32",
    "oleaut32",
    "ucrtbase",
    "vcruntime",
    "msvcp",
)
CATEGORY_PRIORITY = {
    "export": 0,
    "module": 1,
    "config": 2,
    "route": 3,
    "factory_or_service": 4,
    "guid": 5,
    "rtti": 6,
    "nearby": 7,
}


@dataclass(frozen=True)
class SourceString:
    value: str
    offset: int
    encoding: str


@dataclass(frozen=True)
class Identifier:
    value: str
    category: str
    source_offsets: tuple[int, ...]
    source_encodings: tuple[str, ...]


@dataclass(frozen=True)
class Hit:
    identifier: str
    category: str
    file_offset: int
    encoding: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def extract_strings(data: bytes) -> list[SourceString]:
    strings: list[SourceString] = []
    for match in ASCII_STRING.finditer(data):
        strings.append(
            SourceString(match.group().decode("ascii", errors="replace"), match.start(), "ascii")
        )
    for match in UTF16_STRING.finditer(data):
        strings.append(
            SourceString(
                match.group().decode("utf-16le", errors="replace"),
                match.start(),
                "utf-16le",
            )
        )
    return strings


def classify_string(value: str) -> str | None:
    value = value.strip()
    if not 6 <= len(value) <= 180 or "\x00" in value:
        return None
    if value.startswith(RTTI_PREFIXES) and value.endswith("@@"):
        return "rtti"
    if GUID.fullmatch(value):
        return "guid"
    if ROUTE.fullmatch(value):
        return "route"
    if CONFIG_KEY.fullmatch(value):
        return "config"
    if FACTORY_OR_SERVICE.search(value) and IDENTIFIER_SHAPE.fullmatch(value):
        return "factory_or_service"
    return None


def useful_nearby_string(value: str) -> bool:
    return bool(IDENTIFIER_SHAPE.fullmatch(value)) and not value.isdigit()


def read_pe_metadata(path: Path) -> tuple[list[str], list[str], str | None]:
    pe = pefile.PE(str(path), fast_load=False)
    exports: list[str] = []
    export_name: str | None = None
    directory = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
    if directory:
        if directory.name:
            export_name = directory.name.decode("ascii", errors="replace")
        exports = [
            symbol.name.decode("ascii", errors="replace")
            if symbol.name
            else f"ordinal:{symbol.ordinal}"
            for symbol in directory.symbols
        ]

    imports: list[str] = []
    for module in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        name = module.dll.decode("ascii", errors="replace")
        if not name.casefold().startswith(SYSTEM_IMPORT_PREFIXES):
            imports.append(name)
    return exports, sorted(set(imports), key=str.casefold), export_name


def build_identifier_set(
    source: Path,
    nearby_radius: int,
    max_identifiers: int,
) -> tuple[list[Identifier], dict[str, object]]:
    raw = source.read_bytes()
    strings = extract_strings(raw)
    records: dict[str, dict[str, object]] = {}

    def add(value: str, category: str, offset: int = -1, encoding: str = "metadata") -> None:
        value = value.strip()
        if not value:
            return
        record = records.setdefault(
            value,
            {"category": category, "offsets": set(), "encodings": set()},
        )
        old_priority = CATEGORY_PRIORITY.get(str(record["category"]), 99)
        new_priority = CATEGORY_PRIORITY.get(category, 99)
        if new_priority < old_priority:
            record["category"] = category
        if offset >= 0:
            record["offsets"].add(offset)  # type: ignore[union-attr]
        record["encodings"].add(encoding)  # type: ignore[union-attr]

    anchors: list[SourceString] = []
    for item in strings:
        category = classify_string(item.value)
        if category:
            add(item.value, category, item.offset, item.encoding)
            if category != "rtti":
                anchors.append(item)

    exports, imports, export_name = read_pe_metadata(source)
    for value in exports:
        add(value, "export")
    add(source.stem, "module")
    add("CardsDLL", "module")
    if export_name:
        add(export_name, "module")

    for item in strings:
        if not useful_nearby_string(item.value):
            continue
        if any(abs(item.offset - anchor.offset) <= nearby_radius for anchor in anchors):
            add(item.value, "nearby", item.offset, item.encoding)

    identifiers = [
        Identifier(
            value=value,
            category=str(record["category"]),
            source_offsets=tuple(sorted(record["offsets"])),  # type: ignore[arg-type]
            source_encodings=tuple(sorted(record["encodings"])),  # type: ignore[arg-type]
        )
        for value, record in records.items()
    ]
    identifiers.sort(
        key=lambda item: (
            CATEGORY_PRIORITY.get(item.category, 99),
            item.value.casefold(),
        )
    )
    if len(identifiers) > max_identifiers:
        identifiers = identifiers[:max_identifiers]

    metadata = {
        "file": source.name,
        "size": len(raw),
        "sha256": sha256(source),
        "pe_exports": exports,
        "non_system_imports": imports,
        "export_directory_name": export_name,
    }
    return identifiers, metadata


def compile_patterns(identifiers: Iterable[Identifier]) -> tuple[re.Pattern[bytes], re.Pattern[bytes], dict[bytes, Identifier], dict[bytes, Identifier]]:
    ascii_map: dict[bytes, Identifier] = {}
    utf16_map: dict[bytes, Identifier] = {}
    for identifier in identifiers:
        try:
            encoded = identifier.value.encode("ascii")
        except UnicodeEncodeError:
            continue
        ascii_map[encoded] = identifier
        utf16_map[identifier.value.encode("utf-16le")] = identifier

    def compile_map(values: dict[bytes, Identifier]) -> re.Pattern[bytes]:
        alternatives = sorted(values, key=len, reverse=True)
        if not alternatives:
            return re.compile(rb"(?!x)x")
        return re.compile(b"|".join(re.escape(value) for value in alternatives))

    return compile_map(ascii_map), compile_map(utf16_map), ascii_map, utf16_map


def scan_file(
    path: Path,
    identifiers: Iterable[Identifier],
    max_hits_per_identifier: int,
) -> list[Hit]:
    ascii_pattern, utf16_pattern, ascii_map, utf16_map = compile_patterns(identifiers)
    counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    hits: list[Hit] = []

    with path.open("rb") as source:
        if source.seek(0, 2) == 0:
            return hits
        source.seek(0)
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as view:
            for pattern, mapping, encoding in (
                (ascii_pattern, ascii_map, "ascii"),
                (utf16_pattern, utf16_map, "utf-16le"),
            ):
                for match in pattern.finditer(view):
                    identifier = mapping.get(match.group())
                    if identifier is None:
                        continue
                    key = (identifier.value, encoding)
                    if counts[key] >= max_hits_per_identifier:
                        continue
                    counts[key] += 1
                    hits.append(
                        Hit(
                            identifier=identifier.value,
                            category=identifier.category,
                            file_offset=match.start(),
                            encoding=encoding,
                        )
                    )
    hits.sort(key=lambda item: (item.file_offset, item.identifier.casefold()))
    return hits


def render_hits(path: Path, hits: Iterable[Hit]) -> tuple[list[dict[str, object]], dict[str, str]]:
    pe: pefile.PE | None = None
    image_metadata: dict[str, str] = {}
    if path.suffix.casefold() in PE_EXTENSIONS:
        try:
            pe = pefile.PE(str(path), fast_load=True)
            image_metadata = {"image_base": f"0x{pe.OPTIONAL_HEADER.ImageBase:X}"}
        except pefile.PEFormatError:
            pe = None

    rendered: list[dict[str, object]] = []
    for hit in hits:
        item: dict[str, object] = {
            **asdict(hit),
            "file_offset": f"0x{hit.file_offset:X}",
        }
        if pe is not None:
            try:
                rva = pe.get_rva_from_offset(hit.file_offset)
            except pefile.PEFormatError:
                rva = None
            if rva is not None:
                item["rva"] = f"0x{rva:X}"
                item["preferred_va"] = f"0x{pe.OPTIONAL_HEADER.ImageBase + rva:X}"
        rendered.append(item)
    if pe is not None:
        pe.close()
    return rendered, image_metadata


def default_targets(game_root: Path, source: Path) -> list[Path]:
    targets: set[Path] = set()
    for path in game_root.iterdir():
        if path.is_file() and path.suffix.casefold() in PE_EXTENSIONS and path != source:
            targets.add(path)
    for path in game_root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in TEXT_EXTENSIONS:
            continue
        if any(part in SKIPPED_DIRECTORIES for part in path.parts):
            continue
        targets.add(path)
    return sorted(targets, key=lambda path: str(path).casefold())


def display_path(path: Path, game_root: Path) -> str:
    try:
        return str(path.relative_to(game_root)).replace("\\", "/")
    except ValueError:
        return path.name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an indirect CardsDLL component map without copying game content."
    )
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--nearby-radius", type=lambda value: int(value, 0), default=0x200)
    parser.add_argument("--max-identifiers", type=int, default=500)
    parser.add_argument("--max-hits-per-identifier", type=int, default=8)
    parser.add_argument(
        "--high-signal-only",
        action="store_true",
        help="scan only exports, module names, config keys, routes, factories/services, and GUIDs",
    )
    args = parser.parse_args()

    game_root = args.game_root.resolve()
    source = (args.source or game_root / "CardsDLL_Win64_retail.dll").resolve()
    if not game_root.is_dir():
        parser.error(f"game root does not exist: {game_root}")
    if not source.is_file():
        parser.error(f"source DLL does not exist: {source}")
    if args.nearby_radius < 0 or args.max_identifiers <= 0:
        parser.error("nearby radius must be non-negative and max identifiers must be positive")
    if args.max_hits_per_identifier <= 0:
        parser.error("max hits per identifier must be positive")

    targets = [path.resolve() for path in args.target] or default_targets(game_root, source)
    missing = [path for path in targets if not path.is_file()]
    if missing:
        parser.error("missing targets: " + ", ".join(str(path) for path in missing))

    identifiers, source_metadata = build_identifier_set(
        source, args.nearby_radius, args.max_identifiers
    )
    if args.high_signal_only:
        identifiers = [
            identifier
            for identifier in identifiers
            if identifier.category not in {"nearby", "rtti"}
        ]
    target_results: list[dict[str, object]] = []
    for target in targets:
        hits = scan_file(target, identifiers, args.max_hits_per_identifier)
        if not hits:
            continue
        rendered_hits, image_metadata = render_hits(target, hits)
        target_results.append(
            {
                "file": display_path(target, game_root),
                "size": target.stat().st_size,
                "sha256": sha256(target),
                **image_metadata,
                "hits": rendered_hits,
            }
        )

    result = {
        "schema_version": 1,
        "source": source_metadata,
        "settings": {
            "nearby_radius": args.nearby_radius,
            "max_identifiers": args.max_identifiers,
            "max_hits_per_identifier": args.max_hits_per_identifier,
            "high_signal_only": args.high_signal_only,
        },
        "identifier_count": len(identifiers),
        "identifiers": [
            {
                **asdict(identifier),
                "source_offsets": [f"0x{offset:X}" for offset in identifier.source_offsets],
            }
            for identifier in identifiers
        ],
        "target_count": len(targets),
        "targets_with_hits": target_results,
    }
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "identifier_count": len(identifiers),
                    "target_count": len(targets),
                    "targets_with_hits": len(target_results),
                }
            )
        )
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
