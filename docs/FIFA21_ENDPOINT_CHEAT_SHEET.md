# FIFA 21 endpoint cheat sheet

Shareable engineering reference for the Project 21 local FUT runtime. It uses the
same endpoint-family organization that proved useful on the older Aurora 15
server, but it does **not** copy that server's code or assume FIFA 15 payloads are
valid for FIFA 21.

Snapshot: 1 September 2026. Definitive FIFA 21 sources are
[`server.py`](../localfut/runtime-source/localfut21/server.py),
[`redirector_tls.py`](../localfut/runtime-source/redirector_tls.py), and the
tracked verification fixtures. Re-check those files after the runtime changes.

## Read this first

This document describes **code coverage**, not end-to-end game verification.

- **Implemented** means an explicit dispatcher branch builds a meaningful local
  response or mutation.
- **Partial** means useful local behavior exists, but a complete native feature
  flow, persistence path, or response family is still missing.
- **Stub** means the route deliberately returns an empty/minimal response.
- **Trace-only** means the server recognizes an observed exchange but returns no
  modeled payload.
- **Gap** means there is no explicit FIFA 21 implementation. Capture the retail
  request and typed response contract before adding it.

The FUT dispatcher currently returns HTTP 200 plus `{}` and
`X-LocalFUT15-Unknown: 1` for an unknown FUT path. That response is a diagnostic
fallback, **not endpoint support**. Always inspect this header and the
`Unknown FUT/POW request` log before calling a route implemented.

Methods marked `*` are the intended client method, but the current branch does
not strictly reject other methods. Tightening a method is a behavior change and
requires a client trace or regression test.

## Aurora-style family map

This is the reusable part of the Aurora 15 design: keep endpoint families and
state boundaries separate. It is an organization guide, not a payload mapping.

| Endpoint family | FIFA 21 equivalent in this repository | Current FIFA 21 coverage |
| --- | --- | --- |
| Boot/auth | LSX, SSF, redirector, Blaze, EASW, POW, `/ut/auth`, account/profile | Broad local bootstrap; several static/trace-only responses |
| Inventory/squads | `/club`, `/squad/*`, `/item*`, quick-sell | Main reads/mutations implemented; some lifecycle routes missing |
| Active club items | Stadium, kits, badges, consumables, equip/apply operations | Partial |
| Pack store/open | Purchase groups, purchased items, pack content/localization | Main local open/store lifecycle implemented |
| Market | Search, auctions, bids, watchlist, trade pile, settlement | Only empty trade-pile summary; major gap |
| Match/seasons | Match bootstrap, Squad Battles, result settlement, seasons | Bootstrap/Squad Battles partial; settlement and seasons missing |
| Tournaments/Draft | Tournament and Draft state machines | Draft state only; major gap |
| Concepts/objectives/SBCs | Concept squads and progression/challenge services | Gap |

## Connection sequence

1. **Platform/LSX:** EA App owns `127.0.0.1:3216` in `ea-app` mode, or the local
   LSX replacement owns it in `replacement` mode. Never run both owners.
2. **SSF:** `POST /sf/auth` establishes a local SSF session.
3. **Redirector:** TLS request to the `spring18` GOS redirector receives the
   local Blaze endpoint `127.0.0.1:44321`.
4. **Blaze/Fire2:** Util PreAuth, title login, login notifications, PostAuth,
   entitlements, configuration, telemetry, and ticker exchanges establish the
   game session.
5. **EASW/OSDK:** `/extension`, roster, routing, and allowlisted content support
   the transition from Blaze into FUT.
6. **POW:** `/pow/auth`, wallet, and catalog bootstrap the pre-FUT HTTP layer.
7. **FUT:** `/ut/auth`, shards, account/profile, settings, client data, club,
   squad, onboarding, store, and item routes build the FUT state.

A later-stage response cannot repair an earlier failure. Diagnose the first
missing or malformed transition in this order.

## Listener and port map

