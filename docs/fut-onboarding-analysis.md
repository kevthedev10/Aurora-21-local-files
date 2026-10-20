@@ -0,0 +1,407 @@

\# FIFA 21 first-run FUT onboarding analysis



Investigation date: 2026-08-30. This is an implementation map, not a claim that

the full onboarding flow currently works. No saved club was reset, no tutorial

flags were forced, and no game input was automated during this investigation.



\## Scope and evidence



Scope: entering FUT without a completed local club, selecting starter content,

choosing the loan and club cosmetics, reaching the normal FUT hub, and resuming

after interruption. Completing every later FUT objective is outside this flow.



Evidence labels used below:



\- \*\*Native\*\*: strings, callers, serializers, or parsers in the installed EA PC

&#x20; `CardsDLL\_Win64\_retail.dll` inspected through Binary Ninja.

\- \*\*Asset\*\*: locally extracted FIFA 21 localization or database content. A

&#x20; retained string alone does not prove that a branch executes in this build.

\- \*\*Observed\*\*: the user's game screenshots or specific captured game requests.

\- \*\*Server\*\*: current Project 21 source behavior, not EA's original behavior.

\- \*\*Unknown\*\*: not established; do not substitute a FIFA 15/20 assumption.



CardsDLL SHA-256:

`37133BE975C0552C919E2901B7DA203EB458792B5BF43764821AD687DE548A49`.

Native addresses in this document are RVAs relative to preferred base

`0x180000000`, unless explicitly labelled otherwise.



\## The first-run sequence



The DLL contains these numbered onboarding phase names:



| Phase | Native phase name (`<n>\_fut\_onboarding\_flow\_…`) | String RVA | Player-facing purpose |

| --- | --- | --- | --- |

| 0 | `country` | `0x46A470` | Choose the nationality emphasis of the starter pack. |

| 1 | `explore\_squad` | `0x462C50` | Inspect the starter squad and choose an occupied position to improve. |

| 2 | `loan\_select` | `0x462D68` | Choose a high-rated temporary loan player. |

| 3 | `loan\_explore` | `0x462D40` | Inspect the squad after the loan choice. |

| 4 | `home\_kit` | `0x46BE60` | Choose a home kit. |

| 5 | `away\_kit` | `0x46BF38` | Choose an away kit. |

| 6 | `badge` | `0x462840` | Choose a badge. |

| 7 | `summary` | `0x46D888` | Review the starter-club selections. |



The numbering establishes the intended phase sequence, not the database values

that should be written to a progress key. Those are separate contracts.



The current screenshots show phase 1's squad exploration and loan prompt. The

client already has the tutorial screen/audio machinery; an entirely new UI is

not required. An exact `Icebreaker` string was not found in CardsDLL. This does

not establish that every other game module lacks that older name.



The captain-choice request is separate: builder `0x30EED0` constructs

`/{draftId}/draft/choices/captain`, alongside Draft formation, player, and

manager choices. That is Draft, not a missing first-run captain screen. An

ordinary squad's `captain` field is another separate concept.



\## What the available extracted assets establish



Source: `localfut/runtime/mined/tables/eng\_us/LanguageStrings1.json`.

The player-catalog manifest names the supplied `Project-21-V2-main.zip` as its

provenance; this does not independently bind every localization export to the

installed CardsDLL hash. These are available extracted records, not a fresh

complete extraction of every Frostbite UI/config asset from the installed EXE.



| Localization key | Line | Meaning |

| --- | --- | --- |

| `FUT\_ONBOARDING\_PACK\_SELECT\_BODY` | 213863 | The chosen pack includes \*\*more\*\* players of that nationality to help chemistry; it does not promise that every player is from that country. |

| `FUT\_ONBOARDING\_INVALID\_SWAP\_POSITION` | 29213 | A loan replacement must start from an occupied position. |

| `FUT\_ONBOARDING\_SQUAD\_LOAN\_SELECT` | 190573 | Prompts the user to improve the squad with a high-rated loan. |

| `FUT\_ONBOARDING\_LOAN\_OVERLAY\_HEADER` | 225813 | Loan-player choice overlay. |

