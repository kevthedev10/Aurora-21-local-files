import sys, re

EXE = r"E:\fifa\FIFA 21\FIFA21.exe"
KEYS = [
    "LSX", "Challenge", "ChallengeResponse", "ChallengeAccepted",
    "EbisuSDK", "EALS", "EALayerService", "OriginSDK",
    "GetProfile", "GetConfig", "GetSettings", "GetSetting",
    "GetAuthToken", "GetAuthCode", "RequestLicense", "GetGameInfo",
    "GetAllGameInfo", "GetInternetConnectedState", "GetUtcTime",
    "IsLoggedIn", "Login", "UserIndex", "LoginReasonCode",
    "sender=", "version=", "build=", "EALS_EVENTS", "LOGIN_EVENT",
    "GetWalletBalance", "QueryEntitlements", "QueryContent",
    "GetPresence", "QueryPresence", "QueryFriends", "GetBlockList",
    "Persona", "PersonaId", "Nucleus", "session",
]
PAT = re.compile(rb"[\x20-\x7e]{4,}")

hits = {}
with open(EXE, "rb") as f:
    # stream in 64MB chunks
    chunk_size = 64 * 1024 * 1024
    off = 0
    tail = b""
    while True:
        block = f.read(chunk_size)
        if not block:
            break
        data = tail + block
        for m in PAT.finditer(data):
            s = m.group().decode("ascii", "replace")
            abs_off = off + m.start()
            for k in KEYS:
                if k.lower() in s.lower():
                    hits.setdefault(k, []).append((abs_off, s))
                    break
        # keep last 3 bytes to catch strings spanning chunk boundary
        tail = data[-3:]
        off += len(block)

for k in KEYS:
    if k in hits:
        print(f"\n===== {k} ({len(hits[k])} hits) =====")
        shown = 0
        for off, s in hits[k]:
            if shown >= 40:
                print(f"  ... and {len(hits[k])-shown} more")
                break
            print(f"  0x{off:08X}  {s[:200]}")
            shown += 1