| Port | Transport | Service | Current status | Required at server startup? |
| ---: | --- | --- | --- | --- |
| 3216 | TCP | EA App LSX or local Origin LSX | Mode-dependent | Only when local LSX is selected |
| 10853 | HTTP | SSF bootstrap | Implemented | Yes |
| 42230 | HTTPS/TLS 1.2 | GOS redirector | Implemented by separate process | Yes in the launcher path |
| 44321 | TCP Fire2; HTTP fallback | Blaze | Partial | Yes |
| 17502 | HTTP + UDP | QoS | Implemented local loopback shapes | Yes |
| 8090 | HTTP | Primary FUT/POW/auth/CDN service | Implemented/partial by family | Yes |
| 8081 | HTTP | Legacy FUT compatibility listener | Same dispatcher as 8090 | No |
| 42232 | HTTP | EASW/OSDK/content | Partial | No |
| 42231 | HTTP | Cleartext redirector diagnostics | Debug compatibility | No |

The launcher also owns process selection, hosts redirection, certificate trust,
and the TLS redirector. A port listening by itself does not prove that the
listener belongs to this checkout or is running the current source.

## Local identity and token shim

These are synthetic loopback-only routes used by the native auth bridge. Do not
replace the values with real EA credentials or forward requests externally.

| Method | Path | Purpose | Status |
| --- | --- | --- | --- |
| GET | `/localfut/auth/bootstrap` | Version, source hashes, local auth code/client configuration | Implemented; strict method/query/body checks |
| GET | `/localfut/callback` | Empty local redirect target | Implemented |
| POST | `/connect/token` | Authorization-code form exchange for synthetic access/refresh tokens | Implemented; strict content type and fields |
| GET | `/connect/tokeninfo?access_token=...&client_id=...` | Synthetic user/persona lookup | Implemented; strict token/client validation |
| GET | `/localfut/test/status` | Isolated harness identity and DB paths | Test mode only; 404 in normal runtime |

The native helper and `/localfut/auth/bootstrap` are one versioned contract.
Ship and test them together.

## POW bootstrap

| Method | Path/pattern | Purpose | Status |
| --- | --- | --- | --- |
| GET/POST* | `/pow/auth` | Creates POW SID and timestamps | Implemented; method not constrained |
| GET* | `/pow/bank/user/account` | POW wallet shape | Stub: zero POW funds |
| GET* | `/pow/store/game/fifa21/catalog/list` | Catalog index | Partial: two catalog descriptors |
| GET* | `/pow/store/game/fifa21/catalog/0/item/list` | Catalog-zero items | Stub: empty, end-of-list |
| Any | `/pow/*` | Unknown POW fallback | Stub: 200 `{}`; never count as coverage |

## FUT login, boot, and profile

| Method | Path/pattern | Purpose | Status |
| --- | --- | --- | --- |
| GET/POST*; DELETE | `/ut/auth` | Create FUT SID; DELETE ends local session response | Implemented local shape |
| GET* | `/ut/shards`, `/ut/shards/v2` | Advertise local FUT HTTP endpoint | Implemented |
| GET* | `/ut/game/fifa21/resourcemapping` | CardsDLL mapping container | Implemented empty array; no remote mappings |
| GET* | `/ut/game/fifa21/user/accountinfo` | Persona, DOB/age, club identity | Implemented local profile |
| GET* | `/ut/game/fifa21/settings` | Feature configuration | Partial static feature flags |
| GET* | `/ut/game/fifa21/user` | Main native user/club bootstrap document | Implemented local state |
| GET* | `/ut/game/fifa21/userdata` | ELO/account reset flags | Stub/static |
| GET* | `/ut/game/fifa21/usermassinfo`, `/ut/game/fifa21/user/massinfo` | Bulk user/client-data bootstrap | Implemented through suffix aliases |
| GET* | `/ut/game/fifa21/clubuser` | Public persona lookup | Implemented local identity |
| GET* | `/ut/game/fifa21/hub` | Club, squad, trade/watch summaries | Partial; market counts are zero |
| GET* | `/ut/game/fifa21/eventfeed` | FUT event feed | Stub: empty event list |
| GET* | `/ut/game/fifa21/leaderboards/options` | Leaderboard configuration | Stub: empty object |
| GET* | `/ut/game/fifa21/club/stats/*` | Club summary/stat buckets | Implemented local aggregate |
| Any* | `/ut/game/fifa21/phishing/trusteddevice*` | Trusted-device gate | Implemented static trusted state |