| `FUT\_ONBOARDING\_LOAN\_PLAYER\_SELECT\_CONFIRM` | 261163 | The loan signing replaces a named player. |

| `FUT\_LoanPlayerSigned` | 198633 | The retained success text says the replaced player was sent to the Club. This does not prove a particular reserve-slot move. |

| `FUT\_ONBOARDING\_LOAN\_FOOTER` | 179003 | The temporary card displays its permitted number of matches. |

| `FUT\_ONBOARDING\_KIT\_SELECT\_BODY` | 67123 | Select a kit, which can be changed later. |

| `FUT\_ONBOARDING\_BADGE\_SELECT\_BODY` | 324883 | Select a badge, which can be changed later. |

| `FUT\_ONBOARDING\_SUMMARY\_HEADING` | 77608 | Club-summary screen. |

| `FUT\_CHALKTUT\_WELCOME\_DESC\_1` | 9448 | Retained welcome/tutorial text refers to Starter Objectives; its post-summary timing has not been verified. |



Other extracted strings cover empty-position rejection, home/away kit and badge

selection, and a summary listing nation pack, starting eleven, loan player,

home kit, away kit, and badge. Their presence corroborates the native phases.

Older tutorial-match/transfer-upgrade text is also retained; do not assume it

is an additional mandatory first-run stage without a current caller.



The mined player catalog has 19,075 base records: 8,102 below rating 65, 8,570

from 65 through 74, and 2,403 rated 75 or above. Lower-rated players cover all

normal squad positions. These counts show that a modest, position-aware roster

can be built from local data; they do \*\*not\*\* reveal EA's exact starter-pack

rarity mix, nationality weighting, random seed, or loan pool.



The localization also retains `FUT\_STORE\_PACK\_43\_DESC` through

`FUT\_STORE\_PACK\_49\_DESC`. They describe 34 items: 22 players, one manager, five

club items, and six consumables; the quality split is 29 bronze, two silver,

and three gold \*\*across the entire pack\*\*, not necessarily across its players.

This is useful composition evidence, but it does not identify the active

starter-grant template. The native country view actually resolves its choices

as \*\*nation IDs\*\*, using `NationName\_%d\_upper` at `0x17A7A0`, not store pack IDs.

Do not map those seven retained pack IDs directly to country choices.



`FUT\_STORE\_PACK\_2214\_DESC` describes a separate stadium starter pack containing

a TIFO, stadium paint, and crowd chant. Its presence does not establish that the

country/loan tutorial automatically grants it at completion.



Generic `nations`, `teamkits`, and `competitionbadges` tables are catalogs, not

proof of an onboarding selection pool. A table named `playerloans` contains

real-football loan attributes such as loan-from team and loan end date, not FUT

temporary card choices.



The `cards\_ng\_db/manifest.json` export lists 221 nations, 3,449 teamkits, and

353 competitionbadges records. Full UI/config extraction is not available in

this export: `legacy-catalog-candidates.json` and `extracted-assets.json` are

empty. The provided FIFA 20 ZIP repeats the superstar seed and returns an empty

loan list; it is not an authoritative onboarding implementation to port.



\## Bootstrap already corrected



See \[fut-startup-contract.md](fut-startup-contract.md) and commit `1cf34c0`.



\- The old and `/v2/user/accountinfo` routes now share the native account body.

\- `userMassInfo` includes native `userInfo`, full `squad`, and

&#x20; `loanPlayers.loans\[].itemData`.

\- `userInfo.squadList.squad\[]` contains summaries with a string `squadType`.

\- Live HTTP checks and the user's rendered squad prove that the client can now

&#x20; consume the saved starter players. This is not proof of onboarding completion.



\## Recovered native operation contracts



The URL builder at `0x2B8BA0` resolves operation categories from the table at

`0x511BC0` (stride `0x28`, category at offset `+8`) against the base-path table

at `0x441AC0` (stride `0x10`). This distinguishes similar-looking suffixes.

Paths below use the observed PC namespace `/ut/game/fifa21`.



