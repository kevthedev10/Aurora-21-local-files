"""Read-only LSX client probe v2: version-negotiation variants.

Connects to the Origin/EA LSX server on 127.0.0.1:3216 (EA Desktop), tries
the handshake with several version/sender variants, and prints any
post-handshake login-state event / responses. Read-only.
"""
import secrets
import socket
import sys
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


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


def try_handshake(version_attr, sender_attr) -> tuple:
    """Return (accepted_frame, crypto_or_None, buffer, sock)."""
    sock = socket.create_connection(("127.0.0.1", 3216), timeout=10)
    sock.settimeout(12)
    buffer = bytearray()

    challenge_frame = recv_nul_frame(sock, buffer)
    if challenge_frame is None:
        sock.close()
        return "no-challenge", None, None, None
    challenge_xml = challenge_frame.decode("utf-8", "replace")
    root = ET.fromstring(challenge_xml)
    ev = root.find(".//Challenge")
    if ev is None:
        sock.close()
        return "no-challenge-element", None, None, None
    challenge_key = ev.attrib.get("key", "")

    if version_attr is None:
        version_attr = ev.attrib.get("version", "3")

    primary = OriginCrypto(0)
    response = primary.encrypt(challenge_key).hex()
    client_key = secrets.token_hex(16).upper()
    req = (
        f'<LSX><Request id="0" sender="{sender_attr}"><ChallengeResponse '
        f'response="{response}" key="{client_key}" version="{version_attr}" />'
        f"</Request></LSX>"
    )
    send_plain(sock, req)

    accepted_frame = recv_nul_frame(sock, buffer)
    if accepted_frame is None:
        sock.close()
        return "no-accept", None, None, None
    accepted_xml = accepted_frame.decode("utf-8", "replace")
    acc_root = ET.fromstring(accepted_xml)
    acc = acc_root.find(".//ChallengeAccepted")
    if acc is None:
        sock.close()
        return ("rejected", accepted_xml[:400]), None, None, None
    accept_response = acc.attrib.get("response", "")
    b = accept_response.encode("ascii")
    seed = (b[0] << 8) | b[1]
    crypto = OriginCrypto(seed)
    return ("accepted", accepted_xml[:400], crypto, (sock, buffer))


def main() -> int:
    variants = [
        ("echo-server-version", None, "DIAGPROBE"),
        ("v3-EbisuSDK", "3", "EbisuSDK"),
        ("v3-Origin", "3", "Origin"),
        ("v2-EbisuSDK", "2", "EbisuSDK"),
        ("noversion-EbisuSDK", "", "EbisuSDK"),
    ]
    for label, ver, sender in variants:
        print(f"\n[*] === variant {label}: version={ver!r} sender={sender!r} ===", flush=True)
        try:
            status, detail, crypto, conn = try_handshake(ver, sender)
        except Exception as exc:
            print(f"[!] variant {label} failed: {exc}", flush=True)
            continue
        if status != "accepted":
            print(f"[!] handshake not accepted: {status} {detail}", flush=True)
            continue
        sock, buffer = conn
        print(f"[*] ACCEPTED: {detail}", flush=True)
        try:
            frame = recv_nul_frame(sock, buffer)
            if frame is None:
                print("[!] closed before event", flush=True)
            else:
                try:
                    dec = crypto.decrypt(bytes.fromhex(frame.decode("ascii")))
                    print(f"[<] EVENT (decrypted): {dec[:4000]}", flush=True)
                except Exception as exc:
                    print(f"[!] event undecryptable ({exc}); raw={frame[:300]}", flush=True)
        except socket.timeout:
            print("[*] no proactive event within 12s", flush=True)

        for rid, xml in [
            ("1", "<LSX><Request id=\"1\" sender=\"PROBE\"><GetProfile /></Request></LSX>"),
            ("2", "<LSX><Request id=\"2\" sender=\"PROBE\"><GetConfig /></Request></LSX>"),
            ("3", "<LSX><Request id=\"3\" sender=\"PROBE\"><GetAuthToken /></Request></LSX>"),
        ]:
            try:
                send_enc(sock, crypto, xml)
                frame = recv_nul_frame(sock, buffer)
                if frame is None:
                    print("[!] closed mid-probe", flush=True)
                    break
                try:
                    dec = crypto.decrypt(bytes.fromhex(frame.decode("ascii")))
                    print(f"[<] resp[{rid}]: {dec[:4000]}", flush=True)
                except Exception as exc:
                    print(f"[!] resp[{rid}] undecryptable ({exc}); raw={frame[:200]}", flush=True)
            except socket.timeout:
                print(f"[!] resp[{rid}] timed out", flush=True)
                break
        sock.close()
    print("\n[*] probe done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