## Client data and onboarding

| Method | Path/pattern | Purpose | Status |
| --- | --- | --- | --- |
| GET* | `/ut/game/fifa21/clientdata/tutorialpopups` | Tutorial popup state | Stub: none pending |
| GET, PUT | `/ut/game/fifa21/clientdata/onboarding` | Read/save onboarding state | Implemented local state |
| GET, PUT | `/ut/game/fifa21/clientdata/squad` | Read/save squad client markers | Implemented local state |
| GET* | `/ut/game/fifa21/clientdata/pilesize` | Pile limits | Implemented static limits |
| GET* | `/ut/game/fifa21/clientdata/userhubdata` | Hub progression markers | Implemented static values |
| GET* | `/ut/game/fifa21/clientdata/managerquest` | Manager quest markers | Implemented static values |
| GET, PUT | `/ut/game/fifa21/clientdata/loanplayerreward` | Loan-player onboarding marker | Implemented local state |
| GET, POST | `/ut/game/fifa21/onboarding/kits` | Offer kits / save selected home-away kits | Implemented |
| GET | `/ut/game/fifa21/onboarding/badges` | Offer starter badges | Implemented |
| POST | `/ut/game/fifa21/onboarding/badge/{resourceId}` | Save active starter badge and complete onboarding | Implemented |
| GET* | `/ut/game/fifa21/loan/players` | Loan-player choices until onboarding completes | Implemented |
| PUT, POST | `/ut/game/fifa21/loan/player/{offerId}` | Grant selected loan card | Implemented local mutation |

Onboarding is a state machine, not a list of independent 200 responses. Test the
ordered fresh-club flow: loan choices → loan grant → squad save → kit choices →
kit selection → badge choices → badge selection → completed client data.

## Squad, club, stadium, and items

| Method | Path/pattern | Purpose | Status |
| --- | --- | --- | --- |
| GET* | `/ut/game/fifa21/squad/active` | Complete active squad | Implemented |
| PUT, POST | `/ut/game/fifa21/squad/{squadId}` | Save a squad and onboarding loan binding | Implemented |
| GET, POST* | `/ut/game/fifa21/club` | Search/filter/page My Club inventory | Implemented for player, manager, kit/badge, and common filters |
| GET | `/ut/game/fifa21/club/consumables/development` | Consumables/development inventory | Partial; current local catalog |
| GET/PUT/POST* | `/ut/game/fifa21/stadium` | Read native stadium document; accept local save | Partial |
| GET | `/ut/game/fifa21/attributes/metadata?defIds=...` | Player attribute metadata | Implemented array response |
| GET | `/ut/game/fifa21/defid?defId={resourceId}` | Card variants for one base/resource ID | Implemented suffix alias |
| GET | `/ut/game/fifa21/item?idList=...` | Batch instance lookup | Implemented, up to 200 IDs |
| GET | `/ut/game/fifa21/item/{itemId}` | Single instance lookup | Implemented |
| PUT, POST | `/ut/game/fifa21/item` | Move items, store-all, equip kits, preserve duplicates | Implemented local mutation |
| POST | `/ut/game/fifa21/item/resource/{resourceId}` | Apply consumable definition to target item | Partial: position modifier path |
| DELETE | `/ut/game/fifa21/item/{itemId}` | Quick-sell one item | Implemented |
| POST | `/ut/delete/game/fifa21/item` | Quick-sell multiple items | Implemented |

Squad deletion exists in state code but has no explicit HTTP route. Treat it as
a gap, not an available endpoint.

## Store, packs, and presentation content