GET/POST labels below are supported by the native operation names. SignLoanPlayer

and SetOnboardingData both use helper `0x36A340`; named Post operations use

`0x36A580`, and list reads use `0x36A020`. These resolve through WebServiceImpl

to internal request kinds 1, 0, and 2 respectively (`0x2AE7C0`, `0x2AE590`,

`0x2AE9F0`). The final imported HTTP interface's verb mapping has not been

independently verified in this pass; capture methods before tightening routing.



\### Country choice and user creation



1\. Country callback `0x179590` selects a value from the service's native list.

&#x20;  `0x17A7A0` displays those values through `NationName\_%d\_upper`, establishing

&#x20;  that they are nation IDs. An internal log field called `starter\_pack\_id`

&#x20;  must not be mistaken for a store pack definition ID.

2\. It submits `/ut/game/fifa21/user/action/ONBOARDING\_COUNTRY\_<n>\_SELECTED`,

&#x20;  where `<n>` is the selected UI index plus one. The action builder is at

&#x20;  `0x311800`. \*\*The ordinal is not itself the nation ID.\*\*

3\. Success callback `0x179B30` records phase 0 and starts CreateUser at

&#x20;  `/ut/game/fifa21/user` (operation category `0x0D`). Serializer `0x310D60`

&#x20;  includes this body:



```json

{

&#x20; "useFut1Data": false,

&#x20; "clubName": "<chosen/local club name>",

&#x20; "clubAbbr": "<chosen/local abbreviation>",

&#x20; "purchased": false

}

```



The service constructor `0x289AA0` calls initializer `0x2A4DE0`. At

`0x2A54BB` it copies nine nation IDs into the list at service offset `0x12D28`;

getter `0x2A1020` supplies that same list to the country view. The initial

selected nation is 14. Order is fixed in this build:



| Action ordinal | Nation ID | Name in the mined `cards\_ng\_db/nations.json` |

| --- | --- | --- |

| 1 | 14 | England |

| 2 | 54 | Brazil |

| 3 | 18 | France |

| 4 | 52 | Argentina |

| 5 | 21 | Germany |

| 6 | 27 | Italy |

| 7 | 45 | Spain |

| 8 | 38 | Portugal |

| 9 | 34 | Holland (Netherlands) |



CreateUser's URI-extension method `0x310D50` immediately returns: there is no

extra nation query in this chain. The selected country is conveyed by the

preceding action ordinal, not a separate CreateUser body field. Preserve that

selection between the two requests; neither a settings-derived country list

nor a store-pack-ID mapping is needed to reproduce this native list.



CreateUser response parser `0x3111D0` consumes:



| Field | Type / parser |

| --- | --- |

| `bonusPacks` | Boolean |

| `login` | Native user object, parser `0x329190` |

| `squad` | Full native squad, parser `0x3248E0` |

| `starterPack` | Array of native item objects, item parser `0x321910` |

| `userData` | Native user-data object, parser `0x31F960` |



Returning a bare `/user` read response is not an equivalent creation response.



\### Loan selection



The list path is `/ut/game/fifa21/loan/players`, also supplied in bulk under

`loanPlayers`. Offer parser `0x3372F0` recognizes:



\- `defId`: integer offer identifier, later submitted as `LOAN\_ID`.

\- `ranking`: integer used to select eligible offers for the picker.

\- `loans`: integer field separate from the item itself.

\- `itemData`: native player item object.

\- `transferValue`, `gamesPlayed`, `goalsScored`: integer fields; `bio`: string.



Presence in the parser does not prove that every statistic is mandatory.

However, \*\*`itemData` alone is not enough for the picker\*\*. View `0x10A7E0`

allocates five slots, prioritizes matching-position offers with rankings 1–10,

then fills from rank-1 offers in other positions. No eligible offers leads to

the `ServerFatalError` path at `0x10AF17`. Missing/zero rankings can therefore

leave the choice list unusable even when many valid player items are present.



The ID chain is exact: JSON `defId` → first integer of the offer record → UI

`LOAN\_ID` at `0x10ACA7` / `0x10AE77` → request builder `0x2FDD50` →

