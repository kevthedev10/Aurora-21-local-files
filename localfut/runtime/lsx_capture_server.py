#!/usr/bin/env python3
"""Capture-only Origin/LSX listener for FIFA21 PC (offline-FUT RE).

Binds loopback 127.0.0.1:3216 ONLY. For each inbound connection it performs the
documented Maxima/Origin LSX handshake (server-first Challenge), records every
raw byte and every NUL-delimited frame, attempts every candidate decryption, and
writes timestamped artifacts under runtime/captures/.

It exists to record GROUND TRUTH from the exact FIFA21.exe build. It does not
fabricate application-layer responses beyond the handshake. It never binds a
non-loopback address and never makes outbound connections.

Run:  py -3 runtime/lsx_capture_server.py
"""

import argparse
import datetime as dt
import secrets
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception as exc:  # pragma: no cover
    padding = Cipher = algorithms = modes = None
    CRYPTO_ERROR = str(exc)
else:
    CRYPTO_ERROR = None

CAPTURE_ROOT = Path(__file__).resolve().parent / "captures"
CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 3216

# Challenge variants tried in order on each fresh connection. The first matches
# the current server.py LSX handler exactly; the rest are plausible EA Desktop
# alternatives kept in case the client rejects the first format.
CHALLENGE_VARIANTS = [
    {"sender": "EALS", "version": "3", "build": "10.6.1.8"},
    {"sender": "EALS", "version": "2", "build": None},
    {"sender": "Origin", "version": "3", "build": None},
    {"sender": "EbisuSDK", "version": "3", "build": None},
]

GETCONFIG_SERVICES = [
    ("EbisuSDK", "SDK"),
    ("EbisuSDK", "PROFILE"),
    ("XMPP", "PRESENCE"),
    ("XMPP", "FRIENDS"),
    ("Commerce", "COMMERCE"),
    ("EbisuSDK", "RECENTPLAYER"),
    ("EbisuSDK", "IGO"),
    ("EbisuSDK", "MISC"),
    ("EALS", "LOGIN"),
    ("EbisuSDK", "UTILITY"),
    ("XMPP", "XMPP"),
    ("XMPP", "CHAT"),
    ("EbisuSDK", "IGO_EVENT"),
    ("EALS", "EALS_EVENTS"),
    ("EbisuSDK", "LOGIN_EVENT"),
    ("XMPP", "INVITE_EVENT"),
    ("EbisuSDK", "PROFILE_EVENT"),
    ("XMPP", "PRESENCE_EVENT"),
    ("XMPP", "FRIENDS_EVENT"),
    ("Commerce", "COMMERCE_EVENT"),
    ("XMPP", "CHAT_EVENT"),
    ("EbisuSDK", "DOWNLOAD_EVENT"),
    ("EbisuSDK", "PERMISSION"),
    ("EbisuSDK", "RESOURCES"),
    ("EbisuSDK", "BLOCKED_USERS"),
    ("EbisuSDK", "BLOCKED_USER_EVENT"),
    ("EbisuSDK", "GET_USERID"),
    ("EbisuSDK", "ONLINE_STATUS_EVENT"),
    ("EbisuSDK", "ACHIEVEMENT"),
    ("EbisuSDK", "ACHIEVEMENT_EVENT"),
    ("EbisuSDK", "BROADCAST_EVENT"),
    ("PI", "PROGRESSIVE_INSTALLATION"),
    ("PI", "PROGRESSIVE_INSTALLATION_EVENT"),
    ("EbisuSDK", "CONTENT"),
]


def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def esc(v) -> str:
    import html
    return html.escape(str(v), quote=True)


class OriginRandom:
    def __init__(self, seed: int):
        self.seed = int(seed) & 0xFFFFFFFF

    def set_seed(self, seed: int) -> None:
        self.seed = int(seed) & 0xFFFFFFFF

    def next(self) -> int:
        self.seed = (self.seed * 214013 + 2531011) & 0xFFFFFFFF
        return (self.seed >> 16) & 0x7FFF