| Method | Path/pattern | Purpose | Status |
| --- | --- | --- | --- |
| GET* | `/ut/v2/game/fifa21/store/transaction` | Outstanding store transaction state | Stub: no transaction |
| GET* | `/ut/game/fifa21/store/purchasegroup/all` | Visible pack catalog | Implemented from local pack settings |
| GET | `/ut/game/fifa21/purchased/items` | Current unassigned/new items | Implemented |
| POST, PUT | `/ut/game/fifa21/purchased/items` | Buy/open a configured pack | Implemented local coins, generation, duplicates, transaction response |
| GET | `/fut/packs/loc/storepackdescriptions.en_us.xml` | Pack title/description localization | Implemented dynamically |
| GET | `/fut/packs/loc/storepackdescriptions.en_gb.xml` | Pack title/description localization | Implemented dynamically |
| GET | `/fut/items/images/backgrounds/itembgs/futitemraritytunables.json` | Item rarity presentation config | Allowlisted bundled/cache/CDN asset |
| GET | `/fut/packs/packopening/packopeningsetting.json` | Pack animation settings | Allowlisted bundled/cache/CDN asset |
| GET | `/fut/packs/packopening/packopeningconfig.json` | Pack animation config | Allowlisted bundled/cache/CDN asset |
| GET | `/fut/playerheads/g4/fut2dheads.big` | Base player-head archive | Allowlisted bundled/cache/CDN asset |
| GET | `/fut/playerheads/g4/single/p{resourceId}.dds` | Dynamic player head | On-demand validated cache; 404 enables client fallback |
| GET | `/fut/loc/pc/leaderboards.eng_us.xml` | Leaderboard localization | Intentional 404 |
| GET | `/fut/tutorials/cmn` | Tutorial content | Intentional 404 |

The allowlisted cache may fetch official public presentation assets when a
bundled/cache copy is absent. It does not provide account data or gameplay APIs.

## Market and trading

| Method | Path | Purpose | Status |
| --- | --- | --- | --- |
| GET* | `/ut/game/fifa21/tradepile` | Trade-pile summary | Stub: empty auctions |

Everything else in the market family is currently a **gap**: transfer search,
auction creation, bidding/buy-now, watchlist reads/mutations, sold/expired item
settlement, trade offers, and credit/item atomicity. The mature older-server
pattern is useful for dividing these into services, but its FIFA 15 paths and
payloads must not be transplanted. Capture FIFA 21 calls and implement one atomic
workflow at a time.

## Matches and game modes

| Method | Path/pattern | Purpose | Status |
| --- | --- | --- | --- |
| POST | `/ut/game/fifa21/match` | Native pre-match response with active squad | Partial; no result settlement |
| GET/POST* | `/ut/game/fifa21/match/reset` | Reset acknowledgement | Stub/static |
| GET | `/ut/game/fifa21/sqbt/user/hub` | Squad Battles hub/event/opponent index | Partial |
| GET | `/ut/game/fifa21/sqbt/user/opponentsquad/{slot}` | Detailed opponent squad | Partial local generation |
| GET | `/ut/game/fifa21/featuredsquad/user/stats*` | Squad Battles user stats/window | Partial static statistics |
| GET/POST/PUT | Any path containing `squadbattle`, `squad/battle`, or `squad_battle` | Compatibility opponent feed | Broad partial fallback; not proof of an exact endpoint |
| GET | `/ut/game/fifa21/squad/mode/draft/state?mode=...` | Current Draft run/entry state | Partial: state only |

Current gameplay gaps include match-result submission/settlement, rewards,
contracts/injuries/stat progression, online/offline seasons, tournaments, full
Draft entry/formation/captain/picks/opponents/rewards, objectives, SBCs, and most
mode-specific leaderboards. Use the older server only as a checklist of feature
families; obtain native FIFA 21 method/path/body/response evidence first.

## Redirector, QoS, EASW, and SSF

