from __future__ import annotations

import json
import socket
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from compat_server import ServerConfig, create_server, validate_config


class ServerConfigTests(unittest.TestCase):
    def test_accepts_ipv4_and_ipv6_loopback(self) -> None:
        validate_config(ServerConfig(host="127.0.0.1", port=42121))
        validate_config(ServerConfig(host="::1", port=42121))

    def test_rejects_non_loopback_and_hostnames(self) -> None:
        for host in ("0.0.0.0", "192.0.2.10", "localhost"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_config(ServerConfig(host=host, port=42121))

    def test_ipv6_loopback_server_can_bind_when_available(self) -> None:
        try:
            server = create_server(ServerConfig(host="::1", port=0))
        except OSError as error:
            self.skipTest(f"IPv6 loopback is unavailable: {error}")
        self.assertEqual(server.address_family, socket.AF_INET6)
        server.server_close()


class CompatibilityServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(ServerConfig(host="127.0.0.1", port=0))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get_json(self, path: str) -> tuple[int, dict[str, object], dict[str, str]]:
        with urlopen(f"{self.base_url}{path}", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload, dict(response.headers.items())

    def test_health_endpoint(self) -> None:
        status, payload, headers = self.get_json("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_capabilities_are_explicitly_non_emulating(self) -> None:
        status, payload, _ = self.get_json("/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(payload["network_scope"], "loopback-only")
        self.assertEqual(payload["fut_protocol"], "not-yet-implemented")

    def test_unknown_route_does_not_echo_path(self) -> None:
        secret_path = "/unknown/account-like-value"
        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}{secret_path}", timeout=2)
        self.assertEqual(context.exception.code, 404)
        payload = json.loads(context.exception.read().decode("utf-8"))
        self.assertEqual(payload, {"error": "route_not_implemented"})
        self.assertNotIn("account-like-value", json.dumps(payload))

    def test_post_is_disabled_and_does_not_echo_body(self) -> None:
        request = Request(
            f"{self.base_url}/future-protocol",
            data=b"sensitive-placeholder",
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2)
        self.assertEqual(context.exception.code, 501)
        payload = json.loads(context.exception.read().decode("utf-8"))
        self.assertEqual(payload, {"error": "protocol_not_yet_implemented"})
        self.assertNotIn("sensitive-placeholder", json.dumps(payload))

    def test_partial_post_body_does_not_pin_a_request_thread(self) -> None:
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2) as client:
            client.settimeout(2)
            client.sendall(
                b"POST /future-protocol HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 1048576\r\n"
                b"Connection: close\r\n\r\n"
            )
            response = client.recv(4096)
        self.assertIn(b" 501 ", response)

    def test_unsupported_method_includes_allow_header(self) -> None:
        request = Request(f"{self.base_url}/healthz", method="PUT")
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2)
        self.assertEqual(context.exception.code, 405)
        self.assertEqual(context.exception.headers["Allow"], "GET, POST")


if __name__ == "__main__":
    unittest.main()
