from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HIGH_SIGNAL_CATEGORIES = {
    "export",
    "module",
    "config",
    "route",
    "factory_or_service",
    "guid",
}


def build_summary(report: dict[str, Any], max_values: int = 25) -> dict[str, Any]:
    if max_values <= 0:
        raise ValueError("max_values must be positive")

    category_counts = Counter(
        str(identifier.get("category", "unknown"))
        for identifier in report.get("identifiers", [])
    )
    identifier_targets: defaultdict[str, set[str]] = defaultdict(set)
    target_summaries: list[dict[str, Any]] = []

    for target in report.get("targets_with_hits", []):
        file_name = str(target.get("file", "unknown"))
        hits = target.get("hits", [])
        unique_hits: dict[tuple[str, str], None] = {}
        target_category_counts: Counter[str] = Counter()
        high_signal: list[dict[str, str]] = []

        for hit in hits:
            identifier = str(hit.get("identifier", ""))
            category = str(hit.get("category", "unknown"))
            if not identifier:
                continue
            unique_hits[(category, identifier)] = None
            identifier_targets[identifier].add(file_name)

        for category, identifier in sorted(
            unique_hits,
            key=lambda item: (item[0], item[1].casefold()),
        ):
            target_category_counts[category] += 1
            if category in HIGH_SIGNAL_CATEGORIES and len(high_signal) < max_values:
                high_signal.append({"category": category, "identifier": identifier})

        target_summaries.append(
            {
                "file": file_name,
                "hit_count": len(hits),
                "unique_identifier_count": len(unique_hits),
                "category_counts": dict(sorted(target_category_counts.items())),
                "high_signal_identifiers": high_signal,
            }
        )

    shared = [
        {"identifier": identifier, "target_count": len(targets), "targets": sorted(targets)}
        for identifier, targets in identifier_targets.items()
        if len(targets) > 1
    ]
    shared.sort(key=lambda item: (-item["target_count"], item["identifier"].casefold()))

    source = report.get("source", {})
    return {
        "schema_version": report.get("schema_version"),
        "source": {
            "file": source.get("file"),
            "size": source.get("size"),
            "sha256": source.get("sha256"),
        },
        "identifier_count": report.get("identifier_count", 0),
        "identifier_category_counts": dict(sorted(category_counts.items())),
        "target_count": report.get("target_count", 0),
        "targets_with_hits": target_summaries,
        "shared_identifiers": shared[:max_values],
    }


def find_hits(report: dict[str, Any], identifiers: set[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for target in report.get("targets_with_hits", []):
        for hit in target.get("hits", []):
            if str(hit.get("identifier", "")) not in identifiers:
                continue
            matches.append(
                {
                    "file": target.get("file"),
                    **hit,
                }
            )
    return matches


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a CardsDLL component-map report.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--max-values", type=int, default=25)
    parser.add_argument(
        "--identifier",
        action="append",
        default=[],
        help="show exact locations for this identifier; may be repeated",
    )
    args = parser.parse_args()

    if not args.report.is_file():
        parser.error(f"report does not exist: {args.report}")
    if args.max_values <= 0:
        parser.error("max values must be positive")

    report = json.loads(args.report.read_text(encoding="utf-8"))
    output = (
        {"matches": find_hits(report, set(args.identifier))}
        if args.identifier
        else build_summary(report, args.max_values)
    )
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