| Service | Method/path | Status and behavior |
| --- | --- | --- |
| TLS redirector 42230 | Client normally uses `POST /redirector/getServerInstance`; current handler accepts GET/POST on any path | Returns local Blaze host/port. Broad path acceptance should not be mistaken for multiple endpoints. |
| Cleartext redirector 42231 | GET/POST any path | Optional debug equivalent returning local Blaze host/port |
| QoS HTTP 17502 | `GET /qos/qos?qtyp=1|2` | Implemented XML latency/bandwidth probe config |
| QoS HTTP 17502 | `GET /qos/firewall` | Implemented loopback firewall probe config |
| QoS HTTP 17502 | `GET /qos/firetype` | Implemented static firewall type |
| QoS UDP 17502 | DirtySDK probe datagrams | Implemented for known ping/bandwidth request IDs; verify with synthetic test before changing |
| EASW 42232 | `POST /extension` | Partial static session/token headers and result XML |
| EASW 42232 | `GET /roster` | Partial static PC roster/schema XML |
| EASW 42232 | `GET /routing`, `/routing/cfgrouting.xml` | Implemented allowlisted OSDK routing XML |
| EASW 42232 | `GET /ctl` | Stub success document |
| EASW 42232 | `GET /contentfifa/*`, `/content/fifa/*` | Serves existing non-placeholder files under the local content root; missing content is a real 404 |
| SSF 10853 | `POST /sf/auth` | Implemented local SID/timestamps; other paths and all GETs return 404 |

## Blaze Fire2 command matrix

Component and command values are decimal unless a hexadecimal alias is shown.
An `unknown-empty` reply is a trace aid, not component support.

| Component | Command | Label | Status |
| ---: | ---: | --- | --- |
| 9 | 7 | `Util.PreAuth` | Implemented captured-compatible config/QoS payload |
| 9 | 2 | `Util.Ping` | Implemented server time |
| 9 | 1 | `Util.FetchClientConfig` | Implemented per-CFID map |
| 9 | 8 | `Util.PostAuth` | Implemented FIFA post-auth payload |
| 9 | 5 | `Util.GetTelemetryServer` | Implemented local telemetry shape |
| 9 | 12 | `Util.GetUserOptions` | Implemented `FirstTimeFlag=0` map |
| 9 | 10 | `Util.GetUserOption` | Partial single-key response |
| 9 | 22, 28 | Client state / metrics | Trace-only empty replies |
| 35 | 10 | `FIFA35.LoginSession` | Implemented title-specific session bootstrap |
| 10 | 5 | `Subscription.SetState` | Implemented RSUB echo/state response |
| 2249 | 1 | `Ticker.GetSettings` | Implemented local filter list |
| 2249 | 2 | `Ticker.GetGroups` | Implemented local group list |
| 1 | 32 (`0x20`) | `Authentication.ListEntitlements` | Implemented local entitlement list |
| 1 | 40, 50, 60, 152, 10 | Local login variants | Implemented shared local login payload |
| 1 | 29, 48 | Entitlement variants | Implemented shared entitlement payload |
| 1 | 36 (`0x24`) | `Authentication.GetAuthToken` | Implemented local auth token |
| 1 | 30, 90, 100, 110 | Account/persona variants | Partial shared auth/account payload |
| 1 | 70 (`0x46`) | `Authentication.Logout` | Trace-only empty reply |
| 30722 | 20, 8 | UserSessions bootstrap calls | Trace-only empty replies |
| 21 | 10 | Captured bootstrap call | Trace-only empty reply |
| 15 | 2 | Captured bootstrap call | Trace-only empty reply |
| 10 | 1 | Captured bootstrap call | Trace-only empty reply |
| 25 | 6 | Captured bootstrap call | Trace-only empty reply |
| 11 | 2600, 1600 | Captured title calls | Trace-only empty replies |
| 7 | 15, 3 | Captured game calls | Trace-only empty replies |
| 0 | 0 | Captured empty call | Trace-only empty reply |

After `FIFA35.LoginSession`, the server emits UserSessions notifications in this
order: component 30722 commands 2 (`UserAdded`), 8 (`LoginSession`), 1
(`ExtendedDataUpdate`), and 5 (`UserUpdated`). Order is part of the login contract.

