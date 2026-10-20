# Contributing

Contributions should move the project toward a clean-room, loopback-only compatibility service while preserving a clear boundary from EA content and security systems.

## Before submitting work

- Use a lawfully acquired copy of the game.
- Restore original files before reproducing a finding.
- Record the SHA-256 of every analyzed module.
- Use module-relative addresses and state whether ASLR was active.
- Test twice when practical and describe both the trigger and observed result.
- Remove names, account IDs, persona IDs, tokens, paths, IP addresses other than loopback, and payload values.

## Evidence format

```text
build hashes:
module + rva:
tool and version:
trigger:
observed result:
confidence: validated | candidate | rejected
privacy redactions performed:
```

## Unacceptable contributions

- EA executables, DLLs, assets, databases, card art, commentary, keys, or certificates.
- Former server responses copied into a replacement service.
- Account recovery, entitlement bypass, activation bypass, anti-cheat tampering, TLS bypass, or certificate bypass.
- Public matchmaking, transfer markets, FIFA Points, monetization, or connections to active EA infrastructure.
- Instructions whose purpose is cheating in a supported online game.

Original source contributions should be small, testable, and documented. A proposed binary change must be represented only as a hash-locked recipe against a specifically identified lawful local copy, and only after the native configuration route has been exhausted and reviewed.
