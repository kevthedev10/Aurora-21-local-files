import re

EXE = r"E:\fifa\FIFA 21\FIFA21.exe"
START = 0x06CA0000
END   = 0x06CD0000
PAT = re.compile(rb"[\x20-\x7e]{3,}")

with open(EXE, "rb") as f:
    f.seek(START)
    data = f.read(END - START)

prev_end = -1
for m in PAT.finditer(data):
    s = m.group().decode("ascii", "replace")
    off = START + m.start()
    # collapse runs: only print strings that look like code/protocol identifiers
    if any(k in s for k in ("lsx", "LSX", "Origin", "::", "Response", "Request", "Event", "Get", "Challenge", "Auth", "Profile", "Config", "EAL", "Ebisu", "Persona", "Nucleus", "Login", "sx")):
        print(f"0x{off:08X}  {s}")
