from __future__ import annotations

import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlsplit


LOGGER = logging.getLogger("project21.compat_server")
REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 42121


def validate_config(config: ServerConfig) -> None:
    try:
        address = ipaddress.ip_address(config.host)
    except ValueError as error:
        raise ValueError("host must be a literal loopback IP address") from error
    if not address.is_loopback:
        raise ValueError("compatibility server must bind to a loopback address")
    if not 0 <= config.port <= 65535:
        raise ValueError("port must be between 0 and 65535")


class CompatibilityHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class CompatibilityHTTPServerV6(CompatibilityHTTPServer):
    address_family = socket.AF_INET6


class CompatibilityRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Project21Compat/0.1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/v1/capabilities":
            self._send_json(
                HTTPStatus.OK,
                {
                    "service": "project-21-local-compatibility",
                    "network_scope": "loopback-only",
                    "fut_protocol": "not-yet-implemented",
                },
            )
            return
        self._send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "route_not_implemented"},
        )

    def do_POST(self) -> None:
        self._send_json(
            HTTPStatus.NOT_IMPLEMENTED,
            {"error": "protocol_not_yet_implemented"},
        )

    def do_PUT(self) -> None:
        self._reject_unsupported_method()

    def do_PATCH(self) -> None:
        self._reject_unsupported_method()

    def do_DELETE(self) -> None:
        self._reject_unsupported_method()

    def _reject_unsupported_method(self) -> None:
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "method_not_allowed"},
            headers={"Allow": "GET, POST"},
        )

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        status = args[1] if len(args) > 1 else "-"
        LOGGER.info("request client=%s status=%s", self.client_address[0], status)


def create_server(config: ServerConfig) -> CompatibilityHTTPServer:
    validate_config(config)
    address = ipaddress.ip_address(config.host)
    server_type = (
        CompatibilityHTTPServerV6 if address.version == 6 else CompatibilityHTTPServer
    )
    return server_type(
        (config.host, config.port),
        CompatibilityRequestHandler,
    )