`/ut/game/fifa21/loan/player/<defId>`. Do not assume this is the owned item ID

or blindly equate it with the resource ID. The separate `FIFA\_ID` comes from

the item's resource ID masked with `0xFFFFFF` and is cached locally.



SignLoanPlayer response parser `0x2FDDE0` directly invokes native item parser

`0x321910`: \*\*the response is a bare native item\*\*, not an `itemData` wrapper.

The current server's outer response shape is correct on this point.



Success callback `0x109DF0` uses the returned owned-item ID in a subsequent

ordinary `/item` update. Serializer `0x2CB410` puts it in `itemData\[]` with

`pile: "club"`; phase-2 completion follows at `0x10A010`. Signing is therefore

not the entire replacement transaction. The later squad save must be traced

and tested, including its possible onboarding marker, before declaring that

the selected starter has safely moved to Club and the loan occupies its slot.



\### Home and away kits



Onboarding cosmetic operations use category `0x30`, base `ut/%s/onboarding`.



\- GetOnboardingKits: `/ut/game/fifa21/onboarding/kits`.

&#x20; Parser `0x372050` consumes `homeItemDataList` and `awayItemDataList`, each an

&#x20; array of native item objects.

\- PostOnboardingKits: the same path. Serializer `0x315AC0` sends

&#x20; `{"homeKitId":<int>,"awayKitId":<int>}`.

\- Response parser `0x315BC0` expects `homeKitId` and `awayKitId` as 64-bit IDs.

&#x20; Callback `0x190770` registers the returned owned-item IDs as equipped slots

&#x20; 3 and 4. Keep selected definition IDs distinct from granted instance IDs.



There are two UI phases but a paired kit-selection request. Do not assume an

independent grant/commit for each screen without tracing its submit callback.



\### Badge



\- GetOnboardingBadges: `/ut/game/fifa21/onboarding/badges`.

&#x20; Parser `0x35BCC0` consumes `badgeItemDataList`, an array of native item objects.

\- PostOnboardingBadge: `/ut/game/fifa21/onboarding/badge/<resourceId>`.

&#x20; Builder `0x2ADB90` has no request-body serializer.

\- Response parser `0x2FCDB0` expects `{"badgeId":<int64>}`. Callback `0x105310`

&#x20; registers that returned owned-item ID as equipped slot 2.



These operations require saved/granted items, not just a cosmetic name or an

empty success object.



\### Progress versus cosmetic endpoint namespaces



SetOnboardingData is category `0x26`, base `ut/%s/clientdata`, plus the suffix

`/onboarding` from builder `0x30A780`. Its full path is therefore:



```text

/ut/game/fifa21/clientdata/onboarding

```



Serializer `0x30A7A0` sends `{"entries":\[{"key":0,"value":<int>}]}`.

This is \*\*not\*\* `/ut/game/fifa21/onboarding`, the cosmetic base.



Mass-response parser `0x3679D0` consumes separate `loanPlayerClientData` and

`onboardingClientData` sections, each with `entries` key/value data. Its resume

state is not the eight-phase analytics number:



| Input | Native effect | Resume destination |

| --- | --- | --- |

| No recovered selection markers; initial internal state 0 | No completed loan selection | `GoToSignLoan` |

| `loanPlayerClientData`, key 0 | Advance internal state to at least 1; cache the value as the selected FIFA/player definition ID | `GoToKitSelect`, unless later progress already exists |

| `onboardingClientData`, key 1 or 2, nonzero value | Advance internal state to at least 2 | `GoToBadgeSelect`, unless later progress already exists |

| `onboardingClientData`, key 3, nonzero value | Setter remaps requested state 3 to internal state 4 | Normal continuation, with an optional club-name branch |



Getter `0x2A0E90` reads service offset `0x12D24`. Setter `0x2A0EA0` maps 3 to

4, then only advances the stored value. Navigation at `0x040FCF`–`0x041026`

chooses loan for 0, kits for 1, badge for other nonzero/non-4 values, and skips

selection for 4. Badge initialization `0x104E00` can reconstruct its display