class OriginCrypto:
    def __init__(self, seed: int = 0):
        self.key = bytes(range(16))
        self.set_key(seed)

    def set_key(self, seed: int) -> None:
        seed = int(seed) & 0xFFFFFFFF
        if seed == 0:
            self.key = bytes(range(16))
            return
        rng = OriginRandom(7)
        new_seed = (rng.next() + seed) & 0xFFFFFFFF
        rng.set_seed(new_seed)
        self.key = bytes((rng.next() & 0xFF) for _ in range(16))

    @property
    def key_hex(self) -> str:
        return self.key.hex().upper()

    def encrypt_pkcs7(self, text: str) -> bytes:
        padder = padding.PKCS7(128).padder()
        raw = padder.update(text.encode("utf-8")) + padder.finalize()
        enc = Cipher(algorithms.AES(self.key), modes.ECB()).encryptor()
        return enc.update(raw) + enc.finalize()

    def encrypt_ecb(self, data: bytes) -> bytes:
        enc = Cipher(algorithms.AES(self.key), modes.ECB()).encryptor()
        return enc.update(data) + enc.finalize()

    def decrypt_pkcs7(self, ciphertext: bytes) -> str:
        dec = Cipher(algorithms.AES(self.key), modes.ECB()).decryptor()
        padded = dec.update(ciphertext) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        raw = unpadder.update(padded) + unpadder.finalize()
        return raw.decode("utf-8")

    def decrypt_ecb(self, ciphertext: bytes) -> bytes:
        dec = Cipher(algorithms.AES(self.key), modes.ECB()).decryptor()
        return dec.update(ciphertext) + dec.finalize()


def recv_nul_frame(sock: socket.socket, buffer: bytearray, logger) -> tuple:
    """Read one NUL-delimited frame. Returns (frame_bytes, raw_chunks).

    raw_chunks is a list of the raw bytes received before the NUL so we never
    lose framing even if the peer sends multiple frames in one TCP segment.
    """
    raw = bytearray()
    while b"\x00" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            return None, bytes(raw)
        logger(f"  [recv] {len(chunk)} raw bytes: {chunk.hex()}")
        raw.extend(chunk)
        buffer.extend(chunk)
    idx = buffer.index(0)
    frame = bytes(buffer[:idx])
    del buffer[: idx + 1]
    return frame, bytes(raw)


def build_challenge(variant: dict, key: str) -> str:
    v = variant["version"]
    b = variant["build"]
    build_attr = f' build="{esc(b)}"' if b else ""
    ver_attr = f' version="{esc(v)}"' if v else ""
    return (
        f'<LSX><Event sender="{esc(variant["sender"])}">'
        f'<Challenge key="{esc(key)}"{ver_attr}{build_attr} />'
        f"</Event></LSX>"
    )


