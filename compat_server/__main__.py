from __future__ import annotations

import argparse
import logging

from .app import ServerConfig, create_server, validate_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Project 21 loopback-only compatibility server scaffold."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=42121)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ServerConfig(host=args.host, port=args.port)
    try:
        validate_config(config)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(config)
    host, port = server.server_address[:2]
    display_host = f"[{host}]" if ":" in host else host
    print(f"Project 21 compatibility scaffold listening on http://{display_host}:{port}")
    print("FUT protocol routes are intentionally disabled until the native routing seam is validated.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
