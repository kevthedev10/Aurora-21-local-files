"""Read-only synthetic Blaze/Fire2 checks for the local FIFA 21 service."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket

import server


def receive_packet(sock: socket.socket, component: int, command: int) -> server.Fire2Packet:
    buffer = bytearray()
    sock.settimeout(3.0)
    while True:
        chunk = sock.recv(65535)
        if not chunk:
            raise ConnectionError("Blaze closed the synthetic session")
        buffer.extend(chunk)
        while True:
            packet = server._fire2_try_parse(buffer)
            if packet is None:
                break
            if packet.component == component and packet.command == command and packet.msg_type == 1:
                return packet


def check_frame(
    sock: socket.socket,
    label: str,
    component: int,
    command: int,
    payload: bytes = b"",
    expected_tags: tuple[tuple[str, int], ...] = (),
) -> None:
    sock.sendall(server._fire2_build(component, command, 1, 0, payload))
    reply = receive_packet(sock, component, command)
    if reply.msg_type != 1:
        raise AssertionError(f"{label}: expected reply type 1, got {reply.msg_type}")
    missing = [
        tag
        for tag, value_type in expected_tags
        if server._tdf_tag(tag, value_type) not in reply.payload
    ]
    if missing:
        raise AssertionError(f"{label}: missing reply markers {', '.join(missing)}")
    print(f"PASS {label:20} c={reply.component} k={reply.command} payload={len(reply.payload)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).with_name("test-fixtures") / "blaze_flow.json",
    )
    args = parser.parse_args()

    try:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        if fixture.get("protocol") != "fire2" or not fixture.get("stages"):
            raise ValueError("fixture must define protocol=fire2 and at least one stage")
        host = str(fixture.get("host", "127.0.0.1"))
        port = int(fixture.get("port", 44321))
        type_ids = {
            "varint": server._TDF_VARINT,
            "string": server._TDF_STRING,
            "group": server._TDF_GROUP,
            "list": server._TDF_LIST,
            "map": server._TDF_MAP,
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(f"FAIL Blaze fixture: {type(error).__name__}: {error}")
        return 1

    try:
        with socket.create_connection((host, port), timeout=3) as sock:
            for stage in fixture["stages"]:
                expected_tags = tuple(
                    (str(tag), type_ids[str(value_type)])
                    for tag, value_type in stage.get("expected_tags", [])
                )
                check_frame(
                    sock,
                    str(stage["name"]),
                    int(stage["component"]),
                    int(stage["command"]),
                    expected_tags=expected_tags,
                )
    except (AssertionError, ConnectionError, OSError, TimeoutError) as error:
        print(f"FAIL synthetic Blaze flow: {type(error).__name__}: {error}")
        return 1

    print("RESULT synthetic Blaze/Fire2 flow passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

