"""Regenerate localfut/runtime-source/localfut21/sbc.json with FIFA 21 SBCs
that actually match the game.

The file shipped earlier was machine-extracted from an XML and was wrong:
real multi-challenge SBCs were collapsed into a single challenge and the
NATIONS/LEAGUES values were meaningless.  This rebuilds the catalogue from
hand-authored data, web-checked for the permanent Foundations SBCs
(fifauteam.com, dexerto.com) and best-effort tiered for the promo/player
sets that were time-limited.

Schema consumed by localfut21/server.py `_sbc_*`:
  sets[] : {id, tabId, name, description, repeatable, image, challenges[]}
  challenge : {id, order, name, difficulty, requirements:{rules:[...]}, rewards:{entries:[...]}}
  rule  : {"type": ..., "attributes": {...}}
        PLAYERS {min,max} CHEMISTRY {min} RATING {min} RARE {min} TOTW {min}
        NATIONS {min|exact}  (count of distinct nations)
        LEAGUES {min|exact}  (count of distinct leagues)
        SAME_NATION {max} SAME_LEAGUE {max} SAME_CLUB {max}
        QUALITY {value: "gold"|"silver"|"bronze"}
        AGE {max}
  reward: {"type": "PACK"|"COINS"|"XP"|"PLAYER",
           "attributes": {...}, "value": <coins/xp>}
        PACK    {pack: <key>, name: <display>, count: N}
        PLAYER  {resourceId, name, rating, position}
"""
from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parents[1] / \
    "localfut/runtime-source/localfut21/sbc.json"

TABS = [
    {"id": 0, "name": "Foundations", "icon": "sbc_tab_basic"},
    {"id": 1, "name": "Leagues", "icon": "sbc_tab_leagues"},
    {"id": 2, "name": "Nations", "icon": "sbc_tab_nations"},
    {"id": 3, "name": "Hybrid", "icon": "sbc_tab_hybrid"},
    {"id": 4, "name": "Upgrades", "icon": "sbc_tab_upgrades"},
    {"id": 5, "name": "Icons", "icon": "sbc_tab_icons"},
    {"id": 6, "name": "Players", "icon": "sbc_tab_players"},
    {"id": 7, "name": "Challenges", "icon": "sbc_tab_challenges"},
    {"id": 8, "name": "Special", "icon": "sbc_tab_special"},
]

PACK_TYPES = [
    {"id": "bronze", "name": "Bronze Pack"},
    {"id": "silver", "name": "Silver Pack"},
    {"id": "premium_silver_players", "name": "Premium Silver Players Pack"},
    {"id": "gold", "name": "Gold Pack"},
    {"id": "premium_gold", "name": "Premium Gold Pack"},
    {"id": "gold_players", "name": "Gold Players Pack"},
    {"id": "premium_gold_players", "name": "Premium Gold Players Pack"},
    {"id": "electrum_players", "name": "Electrum Players Pack"},
    {"id": "prime_electrum_players", "name": "Prime Electrum Players Pack"},
    {"id": "prime_gold_players", "name": "Prime Gold Players Pack"},
    {"id": "prime_mixed_players", "name": "Prime Mixed Players Pack"},
    {"id": "rare_players", "name": "Rare Players Pack"},
    {"id": "rare_mixed_players", "name": "Rare Mixed Players Pack"},
    {"id": "rare_gold", "name": "Rare Gold Pack"},
    {"id": "jumbo_rare_players", "name": "Jumbo Rare Players Pack"},
    {"id": "mega", "name": "Mega Pack"},
    {"id": "rare_mega", "name": "Rare Mega Pack"},
    {"id": "ultimate", "name": "Ultimate Pack"},
    {"id": "player_pick", "name": "Player Pick"},
]
_PACK_NAME = {p["id"]: p["name"] for p in PACK_TYPES}


