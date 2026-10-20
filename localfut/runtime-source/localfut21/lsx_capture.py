"""Standalone Origin-LSX capture + serve shim (FIFA 21 / OriginSDK 10.6.x).

Binds 127.0.0.1:3216, speaks first with the server Challenge, then logs every
frame (raw hex + decoded) it receives from the game while answering with a
minimal-but-valid set of LSX operations.  Run with EA App CLOSED so this shim
owns the port.  The goal is to (a) confirm the game actually dials 3216 and
(b) record the exact protocol version / crypto the retail binary uses.

Usage:
    py -3 lsx_capture.py [--host 127.0.0.1] [--port 3216] [--log PATH]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import secrets
import socket
import socketserver
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception as _crypto_import_error:  # pragma: no cover
    padding = Cipher = algorithms = modes = None
    _crypto_import_error = str(_crypto_import_error)
else:
    _crypto_import_error = None


# --------------------------------------------------------------------------- #
# Crypto (identical to the OriginSDK deterministic PRNG / AES used by server.py)
# --------------------------------------------------------------------------- #
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

    def encrypt(self, text: str) -> bytes:
        padder = padding.PKCS7(128).padder()
        raw = padder.update(text.encode("utf-8")) + padder.finalize()
        enc = Cipher(algorithms.AES(self.key), modes.ECB()).encryptor()
        return enc.update(raw) + enc.finalize()

    def decrypt(self, ciphertext: bytes) -> str:
        dec = Cipher(algorithms.AES(self.key), modes.ECB()).decryptor()
        padded = dec.update(ciphertext) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        raw = unpadder.update(padded) + unpadder.finalize()
        return raw.decode("utf-8")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_LOCK = threading.Lock()
LOGF = None


def log(msg: str) -> None:
    stamp = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{stamp}] {msg}"
    with LOG_LOCK:
        print(line, flush=True)
        if LOGF is not None:
            LOGF.write(line + "\n")
            LOGF.flush()


def _hexdump(data: bytes, width: int = 32) -> str:
    out = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"    {i:08x}  {hx:<{width*3}}  {asc}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Frame I/O
# --------------------------------------------------------------------------- #
def recv_nul_frame(sock: socket.socket, buffer: bytearray) -> bytes | None:
    while b"\x00" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            return None
        buffer.extend(chunk)
    idx = buffer.index(0)
    frame = bytes(buffer[:idx])
    del buffer[: idx + 1]
    return frame


def send_plain(sock: socket.socket, xml: str) -> None:
    sock.sendall(xml.encode("utf-8") + b"\x00")


def send_enc(sock: socket.socket, crypto: OriginCrypto, xml: str) -> None:
    sock.sendall(crypto.encrypt(xml).hex().encode("ascii") + b"\x00")


def _xml_attr(value) -> str:
    import html

    return html.escape(str(value), quote=True)


def _extract_request(request_xml: str):
    rid = "0"
    name = "Unknown"
    op = None
    try:
        root = ET.fromstring(request_xml)
        req = root.find(".//Request")
        if req is not None:
            rid = req.attrib.get("id", rid)
            children = list(req)
            if children:
                op = children[0]
                name = op.tag.split("}")[-1]
    except Exception:
        pass
    return rid, name, op


def _envelope(rid: str, inner: str, sender: str = "EbisuSDK") -> str:
    return f'<LSX><Response id="{_xml_attr(rid)}" sender="{_xml_attr(sender)}">{inner}</Response></LSX>'


def build_response(request_xml: str) -> str:
    rid, name, op = _extract_request(request_xml)

    if name == "GetConfig":
        services = (
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
        )
        inner = "<GetConfigResponse>" + "".join(
            f'<Service Name="{n}" Facility="{f}" />' for n, f in services
        ) + "</GetConfigResponse>"
    elif name == "GetSettings":
        inner = ('<GetSettingsResponse Language="en_US" Environment="production" '
                 'IsIGOAvailable="false" IsIGOEnabled="false" '
                 'IsTelemetryEnabled="false" IsManualOffline="false" />')
    elif name == "GetSetting":
        setting = ""
        if op is not None:
            setting_id = op.attrib.get("SettingId", "")
            setting = {
                "LANGUAGE": "en_US",
                "ENVIRONMENT": "production",
                "IS_IGO_AVAILABLE": "false",
                "IS_IGO_ENABLED": "false",
                "IS_TELEMETRY_ENABLED": "false",
                "IS_MANUAL_OFFLINE": "false",
            }.get(setting_id, "")
        inner = f'<GetSettingResponse Setting="{_xml_attr(setting)}" />'
    elif name == "GetInternetConnectedState":
        inner = '<InternetConnectedState connected="1" />'
    elif name == "GetUtcTime":
        utc = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        inner = f'<GetUtcTimeResponse Time="{utc}" />'
    elif name == "GetAuthToken":
        inner = '<AuthToken value="LOCAL-FUT21-ACCESS" />'
    elif name == "GetAuthCode":
        inner = '<AuthCode value="LOCAL-FUT21-AUTH-CODE" />'
    elif name == "GetProfile":
        inner = ('<GetProfileResponse UserIndex="0" UserId="1000000001" '
                 'PersonaId="1000000002" Persona="LocalPlayer" AvatarId="" '
                 'Country="PT" IsUnderAge="false" IsSubscriber="false" '
                 'IsTrialSubscriber="false" SubscriberLevel="0" GeoCountry="PT" '
                 'CommerceCountry="PT" CommerceCurrency="EUR" IsSteamSubscriber="false" />')
    elif name == "GetBlockList":
        inner = '<GetBlockListResponse />'
    elif name == "QueryFriends":
        inner = '<QueryFriendsResponse />'
    elif name == "QueryAreFriends":
        inner = '<QueryAreFriendsResponse />'
    elif name == "QueryContent":
        inner = '<QueryContentResponse />'
    elif name == "QueryEntitlements":
        inner = '<QueryEntitlementsResponse />'
    elif name == "GetPresence":
        inner = '<GetPresenceResponse />'
    elif name == "QueryPresence":
        inner = '<QueryPresenceResponse />'
    elif name == "GetWalletBalance":
        inner = '<GetWalletBalanceResponse Balance="0" Currency="EUR" />'
    elif name == "GetGameInfo":
        game_info_id = ""
        if op is not None:
            game_info_id = op.attrib.get("GameInfoId", "").upper()
        values = {
            "UPTODATE": "true",
            "LANGUAGES": "en_US,pt_PT,fr_FR,de_DE,es_ES,it_IT",
            "FREETRIAL": "false",
            "EXPIRATION": "0000-00-00T00:00:00",
            "EXPIRATION_DURATION": "0",
            "INSTALLED_VERSION": "1.0.0.0",
            "INSTALLED_LANGUAGE": "en_US",
            "AVAILABLE_VERSION": "1.0.0.0",
            "DISPLAY_NAME": "EA SPORTS FIFA 21",
            "FULLGAME_PURCHASED": "true",
            "FULLGAME_IS_RELEASED": "true",
            "FULLGAME_RELEASE_DATE": "2020-10-09T00:00:00",
            "MAX_GROUP_SIZE": "22",
            "ENTITLEMENT_SOURCE": "LOCAL",
        }
        inner = f'<GetGameInfoResponse GameInfo="{_xml_attr(values.get(game_info_id, ""))}" />'
    elif name == "GetAllGameInfo":
        now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        inner = ('<GetAllGameInfoResponse UpToDate="true" '
                 'Languages="en_US,pt_PT,fr_FR,de_DE,es_ES,it_IT" FreeTrial="false" '
                 'FullGamePurchased="true" FullGameReleased="true" '
                 'FullGameReleaseDate="2020-10-09T00:00:00" Expiration="" '
                 f'SystemTime="{now}" HasExpiration="false" InstalledVersion="1.0.0.0" '
                 'InstalledLanguage="en_US" AvailableVersion="1.0.0.0" '
                 'DisplayName="EA SPORTS FIFA 21" MaxGroupSize="22" EntitlementSource="LOCAL" />')
    elif name == "RequestLicense":
        inner = '<RequestLicenseResponse License="" />'
    else:
        inner = '<ErrorSuccess Code="0" Description="Local FUT success" />'

    return _envelope(rid, inner)


# --------------------------------------------------------------------------- #
# Connection handler
# --------------------------------------------------------------------------- #
class LSXCaptureHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        sock: socket.socket = self.request
        sock.settimeout(30)
        peer = self.client_address
        log(f"=== LSX client connected from {peer[0]}:{peer[1]} ===")
        buffer = bytearray()

        if padding is None:
            log(f"ERROR: cryptography package unavailable: {_crypto_import_error}")
            return

        challenge_key = secrets.token_hex(16).upper()
        challenge = (
            '<LSX><Event sender="EALS"><Challenge '
            f'key="{challenge_key}" version="3" build="10.6.2.11-fb" />'
            "</Event></LSX>"
        )
        send_plain(sock, challenge)
        log(f"--> [plain] Challenge sent (key={challenge_key})")

        # ChallengeResponse (plaintext, NUL-delimited)
        frame = recv_nul_frame(sock, buffer)
        if frame is None:
            log("<-- client closed before ChallengeResponse")
            return
        log(f"<-- [plain] ChallengeResponse frame ({len(frame)} bytes):")
        log(_hexdump(frame))
        text = frame.decode("utf-8", errors="replace")
        log(f"<-- [plain] decoded: {text}")

        rid, name, op = _extract_request(text)
        if name != "ChallengeResponse" or op is None:
            log(f"WARNING: expected ChallengeResponse, got '{name}'")
            return

        response = op.attrib.get("response", "")
        returned_key = op.attrib.get("key", "")
        protocol_version = op.attrib.get("version", "3")
        log(f"    ChallengeResponse.response = {response}")
        log(f"    ChallengeResponse.key      = {returned_key}")
        log(f"    ChallengeResponse.version  = {protocol_version}")

        primary = OriginCrypto(0)
        expected = primary.encrypt(challenge_key).hex()
        if response.lower() == expected.lower():
            log("    VALIDATION OK: response == AES_00..0f(challenge_key)")
        else:
            log(f"    VALIDATION MISMATCH: expected={expected}")
            log(f"                        got     ={response}")

        if not returned_key:
            log("WARNING: ChallengeResponse has no 'key' attribute; cannot finish")
            return

        accept_response = primary.encrypt(returned_key).hex()

        if protocol_version == "2":
            seed = 0
            crypto = OriginCrypto(0)
        else:
            rb = accept_response.encode("ascii")
            seed = (rb[0] << 8) | rb[1]
            crypto = OriginCrypto(seed)
        log(f"    session seed={seed}  protocol={protocol_version}")

        accepted = _envelope(
            rid,
            f'<ChallengeAccepted response="{_xml_attr(accept_response)}" />',
            sender="EALS",
        )
        send_plain(sock, accepted)
        log(f"--> [plain] ChallengeAccepted sent (response={accept_response})")

        login_event = (
            '<LSX><Event sender="EbisuSDK">'
            '<Login IsLoggedIn="true" UserIndex="0" LoginReasonCode="ALREADY_ONLINE" />'
            "</Event></LSX>"
        )
        send_enc(sock, crypto, login_event)
        log("--> [enc] Login event sent")

        sock.settimeout(120)
        while True:
            frame = recv_nul_frame(sock, buffer)
            if frame is None:
                log(f"<-- client {peer[0]}:{peer[1]} closed")
                break
            if not frame:
                continue
            log(f"<-- [enc] encrypted frame ({len(frame)} bytes):")
            log(_hexdump(frame[:512]) + ("\n    ..." if len(frame) > 512 else ""))
            try:
                encrypted = bytes.fromhex(frame.decode("ascii"))
                request_xml = crypto.decrypt(encrypted)
            except Exception as exc:
                log(f"    DECRYPT FAILED: {exc}")
                break
            log(f"<-- [enc] decrypted request: {request_xml}")
            response_xml = build_response(request_xml)
            send_enc(sock, crypto, response_xml)
            log(f"--> [enc] response: {response_xml}")


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3216)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    global LOGF
    if args.log:
        LOGF = open(args.log, "a", encoding="utf-8")

    if padding is None:
        log(f"FATAL: cryptography package unavailable: {_crypto_import_error}")
        return 2

    try:
        srv = ThreadingTCPServer((args.host, args.port), LSXCaptureHandler)
    except OSError as e:
        log(f"FATAL: cannot bind {args.host}:{args.port}: {e}")
        log("Close EA App / Origin and anything else holding the port, then retry.")
        return 2

    log(f"Origin-LSX capture shim listening on {args.host}:{args.port}")
    log("Launch FIFA 21 now.  Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if LOGF:
            LOGF.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