using the saved loan player and equipped kit. Do not return a 900-billion-range

owned instance ID as the loan client-data value: this parser uses a 32-bit

player/definition ID, with service helper `0x2A0EC0` matching the low 24 bits

of item resource IDs.



Completion acknowledgement has another distinct meaning:



1\. `onboardingClientData` key 0, value 1 sets the local

&#x20;  `ONBOARDING\_JUST\_FINISHED` boolean at service offset `0x12D01`.

2\. Unassigned-screen initialization `0x15E9C0` consumes and clears that boolean,

&#x20;  then constructs SetOnboardingData with \*\*key 0, value 3\*\*.

3\. SetOnboardingData value 2 emits `fut\_onboarding\_flow\_quit` telemetry in

&#x20;  `0x2A11B0`. This does not establish deletion or rollback semantics.



Summary confirmation `0x1A3930` emits phase-7 telemetry, calls helper

`0x2A0FA0` to set a different local boolean at `0x12D02`, then dispatches

`advance` or `GoToReturningUser`. It does not directly prove a persisted

completion write. Selection completion, summary presentation, just-finished

acknowledgement, and later objectives must remain separate states.



The string `ONBOARDING\_SET\_COMPLETED` occurs in an SBC view-model context;

it is not evidence of this first-run completion contract. The optional

`GoToChangeClubName` branch in `0x041179`–`0x0411CB` also means that reaching

internal state 4 is not proof of the identical final screen for every account.



\## Current server gaps, phase by phase



Source: `localfut/runtime-source/localfut21/server.py`.



| Area | Current behavior | Consequence / correction required |

| --- | --- | --- |

| New-club identity | `State.\_init()` immediately creates items and a squad if absent. Account-info always exposes an existing club and `returningUser: 1`. | There is no real pending-country/pending-club state. Do not merely clear the account response before implementing the following creation transaction. |

| Starter creation | `\_seed\_items()` takes up to 14 hard-coded star IDs. `\_seed\_squad()` puts the first 11 in sequential indices. | Wrong starter quality, wrong positions, and empty substitutes/reserves. The native starter request must generate and persist a coherent roster exactly once. |

| Create-user request | `/user` returns `\_native\_user()` regardless of HTTP method. | A creation body would be ignored rather than applied. Exact native request/response details must govern a method-specific implementation. |

| Onboarding configuration | `/settings` exposes only three store enablement entries. | Other settings may be incomplete, but the nine country choices are native constants, not missing settings entries. |

| Loan previews | `\_loan\_player\_choices()` supplies only `itemData`: five highest-rated players per position, seven loan games, no offer `defId` or `ranking`. | The native picker requires ranked offer metadata, not just correctly nested players. The pool and duration are local defaults, not recovered EA rules. |

| Loan signing | `/loan/player/<id>` treats the path ID as a resource ID, accepts PUT/POST, grants to `unassigned`, then separately stores two progress keys. | Resolve a validated offer `defId`; retain the correct bare-item response. Verify the following club move/squad save. A crash between independent commits can leave grant/progress inconsistent. |

| Loan progress | `/clientdata/loanplayerreward` always returns `entries: \[]`, even if a loan was already granted. | Returning/restarted clients cannot learn the stored loan selection from this response. |

| Other client data | `/clientdata/tutorialpopups` is empty, some other routes are constant, and generic client-data writes are absent. | The game may repeat steps or see success without saved progress. Use typed native key/value contracts and actual persistence. |

| Kits and badge | `\_active\_club\_items()` constructs fixed response-only cosmetics with fixed instance IDs. | There is no onboarding choice pool, selection grant, or saved home/away/badge selection. |

| Cosmetic activation | `/item` primarily updates an item's pile; it does not implement a general active-kit/badge selection transaction. | Do not assume that a generic inventory move completes the cosmetic tutorial. |

| Completion and resume | No dedicated first-run completion state is implemented. | Persist completion only after real selections/grants; return the same state in standalone and bulk bootstrap responses. |