# ---- tiny builders --------------------------------------------------------
def rules(**kw):
    """kw: players=(mn,mx) rating=mn chem=mn rare=mn totw=mn nations=('min'|'exact',n)
    leagues=(...) same_nation=mx same_league=mx same_club=mx quality='gold' age=mx"""
    out = []
    pl = kw.get("players", (11, 11))
    out.append({"type": "PLAYERS", "attributes": {"min": pl[0], "max": pl[1]}})
    if kw.get("rating"):
        out.append({"type": "RATING", "attributes": {"min": kw["rating"], "max": 99}})
    if kw.get("chem"):
        out.append({"type": "CHEMISTRY", "attributes": {"min": kw["chem"], "max": 100}})
    if kw.get("rare"):
        out.append({"type": "RARE", "attributes": {"min": kw["rare"]}})
    if kw.get("totw"):
        out.append({"type": "TOTW", "attributes": {"min": kw["totw"]}})
    for key, rtype in (("nations", "NATIONS"), ("leagues", "LEAGUES")):
        if kw.get(key):
            mode, n = kw[key]
            out.append({"type": rtype, "attributes": {mode: n}})
    if kw.get("same_nation"):
        out.append({"type": "SAME_NATION", "attributes": {"max": kw["same_nation"]}})
    if kw.get("same_league"):
        out.append({"type": "SAME_LEAGUE", "attributes": {"max": kw["same_league"]}})
    if kw.get("same_club"):
        out.append({"type": "SAME_CLUB", "attributes": {"max": kw["same_club"]}})
    if kw.get("quality"):
        out.append({"type": "QUALITY", "attributes": {"value": kw["quality"]}})
    if kw.get("age"):
        out.append({"type": "AGE", "attributes": {"max": kw["age"]}})
    return {"rules": out}


def pack(key, count=1):
    return {"type": "PACK",
            "attributes": {"pack": key, "name": _PACK_NAME.get(key, "Pack"), "count": count}}


def coins(v):
    return {"type": "COINS", "attributes": {}, "value": v}


def xp(v):
    return {"type": "XP", "attributes": {}, "value": v}


def player(rid, name, rating, pos):
    return {"type": "PLAYER",
            "attributes": {"resourceId": rid, "name": name, "rating": rating, "position": pos}}


def rewards(*entries):
    return {"entries": list(entries)}


_sid = [0]


def chal(name, req, rew, difficulty=2):
    _sid[0] += 1
    return {"id": f"CH_{_sid[0]}", "order": 0, "name": name, "difficulty": difficulty,
            "requirements": req, "rewards": rew}


def sbc_set(sid, tab, name, desc, challenges, repeatable=False):
    for i, c in enumerate(challenges, 1):
        c["order"] = i
    return {"id": sid, "tabId": tab, "name": name, "description": desc,
            "repeatable": repeatable, "image": f"sbc_set_image_{1001000 + tab}",
            "state": "available", "challenges": challenges,
            "challengeCount": len(challenges),
            "challengeIds": [c["id"] for c in challenges]}


SETS = []

# ======================================================================
# FOUNDATIONS  (permanent, web-verified: fifauteam.com / dexerto.com)
# ======================================================================
SETS.append(sbc_set(
    "FND_LETS_GET_STARTED", 0, "Let's Get Started",
    "The first steps into Squad Building Challenges.",
    [
        chal("First Exchange",
             rules(players=(1, 1)),
             rewards(pack("bronze"), xp(50)), 1),
        chal("The Second Step",
             rules(players=(3, 3), chem=12),
             rewards(pack("bronze"), xp(75)), 1),
        chal("The Third Step",
             rules(players=(4, 4), chem=17, nations=("min", 3)),
             rewards(pack("silver"), xp(100)), 1),
    ]))

SETS.append(sbc_set(
    "FND_LEAGUE_NATION_BASICS", 0, "League and Nation Basics",
    "Learn how leagues and nations drive chemistry.",
    [
        chal("One League Attack",
             rules(players=(5, 5), chem=45, leagues=("exact", 1), nations=("min", 5)),
             rewards(pack("premium_silver_players"), xp(100)), 2),
        chal("One Nation Midfield",
             rules(players=(5, 5), chem=45, nations=("exact", 1)),
             rewards(pack("premium_gold"), xp(100)), 2),
        chal("Multi League & Nation",
             rules(players=(6, 6), chem=48, rating=55, nations=("min", 2), leagues=("min", 2)),
             rewards(pack("premium_gold"), xp(125)), 2),
    ]))

SETS.append(sbc_set(
    "FND_LETS_KEEP_GOING", 0, "Let's Keep Going",
    "Build small squads that hold their chemistry together.",
    [
        chal("Brick Slots",
             rules(players=(2, 2), chem=43, leagues=("min", 2), nations=("min", 2)),
             rewards(pack("bronze"), xp(75)), 2),
        chal("The Correct Position",
             rules(players=(3, 3), chem=12, leagues=("min", 3), nations=("min", 3)),
             rewards(pack("silver"), xp(100)), 2),
        chal("Perfect Links",
             rules(players=(5, 5), chem=40, same_nation=2, same_club=2),
             rewards(pack("premium_silver_players"), coins(500), xp(125)), 3),
        chal("Loyal Lads",
             rules(players=(3, 3), chem=30, nations=("exact", 1), leagues=("min", 3)),
             rewards(pack("premium_silver_players"), xp(125)), 3),
    ]))

