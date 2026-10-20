"""Read-only network checks for the running FIFA 21 local FUT service."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def matches_assertion(payload: Any, assertion: dict[str, Any]) -> bool:
    if assertion.get("type") == "mapping" and not isinstance(payload, dict):
        return False
    key = assertion.get("key")
    if key is not None:
        if not isinstance(payload, dict) or key not in payload:
            return False
        if assertion.get("nonempty") and not payload[key]:
            return False
        if "equals" in assertion and payload[key] != assertion["equals"]:
            return False
    return True


def get_json(base_url: str, path: str) -> tuple[int, Any]:
    request = Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"})
    with urlopen(request, timeout=3) as response:
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).with_name("test-fixtures") / "http_routes.json",
    )
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args()

    try:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        routes = fixture["routes"]
        base_url = args.base_url or str(fixture.get("base_url", "http://127.0.0.1:8090"))
        if not isinstance(routes, list) or not routes:
            raise ValueError("fixture must define a non-empty routes list")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"FAIL HTTP fixture: {type(error).__name__}: {error}")
        return 1

    passed = 0
    for route in routes:
        label = str(route["name"])
        path = str(route["path"])
        try:
            status, payload = get_json(base_url, path)
            ok = status == 200 and matches_assertion(payload, dict(route.get("assert", {})))
            print(f"{'PASS' if ok else 'FAIL'} {label:16} HTTP {status}")
            if ok:
                passed += 1
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            print(f"FAIL {label:16} {type(error).__name__}: {error}")

    print(f"RESULT {passed}/{len(routes)} read-only FIFA21 local FUT checks passed")
    return 0 if passed == len(routes) else 1


if __name__ == "__main__":
    raise SystemExit(main())