class CaptureHandler(socketserver.BaseRequestHandler):
    def log(self, msg: str):
        line = f"[{now_str()}] [{self.conn_id}] {msg}"
        print(line, flush=True)
        if self.fh:
            self.fh.write(line + "\n")
            self.fh.flush()

    def handle(self):
        conn_index = getattr(self.server, "conn_counter", 0)
        self.conn_id = f"conn-{conn_index}"
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.fh = open(CAPTURE_ROOT / f"lsx-{conn_index}-{stamp}.log", "w", encoding="utf-8")

        sock: socket.socket = self.request
        sock.settimeout(30)
        peer = self.client_address
        self.log(f"ACCEPT from {peer[0]}:{peer[1]}")

        if CRYPTO_ERROR is not None:
            self.log(f"FATAL: cryptography unavailable: {CRYPTO_ERROR}")
            self.fh.close()
            return

        buffer = bytearray()

        # ---- Server-first Challenge (Maxima/Origin) ----
        # We round-robin the challenge variants across connections so that if
        # the game opens multiple LSX sockets (it opened two against EA Desktop)
        # we can test more than one format in a single run.
        variant = CHALLENGE_VARIANTS[(conn_index - 1) % len(CHALLENGE_VARIANTS)]
        challenge_key = secrets.token_hex(16).upper()
        challenge = build_challenge(variant, challenge_key)
        self.log(f"SEND Challenge variant={variant} key={challenge_key}")
        self.log(f"  challenge_xml={challenge}")
        try:
            sock.sendall(challenge.encode("utf-8") + b"\x00")
        except Exception as exc:
            self.log(f"FAILED to send challenge: {exc}")
            self.fh.close()
            return

        # ---- Read ChallengeResponse (plaintext NUL frame) ----
        try:
            frame, raw = recv_nul_frame(sock, buffer, self.log)
        except socket.timeout:
            self.log("TIMEOUT waiting for ChallengeResponse (client did not answer this variant)")
            self.fh.close()
            return
        except Exception as exc:
            self.log(f"ERROR reading handshake frame: {exc}")
            self.fh.close()
            return

        if frame is None:
            self.log("CLOSED before ChallengeResponse")
            self.fh.close()
            return

        self.log(f"RECV plaintext handshake frame ({len(frame)} bytes): {frame!r}")
        text = frame.decode("utf-8", "replace")

        rid = "0"
        op = None
        op_name = "Unknown"
        try:
            root = ET.fromstring(text)
            req = root.find(".//Request")
            if req is not None:
                rid = req.attrib.get("id", rid)
                children = list(req)
                if children:
                    op = children[0]
                    op_name = op.tag.split("}")[-1]
            else:
                # Some clients may place ChallengeResponse directly or as Event.
                for tag in (".//ChallengeResponse", ".//Challenge", ".//Request"):
                    found = root.find(tag)
                    if found is not None:
                        op = found
                        op_name = found.tag.split("}")[-1]
                        break
        except Exception as exc:
            self.log(f"  (XML parse of handshake frame failed: {exc})")

        self.log(f"  parsed op={op_name}")
        if op is not None:
            for k, v in op.attrib.items():
                self.log(f"  attr {k}={v!r}")

        if op_name != "ChallengeResponse" or op is None:
            # The client spoke first with some other op. Record and continue in
            # a tolerant read-only loop so we still capture follow-on traffic.
            self.log("  NOTE: expected ChallengeResponse; received different op. Continuing read-only.")
            self.read_loop(sock, buffer, None)
            self.fh.close()
            return

        response = op.attrib.get("response", "")
        returned_key = op.attrib.get("key", "")
        version = op.attrib.get("version", "3")
        self.log(f"  client response={response!r}")
        self.log(f"  client key={returned_key!r}")
        self.log(f"  client version={version!r}")
        self.log(f"  client response length={len(response)} hex chars")

        # ---- Validate response under BOTH plausible models ----
        primary = OriginCrypto(0)
        try:
            model_a = primary.encrypt_pkcs7(challenge_key).hex()  # server.py string model
        except Exception as exc:
            model_a = f"<fail {exc}>"
        try:
            model_b = primary.encrypt_ecb(bytes.fromhex(challenge_key)).hex()  # raw 16-byte model
        except Exception as exc:
            model_b = f"<fail {exc}>"

        self.log(f"  modelA(server.py string-PKCS7) expected={model_a[:64]}... (len={len(model_a) if isinstance(model_a, str) else '?'})")
        self.log(f"  modelB(raw-16B-ECB) expected={model_b} (len={len(model_b) if isinstance(model_b, str) else '?'})")
        match_a = (response.lower() == model_a.lower()) if isinstance(model_a, str) else False
        match_b = (response.lower() == model_b.lower()) if isinstance(model_b, str) else False
        self.log(f"  match(modelA, server.py)={match_a}  match(modelB, raw-ECB)={match_b}")

        if not returned_key:
            self.log("  WARNING: ChallengeResponse has empty/missing 'key' attribute")

        # ---- Send ChallengeAccepted ----
        # Compute the accept response the same way server.py does (string-PKCS7
        # of the client's key attribute), but ALSO record the raw-ECB variant.
        try:
            accept_a = primary.encrypt_pkcs7(returned_key).hex() if returned_key else ""
        except Exception as exc:
            accept_a = ""
            self.log(f"  acceptA computation failed: {exc}")
        try:
            accept_b = primary.encrypt_ecb(bytes.fromhex(returned_key)).hex() if returned_key else ""
        except Exception as exc:
            accept_b = ""
            self.log(f"  acceptB computation failed: {exc}")

        # Use server.py's model A as the actual wire value (faithful replay).
        accept_used = accept_a
        accepted = (
            f'<LSX><Response id="{esc(rid)}" sender="EALS">'
            f'<ChallengeAccepted response="{esc(accept_used)}" />'
            f"</Response></LSX>"
        )
        self.log(f"  acceptA(server.py)={accept_a[:64]}...(len={len(accept_a)})")
        self.log(f"  acceptB(raw-ECB)={accept_b}(len={len(accept_b)})")
        self.log(f"  SEND ChallengeAccepted response={accept_used!r}")
        self.log(f"  accept_xml={accepted}")
        try:
            sock.sendall(accepted.encode("utf-8") + b"\x00")
        except Exception as exc:
            self.log(f"  FAILED to send ChallengeAccepted: {exc}")
            self.fh.close()
            return

        # ---- Derive candidate session keys ----
        candidates = {"primary-v2": bytes(range(16))}

        for label, hexstr in [
            ("seed-from-accept", accept_used),
            ("seed-from-client-response", response),
        ]:
            try:
                b = hexstr.encode("ascii")
                if len(b) >= 2:
                    seed = (b[0] << 8) | b[1]
                    key = OriginCrypto(seed).key
                    candidates[label] = key
                    self.log(f"  candidate key {label}: seed={seed} key={key.hex().upper()}")
            except Exception as exc:
                self.log(f"  candidate key {label} failed: {exc}")

        # Send a single signed-in Login event using the v3 session key (server.py
        # parity: seed = first two ASCII bytes of the accept response). This
        # marks the client online so it proceeds to emit the requests we want.
        # It is clearly logged as server-sent.
        try:
            login_event = (
                '<LSX><Event sender="EbisuSDK">'
                '<Login IsLoggedIn="true" UserIndex="0" LoginReasonCode="ALREADY_ONLINE" />'
                "</Event></LSX>"
            )
            event_crypto = OriginCrypto()
            session_key = candidates.get("seed-from-accept")
            if session_key is not None:
                event_crypto = OriginCrypto()
                event_crypto.key = bytes(session_key)
            try:
                enc = event_crypto.encrypt_pkcs7(login_event).hex().encode("ascii") + b"\x00"
                sock.sendall(enc)
                self.log("  SEND Login event (encrypted, key=" + event_crypto.key_hex + ") : " + login_event)
            except Exception as exc:
                self.log(f"  Login event encrypt/send failed: {exc}")
        except Exception as exc:
            self.log(f"  Login event send failed: {exc}")

        self.read_loop(sock, buffer, candidates)
        self.fh.close()

    def answer_getconfig(self, sock, candidates, request_xml):
        rid = "0"
        try:
            root = ET.fromstring(request_xml)
            req = root.find(".//Request")
            if req is not None:
                rid = req.attrib.get("id", rid)
        except Exception:
            pass
        inner = "<GetConfigResponse>" + "".join(
            f'<Service Name="{name}" Facility="{facility}" />'
            for name, facility in GETCONFIG_SERVICES
        ) + "</GetConfigResponse>"
        resp = f'<LSX><Response id="{esc(rid)}" sender="EbisuSDK">{inner}</Response></LSX>'
        key = candidates.get("seed-from-accept")
        if key is None:
            key = bytes(range(16))
        c = OriginCrypto()
        c.key = bytes(key)
        enc = c.encrypt_pkcs7(resp).hex().encode("ascii") + b"\x00"
        self.log(f"  SEND provisional GetConfigResponse (key={c.key_hex}): {resp}")
        try:
            sock.sendall(enc)
        except Exception as exc:
            self.log(f"  FAILED to send GetConfigResponse: {exc}")

    def read_loop(self, sock, buffer, candidates):
        self.log("ENTER capture loop (answers GetConfig provisionally; logs all other frames)")
        n = 0
        while True:
            try:
                frame, raw = recv_nul_frame(sock, buffer, self.log)
            except socket.timeout:
                self.log("TIMEOUT/closed in capture loop")
                return
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as exc:
                self.log(f"DISCONNECT in capture loop: {exc}")
                return
            except Exception as exc:
                self.log(f"ERROR in capture loop: {exc}")
                return
            if frame is None:
                self.log("CLOSED in capture loop")
                return
            if not frame:
                continue
            n += 1
            self.log(f"FRAME #{n}: {len(frame)} bytes hex={frame[:200].decode('ascii','replace')}{'...' if len(frame)>200 else ''}")

            # Persist raw frame.
            try:
                out = CAPTURE_ROOT / f"lsx-{self.conn_id}-frame-{n}.bin"
                out.write_bytes(frame)
            except Exception as exc:
                self.log(f"  (could not persist raw frame: {exc})")

            try:
                raw_bytes = bytes.fromhex(frame.decode("ascii", "strict"))
            except Exception as exc:
                self.log(f"  not hex: {exc}")
                continue

            session_plaintext = None
            if candidates:
                for label, key in candidates.items():
                    if key is None:
                        continue
                    c = OriginCrypto()
                    c.key = bytes(key)
                    try:
                        dec = c.decrypt_pkcs7(raw_bytes)
                        self.log(f"  DECRYPT[{label}] => {dec[:4000]}")
                        if label == "seed-from-accept" and session_plaintext is None:
                            session_plaintext = dec
                    except Exception as exc:
                        # Try raw ECB without unpadding as a fallback.
                        try:
                            raw_plain = c.decrypt_ecb(raw_bytes)
                            # NUL-strip for readability
                            self.log(f"  DECRYPT[{label}](raw) => {raw_plain!r}")
                        except Exception:
                            self.log(f"  DECRYPT[{label}] failed: {exc}")
            else:
                c = OriginCrypto()
                try:
                    dec = c.decrypt_pkcs7(raw_bytes)
                    self.log(f"  DECRYPT[primary] => {dec[:4000]}")
                    session_plaintext = dec
                except Exception as exc:
                    self.log(f"  DECRYPT[primary] failed: {exc}")

            if session_plaintext is not None and "<GetConfig" in session_plaintext:
                self.answer_getconfig(sock, candidates, session_plaintext)


class CaptureServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=LISTEN_HOST)
    ap.add_argument("--port", type=int, default=LISTEN_PORT)
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print("Refusing non-loopback bind.", file=sys.stderr)
        return 2

    if CRYPTO_ERROR is not None:
        print(f"cryptography unavailable: {CRYPTO_ERROR}", file=sys.stderr)
        return 2

    srv = CaptureServer((args.host, args.port), CaptureHandler)
    # Keep a counter for unique connection ids.
    srv.conn_counter = 0

    print(f"[*] Capture-only LSX listener on {args.host}:{args.port}", flush=True)
    print(f"[*] Artifacts: {CAPTURE_ROOT}", flush=True)
    print(f"[*] Challenge variants: {CHALLENGE_VARIANTS}", flush=True)
    print("[*] Waiting for FIFA21 dbdata.dll OriginSDK connections...", flush=True)

    while True:
        request, client_address = srv.get_request()
        srv.conn_counter += 1
        t = threading.Thread(target=srv.process_request_thread, args=(request, client_address))
        t.daemon = True
        t.start()


if __name__ == "__main__":
    sys.exit(main())