SETS.append(sbc_set(
    "FND_HYBRID_NATIONS", 3, "Hybrid Nations",
    "Squads spanning many nations. A permanent chemistry test.",
    [
        chal("The Final Four",
             rules(rating=70, chem=80, nations=("exact", 4), same_nation=4, same_club=4, rare=4),
             rewards(pack("electrum_players"), xp(150)), 3),
        chal("Six of the Best",
             rules(rating=75, chem=85, nations=("exact", 6), same_nation=3, same_club=3),
             rewards(pack("gold_players"), xp(150)), 3),
        chal("Elite Eight",
             rules(chem=88, nations=("exact", 8), same_nation=2, same_club=3, rare=5, quality="gold"),
             rewards(pack("prime_gold_players"), xp(200)), 4),
        chal("Around the World",
             rules(rating=81, chem=100, nations=("exact", 10), rare=8),
             rewards(pack("rare_players"), xp(250)), 5),
    ]))

SETS.append(sbc_set(
    "FND_HYBRID_LEAGUES", 3, "Hybrid Leagues",
    "Squads spanning many leagues. A permanent chemistry test.",
    [
        chal("Give Me Five",
             rules(rating=69, chem=80, leagues=("exact", 5), same_league=4, same_club=4, rare=6),
             rewards(pack("prime_mixed_players"), xp(150)), 3),
        chal("Seven-League Boots",
             rules(rating=78, chem=85, leagues=("exact", 7), same_league=3, same_club=3),
             rewards(pack("prime_electrum_players"), xp(150)), 4),
        chal("The Whole Nine Yards",
             rules(rating=80, chem=99, leagues=("exact", 9), same_league=2, same_club=2, rare=6),
             rewards(pack("mega"), xp(200)), 4),
        chal("First XI",
             rules(chem=100, leagues=("exact", 11), rare=8, quality="gold"),
             rewards(pack("rare_players"), xp(250)), 5),
    ]))

SETS.append(sbc_set(
    "FND_LEAGUE_NATION_HYBRID", 3, "League and Nation Hybrid",
    "The permanent puzzle: balance nations and leagues at once.",
    [
        chal("The Challenger",
             rules(chem=100, nations=("exact", 2), leagues=("exact", 3), quality="gold"),
             rewards(pack("rare_mixed_players"), xp(150)), 3),
        chal("Advanced",
             rules(rating=79, chem=100, nations=("exact", 3), leagues=("exact", 5)),
             rewards(pack("premium_gold_players"), xp(175)), 4),
        chal("Fiendish",
             rules(rating=81, chem=90, nations=("exact", 6), leagues=("exact", 6),
                   same_nation=3, same_league=3),
             rewards(pack("mega"), xp(200)), 4),
        chal("Puzzle Master",
             rules(rating=82, chem=85, nations=("exact", 9), leagues=("exact", 7), same_club=3),
             rewards(pack("rare_mega"), xp(250)), 5),
    ]))

SETS.append(sbc_set(
    "FND_ADVANCED", 0, "Advanced",
    "Higher-rated squads for better rewards.",
    [
        chal("Gold Rush",
             rules(rating=75, chem=70, quality="gold"),
             rewards(pack("premium_gold"), xp(150)), 3),
        chal("Rising Standard",
             rules(rating=80, chem=75, rare=5),
             rewards(pack("gold_players"), xp(175)), 3),
        chal("Marquee Squad",
             rules(rating=84, chem=80, rare=8),
             rewards(pack("prime_gold_players"), coins(2500), xp(200)), 4),
        chal("Elite Standard",
             rules(rating=86, chem=85, rare=11),
             rewards(pack("rare_players"), coins(5000), xp(250)), 5),
    ]))

