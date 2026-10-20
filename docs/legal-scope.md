# Legal and operational scope

This page records conservative project policy, not legal advice. Contributors are responsible for obtaining advice applicable to their country and circumstances.

## What the primary sources establish

EA's service-update record lists FIFA 21 online services as retired on 6 November 2023. Retirement does not itself grant permission to copy, modify, or redistribute EA software or content.

EA's UK user agreement, last updated 14 May 2026, licenses EA services for personal, non-commercial use and restricts copying, modification, distribution, and reverse engineering unless EA expressly authorizes the activity or applicable law permits it. It separately prohibits tampering with technical or content-protection measures. Community reports about tolerated offline mods are not written EA authorization, so the project does not treat offline modding as blanket approval.

UK Copyright, Designs and Patents Act 1988 section 50B can permit narrowly necessary decompilation by a lawful user to obtain information needed for an independent interoperable program. Its conditions matter: information must not already be readily available; decompilation must be confined to what is necessary; disclosure must be limited to people who need it for the permitted objective; and the information cannot be used to create a substantially similar program or for another restricted act.

That exception is a plausible basis for the clean-room compatibility-service research described here, but it is not an automatic conclusion and does not itself grant permission to distribute EA binaries, modified DLLs, or EA content. Section 296 separately creates risk around publishing circumvention information or supplying circumvention means when tied to infringing copies. Authentication, activation, signatures, anti-cheat, encryption, TLS, certificates, and other protection mechanisms therefore remain outside project scope pending specialist advice or written permission.

The United States video-game preservation exemption in 37 CFR 201.40 is different and fact-specific. Some provisions distinguish individual local gameplay from preservation by eligible libraries, archives, or museums and define a complete game by reference to server-stored copyrightable content. It should not be treated as a worldwide or automatic authorization.

Primary sources:

- [EA service updates](https://www.ea.com/legal/service-updates/a-h)
- [EA user agreement, UK locale](https://www.ea.com/legal/user-agreement?isLocalized=true&setLocale=en-gb)
- [UK Copyright, Designs and Patents Act 1988, section 50B](https://www.legislation.gov.uk/ukpga/1988/48/section/50B)
- [UK Copyright, Designs and Patents Act 1988, section 296](https://www.legislation.gov.uk/ukpga/1988/48/section/296)
- [U.S. Copyright Office, 37 CFR 201.40](https://www.copyright.gov/title37/201/37cfr201-40.html)

## Project policy

- Work from lawfully acquired local copies.
- Build an independently authored compatibility service and synthetic local data.
- Preserve only local gameplay; do not expose a public service.
- Bind services to `127.0.0.1` and reject non-loopback clients.
- Do not contact active EA game-service endpoints during development.
- Do not recover former clubs, inventories, transactions, or server-side content.
- Do not reproduce EA server code, responses, databases, artwork, commentary, or monetized entitlements.
- Do not patch EA App ownership checks, `Activation64.dll`, anti-cheat, encryption, TLS, certificate validation, or signatures.
- Publish original source, sanitized protocol structure, hashes, and module-relative research notes only.
- Seek written permission or specialist legal advice before any work close to entitlement or technical-protection boundaries.
