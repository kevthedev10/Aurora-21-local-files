import re

EXE = r"E:\fifa\FIFA 21\FIFA21.exe"
# search entire file for specific protocol tokens
TOKENS = [
    "ChallengeAccepted", "ChallengeResponse", "Challenge",
    "sender", "Sender", "SenderId",
    "Ebisu", "EALS", "OriginClient",
    "<LSX", "</LSX", "<lsx", "</lsx",
    "lsxsocket", "lsx", "LSX",
    "key=", "response=", "version=",
    "RequestTicket", "ResponseTicket",
    "requestId", "RequestId", "responseId",
    "Envelope", "envelope",
    "EventSender", "Event", "Response", "Request",
    "protocol", "Protocol",
    "sessionKey", "SessionKey", "AES", "aes",
    "NucleusId", "Nucleus", "nucleus",
    "AccessToken", "AuthToken", "authToken",
]
PAT = re.compile(rb"[\x20-\x7e]{3,}")

# Only dump in the OriginSDK code region which is dense with these
START = 0x06C00000
END   = 0x06D00000

with open(EXE, "rb") as f:
    f.seek(START)
    data = f.read(END - START)

seen = set()
for m in PAT.finditer(data):
    s = m.group().decode("ascii", "replace")
    low = s.lower()
    if any(t.lower() in low for t in TOKENS):
        key = (s)
        if key in seen:
            continue
        seen.add(key)
        off = START + m.start()
        print(f"0x{off:08X}  {s}")