# ======================================================================
# LEAGUE SBCs  (one tiered set per league; nation/league IDs are resolved
# by the client from a player's own data, so we only assert squad-wide
# counts + rating + chem here)
# ======================================================================
LEAGUES = [
    ("PL", "Premier League", "Premier League"),
    ("LALIGA", "LaLiga", "LaLiga"),
    ("SERIEA", "Serie A", "Serie A"),
    ("BUNDESLIGA", "Bundesliga", "Bundesliga"),
    ("LIGUE1", "Ligue 1", "Ligue 1"),
]
for code, name, blurb in LEAGUES:
    SETS.append(sbc_set(
        f"LEAGUE_{code}", 1, name,
        f"Submit squads built around {blurb} players.",
        [
            chal("Bronze Tier",
                 rules(rating=68, chem=60, same_league=11, leagues=("exact", 1)),
                 rewards(pack("premium_gold"), coins(500), xp(100)), 2),
            chal("Silver Tier",
                 rules(rating=78, chem=75, same_league=11, leagues=("exact", 1), rare=4),
                 rewards(pack("gold_players"), coins(1500), xp(150)), 3),
            chal("Gold Tier",
                 rules(rating=84, chem=85, same_league=11, leagues=("exact", 1), rare=8),
                 rewards(pack("prime_gold_players"), coins(4000), xp(200)), 4),
        ]))

# ======================================================================
# NATION SBCs
# ======================================================================
NATIONS = [
    ("ENG", "England"), ("ESP", "Spain"), ("GER", "Germany"),
    ("ITA", "Italy"), ("BRA", "Brazil"), ("ARG", "Argentina"),
    ("FRA", "France"), ("POR", "Portugal"),
]
for code, name in NATIONS:
    SETS.append(sbc_set(
        f"NATION_{code}", 2, name,
        f"Submit squads built around {name} players.",
        [
            chal("Bronze Tier",
                 rules(rating=70, chem=65, same_nation=11, nations=("exact", 1)),
                 rewards(pack("premium_gold"), coins(500), xp(100)), 2),
            chal("Silver Tier",
                 rules(rating=80, chem=80, same_nation=11, nations=("exact", 1), rare=5),
                 rewards(pack("gold_players"), coins(1500), xp(150)), 3),
            chal("Gold Tier",
                 rules(rating=85, chem=90, same_nation=11, nations=("exact", 1), rare=9),
                 rewards(pack("prime_gold_players"), coins(4000), xp(200)), 4),
        ]))

# ======================================================================
# UPGRADE SBCs  (single-challenge, repeatable, pick/pack rewards)
# ======================================================================
UPGRADES = [
    ("UP_82", "82+ Player Pick", "player_pick", 82, 80,
     "Guaranteed 82+ rated player pick."),
    ("UP_84", "84+ Player Pick", "player_pick", 84, 85,
     "Guaranteed 84+ rated player pick."),
    ("UP_85", "85+ Upgrade", "prime_gold_players", 85, 90,
     "Guaranteed 85+ rated players pack."),
    ("UP_86", "86+ Player Pick", "player_pick", 86, 95,
     "Guaranteed 86+ rated player pick."),
    ("UP_88", "88+ Player Pick", "player_pick", 88, 100,
     "Guaranteed 88+ rated player pick."),
]
for code, name, pk_key, rat, chem, blurb in UPGRADES:
    SETS.append(sbc_set(
        f"{code}", 4, name, blurb,
        [chal(name,
              rules(rating=rat, chem=chem, rare=11),
              rewards(pack(pk_key)), 4)],
        repeatable=True))

# ======================================================================
# ICON SBCs  (Base Icon single big challenge -> the Icon card)
# ======================================================================
ICONS = [
    ("Pele", "Pelé", 5000063, 98, "CAM"),
    ("Cruyff", "Johan Cruyff", 5000107, 97, "CF"),
    ("Ronaldinho", "Ronaldinho", 5000123, 97, "CAM"),
    ("Gullit", "Ruud Gullit", 5000103, 91, "CAM"),
    ("Eusebio", "Eusébio", 5000132, 94, "ST"),
    ("Matthaus", "Lothar Matthäus", 5000100, 93, "CDM"),
    ("Vieira", "Patrick Vieira", 5000106, 90, "CDM"),
    ("R9", "R9 Ronaldo", 5000112, 96, "ST"),
]
for code, name, rid, rating, pos in ICONS:
    SETS.append(sbc_set(
        f"ICON_{code}", 5, name,
        f"Complete the tiered challenge to earn {name} ({rating} {pos}).",
        [
            chal("Born Legend",
                 rules(rating=82, chem=75, rare=5),
                 rewards(pack("prime_gold_players"), xp(150)), 3),
            chal("The Rise",
                 rules(rating=85, chem=85, rare=8, totw=1),
                 rewards(pack("mega"), xp(200)), 4),
            chal("Legendary",
                 rules(rating=87, chem=95, rare=11, totw=2),
                 rewards(player(rid, name, rating, pos)), 5),
        ]))

