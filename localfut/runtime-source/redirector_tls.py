from __future__ import annotations
import http.server
import os
import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CERT = ROOT / "certs" / "spring18-cert.pem"
KEY = ROOT / "certs" / "spring18-key.pem"
PORT = int(os.environ.get("LOCALFUT_REDIRECTOR_PORT", "42230"))
BLAZE_PORT = 44321

XML = ("""<?xml version="1.0" encoding="UTF-8"?>
<serverinstanceinfo>
  <address member="0">
    <valu>
      <hostname>127.0.0.1</hostname>
      <ip>2130706433</ip>
      <port>{port}</port>
    </valu>
  </address>
  <secure>0</secure>
</serverinstanceinfo>""".format(port=BLAZE_PORT)).encode("utf-8")

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[REDIRECTOR]", self.address_string(), fmt % args, flush=True)

    def reply(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(n) if n else b""
        print("[REDIRECTOR]", self.command, self.path, "body", body[:300], flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(XML)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(XML)

    do_GET = reply
    do_POST = reply


class TLSHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, request_handler, ssl_context):
        self.ssl_context = ssl_context
        super().__init__(server_address, request_handler)

    def get_request(self):
        client_socket, client_address = self.socket.accept()
        print(f"[TLS] TCP client: {client_address[0]}:{client_address[1]}", flush=True)
        try:
            tls_socket = self.ssl_context.wrap_socket(client_socket, server_side=True)
            print(
                f"[TLS] Negotiated: {tls_socket.version()} {tls_socket.cipher()[0]}",
                flush=True,
            )
            return tls_socket, client_address
        except Exception as exc:
            print(f"[TLS] Handshake failed: {type(exc).__name__}: {exc}", flush=True)
            client_socket.close()
            raise


def main():
    if not CERT.exists() or not KEY.exists():
        print("Missing certs. Run GENERATE_LOCAL_CERT.cmd first.")
        raise SystemExit(2)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    except ssl.SSLError:
        ctx.set_ciphers("DEFAULT")
    # DirtySDK's embedded trust store validates the issuer itself after the
    # launcher swaps the pinned CA modulus. Sending our self-signed replacement
    # root in the server chain makes FIFA 21 reject the handshake with
    # certificate_unknown. Match the working legacy redirector shape and send
    # the leaf only; the client supplies the patched trust anchor.
    cert_bundle = CERT
    ctx.load_cert_chain(str(cert_bundle), str(KEY))

    def log_sni(_ssl_sock, server_name, _ssl_context):
        print(f"[TLS] Client SNI: {server_name or '<none>'}", flush=True)

    ctx.set_servername_callback(log_sni)
    httpd = TLSHTTPServer(("0.0.0.0", PORT), Handler, ctx)

    print(f"FIFA 21 TLS redirector listening on 0.0.0.0:{PORT}", flush=True)
    print("SNI target: spring18.gosredirector.ea.com", flush=True)
    print(f"Certificate bundle: {cert_bundle.name}", flush=True)
    print("Certificate issuer: local GOS 2015 Certificate Authority bridge", flush=True)
    print(f"Returning local Blaze endpoint 127.0.0.1:{BLAZE_PORT}", flush=True)
    httpd.serve_forever()

if __name__ == "__main__":
    main()
