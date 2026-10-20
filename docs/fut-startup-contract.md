# FIFA 21 FUT startup response contract

Verified against the installed EA PC `CardsDLL_Win64_retail.dll`, SHA-256
`37133BE975C0552C919E2901B7DA203EB458792B5BF43764821AD687DE548A49`.
Addresses below are RVAs, not process-specific addresses.

## Why the tutorial appeared empty

The observed client requested `/ut/game/fifa21/v2/user/accountinfo`, followed by
`/ut/game/fifa21/userMassInfo?screen=futlivemsggen4`. The first route fell through
to the unknown-route HTTP 200 `{}` response. The second returned only a shallow
`userInfo` object and credits. It did not carry the populated squad or loan data
that was available from the standalone routes.

At that point, the running server had 19,075 player definitions, 11 populated
active-squad slots, and 40 loan previews. Testing those standalone endpoints did
not prove that the native startup path received any of them.

## Native bulk-response sections

The mass-info decoder is at `0x3679D0`. Its field-name enum is resolved through
the table initialized by `0x37AB00`.

| JSON section | Parser evidence | Existing response builder |
| --- | --- | --- |
| `userInfo` | dispatch `0x368F7A` to `0x329190` | `_native_user()` |
| `squad` | dispatch `0x368A4B` to `0x3248E0` | `_native_squad()` |
| `loanPlayers` | dispatch `0x368239` to `0x3372F0` | `_loan_player_choices()` |

Loan choices are nested at `loanPlayers.loans[].itemData`. The loan decoder
reads `loans` at `0x3373E3` and each `itemData` at `0x337566`, using the native
item decoder at `0x321910`. A root-level `loans` array is not equivalent.

`userInfo` has no extra `user` wrapper. Its currencies are `currencies[]` objects
(parsed at `0x329322`), not a substitute bare credits balance. Its
`squadList.squad[]` entries are summaries, decoded by `0x31F420`, with fields
`id`, `squadName`, `rating`, `chemistry`, `formation`, and `squadType`.
`squadType` is a string enum (`REGULAR_SQUAD`, `DREAM_SQUAD`, or `DRAFT_SQUAD`),
not an integer; the enum parser is at `0x3478D0`. Seeded local squads use
`REGULAR_SQUAD`.
The active squad's full player bindings belong in the separate root `squad`.

Optional client-data sections contain `entries: [{key: int32, value: int32}]`
and can change onboarding progress. No such gate surrounds loan-list parsing,
so this correction does not invent client-data entries to force progress.

## Scope and verification

- Both observed account-info paths now use the existing native account response.
  This is a routing correction; the EXE-side account/year-selection parser has
  not been recovered, so this change does not invent a new account schema.
- Startup uses existing saved squads/items and read-only loan previews. It must
  not reset a club, grant preview players, or change credits.
- `node localfut/verify-fut-startup.mjs --self-test` tests the validator offline.
- `node localfut/verify-fut-startup.mjs` checks the running loopback server,
  including response nesting, squad/item bindings, and before/after club state.

## Verification result (2026-08-30)

- Offline validator: valid fixtures accepted, 31 invalid type/nesting/binding or
  mutation cases rejected.
- Restarted loopback server: both account aliases, native user and summary
  types, 11 populated slots out of 23, 40 loan choices, and matching bulk
  sections passed. All 14 club item identities and currency balances matched
  the snapshot taken before the restart and remained unchanged during probes.
- Existing auth check passed PreAuth plus 24 loopback HTTP checks, including the
  loaded source hash and query redaction, after launcher readiness completed.
- The user's game then rendered the 11 saved players. This confirms native
  consumption of the squad data, but not completion of the loan tutorial.

The displayed superstar lineup is the existing seed, not a grant by this fix:
`_seed_items()` takes up to 14 configured `starter_players`; `_seed_squad()`
assigns only the first 11 to sequential indices without matching positions.
It does not populate substitute slots. That seed and saved club are unchanged.
Card labels showing `NOT FOUND` remain a separate metadata/localization issue
whose exact cause has not yet been established.

These checks do not prove complete FUT compatibility. Hospitality settings,
other optional bulk sections, and the separate CensusData retry loop are not
implemented by this correction.