# ======================================================================
# PLAYER SBCs  (Flashback / special player cards, 1-2 challenges)
# ======================================================================
PLAYERS_SBC = [
    ("MESSI", "Lionel Messi", 158023, 95, "RW"),
    ("KANTE", "N'Golo Kanté", 215914, 93, "CDM"),
    ("DEPAY", "Memphis Depay", 200145, 90, "CF"),
    ("SANE", "Leroy Sané", 225166, 90, "RM"),
    ("ZAHA", "Wilfried Zaha", 209658, 89, "LW"),
    ("COMAN", "Kingsley Coman", 213345, 90, "RM"),
    ("UMTITI", "Samuel Umtiti", 210514, 87, "CB"),
]
for code, name, rid, rating, pos in PLAYERS_SBC:
    SETS.append(sbc_set(
        f"PLAYER_{code}", 6, name,
        f"Complete both challenges to earn {name} ({rating} {pos}).",
        [
            chal("Foundation",
                 rules(rating=84, chem=80, rare=6),
                 rewards(pack("prime_gold_players"), xp(150)), 3),
            chal("Final Push",
                 rules(rating=86, chem=90, rare=10, totw=1),
                 rewards(player(rid, name, rating, pos)), 5),
        ]))

# ======================================================================
# PROMO CHALLENGE SBCs  (time-limited historically; plausible tiers)
# ======================================================================
PROMO_CH = [
    ("CH_TOTY", "TOTY Challenge", "TOTY", "rare_players"),
    ("CH_FUTURE", "Future Stars Challenge", "Future Stars", "rare_players"),
    ("CH_FREEZE", "FUT Freeze Challenge", "FUT Freeze", "rare_mixed_players"),
    ("CH_WHATIF", "What If Challenge", "What If", "rare_players"),
    ("CH_HEADLINERS", "Headliners Challenge", "Headliners", "rare_players"),
    ("CH_RTTF", "Road to the Final Challenge", "Road to the Final", "rare_players"),
    ("CH_SHAPESHIFTERS", "Shapeshifters Challenge", "Shapeshifters", "rare_mixed_players"),
]
for code, name, promo, pk_key in PROMO_CH:
    SETS.append(sbc_set(
        code, 7, name,
        f"A {promo} promo challenge. Submit the squad for a guaranteed pack.",
        [
            chal("Squad 1",
                 rules(rating=83, chem=80, rare=8),
                 rewards(pack("prime_gold_players"), xp(150)), 3),
            chal("Squad 2",
                 rules(rating=86, chem=90, rare=11, totw=2),
                 rewards(pack(pk_key), coins(5000), xp(250)), 5),
        ]))

# ======================================================================
# SPECIAL / SEASONAL SBCs  (Party Bag, Summer Stars, FOF, ...)
# ======================================================================
SPECIAL = [
    ("SP_PARTY", "Party Bag", "A lucky-dip pack for a mid-rated squad.", "premium_gold_players", 82, 75),
    ("SP_SUMMER", "Summer Stars", "Summer promo squad for a players pack.", "prime_gold_players", 84, 80),
    ("SP_FOF", "Festival of FUTball", "Festival of FUTball squad submission.", "mega", 85, 85),
    ("SP_FPD", "FUT Player Days", "FUT Player Days squad submission.", "prime_gold_players", 84, 80),
    ("SP_SILVER", "Silver Stars", "A silver-only squad for an untradeable pack.", "gold_players", 0, 60),
]
for code, name, blurb, pk_key, rat, chem in SPECIAL:
    r = dict(chem=chem)
    if rat:
        r["rating"] = rat
    if code == "SP_SILVER":
        r["quality"] = "silver"
    SETS.append(sbc_set(
        code, 8, name, blurb,
        [chal(name, rules(**r), rewards(pack(pk_key), coins(2500), xp(150)), 3)],
        repeatable=True))

# ---- emit --------------------------------------------------------------
doc = {
    "schema": "localfut-fifa21-sbc-catalog-2",
    "source": {"type": "hand-authored",
               "verified": "Foundations SBCs checked against fifauteam.com and dexerto.com; "
                           "promo/player/icon sets are best-effort tiered reconstructions"},
    "enabled": True,
    "tabs": TABS,
    "packTypes": PACK_TYPES,
    "sets": SETS,
    "counts": {"tabs": len(TABS), "sets": len(SETS),
               "challenges": sum(len(s["challenges"]) for s in SETS)},
}
OUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
print(f"wrote {OUT}")
print(f"  {doc['counts']['sets']} sets, {doc['counts']['challenges']} challenges")
