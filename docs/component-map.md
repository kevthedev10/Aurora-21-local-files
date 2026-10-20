# CardsDLL component map

Date: 25 August 2026. Status: validated static identifiers; cross-reference ownership remains a candidate until Binary Ninja analysis completes.

## Purpose

The current Ultimate Team transition fails before `CardsDLL_Win64_retail.dll` loads. This map searches other local game components for exact identifiers derived from CardsDLL so the earlier plugin or feature registry can be found without patching code, forcing branches, or repeating rejected loader sweeps.

The report stores names, categories, hashes, file offsets, RVAs, and preferred virtual addresses. It does not copy game code or include captured account/session values.

## Reproduce the high-signal pass

```powershell
python tools\component_map.py `
  --game-root "C:\Program Files\EA Games\FIFA 21" `
  --target "C:\Program Files\EA Games\FIFA 21\FIFA21.exe" `
  --target "C:\Program Files\EA Games\FIFA 21\powdll_Win64_retail.dll" `
  --target "C:\Program Files\EA Games\FIFA 21\sysdll_Win64_retail.dll" `
  --target "C:\Program Files\EA Games\FIFA 21\dbdata.dll" `
  --target "C:\Program Files\EA Games\FIFA 21\FootballCompEng_Win64_retail.dll" `
  --high-signal-only `
  --output work\cards-component-map-high-signal.json

python tools\component_map_report.py `
  work\cards-component-map-high-signal.json `
  --identifier CardsDLL `
  --identifier StubFifaDispatchers `
  --identifier StubFifaGameDb `
  --identifier StubFifaOnline `
  --identifier ut/online
```

The generated `work/` report is deliberately ignored by Git because it describes a contributor's local game installation.

## Result summary

Source: `CardsDLL_Win64_retail.dll`, SHA-256 `37133BE975C0552C919E2901B7DA203EB458792B5BF43764821AD687DE548A49`.

The high-signal set contains 115 unique identifiers: 77 route names, 30 configuration keys, three factory/service names, three module names, and two exports.

| Target | Unique high-signal matches | Interpretation |
| --- | ---: | --- |
| `FIFA21.exe` | 9 | Contains the CardsDLL module stem, plugin export names, all three service-factory names, two online config keys, and `ut/online` |
| `powdll_Win64_retail.dll` | 4 | Shares both plugin exports plus `StubFifaGameDb` and `StubFifaOnline` |
| `FootballCompEng_Win64_retail.dll` | 2 | Shares the generic plugin exports only |
| `sysdll_Win64_retail.dll` | 0 | No high-signal exact match in this pass |
| `dbdata.dll` | 0 | No high-signal exact match in this pass |

`FIFA21.exe` SHA-256 for this map is `05CC56F897201A6BA74D608F971BCF157B1A46C10E9CDA335E868C17004FBC71`.

## Binary Ninja navigation anchors

Preferred addresses assume the executable's image base of `0x140000000`; use RVAs when ASLR or a rebased database is involved.

| Identifier | File offset | RVA | Preferred VA | Why it matters |
| --- | ---: | ---: | ---: | --- |
| `PlugInitialize_` | `0x6BFC480` | `0x6BFDA80` | `0x146BFDA80` | Candidate generic plugin loader/export resolver |
| `PlugDeinitialize_` | `0x6BFC490` | `0x6BFDA90` | `0x146BFDA90` | Adjacent plugin lifecycle anchor |
| `StubFifaDispatchers` | `0x6C22320` | `0x6C23920` | `0x146C23920` | Service-factory cluster |
| `StubFifaGameDb` | `0x6C225B0` | `0x6C23BB0` | `0x146C23BB0` | Shared with powdll initialization |
| `StubFifaOnline` | `0x6C227D8` | `0x6C23DD8` | `0x146C23DD8` | Shared with powdll initialization |
| `CardsDLL` | `0x7072550` | `0x7073B50` | `0x147073B50` | Direct module/feature name in the executable |
| `ONLINE/HIDEDESYNCS` | `0x70733E0` | `0x70749E0` | `0x1470749E0` | Nearby online configuration namespace |
| first `ut/online` | `0x7093D0C` | `0x709530C` | `0x14709530C` | First of eight exact route-name occurrences |
| `ONLINE/NO_AUTO_SQUAD` | `0x70975F8` | `0x7098BF8` | `0x147098BF8` | End of the observed route/config cluster |

The eight `ut/online` RVAs are `0x709530C`, `0x70958EC`, `0x70960BC`, `0x70960EC`, `0x709611C`, `0x709614C`, `0x70964BC`, and `0x70975C4`.

## Current interpretation

The executable's direct `CardsDLL` name and its separate plugin-export strings are evidence for a native component-registration or plugin-loading mechanism above the point where CardsDLL itself becomes resident. They do not yet prove that the `CardsDLL` string is referenced by executable code; a serialized registry, hashed lookup, or data-driven table remains possible.

The next static action is to enumerate data and code references to `0x147073B50`, then compare the owning function or table with references to `PlugInitialize_` and the three stub-service names. A useful result is a natural loader/registry call chain. A zero-xref result should redirect work toward table parsing and runtime read breakpoints, not another exhaustive `LdrLoadDll` sweep.

No modification is justified by this map alone.
