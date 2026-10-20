"""End-to-end read-only checks for the local FIFA 21 session path."""
from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import server
from verify_blaze_flow import check_frame


def get_json(base_url: str, path: str) -> Any:
    request = Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"})
    with urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {path}")
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    passed = 0
    try:
        with socket.create_connection(("127.0.0.1", 44321), timeout=3) as sock:
            check_frame(
                sock,
                "Util.PreAuth",
                9,
                7,
                expected_tags=(("CONF", server._TDF_GROUP), ("QOSS", server._TDF_GROUP)),
            )
            check_frame(sock, "Util.Ping", 9, 2, expected_tags=(("STIM", server._TDF_VARINT),))
            check_frame(sock, "Util.PostAuth", 9, 8)
            check_frame(
                sock,
                "FIFA35.LoginSession",
                35,
                10,
                expected_tags=(("DSNM", server._TDF_STRING), ("PID", server._TDF_VARINT)),
            )
        passed += 1
        print("PASS blaze-session      synthetic Fire2 sequence")

        checks = (
            ("auth", "/ut/auth", lambda value: value.get("protocol") == "http"),
            ("shards", "/ut/shards", lambda value: bool(value.get("shardInfo"))),
            ("account", "/ut/game/fifa21/user/accountinfo", lambda value: "userAccountInfo" in value),
            ("user", "/ut/game/fifa21/user", lambda value: "clubPlayers" in value),
            ("squad", "/ut/game/fifa21/squad/active", lambda value: "players" in value),
            ("club", "/ut/game/fifa21/club?count=20", lambda value: "itemData" in value),
        )
        for label, path, predicate in checks:
            value = get_json("http://127.0.0.1:8090", path)
            if not predicate(value):
                raise AssertionError(f"{label}: response shape failed")
            passed += 1
            print(f"PASS {label:20} HTTP 200")
    except (AssertionError, HTTPError, URLError, ConnectionError, OSError, TimeoutError, ValueError, RuntimeError) as error:
        print(f"FAIL local session: {type(error).__name__}: {error}")
        return 1

    print(f"RESULT local synthetic session passed ({passed}/7 stages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

