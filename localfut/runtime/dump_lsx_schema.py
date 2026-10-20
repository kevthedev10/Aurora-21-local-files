import re

EXE = r"E:\fifa\FIFA 21\FIFA21.exe"
START = 0x06CC7400
END   = 0x06CC8A00
PAT = re.compile(rb"[\x20-\x7e]{2,}")

with open(EXE, "rb") as f:
    f.seek(START)
    data = f.read(END - START)

for m in PAT.finditer(data):
    s = m.group().decode("ascii", "replace")
    off = START + m.start()
    print(f"0x{off:08X}  {s}")