| Unknown requests | Unknown routes return HTTP 200 `{}` with `X-LocalFUT15-Unknown: 1`. | Empty success can disguise missing onboarding operations. Diagnose by route/schema, not by status alone. |



The separate `offline\_data.py` utility also seeds only eleven squad bindings.

It deletes existing player records, squads, and unopened packs while reseeding;

it must not be run against the saved club as a generic onboarding fix.



\## Observed traffic and its limits



At 17:19:50 local time on 2026-08-30 the game issued:



```text

PUT /ut/game/fifa21/clientdata/squad

{"entries":\[{"key":5,"value":0}]}

```



The server logged this as unknown and returned an empty success response. The

meaning of squad client-data key 5 has not yet been established; its presence

does not justify assigning it an invented onboarding meaning.



Read-only probes confirmed that the running mass response has only `userInfo`,

`squad`, `loanPlayers`, and `credits`, and that loan progress is empty. The logs

contain both game traffic and our diagnostic GETs. Standalone squad/loan GET

counts cannot all be attributed to the game.



There is not yet a captured successful country creation, loan signing, kit or

badge selection, or full tutorial completion from this local implementation.



\## Implementation and verification rules



These are requirements for the upcoming implementation, not claimed behavior

of EA's server:



1\. Keep existing clubs intact. Test a genuinely new club in isolated local state

&#x20;  or take an explicit, recoverable migration path; never silently reseed users.

2\. Separate client UI phases from server progress keys. Mirror only recovered

&#x20;  meanings and serializers, not guesses based on phase numbers.

3\. Create the starter items, formation bindings, and initial progress together.

&#x20;  Match positions and populate substitutes/reserves; preserve real mined IDs.

4\. Make grants and progress updates transactional and retry-safe. A repeated

&#x20;  country/loan/cosmetic selection must not duplicate items or rewards.

5\. Distinguish preview definitions from owned item instances. Do not grant the

&#x20;  entire loan preview pool, fabricate resource IDs, or discard the replaced

&#x20;  starter without a verified operation.

6\. Make bulk and standalone user/squad/client-data responses reflect the same

&#x20;  saved state. Restarting must resume at the appropriate unfinished step.

7\. Verify each screen transition in the native game, then test interruption

&#x20;  after country, loan, home kit, away kit, badge, and final confirmation.

8\. After completion, exit/re-enter FUT and restart server/game. The normal hub

&#x20;  must return without repeating onboarding, losing selections, or regranting

&#x20;  starters/loans.



\## Remaining native-contract work



The screen sequence, country ordinals, creation body/parser, loan offer filter,

loan response shape, cosmetic contracts, and key-driven resume destinations

are recovered. The following are still unproven and must not be filled with

unlabelled assumptions:



\- EA's exact starter-grant template, ratings/rarities, nationality weighting,

&#x20; formation/bench composition, loan durations, and cosmetic offer pools. Those

&#x20; are server-side policy, not supplied in full by the local item catalogs.

\- The first-run versus existing/returning-club gate and the optional club-name

&#x20; branch: account-info shape alone is not proof of every gating field.

\- The final HTTP verb mapping for the imported transport interface.

\- The squad-save chain after a loan swap. URI builder `0x2E7D40` conditionally

&#x20; adds `?onboarding=true`, and builder `0x346250` conditionally adds

&#x20; `\&onboarding=true`; their applicability to this exact first-run transaction

&#x20; still needs a caller/traffic trace. Do not discard these query flags.

\- Which successful grant/save sets each server-side progress marker, when

&#x20; key 0 becomes 1, and the exact acknowledgement response required. Native

&#x20; read semantics do not alone establish a complete server write protocol.

\- Any mandatory post-summary objectives/stadium step: retained text is not an

&#x20; executed state machine.



Next implementation order: ranked loan offers and modest position-aware starter

creation; real country/user-creation state; loan move/squad persistence; paired

kits and badge grants; then completion/resume and fresh-club interruption tests.

Keep fresh-club tests isolated from existing saved users. Do not use FIFA 15's

`/historical` / `INTRO\_DONE` gate as a substitute: it describes the older game,

not this build of FIFA 21.