Fire2 ping messages (message type 4) receive ping replies (type 5). All other
unhandled request commands receive an empty reply and a diagnostic capture. Never
interpret the empty reply as a modeled command.

## Local LSX operation matrix

The replacement listener performs the OriginSDK challenge first, then handles
encrypted NUL-delimited XML operations. EA App mode bypasses this listener.

| Operation | Status |
| --- | --- |
| `ChallengeResponse` / `ChallengeAccepted` | Implemented protocol 2/3 handshake |
| Signed-in `Login` event | Emitted after successful handshake |
| `GetConfig` | Implemented service/facility list |
| `GetSettings`, `GetSetting` | Implemented local environment/language/offline flags |
| `GetInternetConnectedState`, `GetUtcTime` | Implemented |
| `GetAuthToken`, `GetAuthCode` | Implemented synthetic local values |
| `GetProfile` | Implemented local user/persona/age profile |
| `GetBlockList`, `QueryFriends`, `QueryAreFriends` | Stub empty responses |
| `QueryContent`, `QueryEntitlements` | Stub empty responses |
| `GetPresence`, `QueryPresence` | Stub empty responses |
| `GetWalletBalance` | Stub zero balance |
| `GetGameInfo`, `GetAllGameInfo` | Implemented local installed/owned/released state |
| `RequestLicense` | Implemented schema-compatible empty local license response; it does not grant a retail license |
| Any other operation | Generic success plus warning; trace before modeling |

## Coverage gaps to prioritize

The family separation used on the mature older server suggests this order, but
each FIFA 21 route still needs native evidence:

1. **Unknown-route inventory:** collect method, path, query, request body, response
   consumer, and first client failure. Replace one generic 200 at a time.
2. **Onboarding replay:** verify a fresh club reaches the normal hub with correct
   squad, loan, kits, badge, client markers, and persistence after restart.
3. **Inventory completeness:** managers, all consumable categories, active items,
   squad delete/create variants, duplicates, pile limits, and atomic item moves.
4. **Store lifecycle:** catalog refresh, purchase validation, pack reveal, Store
   All, duplicates, quick-sell, restart persistence, and failure rollback.
5. **Market lifecycle:** search → list → bid/buy-now → watch/trade pile → expiry →
   settlement. Credits and item ownership must change atomically.
6. **Match lifecycle:** create → opponent/mode bootstrap → result submission →
   settlement → rewards → contracts/stats → idempotent retry.
7. **Modes:** Squad Battles completion, Draft, seasons, tournaments, objectives,
   SBCs, leaderboards, and event feeds.
8. **Protocol hardening:** replace Blaze trace-empty and broad HTTP success fallbacks
   only when capture evidence identifies the required typed response.

## Verification shortcuts

These checks use synthetic/local data and do not prove native gameplay:

```powershell
# Exact 20-route read-only HTTP smoke fixture
py -3 localfut/runtime-source/localfut21/verify_live_fut.py

# Synthetic Fire2 request/reply stages
py -3 localfut/runtime-source/localfut21/verify_blaze_flow.py

# Combined synthetic Blaze plus read-only FUT session check
py -3 localfut/runtime-source/localfut21/verify_local_session.py

# Source/bootstrap/auth contract self-test
node localfut/verify-auth-bootstrap.mjs --self-test
```

The user has asked that Python not be used for binary analysis. These commands
only execute the repository's existing runtime verification tools. If that is
still undesirable on a machine, use the Node auth self-test and inspect native
traffic with the approved debugger workflow.

For every manual native test, record:

- game executable product version/hash;
- launcher/runtime commit;
- LSX mode and verified process owner;
- first relevant request and response;
- whether the result survived a clean restart;
- exact screen reached and remaining failure;
- whether evidence is source-only, synthetic, isolated runtime, or live client.

Do not report "FUT works" from a healthy port, HTTP 200, successful login, or a
single rendered screen. State exactly which transition and feature were verified.
