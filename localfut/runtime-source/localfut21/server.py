from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import logging
import os
import random
import re
import secrets
import html
import socket
import socketserver
import sqlite3
import struct
import sys
import threading
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from user_config import read_user_config, user_config_root

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception as _crypto_import_error:
    padding = Cipher = algorithms = modes = None
else:
    _crypto_import_error = None

APP_NAME = "FIFA21 Local FUT"
VERSION = "0.2-beta2"
ROOT = Path(__file__).resolve().parent
SERVER_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
ONBOARDING_SOURCE_SHA256 = hashlib.sha256((ROOT / "onboarding.py").read_bytes()).hexdigest()
CONTENT_ROOT = ROOT / "contentfifa"
LOCAL_AUTH_SERVER_CLIENT_ID = "localfut21-blaze-server"
LOCAL_AUTH_SDK_CLIENT_ID = "localfut21-blaze-sdk"
LOCAL_AUTH_SSF_CLIENT_ID = "localfut21-ssf"
LOCAL_AUTH_CLIENT_IDS = (
    LOCAL_AUTH_SERVER_CLIENT_ID, LOCAL_AUTH_SDK_CLIENT_ID, LOCAL_AUTH_SSF_CLIENT_ID,
)
# Emulator-only values. Never request, validate, or forward EA credentials here.
LOCAL_AUTH_CODE = "LOCAL-FUT21-AUTH-CODE"
LOCAL_AUTH_ACCESS_TOKEN = "LOCAL-FUT21-ACCESS"
LOCAL_AUTH_REFRESH_TOKEN = "LOCAL-FUT21-REFRESH"
LOCAL_AUTH_PATHS = frozenset((
    "/localfut/auth/bootstrap", "/connect/token", "/connect/tokeninfo", "/localfut/callback",
))
# Keep runtime state outside the FIFA installation directory. FIFA is commonly
# installed under Program Files, where a normal user cannot create SQLite/log
# files. Preferences use Project21\LocalFUT; the existing club database/logs
# remain in %%LOCALAPPDATA%%\FIFA21LocalFUT (no saved-club migration).
_local_app_data = os.environ.get("LOCALAPPDATA")
_runtime_root_override = os.environ.get("LOCALFUT_RUNTIME_ROOT")
DEFAULT_RUNTIME_ROOT = (
    Path(_local_app_data) / "FIFA21LocalFUT" if _local_app_data
    else Path.home() / "AppData" / "Local" / "FIFA21LocalFUT"
).resolve()
TEST_MODE = any(arg.split("=", 1)[0] == "--http-only-test-port" for arg in sys.argv[1:])
# HTTP match settlement and Blaze run on separate local threads.  Signal the
# next live Blaze tick so FUT immediately refreshes its post-match user state.
PENDING_USERSESSION_REFRESH = threading.Event()
if _runtime_root_override:
    RUNTIME_ROOT = Path(_runtime_root_override).expanduser().resolve()
elif _local_app_data:
    RUNTIME_ROOT = Path(_local_app_data) / "FIFA21LocalFUT"
else:
    RUNTIME_ROOT = Path.home() / "AppData" / "Local" / "FIFA21LocalFUT"
_test_config_root = Path(os.environ.get("LOCALFUT21_USER_CONFIG_ROOT", ".")).resolve()
if TEST_MODE and (not _runtime_root_override or not Path(_runtime_root_override).is_absolute()
                  or RUNTIME_ROOT == DEFAULT_RUNTIME_ROOT
                  or DEFAULT_RUNTIME_ROOT in RUNTIME_ROOT.parents
                  or RUNTIME_ROOT in DEFAULT_RUNTIME_ROOT.parents
                  or RUNTIME_ROOT not in _test_config_root.parents):
    raise ValueError("HTTP-only tests require separate runtime and user-config roots")
DATA = RUNTIME_ROOT / "data"
LOGS = RUNTIME_ROOT / "logs"
CONTENT_CACHE = RUNTIME_ROOT / "content-cache"
DB_PATH = DATA / "localfut15.sqlite3"
CONFIG_PATH = user_config_root() / "config.json"

DATA.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)
CONTENT_CACHE.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    defaults = {
        "display_name": "LocalPlayer",
        "date_of_birth": "2001-08-20",
        "minimum_age": 18,
        "nucleus_id": 1000000001,
        "persona_id": 1000000002,
        "club_id": 1,
        "club_name": "Local FC",
        "club_abbr": "LCL",
        "credits": 500000,
        "fut_game": "fifa21",
        "game_sku": "FFA21PCC",
        "fut_sku": "FUT21PCC",
        "redirect_port": 42231,
        "blaze_port": 44321,
        "qos_port": 17502,
        "easw_port": 42232,
        "ssf_port": 10853,
        "fut_port": 8090,
        "legacy_fut_port": 8081,
        "lsx_port": 3216,
        "starter_players": [20801, 158023, 190871, 176580, 173731, 188545, 153079, 183277, 167495, 155862, 164240, 177003, 182521, 195864],
        "initial_club_player_count": 250,
        "pack_size": 12,
        "pack_cost": 7500,
        "special_pack_chance": 0.10,
        "rare_flag": 1,
        "debug_unknown_http": True,
    }
    defaults = read_user_config("config.json", defaults)

    # 0.1.9.1: network topology is protocol compatibility data, not a user
    # preference.  The original 0.1 package wrote blaze_port=17502 and
    # fut_port=8081 into config.json; leaving that file in place silently
    # overrode the V3-captured topology introduced in 0.1.9.  Force these
    # ports after loading config so stale installs cannot regress routing.
    defaults.update({
        "redirect_port": 42231,
        "blaze_port": 44321,
        "qos_port": 17502,
        "easw_port": 42232,
        "ssf_port": 10853,
        "fut_port": 8090,
        "legacy_fut_port": 8081,
        "lsx_port": 3216,
    })
    return defaults

CFG = load_config()

# Native FIFA database identifiers used for local Squad Battles matches.
# Stadium asset 2 is Santiago Bernabeu; its FUT stadium item definition is
# 6200001.  Barcelona's installed FIFA 21 home/away kit definitions are
# 6301491 and 6301492 respectively.
SQUAD_BATTLE_STADIUM_ID = 2
SQUAD_BATTLE_STADIUM_RESOURCE_ID = 6200001
SQUAD_BATTLE_STADIUM_CARD_ASSET_ID = 1
SQUAD_BATTLE_STADIUM_NAME = "Santiago Bernabeu"
MY_STADIUM_CARD_RESOURCE_ID = 6200058
MY_STADIUM_CARD_ASSET_ID = 261
SQUAD_BATTLE_OPPONENT_KITS = {
    "activeHomeKit": (6301491, 241, 0, "FC Barcelona Home Kit"),
    "activeAwayKit": (6301492, 241, 1, "FC Barcelona Away Kit"),
}


def local_auth_config() -> dict[str, str]:
    base_url = f"http://127.0.0.1:{int(CFG['fut_port'])}/"
    return {
        "blazeServerClientId": LOCAL_AUTH_SERVER_CLIENT_ID,
        "blazeSdkClientId": LOCAL_AUTH_SDK_CLIENT_ID,
        "blazeSdkClientSecret": "",
        "nucleusConnect": base_url,
        "identityRedirectUri": base_url + "localfut/callback",
    }


def mock_account_age() -> int:
    """Return the age of the local test account from its test-only DOB."""
    dob = _dt.date.fromisoformat(str(CFG["date_of_birth"]))
    today = _dt.date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def mock_account_is_under_age() -> bool:
    return mock_account_age() < int(CFG["minimum_age"])


PACK_SETTINGS_PATH = user_config_root() / "pack_settings.json"

# Every special rarity present in the imported WeFUT FIFA 21 catalog is a valid
# pack pool. Established headline types retain their former relative weights;
# the remaining promotions start at a low weight so fresh installs can surface
# the whole catalog without making every pack overwhelmingly special.
DEFAULT_SPECIAL_WEIGHTS = {
    "3":55, "4":4, "5":2, "6":3, "8":12, "9":5, "10":1, "11":8,
    "12":1, "16":1, "21":1, "22":1, "25":1, "30":1, "32":1,
    "33":1, "34":1, "37":1, "42":1, "43":1, "45":1, "46":1,
    "47":1, "48":1, "49":1, "50":1, "51":1, "52":1, "53":1,
    "54":1, "55":1, "57":1, "58":1, "59":1, "60":1, "61":1,
    "62":1, "64":1, "65":1, "68":1, "70":1, "71":1, "75":1,
    "76":1, "77":1, "79":1, "81":1, "82":1, "84":1, "86":1,
    "87":1, "89":1, "90":1, "91":1, "92":1, "93":1, "94":1,
    "95":1, "96":1, "98":1,
}
DEFAULT_PACK_SETTINGS = {
    "next_pack_id": 8,
    "packs": {
        "1": {"enabled": True, "name":"Bronze Pack","bio":"A bronze pack containing 12 items with 1 guaranteed rare and a chance of a Manager.","price":2000,"size":12,"gold_weight":0,"silver_weight":0,"bronze_weight":100,"rare_slots":1,"special_chance":0.10,"manager_chance":0.20,"special_weights":dict(DEFAULT_SPECIAL_WEIGHTS)},
        "2": {"enabled": True, "name":"Silver Pack","bio":"A silver pack containing 12 items with 1 guaranteed rare and a chance of a Manager.","price":2500,"size":12,"gold_weight":0,"silver_weight":100,"bronze_weight":0,"rare_slots":1,"special_chance":0.10,"manager_chance":0.20,"special_weights":dict(DEFAULT_SPECIAL_WEIGHTS)},
        "3": {"enabled": True, "name":"Gold Pack","bio":"A gold pack containing 12 items with 3 guaranteed rares and a chance of a Manager.","price":7500,"size":12,"gold_weight":100,"silver_weight":0,"bronze_weight":0,"rare_slots":3,"special_chance":0.10,"manager_chance":0.20,"special_weights":dict(DEFAULT_SPECIAL_WEIGHTS)},
        "4": {"enabled": True, "name":"Elite Ten 82+ Pack","bio":"Contains 10 Gold players rated 82 or higher, including at least 1 Special player.","price":50000,"size":10,"gold_weight":100,"silver_weight":0,"bronze_weight":0,"rare_slots":10,"min_rating":82,"guaranteed_specials":1,"special_chance":0.15,"manager_chance":0.0,"store_group":"special","special_weights":dict(DEFAULT_SPECIAL_WEIGHTS)},
        "5": {"enabled": True, "name":"Guaranteed 99 pack","bio":"Ouuuu shiiii, fym ouuuu shi?","price":1000,"size":1,"gold_weight":100,"silver_weight":0,"bronze_weight":0,"rare_slots":1,"min_rating":99,"guaranteed_specials":1,"special_chance":0.0,"manager_chance":0.0,"allow_unweighted_specials":True,"store_group":"special","special_weights":{}},
        "6": {"enabled": True, "name":"Guaranteed 92+ x10 Player Pack","bio":"Contains 10 unique players rated 92 or higher.","price":25000,"size":10,"gold_weight":100,"silver_weight":0,"bronze_weight":0,"rare_slots":10,"min_rating":92,"guaranteed_specials":10,"special_chance":0.0,"manager_chance":0.0,"allow_unweighted_specials":True,"store_group":"special","special_weights":{}},
        "7": {"enabled": True, "name":"Guaranteed Prime Moments Icon Pack","bio":"Contains one high-rated Icon player.","price":50000,"size":1,"gold_weight":100,"silver_weight":0,"bronze_weight":0,"rare_slots":1,"min_rating":90,"guaranteed_specials":1,"special_chance":0.0,"manager_chance":0.0,"allow_unweighted_specials":True,"allowed_rareflags":[12],"store_group":"special","special_weights":{}},
    },
}

def _merge_pack_settings(raw, overlay=None):
    if isinstance(overlay, dict):
        combined = json.loads(json.dumps(raw if isinstance(raw, dict) else {}))
        for key, value in overlay.items():
            if key == "packs" and isinstance(value, dict):
                packs = combined.setdefault("packs", {})
                if not isinstance(packs, dict):
                    packs = {}
                    combined["packs"] = packs
                for pack_id, pack_cfg in value.items():
                    if isinstance(pack_cfg, dict) and isinstance(packs.get(str(pack_id)), dict):
                        packs[str(pack_id)].update(pack_cfg)
                    else:
                        packs[str(pack_id)] = pack_cfg
            else:
                combined[key] = value
        raw = combined
    out=json.loads(json.dumps(DEFAULT_PACK_SETTINGS))
    if not isinstance(raw,dict):
        return out

    # v25 migration support: older Pack Studio versions stored one global
    # special_weights object. Use it only as a fallback for packs that do not
    # yet contain their own weights. Existing personal weights remain unchanged;
    # newly imported rarity keys are added disabled to old custom packs.
    legacy_weights=raw.get("special_weights") if isinstance(raw.get("special_weights"),dict) else DEFAULT_SPECIAL_WEIGHTS
    legacy_weights={str(k):v for k,v in legacy_weights.items()}

    if isinstance(raw.get("packs"),dict):
        out["packs"]={}
        for pid,cfg in raw["packs"].items():
            if isinstance(cfg,dict):
                base={"enabled":True,"name":f"Custom Pack {pid}","bio":"A custom FIFA 21 Ultimate Team pack.","price":7500,"size":12,
                      "gold_weight":100,"silver_weight":0,"bronze_weight":0,
                      "rare_slots":3,"special_chance":0.10,
                      "manager_chance":0.20 if str(pid) in ("1", "2", "3") else 0.0,
                      "special_weights":dict(legacy_weights)}
                base.update(cfg)
                weights=base.get("special_weights") if isinstance(base.get("special_weights"),dict) else legacy_weights
                weights={str(k):v for k,v in weights.items()}
                for rf, default_weight in DEFAULT_SPECIAL_WEIGHTS.items():
                    # A pack saved by an older build has never made a choice
                    # about newly imported FIFA 21 promotions. Make those new
                    # pools live by default; preserve an explicit zero chosen
                    # by the user for a rarity that already existed.
                    weights.setdefault(rf, default_weight)
                base["special_weights"]=weights
                out["packs"][str(pid)]=base
        # Introduce the built-in Promo pack to profiles created before it
        # existed. A saved pack 4 (including an explicitly disabled one) wins.
        if "4" not in out["packs"]:
            out["packs"]["4"] = json.loads(json.dumps(DEFAULT_PACK_SETTINGS["packs"]["4"]))
        if "5" not in out["packs"]:
            out["packs"]["5"] = json.loads(json.dumps(DEFAULT_PACK_SETTINGS["packs"]["5"]))
        elif (
            out["packs"]["5"].get("name") in {"Aqua Pro Neymar Pack", "99 Neymar Jr Pack"}
            or (
                out["packs"]["5"].get("name") == "Guaranteed 99 pack"
                and not bool(out["packs"]["5"].get("allow_unweighted_specials",False))
            )
        ):
            # Migrate prior generated definitions to the current guaranteed-99
            # pack without overwriting user-made pack settings.
            out["packs"]["5"] = json.loads(json.dumps(DEFAULT_PACK_SETTINGS["packs"]["5"]))
        for pack_id in ("6", "7"):
            if pack_id not in out["packs"]:
                out["packs"][pack_id] = json.loads(json.dumps(DEFAULT_PACK_SETTINGS["packs"][pack_id]))
    try:
        requested_next=int(raw.get("next_pack_id", 0) or 0)
        out["next_pack_id"]=max(requested_next, max([int(x) for x in out["packs"]] or [4])+1)
    except Exception:
        out["next_pack_id"]=max([int(x) for x in out["packs"]] or [4])+1
    return out

def load_pack_settings():
    try:
        bundled = {}
        bundled_path = ROOT / "pack_settings.json"
        if bundled_path.exists():
            bundled = json.loads(bundled_path.read_text(encoding="utf-8"))
        merged = _merge_pack_settings(DEFAULT_PACK_SETTINGS, bundled)
        return _merge_pack_settings(merged, read_user_config("pack_settings.json", {}))
    except Exception as exc:
        log.warning("Could not load pack_settings.json: %s", exc)
    return _merge_pack_settings({})

def get_pack_cfg(pack_id:int):
    settings=load_pack_settings()
    cfg=dict(settings.get("packs",{}).get(str(int(pack_id)),{}))
    if not cfg:
        cfg = dict(_SBC_REWARD_PACK_SETTINGS.get(int(pack_id), {}))
        if cfg:
            cfg.setdefault("special_chance", 0.10)
            cfg.setdefault("special_weights", dict(DEFAULT_SPECIAL_WEIGHTS))
    cfg["_special_weights"]=dict(cfg.get("special_weights",{}))
    return cfg


# Native reward-pack IDs used by the SBC catalogue. They are intentionally
# absent from the paid storefront configuration; owned copies appear only in
# My Packs and cost no coins when opened.
_SBC_REWARD_PACK_SETTINGS: dict[int, dict[str, Any]] = {
    100: {"name": "Bronze Pack", "size": 12, "bronze_weight": 100, "rare_slots": 1},
    200: {"name": "Silver Pack", "size": 12, "silver_weight": 100, "rare_slots": 1},
    201: {"name": "Premium Silver Players Pack", "size": 12,
          "silver_weight": 100, "rare_slots": 3},
    300: {"name": "Gold Pack", "size": 12, "gold_weight": 100, "rare_slots": 1},
    301: {"name": "Premium Gold Pack", "size": 12, "gold_weight": 100, "rare_slots": 3},
    304: {"name": "Gold Players Pack", "size": 12, "gold_weight": 100, "rare_slots": 1},
    305: {"name": "Premium Gold Players Pack", "size": 12, "gold_weight": 100, "rare_slots": 3},
    306: {"name": "Electrum Players Pack", "size": 12,
          "gold_weight": 50, "silver_weight": 50, "rare_slots": 1},
    307: {"name": "Prime Electrum Players Pack", "size": 12,
          "gold_weight": 50, "silver_weight": 50, "rare_slots": 6},
    308: {"name": "Prime Gold Players Pack", "size": 12,
          "gold_weight": 100, "rare_slots": 6},
    309: {"name": "Prime Mixed Players Pack", "size": 12,
          "gold_weight": 34, "silver_weight": 33, "bronze_weight": 33,
          "rare_slots": 6},
    400: {"name": "Rare Gold Pack", "size": 12, "gold_weight": 100, "rare_slots": 12},
    401: {"name": "Rare Players Pack", "size": 12, "gold_weight": 100, "rare_slots": 12},
    402: {"name": "Jumbo Rare Players Pack", "size": 24, "gold_weight": 100, "rare_slots": 24},
    403: {"name": "Mega Pack", "size": 30, "gold_weight": 100, "rare_slots": 18},
    404: {"name": "Rare Mega Pack", "size": 30, "gold_weight": 100, "rare_slots": 30},
    405: {"name": "Ultimate Pack", "size": 30, "gold_weight": 100, "rare_slots": 30},
    406: {"name": "Rare Mixed Players Pack", "size": 12,
          "gold_weight": 34, "silver_weight": 33, "bronze_weight": 33,
          "rare_slots": 12},
    513: {"name": "82+ Player Pick", "size": 1, "gold_weight": 100,
          "rare_slots": 1, "min_rating": 82, "player_pick_count": 4},
    514: {"name": "84+ Player Pick", "size": 1, "gold_weight": 100,
          "rare_slots": 1, "min_rating": 84, "player_pick_count": 4},
    515: {"name": "86+ Player Pick", "size": 1, "gold_weight": 100,
          "rare_slots": 1, "min_rating": 86, "player_pick_count": 4},
    516: {"name": "88+ Player Pick", "size": 1, "gold_weight": 100,
          "rare_slots": 1, "min_rating": 88, "player_pick_count": 4},
}

# FIFA 21 base-card metadata for the starter pool.
# Face stats are [PAC, SHO, PAS, DRI, DEF, PHY] (GK: [DIV, HAN, KIC, REF, SPE, POS]).
PLAYER_DB = {
    20801:  {"name":"Cristiano Ronaldo","rating":92,"preferredPosition":"LW","leagueId":53,"teamid":243,"nation":38,"face":[93,93,81,91,32,79]},
    158023: {"name":"Lionel Messi","rating":93,"preferredPosition":"CF","leagueId":53,"teamid":241,"nation":52,"face":[93,89,86,96,27,62]},
    190871: {"name":"Neymar Jr","rating":86,"preferredPosition":"LW","leagueId":53,"teamid":241,"nation":54,"face":[90,80,72,92,30,58]},
    176580: {"name":"Luis Suarez","rating":89,"preferredPosition":"ST","leagueId":53,"teamid":241,"nation":60,"face":[83,87,79,88,42,79]},
    173731: {"name":"Gareth Bale","rating":87,"preferredPosition":"RM","leagueId":53,"teamid":243,"nation":50,"face":[94,83,83,84,63,81]},
    188545: {"name":"Robert Lewandowski","rating":87,"preferredPosition":"ST","leagueId":19,"teamid":21,"nation":37,"face":[81,84,74,85,39,78]},
    153079: {"name":"Sergio Aguero","rating":86,"preferredPosition":"ST","leagueId":13,"teamid":10,"nation":52,"face":[88,86,77,88,28,66]},
    183277: {"name":"Eden Hazard","rating":88,"preferredPosition":"LM","leagueId":13,"teamid":5,"nation":7,"face":[89,82,84,91,32,64]},
    167495: {"name":"Manuel Neuer","rating":90,"preferredPosition":"GK","leagueId":19,"teamid":21,"nation":21,"face":[87,85,92,86,58,90]},
    155862: {"name":"Sergio Ramos","rating":87,"preferredPosition":"CB","leagueId":53,"teamid":243,"nation":45,"face":[79,60,71,66,87,82]},
    164240: {"name":"Thiago Silva","rating":87,"preferredPosition":"CB","leagueId":16,"teamid":73,"nation":54,"face":[78,57,72,72,90,80]},
    177003: {"name":"Luka Modric","rating":87,"preferredPosition":"CM","leagueId":53,"teamid":243,"nation":10,"face":[76,74,85,89,71,70]},
    182521: {"name":"Toni Kroos","rating":85,"preferredPosition":"CM","leagueId":53,"teamid":243,"nation":21,"face":[58,81,89,84,58,69]},
    195864: {"name":"Paul Pogba","rating":83,"preferredPosition":"CM","leagueId":31,"teamid":45,"nation":18,"face":[76,78,79,83,73,88]},
}

# Optional external player databases. The custom catalog is loaded last so
# locally authored cards survive updates to the imported public catalog.
PLAYER_DB_PATH = ROOT / "players.json"
CUSTOM_PLAYER_DB_PATH = ROOT / "custom-players.json"
for player_db_path in (PLAYER_DB_PATH, CUSTOM_PLAYER_DB_PATH):
    if not player_db_path.exists():
        continue
    try:
        raw_player_db = json.loads(player_db_path.read_text(encoding="utf-8"))
        if not isinstance(raw_player_db, dict):
            raise ValueError("player catalog must be a JSON object")
        for rid, meta in raw_player_db.items():
            if isinstance(meta, dict):
                PLAYER_DB[int(rid)] = meta
    except Exception as error:
        log.warning("Could not load player catalog %s: %s", player_db_path.name, error)

# WeFUT's custom-avatar IDs for Icon Moments live in the same zero-revision
# namespace as ordinary FIFA players. For example, Moments Pele was imported
# as 246525, which FIFA 21 resolves as Allsop before it considers the supplied
# card metadata. Preserve the custom player ID in the low 24 bits (and as the
# dynamic-head source), but publish the FUT item in a revision namespace.
PLAYER_RESOURCE_ID_MIGRATIONS: dict[int, int] = {}
_RESOURCE_REVISION_SIZE = 0x01000000
for old_resource_id, old_meta in list(PLAYER_DB.items()):
    if (int(old_meta.get("rareflag", 0) or 0) != 84
            or int(old_resource_id) >= _RESOURCE_REVISION_SIZE):
        continue
    new_resource_id = int(old_resource_id) + _RESOURCE_REVISION_SIZE
    while new_resource_id in PLAYER_DB:
        new_resource_id += _RESOURCE_REVISION_SIZE
    meta = dict(old_meta)
    meta["resourceId"] = new_resource_id
    meta["dynamicImageResourceId"] = int(old_resource_id)
    replacements = list(meta.get("replacesResourceIds", []))
    if int(old_resource_id) not in replacements:
        replacements.append(int(old_resource_id))
    meta["replacesResourceIds"] = replacements
    PLAYER_DB.pop(int(old_resource_id), None)
    PLAYER_DB[new_resource_id] = meta
    PLAYER_RESOURCE_ID_MIGRATIONS[int(old_resource_id)] = new_resource_id
log_player_count = len(PLAYER_DB)

# assetId -> meta index for resolving a stored card back to its catalogue
# entry when the id it carries is not a direct PLAYER_DB key.  Built once
# (a per-call scan of PLAYER_DB was slow enough to stall Draft handoffs).
_PLAYER_DB_BY_ASSET: dict[int, dict[str, Any]] = {}
for _rid, _meta in PLAYER_DB.items():
    if not isinstance(_meta, dict):
        continue
    try:
        _asset = int(_meta.get("assetId", 0) or 0)
    except (TypeError, ValueError):
        _asset = 0
    if _asset and _asset not in _PLAYER_DB_BY_ASSET:
        _PLAYER_DB_BY_ASSET[_asset] = _meta

# Real FUT manager-card definitions exported from the matching cards_ng_db.
# These are not the similarly named career-mode records in fifa_ng_db: FUT
# manager resource IDs occupy their own card range (for example 1001067).
MANAGER_DB_PATH = ROOT / "managers.json"
MANAGER_DB: dict[int, dict[str, Any]] = {}
try:
    raw_manager_db = json.loads(MANAGER_DB_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_manager_db, dict):
        raise ValueError("manager catalog must be a JSON object")
    for rid, meta in raw_manager_db.items():
        if isinstance(meta, dict):
            MANAGER_DB[int(rid)] = meta
except Exception as error:
    log.warning("Could not load FIFA 21 manager catalog: %s", error)


def manager_meta(resource_id: int) -> dict[str, Any]:
    return MANAGER_DB.get(int(resource_id), {})


KIT_DB_PATH = ROOT / "kits.json"
KIT_DB: list[dict[str, Any]] = []
try:
    raw_kit_db = json.loads(KIT_DB_PATH.read_text(encoding="utf-8"))
    raw_kits = raw_kit_db.get("kits", []) if isinstance(raw_kit_db, dict) else []
    KIT_DB = [kit for kit in raw_kits
              if isinstance(kit, dict) and int(kit.get("resourceId", 0) or 0) > 0]
except Exception as error:
    log.warning("Could not load FIFA 21 kit catalog: %s", error)


# FIFA 21 stadium venue ids (recovered from the game's own stadium preview
# assets -- every id is real and safe to select).
STADIUM_DB_PATH = ROOT / "stadiums.json"
STADIUM_DB: list[dict[str, Any]] = []
STADIUM_DEFAULT_ID = 261
try:
    _raw_stadiums = json.loads(STADIUM_DB_PATH.read_text(encoding="utf-8"))
    STADIUM_DEFAULT_ID = int(_raw_stadiums.get("default", 261) or 261)
    STADIUM_DB = [
        {"id": int(s["id"]), "name": str(s.get("name", f"Stadium {s['id']}")),
         "capacity": int(s.get("capacity", 45000) or 45000)}
        for s in _raw_stadiums.get("stadiums", [])
        if isinstance(s, dict) and int(s.get("id", 0) or 0) > 0
    ]
except Exception as error:
    log.warning("Could not load FIFA 21 stadium catalog: %s", error)
_STADIUM_BY_ID = {s["id"]: s for s in STADIUM_DB}


def _selected_stadium_id() -> int:
    """The club's currently chosen home venue id (persisted via /stadium PUT)."""
    try:
        sid = int(STATE.get("selected_stadium_id", STADIUM_DEFAULT_ID) or STADIUM_DEFAULT_ID)
    except (TypeError, ValueError):
        sid = STADIUM_DEFAULT_ID
    return sid if sid in _STADIUM_BY_ID else STADIUM_DEFAULT_ID


# Native FIFA position-change definitions. These are training cards in
# fcc_trainingcards; synthetic item type/IDs render as broken player cards.
_POSITION_MODIFIER_DEFINITIONS = (
    (5003059, 91, "LWB", "LB"), (5003060, 92, "LB", "LWB"),
    (5003061, 93, "RWB", "RB"), (5003062, 94, "RB", "RWB"),
    (5003063, 95, "LM", "LW"), (5003064, 96, "RM", "RW"),
    (5003065, 97, "LW", "LM"), (5003066, 98, "RW", "RM"),
    (5003067, 99, "LW", "LF"), (5003068, 100, "RW", "RF"),
    (5003069, 101, "LF", "LW"), (5003070, 102, "RF", "RW"),
    (5003071, 103, "CM", "CAM"), (5003072, 104, "CAM", "CM"),
    (5003073, 105, "CDM", "CM"), (5003074, 106, "CM", "CDM"),
    (5003075, 107, "CAM", "CF"), (5003076, 108, "CF", "CAM"),
    (5003077, 109, "CF", "ST"), (5003078, 110, "ST", "CF"),
)
_POSITION_MODIFIER_BY_RESOURCE = {
    resource_id: {"cardsubtypeid": subtype, "source": source, "target": target}
    for resource_id, subtype, source, target in _POSITION_MODIFIER_DEFINITIONS
}

def player_meta(resource_id: int) -> dict[str, Any]:
    return PLAYER_DB.get(int(resource_id), {})


# The pro-player shell uses a custom definition ID while FIFA still consults
# the underlying gold asset ID on some squad/detail screens.  Keep this mapping
# explicit so both lookups receive the same maxed gameplay data.
_AQUA_PRO_RESOURCE_IDS = frozenset((167963031, 167996618, 167988548))
_AQUA_PRO_BASE_TO_RESOURCE_ID = {
    190871: 167963031,  # Neymar Jr
    224458: 167996618,  # Diogo Jota
    216388: 167988548,  # Allan Saint-Maximin
}


def aqua_pro_resource_id(resource_id: int) -> int | None:
    resource_id = int(resource_id)
    if resource_id in _AQUA_PRO_RESOURCE_IDS:
        return resource_id
    return _AQUA_PRO_BASE_TO_RESOURCE_ID.get(resource_id)


def player_attribute_list(resource_id: int) -> list[dict[str, int]]:
    face = player_meta(resource_id).get("face", [])
    return [{"index": i, "value": int(v)} for i, v in enumerate(face)]


_PLAYER_ATTRIBUTE_GROUPS = (
    ("acceleration", "sprintspeed"),
    ("positioning", "finishing", "shotpower", "longshots", "volleys", "penalties"),
    ("vision", "crossing", "freekickaccuracy", "shortpassing", "longpassing", "curve"),
    ("agility", "balance", "reactions", "ballcontrol", "dribbling", "composure"),
    ("interceptions", "headingaccuracy", "marking", "standingtackle", "slidingtackle"),
    ("jumping", "stamina", "strength", "aggression"),
)
_PLAYER_GK_ATTRIBUTES = ("gkdiving", "gkhandling", "gkkicking", "gkreflexes", "gkspeed", "gkpositioning")


def player_attribute_metadata(resource_id: int) -> dict[str, Any]:
    """Build one record for FIFA 21's native player-attribute decoder.

    CardsDLL's ``FutGetPlayerAttributesServerResponse`` parser expects a bare
    JSON array of these records.  In particular, ``ingameattribs`` and
    ``attributeArray`` are arrays of integers; returning the web-app style
    ``[{index, value}]`` objects makes the native parser reject the response.
    """
    resource_id = int(resource_id)
    custom_resource_id = aqua_pro_resource_id(resource_id)
    meta = player_meta(custom_resource_id or resource_id)
    if custom_resource_id is not None:
        # Keep defId identical to the identifier requested by the client.  The
        # details screen alternates between the custom definition and base
        # asset IDs and indexes the response by this exact value.
        return {
            "defId": resource_id,
            "ingameattribs": [99] * 34,
            "attributeArray": [99] * 6,
            "rating": 99,
            "trait1": int(meta.get("trait1", 0) or 0),
            "trait2": int(meta.get("trait2", 0) or 0),
            "version": int(meta.get("version", 0) or 0),
        }
    face = [max(0, min(99, int(value))) for value in meta.get("face", [])]
    face += [0] * max(0, 6 - len(face))
    is_goalkeeper = str(meta.get("preferredPosition", "")).upper() == "GK"
    attributes: dict[str, int] = {}
    supplied_attributes = {
        str(name).lower().replace("_", ""): value
        for name, value in (meta.get("attributes", {}) or {}).items()
    }
    groups = (_PLAYER_GK_ATTRIBUTES,) if is_goalkeeper else _PLAYER_ATTRIBUTE_GROUPS
    for group_index, names in enumerate(groups):
        fallback = face[group_index] if group_index < len(face) else 0
        for name in names:
            attributes[name] = max(0, min(99, int(supplied_attributes.get(name, fallback))))

    # CardsDLL reserves 34 integer slots for the detailed attributes.  FIFA's
    # player database has 29 outfield attributes and six goalkeeper values;
    # GK speed is derived by the game and is therefore omitted here.
    ingame_order = tuple(name for group in _PLAYER_ATTRIBUTE_GROUPS for name in group) + (
        "gkdiving", "gkhandling", "gkkicking", "gkreflexes", "gkpositioning",
    )
    ingame_attributes = [
        max(0, min(99, int(supplied_attributes.get(name, attributes.get(name, 0)))))
        for name in ingame_order
    ]
    return {
        "defId": resource_id,
        "ingameattribs": ingame_attributes,
        "attributeArray": face[:6],
        "rating": max(0, min(99, int(meta.get("rating", 0) or 0))),
        "trait1": int(meta.get("trait1", 0) or 0),
        "trait2": int(meta.get("trait2", 0) or 0),
        "version": int(meta.get("version", 0) or 0),
    }


def high_rating_pack_weight(resource_id: int) -> float:
    """Apply a restrained boost to elite cards without excluding lower cards."""
    rating = int(player_meta(resource_id).get("rating", 0) or 0)
    if rating < 90:
        return 1.0
    # 90=1.35x, 92=2.05x, 95=3.10x; 97-99 remain capped at 3.5x.
    return min(3.5, 1.0 + (rating - 89) * 0.35)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOGS / "localfut15.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("localfut15")
log.info("Loaded %d FIFA 21 player definitions", log_player_count)

try:
    _special_pool_counts={}
    for _rid,_meta in PLAYER_DB.items():
        _rf=int(_meta.get("rareflag",0) or 0)
        if _rf>1:
            _special_pool_counts[_rf]=_special_pool_counts.get(_rf,0)+1
    log.info("Special rarity pools: %s", dict(sorted(_special_pool_counts.items())))
except Exception:
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def now_s() -> int:
    return int(time.time())


def json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def safe_json(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return {}


_EA_FUT_CONTENT_BASE = (
    "https://fifa21.content.easports.com/fifa/fltOnlineAssets/"
    "21D4F1AC-91A3-458D-A64E-895AA6D871D1/2021"
)
_EA_FUT_CONTENT_FILES = {
    "/fut/items/images/backgrounds/itembgs/futitemraritytunables.json": (
        "/fut/items/images/backgrounds/itemBGs/futitemraritytunables.json", "application/json; charset=utf-8",
    ),
    "/fut/packs/packopening/packopeningsetting.json": (
        "/fut/packs/packopening/packopeningsetting.json", "application/json; charset=utf-8",
    ),
    "/fut/packs/packopening/packopeningconfig.json": (
        "/fut/packs/packopening/packopeningconfig.json", "application/json; charset=utf-8",
    ),
    "/fut/playerheads/g4/fut2dheads.big": (
        "/fut/playerheads/g4/fut2dheads.big", "application/octet-stream",
    ),
}
_content_cache_lock = threading.Lock()


def _cached_fut_content(path: str) -> tuple[bytes, str] | None:
    """Serve a small allowlisted FUT presentation asset from a local cache.

    The game requests these paths through its local FUT listener.  They are
    public presentation data, not account data; cache the exact response to
    make subsequent launches independent of the CDN being reachable.
    """
    asset = _EA_FUT_CONTENT_FILES.get(path)
    if asset is None:
        return None
    remote_path, content_type = asset

    cache_path = (CONTENT_CACHE / path.lstrip("/")).resolve()
    try:
        cache_path.relative_to(CONTENT_CACHE.resolve())
    except ValueError:
        return None

    bundled_path = (CONTENT_ROOT / path.lstrip("/")).resolve()
    try:
        bundled_path.relative_to(CONTENT_ROOT.resolve())
        body = bundled_path.read_bytes()
        if body:
            return body, content_type
    except OSError:
        pass

    try:
        body = cache_path.read_bytes()
        if body:
            return body, content_type
    except OSError:
        pass

    with _content_cache_lock:
        try:
            body = cache_path.read_bytes()
            if body:
                return body, content_type
        except OSError:
            pass

        url = _EA_FUT_CONTENT_BASE + remote_path
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FIFA21-LocalFUT/0.2"})
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if not body or len(body) > 2 * 1024 * 1024:
                raise ValueError("unexpected FUT content size")
            # These three files are JSON. Validate before caching a CDN error page.
            json.loads(body.decode("utf-8"))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(body)
            log.info("Cached official FUT content asset: %s (%d bytes)", path, len(body))
            return body, content_type
        except Exception as error:
            log.warning("FUT content asset unavailable path=%s error=%s", path, error)
            return None


def _cached_dynamic_player_head(resource_id: int) -> bytes | None:
    """Return one native FIFA 21 dynamic player-head DDS, cached on demand."""
    resource_id = int(resource_id)
    if resource_id <= 0:
        return None
    # LOCALFUT_CUSTOM_PLAYERHEAD_OVERRIDE_BEGIN
    # Resource-ID-specific local portrait override. This lets one custom
    # special card use its own portrait without changing the base/gold card.
    custom_head_path = ROOT / "custom-playerheads" / f"p{resource_id}.dds"
    try:
        custom_body = custom_head_path.read_bytes()
        if len(custom_body) >= 128 and custom_body.startswith(b"DDS "):
            log.info(
                "Serving custom player head resource=%d file=%s",
                resource_id, custom_head_path.name,
            )
            return custom_body
        if custom_head_path.exists():
            log.warning(
                "Ignoring invalid custom player head resource=%d file=%s",
                resource_id, custom_head_path,
            )
    except OSError:
        pass
    # LOCALFUT_CUSTOM_PLAYERHEAD_OVERRIDE_END
    # A custom rarity uses a new resource ID.  Its player photo must still come
    # from a real FIFA item resource, otherwise an unrelated official dynamic
    # image can be shown if that custom number happens to overlap one.
    meta = player_meta(resource_id)
    if aqua_pro_resource_id(resource_id) is not None:
        source_resource_id = int(meta.get("assetId", resource_id))
    else:
        source_resource_id = int(meta.get("dynamicImageResourceId", resource_id))
    relative = f"fut/playerheads/g4/single/p{source_resource_id}.dds"
    cache_path = (CONTENT_CACHE / relative).resolve()
    try:
        cache_path.relative_to(CONTENT_CACHE.resolve())
    except ValueError:
        return None

    def valid_dds(body: bytes) -> bool:
        return len(body) >= 128 and body.startswith(b"DDS ")

    try:
        body = cache_path.read_bytes()
        if valid_dds(body):
            return body
    except OSError:
        pass

    with _content_cache_lock:
        try:
            body = cache_path.read_bytes()
            if valid_dds(body):
                return body
        except OSError:
            pass
        try:
            request = urllib.request.Request(
                f"{_EA_FUT_CONTENT_BASE}/{relative}",
                headers={"User-Agent": "FIFA21-LocalFUT/0.2"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024 or not valid_dds(body):
                raise ValueError("invalid dynamic player-head DDS")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
            temporary.write_bytes(body)
            temporary.replace(cache_path)
            log.info(
                "Cached FIFA 21 dynamic player head resource=%d source=%d bytes=%d",
                resource_id, source_resource_id, len(body),
            )
            return body
        except Exception as error:
            # A real 404 lets FIFA fall back to the base face in fut2dheads.big.
            log.info(
                "Dynamic player head unavailable resource=%d source=%d error=%s",
                resource_id, source_resource_id, error,
            )
            return None


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


class State:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self.lock:
            c = self.conn.cursor()
            c.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items(
                    id INTEGER PRIMARY KEY,
                    resource_id INTEGER NOT NULL,
                    pile TEXT NOT NULL DEFAULT 'club',
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS squads(
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS unopened_packs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pack_id INTEGER NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS draft_runs(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS squad_battles(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    data TEXT NOT NULL
                );
                """
            )
            self.conn.commit()
            self.set_default("credits", int(CFG["credits"]))
            self.set_default("next_item_id", 900000000000)
            self.set_default("sid", "LOCAL15-" + sha1_text(str(time.time()))[:20].upper())
            self.set_default("token", "LOCAL15TOKEN-" + sha1_text(str(time.time() + 1))[:24].upper())
            self.set_default("established", now_s())
            self._migrate_custom_resource_ids()
            self._migrate_legacy_manager_items()
            self._migrate_direct_player_pick_tokens()
            self._ensure_initial_club_players()
            self._ensure_initial_club_manager()
            self._ensure_initial_club_kits()
            self._enrich_existing_player_items()
            if not self._has_any_squads():
                self._seed_squad()
            else:
                self._repair_empty_active_squad()

    def set_default(self, key: str, value: Any) -> None:
        row = self.conn.execute("SELECT 1 FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            self.conn.execute("INSERT INTO kv(key,value) VALUES(?,?)", (key, json.dumps(value)))
            self.conn.commit()

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row[0])
            except Exception:
                return row[0]

    def set(self, key: str, value: Any) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.conn.commit()

    def credits(self) -> int:
        return int(self.get("credits", 0))

    def add_credits(self, amount: int) -> int:
        v = max(0, self.credits() + int(amount))
        self.set("credits", v)
        return v

    def sid(self) -> str:
        return str(self.get("sid"))

    def token(self) -> str:
        return str(self.get("token"))

    def draft_run(self) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute("SELECT data FROM draft_runs WHERE id=1").fetchone()
            if not row:
                return None
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError, json.JSONDecodeError):
                log.warning("Ignoring malformed saved Draft run")
                return None
            return data if isinstance(data, dict) else None

    def save_draft_run(self, data: dict[str, Any]) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO draft_runs(id,data) VALUES(1,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (json.dumps(data),),
            )
            self.conn.commit()

    def clear_draft_run(self) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM draft_runs WHERE id=1")
            self.conn.commit()

    def squad_battle_state(self) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute("SELECT data FROM squad_battles WHERE id=1").fetchone()
            if not row:
                return None
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError, json.JSONDecodeError):
                log.warning("Ignoring malformed saved Squad Battles state")
                return None
            return data if isinstance(data, dict) else None

    def save_squad_battle_state(self, data: dict[str, Any]) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO squad_battles(id,data) VALUES(1,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (json.dumps(data),),
            )
            self.conn.commit()

    def _has_any_items(self) -> bool:
        return self.conn.execute("SELECT 1 FROM items LIMIT 1").fetchone() is not None

    def _has_any_squads(self) -> bool:
        return self.conn.execute("SELECT 1 FROM squads LIMIT 1").fetchone() is not None

    def next_item_id(self) -> int:
        with self.lock:
            v = int(self.get("next_item_id", 900000000000)) + 1
            self.set("next_item_id", v)
            return v

    def add_unopened_pack(self, pack_id: int, source: str = "reward") -> int:
        with self.lock:
            cursor = self.conn.execute(
                "INSERT INTO unopened_packs(pack_id,data) VALUES(?,?)",
                (int(pack_id), json.dumps({"source": str(source), "created": now_s()})),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def unopened_pack_ids(self) -> list[int]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT pack_id FROM unopened_packs ORDER BY id"
            ).fetchall()
        return [int(row["pack_id"]) for row in rows]

    def take_unopened_pack(self, pack_id: int) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT id FROM unopened_packs WHERE pack_id=? ORDER BY id LIMIT 1",
                (int(pack_id),),
            ).fetchone()
            if not row:
                return False
            self.conn.execute("DELETE FROM unopened_packs WHERE id=?", (int(row["id"]),))
            self.conn.commit()
            return True

    def player_discard_value(self, resource_id: int) -> int:
        """Local FUT quick-sell values.

        User-selected economy:
          common/base non-rare = 360
          base rare            = 700
          special              = 10,000-17,000 based on card rarity/type
        """
        meta = player_meta(resource_id)
        try:
            rareflag = int(meta.get("rareflag", 0) or 0)
        except Exception:
            rareflag = 0

        is_special = bool(meta.get("special", False)) or rareflag > 1

        if not is_special:
            return 700 if rareflag == 1 else 360

        # FIFA 21 special shells. Values deliberately stay in the requested
        # 10k-17k range and increase roughly with prestige of the card type.
        special_values = {
            3: 10000,   # IF / TOTW
            4: 11500,   # Hero / Purple
            5: 17000,   # TOTY
            6: 13500,   # Record Breaker
            7: 11000,   # St Patrick / Green
            8: 12500,   # MOTM / Orange
            9: 12000,   # FUTTIES / Pink
            10: 12000,  # Teal / other special
            11: 16000,  # TOTS
            12: 15000,  # Legend
            13: 13500,
            14: 13500,
        }
        return int(special_values.get(rareflag, 12000))

    def build_player_item(
        self,
        resource_id: int,
        item_id: int,
        pile: str,
        loan_games: int = 0,
    ) -> dict[str, Any]:
        meta = player_meta(resource_id)
        loan_games = max(0, int(loan_games))
        return {
            "id": item_id,
            "timestamp": now_s(),
            "resourceId": int(resource_id),
            "assetId": int(meta.get("assetId", resource_id)),
            "rating": int(meta.get("rating", 0)),
            "itemState": "free",
            "rareflag": int(meta.get("rareflag", CFG.get("rare_flag", 1))),
            "formation": "f442",
            "leagueId": int(meta.get("leagueId", 0)),
            "injuryType": "none",
            "injuryGames": 0,
            "lastSalePrice": 0,
            "fitness": 99,
            "training": 0,
            "suspension": 0,
            "contract": 0 if loan_games else 7,
            "contracts": 0 if loan_games else 7,
            "preferredPosition": str(meta.get("preferredPosition", "")),
            "preferredFoot": str(meta.get("preferredFoot", "") or ""),
            "weakFoot": int(meta.get("weakFoot", 0) or 0),
            "skillMoves": int(meta.get("skillMoves", 0) or 0),
            "workRateAttack": str(meta.get("workRateAttack", "") or ""),
            "workRateDefense": str(meta.get("workRateDefense", "") or ""),
            "playStyle": 0,
            "discardValue": 0 if loan_games else self.player_discard_value(resource_id),
            "itemType": "player",
            "cardsubtypeid": int(meta.get("cardsubtypeid", 0) or 0),
            "owners": 1,
            "untradeable": bool(loan_games or meta.get("untradeable", False)),
            "morale": 50,
            "statsList": [],
            "lifetimeStats": [],
            "attributeList": player_attribute_list(resource_id),
            "teamid": int(meta.get("teamid", 0)),
            "assists": 0,
            "lifetimeAssists": 0,
            "loans": loan_games,
            "loyaltyBonus": 1,
            "pile": pile,
            "nation": int(meta.get("nation", 0)),
            "resourceGameYear": 2021,
            "marketDataMinPrice": 150,
            "marketDataMaxPrice": 15000000,
            "firstName": str(meta.get("firstName", "") or ""),
            "lastName": str(meta.get("lastName", "") or ""),
            "commonname": str(meta.get("commonname", "") or ""),
        }

    def make_player_item(
        self,
        resource_id: int,
        pile: str = "club",
        loan_games: int = 0,
    ) -> dict[str, Any]:
        item_id = self.next_item_id()
        # Keep the instance fields native, but also include the FIFA 21 base-card
        # metadata needed by the client during pack reveals and club rendering.
        item = self.build_player_item(resource_id, item_id, pile, loan_games)
        with self.lock:
            self.conn.execute(
                "INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                (item_id, int(resource_id), pile, json.dumps(item)),
            )
            self.conn.commit()
        return item

    def make_player_pick_item(
        self,
        min_rating: int,
        choice_count: int = 4,
        pile: str = "unassigned",
        source_pack_id: int = 0,
        preview_resource_id: int = 0,
    ) -> dict[str, Any]:
        """Create FIFA 21's native unlockable player-pick misc item.

        Every other working item type in this server (consumables, packs,
        real player cards) carries a genuine, resolvable resourceId; this
        token was the one exception, hardcoded to 0. Any client-side lookup
        by resourceId during the confirm transition -- card art, identity,
        anything -- would resolve to nothing no matter what the HTTP
        response said, which fits every observed crash: it always died a
        couple of seconds after a clean 200, using data it already had
        cached from when the token was created, not from the response.
        preview_resource_id should be a real qualifying candidate so the
        token itself points at something that actually exists.
        """
        min_rating = max(0, min(99, int(min_rating)))
        choice_count = max(2, min(5, int(choice_count)))
        item_id = self.next_item_id()
        name = f"1 of {choice_count} {min_rating}+ Player Pick"
        preview_resource_id = int(preview_resource_id or 0)
        item = {
            "id": item_id,
            "timestamp": now_s(),
            "resourceId": preview_resource_id,
            "assetId": preview_resource_id,
            "carddbid": preview_resource_id,
            "cardassetid": 34,
            "itemType": "misc",
            # FIFA 21/current FUT clients identify a player-pick token by the
            # misc-card subtype, not by a one-player pack definition.
            "cardsubtypeid": 237,
            "subtype": 237,
            "subType": 237,
            "itemState": "free",
            "stackCount": 1,
            "untradeableCount": 1,
            "untradeable": True,
            "owners": 1,
            "pile": pile,
            "rating": min_rating,
            "rareflag": 1,
            "discardValue": 0,
            "name": name,
            "description": f"Choose one of {choice_count} players rated {min_rating} or higher.",
            "IS_UNLOCKABLE": True,
            "UNLOCKABLE_TYPE": "PLAYER_PICK",
            # CardWrapper has a dedicated UNLOCKS/EMPTY_UNLOCKS renderer. A
            # player-pick token is not a player catalog row, so marking this as
            # PLAYER makes resourceId 0 render as the grey placeholder.
            "CARD_TYPE": "UNLOCKS",
            "cardType": "UNLOCKS",
            "card_type": "UNLOCKS",
            "UNLOCKS": [],
            "choiceCount": choice_count,
            "minRating": min_rating,
            "sourcePackId": int(source_pack_id or 0),
            "resourceGameYear": 2021,
        }
        with self.lock:
            self.conn.execute(
                "INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                (item_id, preview_resource_id, pile, json.dumps(item)),
            )
            self.conn.execute(
                "INSERT INTO kv(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"player_pick:{item_id}", json.dumps({
                    "minRating": min_rating,
                    "choiceCount": choice_count,
                    "choices": [],
                })),
            )
            self.conn.commit()
        return item

    def _migrate_direct_player_pick_tokens(self) -> None:
        """Recover picks granted directly by the short-lived SBC implementation.

        Native FIFA expects SBC picks to enter My Packs first. Tokens produced
        by opening such a pack carry sourcePackId; old direct-grant tokens do
        not, so they can be safely converted once without looping on restart.
        """
        rating_to_pack = {82: 513, 84: 514, 86: 515, 88: 516}
        with self.lock:
            rows = self.conn.execute("SELECT id,data FROM items WHERE pile='unassigned'").fetchall()
            migrated = 0
            normalized = 0
            for row in rows:
                try:
                    item = json.loads(row["data"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if (str(item.get("itemType", "")).lower() != "misc"
                        or int(item.get("cardsubtypeid", item.get("subtype", 0)) or 0) != 237):
                    continue
                if int(item.get("sourcePackId", 0) or 0) > 0:
                    # Upgrade tokens created by older builds remain usable.
                    # Repair their presentation in place so CardWrapper uses
                    # the native white unlock-card art after a restart.
                    if any(item.get(key) != "UNLOCKS"
                           for key in ("CARD_TYPE", "cardType", "card_type")):
                        item.update({
                            "CARD_TYPE": "UNLOCKS", "cardType": "UNLOCKS",
                            "card_type": "UNLOCKS", "IS_UNLOCKABLE": True,
                            "UNLOCKABLE_TYPE": "PLAYER_PICK",
                        })
                        self.conn.execute(
                            "UPDATE items SET data=? WHERE id=?",
                            (json.dumps(item), int(row["id"])),
                        )
                        normalized += 1
                    continue
                min_rating = int(item.get("minRating", item.get("rating", 82)) or 82)
                pack_id = rating_to_pack.get(min_rating, 513)
                self.conn.execute(
                    "INSERT INTO unopened_packs(pack_id,data) VALUES(?,?)",
                    (pack_id, json.dumps({"source": "sbc-player-pick-recovery", "created": now_s()})),
                )
                self.conn.execute("DELETE FROM items WHERE id=?", (int(row["id"]),))
                self.conn.execute("DELETE FROM kv WHERE key=?", (f"player_pick:{int(row['id'])}",))
                migrated += 1
            if migrated or normalized:
                self.conn.commit()
            if migrated:
                log.info("Recovered %d directly granted player pick(s) into My Packs", migrated)
            if normalized:
                log.info("Updated %d player-pick token(s) to native unlock-card art", normalized)

    def pending_player_pick_tokens(self) -> list[dict[str, Any]]:
        return [
            item for item in self.list_items()
            if str(item.get("itemType", "")).lower() == "misc"
            and int(item.get("cardsubtypeid", item.get("subtype", 0)) or 0) == 237
        ]

    def redeem_player_pick(self, token_id: int) -> list[dict[str, Any]]:
        token = self.get_item(int(token_id))
        if not isinstance(token, dict) or str(token.get("itemType", "")).lower() != "misc" \
                or int(token.get("cardsubtypeid", token.get("subtype", 0)) or 0) != 237:
            raise ValueError("player pick item not found")
        state_key = f"player_pick:{int(token_id)}"
        state = self.get(state_key, {})
        if isinstance(state, dict) and isinstance(state.get("choices"), list) and state["choices"]:
            # Repair already-open picks created by an older runtime so the
            # user does not have to discard the token and complete the SBC
            # again after upgrading.
            choices = state["choices"]
            repaired = False
            for choice_index, choice in enumerate(choices):
                if not isinstance(choice, dict):
                    continue
                for key in ("globalChoiceIndex", "localChoiceIndex", "choiceIndex"):
                    if choice.get(key) != choice_index:
                        choice[key] = choice_index
                        repaired = True
            if repaired:
                state["choices"] = choices
                self.set(state_key, state)
            return choices

        min_rating = max(0, min(99, int(token.get("minRating", token.get("rating", 0)) or 0)))
        choice_count = max(2, min(5, int(token.get("choiceCount", 4) or 4)))
        candidates = [
            int(resource_id) for resource_id in PLAYER_DB
            if int(player_meta(int(resource_id)).get("rating", 0) or 0) >= min_rating
            # Custom Aqua Pro shells (a maxed-stats identity substituted onto a
            # real player's asset) go through dedicated attribute/portrait
            # handling everywhere else (player_attribute_metadata,
            # _cached_dynamic_player_head). build_player_item has no such
            # special case, so offering one here produces a malformed reward
            # card that crashes the client on selection. Real player cards
            # only for picks.
            and aqua_pro_resource_id(int(resource_id)) is None
        ]
        if len(candidates) < choice_count:
            raise RuntimeError(f"only {len(candidates)} players satisfy the {min_rating}+ pick")

        selected: list[int] = []
        remaining = list(candidates)
        while remaining and len(selected) < choice_count:
            weights = [max(0.0001, float(high_rating_pack_weight(rid))) for rid in remaining]
            rid = int(random.choices(remaining, weights=weights, k=1)[0])
            selected.append(rid)
            remaining.remove(rid)

        owned = self.owned_player_resource_ids()
        choices = []
        for choice_index, resource_id in enumerate(selected):
            choice = self.build_player_item(
                resource_id, self.next_item_id(), "unassigned"
            )
            choice["untradeable"] = True
            choice["untradeableCount"] = 1
            choice["discardValue"] = 0
            # A pack's revealed cards get this same duplicate marker before
            # display (create_pack -> would_duplicate_owned_player). With a
            # 1000+ card club, at least one of four random 82+ picks is
            # commonly already owned.
            is_dup = int(resource_id) in owned
            choice["duplicate"] = is_dup
            choice["isDuplicate"] = is_dup
            choice["playerPickItemId"] = int(token_id)
            # The native FIFA 21 picker reads these fields before it sends the
            # confirmation request. Missing indexes leave its selected choice
            # undefined and can crash the client before the request reaches us.
            choice["globalChoiceIndex"] = choice_index
            choice["localChoiceIndex"] = choice_index
            choice["choiceIndex"] = choice_index
            choices.append(choice)
        self.set(state_key, {
            "minRating": min_rating,
            "choiceCount": choice_count,
            "choices": choices,
        })
        return choices

    def select_player_pick(self, token_id: int, selection: Any) -> dict[str, Any]:
        choices = self.redeem_player_pick(int(token_id))
        selected = None
        try:
            numeric = int(selection)
        except (TypeError, ValueError):
            numeric = -1
        if 0 <= numeric < len(choices):
            selected = choices[numeric]
        if selected is None:
            selected = next((choice for choice in choices if numeric in (
                int(choice.get("id", 0) or 0),
                int(choice.get("resourceId", 0) or 0),
                int(choice.get("assetId", 0) or 0),
            )), None)
        if selected is None:
            raise ValueError("selected player is not part of this pick")

        selected = dict(selected)
        for picker_key in (
            "playerPickItemId", "globalChoiceIndex", "localChoiceIndex", "choiceIndex"
        ):
            selected.pop(picker_key, None)
        selected["pile"] = "unassigned"
        # Keep the item-instance id assigned when this choice was revealed.
        # The native picker already has that exact item in its in-memory
        # choice list. Reusing the unlock token's id for a different resource
        # makes the response describe an object the picker has never seen and
        # leaves its selected choice id unresolved during the transition to
        # New Items.
        selected_id = int(selected["id"])
        with self.lock:
            token = self.conn.execute("SELECT 1 FROM items WHERE id=?", (int(token_id),)).fetchone()
            if not token:
                raise ValueError("player pick item has already been redeemed")
            self.conn.execute("DELETE FROM items WHERE id=?", (int(token_id),))
            self.conn.execute(
                "INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                (selected_id, int(selected["resourceId"]), "unassigned", json.dumps(selected)),
            )
            self.conn.execute("DELETE FROM kv WHERE key=?", (f"player_pick:{int(token_id)}",))
            self.conn.commit()
        # A pick offered from the same wide candidate pool as any pack has the
        # same chance of landing on a card already owned. Normal pack reveals
        # always run this same check and mark the result before it is shown
        # (create_pack: would_duplicate_owned_player -> mark_item_duplicate);
        # this path skipped it, leaving the granted item without the
        # duplicate/pile markers the reward-confirmation view expects when it
        # is already-owned -- a very plausible crash trigger given the club
        # has 1000+ items in it by the time picks are being opened.
        if self.would_duplicate_owned_player(selected_id):
            self.mark_item_duplicate(selected_id, True)
            refreshed = self.get_item(selected_id)
            if refreshed is not None:
                selected = refreshed
        return selected

    def _migrate_legacy_manager_items(self) -> None:
        """Replace career-mode manager placeholders with native FUT cards.

        Keep each owned instance id intact so saved squad references continue
        to resolve. Older builds used fifa_ng_db.manager.managerid here, but the
        retail client validates against cards_ng_db.managercards.carddbid.
        """
        if not MANAGER_DB:
            return
        fallback_resource_id = min(
            MANAGER_DB,
            key=lambda rid: (-int(MANAGER_DB[rid].get("rating", 0) or 0), int(rid)),
        )
        with self.lock:
            rows = self.conn.execute("SELECT id, resource_id, pile, data FROM items").fetchall()
            migrated = 0
            for row in rows:
                try:
                    old_item = json.loads(row["data"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if str(old_item.get("itemType", "")) != "manager":
                    continue
                old_resource_id = int(old_item.get("resourceId", row["resource_id"]) or 0)
                if old_resource_id in MANAGER_DB:
                    continue

                replacement_id = old_resource_id + 1_000_000
                if replacement_id not in MANAGER_DB:
                    old_name = " ".join((
                        str(old_item.get("firstName", "") or "").strip(),
                        str(old_item.get("lastName", "") or "").strip(),
                    )).strip().casefold()
                    replacement_id = next((
                        rid for rid, meta in MANAGER_DB.items()
                        if str(meta.get("name", "") or "").strip().casefold() == old_name
                    ), fallback_resource_id)

                replacement = self.build_manager_item(
                    replacement_id,
                    int(row["id"]),
                    str(row["pile"] or old_item.get("pile", "club") or "club"),
                )
                self.conn.execute(
                    "UPDATE items SET resource_id=?, data=? WHERE id=?",
                    (replacement_id, json.dumps(replacement), int(row["id"])),
                )
                migrated += 1
            if migrated:
                self.conn.commit()
                log.info("Migrated %d career manager placeholder(s) to native FUT cards", migrated)

    def build_manager_item(
        self,
        resource_id: int,
        item_id: int,
        pile: str,
    ) -> dict[str, Any]:
        """Build FIFA 21's native manager-item shape from a real definition."""
        meta = manager_meta(resource_id)
        if not meta:
            raise ValueError(f"unknown FIFA 21 manager definition {resource_id}")
        rating = int(meta.get("rating", 75) or 75)
        rareflag = int(meta.get("rareflag", 1 if rating >= 80 else 0) or 0)
        return {
            "id": int(item_id),
            "timestamp": now_s(),
            "resourceId": int(resource_id),
            "assetId": int(resource_id),
            "carddbid": int(meta.get("carddbid", resource_id) or resource_id),
            "stackCount": 1,
            "untradeableCount": 0,
            "rating": rating,
            "itemState": "free",
            "rareflag": rareflag,
            "leagueId": int(meta.get("leagueId", 0) or 0),
            "nation": int(meta.get("nation", 0) or 0),
            "teamid": int(meta.get("teamid", 0) or 0),
            "contract": 7,
            "contracts": 7,
            "negotiation": int(meta.get("negotiation", 0) or 0),
            "discardValue": 300 if rareflag else 150,
            "lastSalePrice": 0,
            "itemType": "manager",
            "cardsubtypeid": 4,
            "category": 0,
            "owners": 1,
            "untradeable": False,
            "pile": str(pile),
            "resourceGameYear": 2021,
            "marketDataMinPrice": 150,
            "marketDataMaxPrice": 15000000,
            "firstName": str(meta.get("firstName", "") or ""),
            "lastName": str(meta.get("lastName", "") or ""),
            "commonname": str(meta.get("commonname", "") or ""),
        }

    def make_manager_item(self, resource_id: int, pile: str = "club") -> dict[str, Any]:
        item_id = self.next_item_id()
        item = self.build_manager_item(resource_id, item_id, pile)
        with self.lock:
            self.conn.execute(
                "INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                (item_id, int(resource_id), str(pile), json.dumps(item)),
            )
            self.conn.commit()
        return item

    def remove_non_squad_player_items(self, resource_id: int) -> int:
        """Remove an explicitly selected card definition outside an active squad."""
        resource_id = int(resource_id)
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, pile FROM items WHERE resource_id=?", (resource_id,)
            ).fetchall()
            if any(str(row["pile"]) == "squad" for row in rows):
                raise ValueError("refusing to remove a card currently assigned to a squad")
            self.conn.execute("DELETE FROM items WHERE resource_id=?", (resource_id,))
            self.conn.commit()
        return len(rows)

    def _migrate_custom_resource_ids(self) -> None:
        """Move explicitly superseded custom cards to their current definition.

        The card instance id is intentionally retained, so any squad references
        remain valid.  Only IDs declared by a custom catalog entry are migrated;
        native cards are never touched.
        """
        migrated = 0
        for new_resource_id, meta in PLAYER_DB.items():
            try:
                new_resource_id = int(new_resource_id)
                old_resource_ids = [int(value) for value in meta.get("replacesResourceIds", [])]
            except (TypeError, ValueError):
                continue

            for old_resource_id in old_resource_ids:
                if old_resource_id <= 0 or old_resource_id == new_resource_id:
                    continue
                rows = self.conn.execute(
                    "SELECT id, data FROM items WHERE resource_id=?", (old_resource_id,)
                ).fetchall()
                for row in rows:
                    try:
                        data = json.loads(row["data"])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        log.warning("Skipping malformed item data during custom card migration: %s", row["id"])
                        continue
                    data["resourceId"] = new_resource_id
                    data["assetId"] = int(meta.get("assetId", new_resource_id))
                    self.conn.execute(
                        "UPDATE items SET resource_id=?, data=? WHERE id=?",
                        (new_resource_id, json.dumps(data), int(row["id"])),
                    )
                    migrated += 1
        if migrated:
            self.conn.commit()
            log.info("Migrated %d superseded custom FUT card instance(s)", migrated)

    def _enrich_existing_player_items(self) -> None:
        """Backfill FIFA 21 metadata into items created by older 0-rated builds."""
        with self.lock:
            rows = self.conn.execute("SELECT id, resource_id, data FROM items").fetchall()
            changed = 0
            for row in rows:
                rid = int(row["resource_id"])
                meta = player_meta(rid)
                if not meta:
                    continue
                data = json.loads(row["data"])
                data["rating"] = int(meta.get("rating", data.get("rating", 0)))
                data["preferredPosition"] = str(meta.get("preferredPosition", data.get("preferredPosition", "")))
                data["preferredFoot"] = str(meta.get("preferredFoot", data.get("preferredFoot", "")) or "")
                data["weakFoot"] = int(meta.get("weakFoot", data.get("weakFoot", 0)) or 0)
                data["skillMoves"] = int(meta.get("skillMoves", data.get("skillMoves", 0)) or 0)
                data["workRateAttack"] = str(meta.get("workRateAttack", data.get("workRateAttack", "")) or "")
                data["workRateDefense"] = str(meta.get("workRateDefense", data.get("workRateDefense", "")) or "")
                data["leagueId"] = int(meta.get("leagueId", data.get("leagueId", 0)))
                data["teamid"] = int(meta.get("teamid", data.get("teamid", 0)))
                data["nation"] = int(meta.get("nation", data.get("nation", 0)))
                data["rareflag"] = int(meta.get("rareflag", data.get("rareflag", CFG.get("rare_flag", 1))) or 0)
                data["firstName"] = str(meta.get("firstName", data.get("firstName", "")) or "")
                data["lastName"] = str(meta.get("lastName", data.get("lastName", "")) or "")
                data["commonname"] = str(meta.get("commonname", data.get("commonname", "")) or "")
                data["assetId"] = int(meta.get("assetId", data.get("assetId", rid)) or rid)
                # Refresh the native six-slot item payload. Older experimental
                # aqua cards stored 34 entries here, which shifts subsequent
                # fields in FIFA 21's item decoder and corrupts names/workrates.
                data["attributeList"] = player_attribute_list(rid)
                loan_games = max(0, int(data.get("loans", 0) or 0))
                data["discardValue"] = 0 if loan_games else self.player_discard_value(rid)
                data["attributeList"] = player_attribute_list(rid)
                data["cardsubtypeid"] = int(meta.get("cardsubtypeid", 0) or 0)
                data["untradeable"] = bool(loan_games or meta.get("untradeable", False))
                data["resourceGameYear"] = 2021
                data["loans"] = loan_games
                self.conn.execute("UPDATE items SET data=? WHERE id=?", (json.dumps(data), int(row["id"])))
                changed += 1
            if changed:
                self.conn.commit()
                log.info("Enriched %d existing FUT player items with FIFA 21 metadata", changed)

    def _initial_player_resource_ids(self, count: int) -> list[int]:
        """Choose a deterministic, playable initial club from the native catalog."""
        selected: list[int] = []
        seen: set[int] = set()

        def add(resource_id: Any) -> None:
            try:
                rid = int(resource_id)
            except Exception:
                return
            if rid in PLAYER_DB and rid not in seen:
                seen.add(rid)
                selected.append(rid)

        # Preserve the established starter order so existing installs retain the
        # same first XI. Fill the rest with base cards, strongest first.
        for rid in CFG.get("starter_players", []):
            add(rid)
        base_cards = sorted(
            (
                (int(meta.get("rating", 0) or 0), rid)
                for rid, meta in PLAYER_DB.items()
                if int(meta.get("rareflag", 0) or 0) in (0, 1)
            ),
            key=lambda candidate: (-candidate[0], candidate[1]),
        )
        for _rating, rid in base_cards:
            if len(selected) >= count:
                break
            add(rid)
        return selected[:count]

    def _ensure_initial_club_players(self) -> None:
        """Add missing initial players without resetting or replacing club state."""
        target = max(11, int(CFG.get("initial_club_player_count", 250) or 250))
        owned = self.owned_player_resource_ids()
        added = 0
        for rid in self._initial_player_resource_ids(target):
            if len(owned) >= target:
                break
            if rid in owned:
                continue
            self.make_player_item(rid, "club")
            owned.add(rid)
            added += 1
        if added:
            log.info("Added %d FIFA 21 players to the initial club roster (owned=%d target=%d)",
                     added, len(owned), target)

    def _ensure_initial_club_manager(self) -> None:
        """Give existing and new clubs one real manager without resetting state."""
        if any(str(item.get("itemType", "")) == "manager"
               for item in self.list_items()):
            return
        if not MANAGER_DB:
            log.warning("No FIFA 21 manager definitions are available")
            return
        resource_id = min(
            MANAGER_DB,
            key=lambda rid: (-int(MANAGER_DB[rid].get("rating", 0) or 0), int(rid)),
        )
        manager = self.make_manager_item(resource_id, "club")
        log.info(
            "Added initial FIFA 21 manager item=%d definition=%d name=%s",
            int(manager["id"]), int(resource_id),
            str(MANAGER_DB[resource_id].get("name", "")),
        )

    @staticmethod
    def _squad_item_ids(squad: dict[str, Any]) -> list[int]:
        item_ids: list[int] = []
        for position in squad.get("players", []) if isinstance(squad.get("players"), list) else []:
            if not isinstance(position, dict):
                continue
            raw_id = position.get("itemId")
            if raw_id is None and isinstance(position.get("itemData"), dict):
                raw_id = position["itemData"].get("id")
            try:
                item_ids.append(int(raw_id))
            except Exception:
                continue
        return item_ids

    def _repair_empty_active_squad(self) -> None:
        """Populate a wholly empty saved squad while preserving non-empty squads."""
        with self.lock:
            row = self.conn.execute(
                "SELECT id, data FROM squads ORDER BY active DESC,id LIMIT 1"
            ).fetchone()
        if not row:
            return
        try:
            squad = json.loads(row["data"])
        except Exception:
            return
        owned_ids = {int(item.get("id", 0) or 0) for item in self.list_items("club")}
        if any(item_id in owned_ids for item_id in self._squad_item_ids(squad)):
            return
        players = self.list_items("club")[:11]
        if not players:
            return
        squad["players"] = [
            {"index": idx, "itemId": item["id"], "loyaltyBonus": 1}
            for idx, item in enumerate(players)
        ]
        squad["active"] = True
        with self.lock:
            self.conn.execute(
                "UPDATE squads SET active=1, data=? WHERE id=?",
                (json.dumps(squad), int(row["id"])),
            )
            self.conn.commit()
        log.info("Repaired empty FUT squad %s with %d owned players", row["id"], len(players))

    def list_items(self, pile: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            if pile:
                rows = self.conn.execute("SELECT data FROM items WHERE pile=? ORDER BY id", (pile,)).fetchall()
            else:
                rows = self.conn.execute("SELECT data FROM items ORDER BY id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def update_item_pile(self, item_id: int, pile: str) -> bool:
        with self.lock:
            row = self.conn.execute("SELECT data FROM items WHERE id=?", (int(item_id),)).fetchone()
            if not row:
                return False
            data = json.loads(row[0])
            data["pile"] = pile
            self.conn.execute("UPDATE items SET pile=?, data=? WHERE id=?", (pile, json.dumps(data), int(item_id)))
            self.conn.commit()
            return True

    def apply_position_modifier(self, resource_id: int, target_item_id: int) -> dict[str, Any]:
        """Apply and consume one native position-change training card."""
        resource_id = int(resource_id)
        definition = _POSITION_MODIFIER_BY_RESOURCE.get(resource_id)
        if not definition:
            raise ValueError(f"unknown position modifier definition {resource_id}")
        with self.lock:
            consumable_rows = self.conn.execute(
                "SELECT id,data FROM items WHERE resource_id=? ORDER BY id",
                (resource_id,),
            ).fetchall()
            consumable_row = next((row for row in consumable_rows
                                   if str(json.loads(row["data"]).get("pile", "")) == "club"), None)
            if consumable_row is None:
                raise ValueError("position modifier is not owned")
            target_row = self.conn.execute(
                "SELECT data FROM items WHERE id=?", (int(target_item_id),)
            ).fetchone()
            if target_row is None:
                raise ValueError("position modifier target was not found")
            target = json.loads(target_row["data"])
            if str(target.get("itemType", "player")) != "player":
                raise ValueError("position modifier target is not a player")
            current = str(target.get("preferredPosition", "") or "").upper()
            required = str(definition["source"])
            if current != required:
                raise ValueError(f"position modifier requires {required}, target is {current}")
            target["preferredPosition"] = str(definition["target"])
            self.conn.execute(
                "UPDATE items SET data=? WHERE id=?",
                (json.dumps(target), int(target_item_id)),
            )
            self.conn.execute("DELETE FROM items WHERE id=?", (int(consumable_row["id"]),))
            self.conn.commit()
            return target

    def _ensure_initial_club_kits(self) -> None:
        """Give both match-day slots real, resolvable owned kit instances."""
        defaults = (
            (6300074, "activeHomeKit", 1, 0, "Arsenal Home Kit"),
            (6301345, "activeAwayKit", 1, 1, "Arsenal Away Kit"),
        )
        stored = []
        changed = False
        for row in self.conn.execute("SELECT id,data FROM items").fetchall():
            try:
                item = json.loads(row["data"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if str(item.get("itemType", "")) == "kit":
                kit_type = int(item.get("kitType", -1) if item.get("kitType") is not None else -1)
                native_category = 2 + kit_type if kit_type in (0, 1, 2) else int(item.get("category", 2) or 2)
                if (int(item.get("category", 0) or 0) != native_category
                        or int(item.get("teamkittypetechid", -1) if item.get("teamkittypetechid") is not None else -1) != kit_type):
                    item["category"] = native_category
                    item["teamkittypetechid"] = kit_type
                    self.conn.execute("UPDATE items SET data=? WHERE id=?", (json.dumps(item), int(row["id"])))
                    changed = True
                stored.append((int(row["id"]), item))

        active_by_state = {
            str(item.get("itemState", "")): (item_id, item)
            for item_id, item in stored
            if str(item.get("itemState", "")) in ("activeHomeKit", "activeAwayKit")
        }
        for resource_id, item_state, team_id, kit_type, name in defaults:
            existing = active_by_state.get(item_state)
            if existing:
                # Preserve user choices. Only repair the legacy state where the
                # same definition occupied both match-day slots.
                if item_state == "activeHomeKit":
                    continue
                home = active_by_state.get("activeHomeKit")
                home_resource = int(home[1].get("resourceId", 0) or 0) if home else 0
                existing_resource = int(existing[1].get("resourceId", 0) or 0)
                if existing_resource and existing_resource != home_resource:
                    continue

            # Prefer an already-owned copy from the all-kits pack.
            owned = next(
                ((item_id, item) for item_id, item in stored
                 if int(item.get("resourceId", 0) or 0) == resource_id),
                None,
            )
            if owned is None:
                item_id = self.next_item_id()
                item = {
                    "id": item_id, "timestamp": now_s(),
                    "family": "kits", "table": "fcc_kitcards",
                    "resourceId": resource_id, "carddbid": resource_id,
                    "assetId": resource_id, "cardassetid": 35,
                    "itemType": "kit", "cardsubtypeid": 9,
                    "itemState": item_state, "owners": 1,
                    "untradeable": True, "discardValue": 10,
                    "lastSalePrice": 0, "stackCount": 1,
                    "untradeableCount": 1, "pile": "club", "islocked": 0,
                    "rating": 83, "rareflag": 1, "value": 83,
                    "weightrare": 10, "category": 2 + kit_type, "teamid": team_id,
                    "kitType": kit_type, "teamkittypetechid": kit_type,
                    "year": 0, "resourceGameYear": 2021,
                    "name": name,
                }
                self.conn.execute(
                    "INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                    (item_id, resource_id, "club", json.dumps(item)),
                )
                stored.append((item_id, item))
            else:
                item_id, item = owned
                item = dict(item)
                item.update({"itemState": item_state, "pile": "club"})
                self.conn.execute(
                    "UPDATE items SET pile=?,data=? WHERE id=?",
                    ("club", json.dumps(item), item_id),
                )

            # A slot must have exactly one active instance.
            for other_id, other in stored:
                if other_id == item_id or str(other.get("itemState", "")) != item_state:
                    continue
                other = dict(other)
                other["itemState"] = "free"
                self.conn.execute("UPDATE items SET data=? WHERE id=?", (json.dumps(other), other_id))
            changed = True

        if changed:
            self.conn.commit()
            log.info("Ensured distinct native owned home/away kit instances")

    def equip_kit(self, item_id: int, item_state: str) -> dict[str, Any]:
        """Persist one owned kit as the active home or away strip."""
        if item_state not in ("activeHomeKit", "activeAwayKit"):
            raise ValueError("invalid active kit slot")
        with self.lock:
            row = self.conn.execute("SELECT data FROM items WHERE id=?", (int(item_id),)).fetchone()
            if row is None:
                raise ValueError("kit was not found")
            selected = json.loads(row["data"])
            if str(selected.get("itemType", "")) != "kit":
                raise ValueError("selected item is not a kit")
            for stored in self.conn.execute("SELECT id,data FROM items").fetchall():
                data = json.loads(stored["data"])
                if (int(stored["id"]) != int(item_id)
                        and str(data.get("itemType", "")) == "kit"
                        and str(data.get("itemState", "")) == item_state):
                    data["itemState"] = "free"
                    self.conn.execute("UPDATE items SET data=? WHERE id=?",
                                      (json.dumps(data), int(stored["id"])))
            selected["itemState"] = item_state
            selected["pile"] = "club"
            self.conn.execute("UPDATE items SET pile='club',data=? WHERE id=?",
                              (json.dumps(selected), int(item_id)))
            self.conn.commit()
            return selected

    def equip_club_customisation(self, item_id: int, item_state: str) -> dict[str, Any]:
        """Persist a verified owned Match Day cosmetic as the active item."""
        expected = {
            "activeBadge": ("custom", 11),
            "activeBall": ("ball", 30),
        }
        if item_state not in expected:
            raise ValueError("unsupported club customisation slot")
        expected_type, expected_subtype = expected[item_state]
        with self.lock:
            row = self.conn.execute("SELECT data FROM items WHERE id=?", (int(item_id),)).fetchone()
            if row is None:
                raise ValueError("customisation item was not found")
            selected = json.loads(row["data"])
            if (str(selected.get("itemType", "")) != expected_type
                    or int(selected.get("cardsubtypeid", 0) or 0) != expected_subtype):
                raise ValueError("selected item does not match the requested slot")
            for stored in self.conn.execute("SELECT id,data FROM items").fetchall():
                data = json.loads(stored["data"])
                if (int(stored["id"]) != int(item_id)
                        and str(data.get("itemState", "")) == item_state):
                    data["itemState"] = "free"
                    self.conn.execute("UPDATE items SET data=? WHERE id=?",
                                      (json.dumps(data), int(stored["id"])))
            selected.update({"itemState": item_state, "pile": "club"})
            self.conn.execute("UPDATE items SET pile='club',data=? WHERE id=?",
                              (json.dumps(selected), int(item_id)))
            self.conn.commit()
            return selected

    def delete_item(self, item_id: int) -> bool:
        with self.lock:
            cur = self.conn.execute("DELETE FROM items WHERE id=?", (int(item_id),))
            self.conn.commit()
            return cur.rowcount > 0

    def discard_items(self, item_ids: list[int]) -> tuple[int, list[int]]:
        """Delete owned item instances and credit their stored server-side values.

        Never trusts a discard value supplied by the FIFA client. Each item is
        looked up in SQLite and its expected value is recalculated from players.json.
        """
        clean_ids = []
        seen = set()
        for raw in item_ids:
            try:
                iid = int(raw)
            except Exception:
                continue
            if iid > 0 and iid not in seen:
                seen.add(iid)
                clean_ids.append(iid)

        if not clean_ids:
            return self.credits(), []

        payout = 0
        deleted = []

        with self.lock:
            for iid in clean_ids:
                row = self.conn.execute(
                    "SELECT resource_id, data FROM items WHERE id=?",
                    (iid,),
                ).fetchone()
                if not row:
                    continue

                try:
                    data = json.loads(row["data"] if hasattr(row, "keys") else row[1])
                except Exception:
                    data = {}

                # Do not allow squad-bound cards to disappear through an odd client call.
                pile = str(data.get("pile", ""))
                if pile == "squad":
                    log.warning("Refused discard of squad-bound item %d", iid)
                    continue

                try:
                    rid = int(data.get("resourceId", row["resource_id"] if hasattr(row, "keys") else row[0]) or 0)
                except Exception:
                    rid = int(row["resource_id"] if hasattr(row, "keys") else row[0])

                value = (self.player_discard_value(rid)
                         if str(data.get("itemType", "player")) == "player"
                         else int(data.get("discardValue", 0) or 0))
                payout += max(0, int(value))

                cur = self.conn.execute("DELETE FROM items WHERE id=?", (iid,))
                if cur.rowcount:
                    deleted.append(iid)

            if deleted and payout:
                current = int(self.get("credits", 0))
                new_total = max(0, current + payout)
                self.conn.execute(
                    "INSERT INTO kv(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("credits", json.dumps(new_total)),
                )
            self.conn.commit()

        log.warning(
            "QUICKSELL items=%s payout=%d newCredits=%d",
            deleted, payout, self.credits()
        )
        return self.credits(), deleted

    def clear_pile(self, pile: str) -> int:
        """Remove all transient items from a pile and return the number removed."""
        with self.lock:
            cur = self.conn.execute("DELETE FROM items WHERE pile=?", (str(pile),))
            self.conn.commit()
            return int(cur.rowcount or 0)

    def owned_player_resource_ids(self) -> set[int]:
        """Return player definitions already owned in the club or a squad.

        Unassigned pack items are intentionally excluded: FIFA's duplicate marker
        should compare a newly opened card against cards the user already owns,
        not against the rest of the current pack reveal.
        """
        owned: set[int] = set()
        for pile in ("club", "squad"):
            for item in self.list_items(pile):
                if str(item.get("itemType", "player")) != "player":
                    continue
                try:
                    rid = int(item.get("resourceId", item.get("assetId", 0)) or 0)
                except Exception:
                    rid = 0
                if rid:
                    owned.add(rid)
        return owned

    def owned_player_item_by_resource_id(self, resource_id: int) -> dict[str, Any] | None:
        """Return an already-owned club/squad item for a player definition."""
        rid = int(resource_id or 0)
        if not rid:
            return None
        for pile in ("club", "squad"):
            for item in self.list_items(pile):
                if str(item.get("itemType", "player")) != "player":
                    continue
                try:
                    item_rid = int(item.get("resourceId", item.get("assetId", 0)) or 0)
                except Exception:
                    continue
                if item_rid == rid:
                    return item
        return None

    def duplicate_item_ids(self, items: list[dict[str, Any]] | None = None) -> list[int]:
        """Return item-instance IDs whose player definition is already owned."""
        owned = self.owned_player_resource_ids()
        check = items if items is not None else self.list_items("unassigned")
        dupes: list[int] = []
        for item in check:
            if str(item.get("itemType", "player")) != "player":
                continue
            try:
                rid = int(item.get("resourceId", item.get("assetId", 0)) or 0)
                iid = int(item.get("id", 0) or 0)
            except Exception:
                continue
            if rid in owned and iid:
                dupes.append(iid)
        return dupes

    def mark_item_duplicate(self, item_id: int, is_duplicate: bool = True) -> bool:
        """Persist only a conservative duplicate marker/pile state."""
        with self.lock:
            row = self.conn.execute("SELECT data FROM items WHERE id=?", (int(item_id),)).fetchone()
            if not row:
                return False
            try:
                data = json.loads(row[0])
            except Exception:
                return False
            # Do not inject UI-only symbols into the native FUT item schema.
            for key in ("IS_DUPLICATE", "isItemAlreadyInClub", "DUPLICATE_CARD_REF", "duplicateCardRef"):
                data.pop(key, None)
            data["duplicate"] = bool(is_duplicate)
            data["isDuplicate"] = bool(is_duplicate)
            if is_duplicate:
                data["pile"] = "unassigned"
            self.conn.execute("UPDATE items SET pile=?, data=? WHERE id=?",
                              (str(data.get("pile", "unassigned")), json.dumps(data), int(item_id)))
            self.conn.commit()
            return True

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        """Return one stored FUT item by item-instance id."""
        with self.lock:
            row = self.conn.execute("SELECT data FROM items WHERE id=?", (int(item_id),)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def would_duplicate_owned_player(self, item_id: int) -> bool:
        """True when moving this player into club/squad would duplicate a definition."""
        item = self.get_item(item_id)
        if not item or str(item.get("itemType", "player")) != "player":
            return False
        try:
            rid = int(item.get("resourceId", item.get("assetId", 0)) or 0)
        except Exception:
            return False
        if not rid:
            return False
        # Compare against cards already owned in club/squad. The item being moved
        # is normally still unassigned, so it is naturally excluded here.
        return rid in self.owned_player_resource_ids()

    def _seed_squad(self) -> None:
        players = self.list_items("club")[:11]
        lineup = []
        for idx, item in enumerate(players):
            lineup.append({"index": idx, "itemId": item["id"], "loyaltyBonus": 1})
        squad = {
            "id": 1,
            "squadId": 1,
            "name": "Local XI",
            "squadName": "Local XI",
            "formation": "f442",
            "rating": 0,
            "chemistry": 0,
            "active": True,
            "players": lineup,
            "kicktakers": [],
        }
        with self.lock:
            self.conn.execute(
                "INSERT INTO squads(id,name,active,data) VALUES(1,?,?,?)",
                ("Local XI", 1, json.dumps(squad)),
            )
            self.conn.commit()

    def list_squads(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT data FROM squads ORDER BY active DESC,id").fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_squad(self, payload: Any) -> dict[str, Any]:
        # Accept the game's native JSON shape without over-normalizing it.
        src = payload if isinstance(payload, dict) else {}
        if isinstance(src.get("squad"), dict):
            src = src["squad"]
        sid = int(src.get("id") or src.get("squadId") or 1)
        name = str(src.get("name") or src.get("squadName") or f"Squad {sid}")
        src["id"] = sid
        src["squadId"] = sid
        src["name"] = name
        src["squadName"] = name
        # FIFA sometimes submits id=0 for the visible manager card. Persisting
        # that turns the manager slot into a NullItem and the pre-match screen
        # rejects the squad. Repair it to a real owned manager instance.
        managers = src.get("manager", []) if isinstance(src.get("manager"), list) else []
        manager_id = 0
        if managers and isinstance(managers[0], dict):
            manager_data = managers[0].get("itemData") if isinstance(managers[0].get("itemData"), dict) else managers[0]
            try:
                manager_id = int(manager_data.get("id", 0) or 0)
            except Exception:
                manager_id = 0
        manager_item = self.get_item(manager_id) if manager_id else None
        if not manager_item or str(manager_item.get("itemType", "")) != "manager":
            owned_managers = [item for item in self.list_items()
                              if str(item.get("itemType", "")) == "manager"
                              and int(item.get("id", 0) or 0) > 0]
            if owned_managers:
                owned_managers.sort(key=lambda item: (-int(item.get("rating", 0) or 0), int(item.get("id", 0) or 0)))
                manager_id = int(owned_managers[0]["id"])
                src["manager"] = [{"id": manager_id, "dream": False}]
        active = 1 if src.get("active", sid == 1) else 0
        with self.lock:
            if active:
                self.conn.execute("UPDATE squads SET active=0")
            self.conn.execute(
                "INSERT INTO squads(id,name,active,data) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,active=excluded.active,data=excluded.data",
                (sid, name, active, json.dumps(src)),
            )
            self.conn.commit()
        return src

    def delete_squad(self, sid: int) -> bool:
        with self.lock:
            cur = self.conn.execute("DELETE FROM squads WHERE id=?", (int(sid),))
            self.conn.commit()
            return cur.rowcount > 0

    def create_pack(self, pack_id: int = 1) -> dict[str, Any]:
        cleared=self.clear_pile("unassigned")
        if cleared:
            log.info("Cleared %d stale unassigned pack item(s) before new pack",cleared)

        pcfg=get_pack_cfg(pack_id)
        if pcfg.get("player_pick_count"):
            min_rating = int(pcfg.get("min_rating", 0) or 0)
            candidates = [
                rid for rid, meta in PLAYER_DB.items()
                if int(meta.get("rating", 0) or 0) >= min_rating
                and aqua_pro_resource_id(int(rid)) is None
            ]
            if not candidates:
                candidates = list(PLAYER_DB.keys())
            weights = [max(0.0001, float(high_rating_pack_weight(rid))) for rid in candidates]
            preview_resource_id = int(random.choices(candidates, weights=weights, k=1)[0])
            token = self.make_player_pick_item(
                min_rating,
                int(pcfg.get("player_pick_count", 4) or 4),
                "unassigned",
                int(pack_id),
                preview_resource_id,
            )
            return {
                "packId": int(pack_id), "purchasedPackId": int(pack_id),
                "packType": "PLAYER_PICK", "itemList": [token],
                "itemData": [token], "items": [token], "newcards": 1,
                "numberItems": 1, "duplicateItemIdList": [],
                "credits": self.credits(),
            }
        if pcfg.get("set_stadium_id"):
            # Stadium-selector pack: opening it just sets the club's home
            # venue.  The in-game My Stadium slot editors crash client-side
            # (pre-existing), so this is the working way to change stadium.
            try:
                sid = int(pcfg["set_stadium_id"])
            except (TypeError, ValueError):
                sid = STADIUM_DEFAULT_ID
            if sid not in _STADIUM_BY_ID:
                sid = STADIUM_DEFAULT_ID
            self.set("selected_stadium_id", sid)
            log.info("Home stadium set via pack %s -> %d (%s)",
                     pack_id, sid, _STADIUM_BY_ID.get(sid, {}).get("name", "?"))
            return {"itemData": [], "duplicateItemIdList": [], "numberItems": 0}
        if pcfg.get("all_consumables"):
            # One of every native FIFA 21 position-change training card.
            native_items = []
            with self.lock:
                for resource_id, subtype, source, target in _POSITION_MODIFIER_DEFINITIONS:
                    item_id = self.next_item_id()
                    item = {
                        "id": item_id, "timestamp": now_s(),
                        "resourceId": resource_id, "carddbid": resource_id,
                        "cardassetid": 34, "assetId": resource_id,
                        "itemType": "training", "itemState": "free",
                        "cardsubtypeid": subtype, "consumableType": 3,
                        "consumableId": subtype, "sourcePosition": source,
                        "targetPosition": target, "position": target,
                        "positionModifier": f"{source}-{target}",
                        "rating": 90 if subtype in (92, 94, 99, 101, 103, 106) else 95,
                        "rareflag": 1, "category": 0, "class": "Player",
                        "resourceGameYear": 2021, "discardValue": 80,
                        "pile": "unassigned",
                    }
                    self.conn.execute("INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                                      (item_id, item["resourceId"], "unassigned", json.dumps(item)))
                    native_items.append(item)
                self.conn.commit()
            return {"itemData": native_items, "duplicateItemIdList": [],
                    "numberItems": len(native_items)}
        if pcfg.get("all_kits"):
            if not KIT_DB:
                raise RuntimeError("FIFA 21 kit catalog is unavailable")
            pack_size = max(1, min(30, int(pcfg.get("size", 30) or 30)))
            owned_resources = {
                int(item.get("resourceId", 0) or 0) for item in self.list_items()
                if str(item.get("itemType", "")) == "kit"
            }
            candidates = [kit for kit in KIT_DB
                          if int(kit.get("resourceId", 0) or 0) not in owned_resources]
            if len(candidates) < pack_size:
                candidates = list(KIT_DB)
            selected_kits = random.sample(candidates, min(pack_size, len(candidates)))
            native_items = []
            with self.lock:
                for kit in selected_kits:
                    item_id = self.next_item_id()
                    resource_id = int(kit["resourceId"])
                    item = {
                        "id": item_id, "timestamp": now_s(),
                        "family": "kits", "table": "fcc_kitcards",
                        "resourceId": resource_id, "carddbid": resource_id,
                        "assetId": resource_id, "cardassetid": 35,
                        "itemType": "kit", "cardsubtypeid": 9,
                        "itemState": "free", "owners": 1,
                        "untradeable": True, "discardValue": 10,
                        "lastSalePrice": 0, "stackCount": 1,
                        "untradeableCount": 1, "pile": "unassigned",
                        "islocked": 0, "rating": 83, "rareflag": 1,
                        "value": 83, "weightrare": 10,
                        "category": 2 + int(kit.get("kitType", 0) or 0),
                        "teamid": int(kit.get("teamid", 0) or 0),
                        "kitType": int(kit.get("kitType", -1)
                                       if kit.get("kitType") is not None else -1),
                        "teamkittypetechid": int(kit.get("kitType", -1)
                                                  if kit.get("kitType") is not None else -1),
                        "year": 0, "resourceGameYear": 2021,
                        "name": str(kit.get("name", "FIFA 21 Kit")),
                    }
                    self.conn.execute("INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                                      (item_id, resource_id, "unassigned", json.dumps(item)))
                    native_items.append(item)
                self.conn.commit()
            return {"itemData": native_items, "duplicateItemIdList": [],
                    "numberItems": len(native_items)}
        if not pcfg:
            raise RuntimeError(f"Unknown pack id {pack_id}")

        pack_size=max(1,min(30,int(pcfg.get("size",12))))
        rare_slots=max(0,min(pack_size,int(pcfg.get("rare_slots",3))))
        min_rating=max(0,min(99,int(pcfg.get("min_rating",0) or 0)))
        guaranteed_specials=max(0,min(rare_slots,int(pcfg.get("guaranteed_specials",0) or 0)))
        special_chance=max(0.0,min(1.0,float(pcfg.get("special_chance",0.10))))
        manager_chance=max(0.0,min(1.0,float(pcfg.get("manager_chance",0.0) or 0.0)))
        tier_weights={
            "gold":max(0.0,float(pcfg.get("gold_weight",0) or 0)),
            "silver":max(0.0,float(pcfg.get("silver_weight",0) or 0)),
            "bronze":max(0.0,float(pcfg.get("bronze_weight",0) or 0)),
        }
        if sum(tier_weights.values())<=0:
            tier_weights={"gold":100.0,"silver":0.0,"bronze":0.0}

        special_weights={}
        for k,v in pcfg.get("_special_weights",{}).items():
            try:
                rf=int(k)
                special_weights[rf]=max(0.0,float(v))
            except Exception: pass
        allow_unweighted_specials=bool(pcfg.get("allow_unweighted_specials",False))
        allowed_rareflags=None
        if isinstance(pcfg.get("allowed_rareflags"),list):
            allowed_rareflags=set()
            for value in pcfg["allowed_rareflags"]:
                try: allowed_rareflags.add(int(value))
                except (TypeError, ValueError): pass

        def tier_for(meta):
            t=str(meta.get("tier","") or "").lower()
            if t in ("gold","silver","bronze"): return t
            r=int(meta.get("rating",0) or 0)
            return "gold" if r>=75 else "silver" if r>=65 else "bronze"

        pools={t:{"common":[],"rare":[],"special":[]} for t in ("gold","silver","bronze")}
        for rid0 in PLAYER_DB.keys():
            rid=int(rid0); meta=player_meta(rid); t=tier_for(meta)
            rating=int(meta.get("rating",0) or 0)
            if rating < min_rating:
                continue
            rf=int(meta.get("rareflag",0) or 0)
            if allowed_rareflags is not None and rf not in allowed_rareflags:
                continue
            issp=bool(meta.get("special",False)) or rf>1
            # The shipped player database contains base-player metadata but
            # no rare flags. Derive a stable fallback pool so a pack with
            # guaranteed rare slots can still be opened locally.
            fallback_rare = (
                (t == "gold" and rating >= 82)
                or (t == "silver" and rating >= 72)
                or (t == "bronze" and rating >= 64)
            )
            pools[t]["special" if issp else "rare" if rf == 1 or fallback_rare else "common"].append(rid)
        for t in pools:
            for k in pools[t]: random.shuffle(pools[t][k])

        selected=[]; used=set(); forced_rare=set()

        def weighted_choice(opts):
            arr=[(v,float(w)) for v,w in opts if float(w)>0]
            if not arr: return None
            total=sum(w for _,w in arr); r=random.random()*total; acc=0
            for v,w in arr:
                acc+=w
                if r<=acc: return v
            return arr[-1][0]

        def choose_tier():
            return weighted_choice(list(tier_weights.items())) or "gold"

        def pop_unique(pool):
            while pool:
                rid=pool.pop()
                if rid not in used:
                    used.add(rid); return rid
            return None

        def pop_pack_rare(pool):
            candidates=[rid for rid in pool if rid not in used]
            rid=weighted_choice([(rid,high_rating_pack_weight(rid)) for rid in candidates])
            if rid is None:
                return None
            used.add(rid)
            try: pool.remove(rid)
            except ValueError: pass
            return rid

        def choose_special(tier):
            # special_weights are rarity/type weights, not per-card weights.
            # Choose the rareflag first, then a card within that rareflag.
            # Otherwise a type with hundreds of cards (e.g. IF/TOTW) overwhelms
            # a type with only a handful of cards even when both sliders are equal.
            by_type={}
            for rid in pools[tier]["special"]:
                if rid in used:
                    continue
                rf=int(player_meta(rid).get("rareflag",0) or 0)
                weight=float(special_weights.get(rf,0))
                if weight<=0 and allow_unweighted_specials:
                    weight=1.0
                if weight<=0:
                    continue
                by_type.setdefault(rf,[]).append(rid)
            if not by_type:
                return None
            rf=weighted_choice([(
                rf,
                float(special_weights.get(rf,0)) or (1.0 if allow_unweighted_specials else 0.0),
            ) for rf in by_type])
            if rf is None:
                return None
            candidates=by_type.get(int(rf),[])
            if not candidates:
                return None
            rid=weighted_choice([(candidate,high_rating_pack_weight(candidate))
                                 for candidate in candidates])
            if rid is None:
                return None
            used.add(rid)
            try: pools[tier]["special"].remove(rid)
            except ValueError: pass
            return rid

        # guaranteed rare slots
        for slot_index in range(rare_slots):
            tier=choose_tier()
            must_be_special=slot_index < guaranteed_specials
            rid=choose_special(tier) if must_be_special or random.random()<special_chance else None
            if rid is None and must_be_special:
                for alt in (t for t,w in tier_weights.items() if w>0 and t!=tier):
                    rid=choose_special(alt)
                    if rid is not None: break
            if rid is None and must_be_special:
                raise RuntimeError("Configured guaranteed special pool is empty")
            if rid is None: rid=pop_pack_rare(pools[tier]["rare"])
            if rid is None: rid=choose_special(tier)
            if rid is None:
                # only fall back to another ENABLED tier, not hidden tiers
                enabled=[t for t,w in tier_weights.items() if w>0 and t!=tier]
                for alt in enabled:
                    rid=pop_pack_rare(pools[alt]["rare"]) or choose_special(alt)
                    if rid is not None: break
            if rid is None:
                raise RuntimeError("Configured rare/special pool is empty")
            selected.append(rid)
            if int(player_meta(rid).get("rareflag",0) or 0) == 0:
                forced_rare.add(rid)

        # common slots
        for _ in range(pack_size-rare_slots):
            tier=choose_tier()
            rid=pop_unique(pools[tier]["common"])
            if rid is None:
                enabled=[t for t,w in tier_weights.items() if w>0 and t!=tier]
                for alt in enabled:
                    rid=pop_unique(pools[alt]["common"])
                    if rid is not None: break
            if rid is None:
                raise RuntimeError("Configured common pool is empty")
            selected.append(rid)

        # Pack reveal order: SPECIAL cards first, regardless of OVR; then highest OVR first.
        # Within each group, rareflag/resourceId are deterministic tie-breakers.
        selected=sorted(selected,key=lambda rid:(
            0 if (bool(player_meta(rid).get("special",False)) or int(player_meta(rid).get("rareflag",0) or 0)>1) else 1,
            -int(player_meta(rid).get("rating",0) or 0),
            -int(player_meta(rid).get("rareflag",0) or 0),
            -int(rid)))
        pack_entries=[("player", rid) for rid in selected]
        if pack_entries and MANAGER_DB and random.random() < manager_chance:
            enabled_tiers={tier for tier, weight in tier_weights.items() if weight > 0}
            manager_candidates=[]
            for manager_id, meta in MANAGER_DB.items():
                rating=int(meta.get("rating",0) or 0)
                tier="gold" if rating>=75 else "silver" if rating>=65 else "bronze"
                if tier in enabled_tiers:
                    manager_candidates.append(int(manager_id))
            if manager_candidates:
                # Replace only the lowest-priority common player. Guaranteed
                # rare/special player slots and player-only promo packs remain intact.
                pack_entries[-1] = ("manager", random.choice(manager_candidates))
        owned_before=self.owned_player_resource_ids()

        # IMPORTANT: FIFA 21 PC does not reliably preserve the JSON array order on
        # the New Items screen. It can rebuild the unassigned collection using the
        # item-instance metadata. Encode the desired reveal order into the actual
        # item IDs/timestamps as well as the response arrays:
        #   - create worst -> best so the best/special card receives the HIGHEST id
        #   - newest timestamp = best/special card
        # This makes descending ID/timestamp client-side ordering resolve to exactly
        # the same order we want to display.
        created_reversed=[]
        for item_type, rid in reversed(pack_entries):
            item=(self.make_manager_item(rid,"unassigned")
                  if item_type == "manager"
                  else self.make_player_item(rid,"unassigned"))
            if item_type == "player" and rid in forced_rare:
                item["rareflag"]=1
            created_reversed.append(item)
        items=list(reversed(created_reversed))
        base_ts=now_s()
        for rank,item in enumerate(items):
            item["timestamp"]=base_ts-rank
            with self.lock:
                self.conn.execute("UPDATE items SET data=? WHERE id=?", (json.dumps(item), int(item.get("id",0))))
        with self.lock:
            self.conn.commit()

        dup_ids=[int(x["id"]) for x in items
                 if str(x.get("itemType", "player")) == "player"
                 and int(x.get("resourceId",x.get("assetId",0)) or 0) in owned_before]
        dup_list=[]
        for x in items:
            iid=int(x.get("id",0) or 0); rid=int(x.get("resourceId",x.get("assetId",0)) or 0)
            if iid not in dup_ids: continue
            own=self.owned_player_item_by_resource_id(rid)
            ownid=int(own.get("id",0) or 0) if isinstance(own,dict) else 0
            dup_list.append({"itemId":iid,"duplicateItemId":ownid} if ownid else {"itemId":iid})
        for iid in dup_ids: self.mark_item_duplicate(iid,True)
        if dup_ids:
            refreshed={int(x.get("id",0)):x for x in self.list_items("unassigned")}
            items=[refreshed.get(int(x.get("id",0)),x) for x in items]

        items=sorted(items,key=lambda x:(
            2 if str(x.get("itemType", "player")) == "manager" else
            0 if (bool(x.get("special",False)) or int(x.get("rareflag",0) or 0)>1) else 1,
            -int(x.get("rating",0) or 0),
            -int(x.get("rareflag",0) or 0),
            -int(x.get("resourceId",x.get("assetId",0)) or 0)))
        log.warning("CUSTOM PACK id=%s name=%s size=%d rareSlots=%d minRating=%d guaranteedSpecials=%d specialChance=%.1f%% managerChance=%.1f%%",
                    pack_id,pcfg.get("name"),pack_size,rare_slots,min_rating,
                    guaranteed_specials,special_chance*100,manager_chance*100)
        if items:
            log.warning("PACK HEADLINE CANDIDATE id=%s resource=%s rating=%s rareflag=%s discardValue=%s",
                        items[0].get("id"),items[0].get("resourceId"),items[0].get("rating"),
                        items[0].get("rareflag"),items[0].get("discardValue"))
        return {"packId":int(pack_id),"purchasedPackId":int(pack_id),"packType":"CUSTOM_PACK",
                "itemList":items,"itemData":items,"items":items,
                "newcards":len(items)-len(dup_ids),"numberItems":len(items),
                "duplicateItemIdList":dup_list,"credits":self.credits()}


def _native_pack_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return the item shape used by native FUT pack/unassigned responses.

    Keep the real resourceId (the client uses it to resolve the player/card), but
    omit local-only/derived keys that are not part of the original pack item
    payload.  In particular, do not send assetId alongside resourceId here: the
    native client derives the base asset from resourceId.
    """
    out = dict(item)
    # Keep assetId on FIFA 21 PC. Removing it can make some base cards render
    # as NOT FOUND even when resourceId is valid. Only strip server-only fields.
    for key in ("pile", "special", "tier", "wefutCard"):
        out.pop(key, None)
    if str(out.get("itemType", "player")) == "player" and _is_special_card(out):
        # Icon / TOTY / TOTS ... cards have no entry in FIFA 21's bundled
        # roster for their assetId, so the pack-reveal card renders blank
        # unless the name is supplied here.  Plain gold/silver/bronze cards
        # resolve fine on their own and must be left untouched (adding fields
        # broke consumables / Draft).  Skill moves / weak foot are not
        # overridden -- doing so showed wrong star ratings on cards like Pelé.
        out.update(_player_name_fields(out))
    return out


STATE = State(DB_PATH)


def local_user_info() -> dict[str, Any]:
    return {
        "personaId": int(CFG["persona_id"]),
        "personaName": CFG["display_name"],
        "nucleusId": int(CFG["nucleus_id"]),
        "clubId": int(CFG["club_id"]),
        "clubName": CFG["club_name"],
        "clubAbbr": CFG["club_abbr"],
        "credits": STATE.credits(),
    }


def account_info() -> dict[str, Any]:
    club = {
        "clubId": int(CFG["club_id"]),
        "clubName": CFG["club_name"],
        "clubAbbr": CFG["club_abbr"],
        "year": 2015,
        "lastAccessTime": now_s(),
        "skuAccessList": [CFG["game_sku"]],
    }
    persona = {
        "personaId": int(CFG["persona_id"]),
        "personaName": CFG["display_name"],
        "userClubList": [club],
    }
    return {
        "userAccountInfo": {
            "nucleusId": int(CFG["nucleus_id"]),
            "dateOfBirth": str(CFG["date_of_birth"]),
            "age": mock_account_age(),
            "isUnderAge": mock_account_is_under_age(),
            "personas": [persona],
        },
        "personas": [persona],
    }


def onboarding_complete() -> bool:
    """Treat the persisted badge selection as completion for older profiles."""
    return bool(STATE.get("onboarding_complete", False)
                or int(STATE.get("onboarding_badge_choice", 0) or 0))


def _stored_client_data(kind: str) -> dict[int, int]:
    raw = STATE.get(f"clientdata:{kind}", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[int, int] = {}
    for key, value in raw.items():
        try:
            parsed_key = int(key)
        except (TypeError, ValueError):
            continue
        if (0 <= parsed_key <= 4096 and isinstance(value, int)
                and not isinstance(value, bool) and -(2**31) <= value <= 2**31 - 1):
            result[parsed_key] = value
    return result


def _client_data_payload(values: dict[int, int]) -> dict[str, list[dict[str, int]]]:
    return {"entries": [{"key": key, "value": values[key]} for key in sorted(values)]}


def _save_client_data(kind: str, payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return 400, {"reason": "expected client-data entries"}
    entries = payload["entries"]
    if len(entries) > 256:
        return 400, {"reason": "too many client-data entries"}
    values = _stored_client_data(kind)
    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return 400, {"reason": "invalid client-data entry"}
        key, value = entry.get("key"), entry.get("value")
        if (isinstance(key, bool) or not isinstance(key, int) or not 0 <= key <= 4096
                or isinstance(value, bool) or not isinstance(value, int)
                or not -(2**31) <= value <= 2**31 - 1 or key in seen):
            return 400, {"reason": "invalid client-data key/value"}
        seen.add(key)
        values[key] = value
    STATE.set(f"clientdata:{kind}", {str(key): value for key, value in values.items()})
    return 200, _client_data_payload(values)


def _completed_onboarding_client_data() -> dict[str, list[dict[str, int]]]:
    values = _stored_client_data("onboarding")
    if onboarding_complete():
        values[0] = 3
        kits = STATE.get("onboarding_kit_choices", [])
        if isinstance(kits, list) and len(kits) >= 2:
            values[1], values[2] = int(kits[0]), int(kits[1])
        else:
            equipped = {str(item.get("itemState", "")): int(item.get("resourceId", 0) or 0)
                        for item in _active_club_items()}
            if equipped.get("activeHomeKit"):
                values[1] = equipped["activeHomeKit"]
            if equipped.get("activeAwayKit"):
                values[2] = equipped["activeAwayKit"]
        badge = int(STATE.get("onboarding_badge_choice", 0) or 0)
        if not badge:
            badge = next((int(item.get("resourceId", 0) or 0) for item in _active_club_items()
                          if str(item.get("itemState", "")) == "activeBadge"), 0)
        if badge:
            values[3] = badge
    return _client_data_payload(values)


def _completed_loan_client_data() -> dict[str, list[dict[str, int]]]:
    values = _stored_client_data("loanplayerreward")
    if not onboarding_complete():
        return _client_data_payload(values)
    resource_id = int(STATE.get("onboarding_loan_resource_id", 0) or 0)
    if resource_id:
        values[0] = resource_id & 0xFFFFFF
    return _client_data_payload(values)


def _squad_client_data() -> dict[str, list[dict[str, int]]]:
    values = _stored_client_data("squad")
    if onboarding_complete():
        values.setdefault(5, 0)
    return _client_data_payload(values)


def user_mass_info() -> dict[str, Any]:
    # CardsDLL's startup decoder dispatches these sections to the same native
    # decoders as /user, /squad/active and /loan/players. Keep this bulk route
    # aligned with those standalone responses; a shallow userInfo object makes
    # the first-run squad screen render every position as empty.
    return {
        "userInfo": _native_user(),
        "squad": _native_squad(),
        # Sending the starter-loan offers on every login makes FIFA re-enter
        # first-run onboarding. Once the badge selection is persisted, expose
        # an empty offer list and the completion client-data records instead.
        "loanPlayers": {"loans": []} if onboarding_complete() else _loan_player_choices(),
        # FIFA consumes these progress records from the mass bootstrap. Merely
        # implementing their standalone URLs leaves the client in the starter
        # squad "Advance" screen on every login.
        "loanPlayerClientData": _completed_loan_client_data(),
        "onboardingClientData": _completed_onboarding_client_data(),
        "credits": STATE.credits(),
    }


def club_info() -> dict[str, Any]:
    items = STATE.list_items("club")
    return {
        "clubInfo": {
            "clubId": int(CFG["club_id"]),
            "clubName": CFG["club_name"],
            "clubAbbr": CFG["club_abbr"],
            "established": 2014,
            "credits": STATE.credits(),
        },
        "clubId": int(CFG["club_id"]),
        "clubName": CFG["club_name"],
        "clubAbbr": CFG["club_abbr"],
        "clubCount": len(items),
        "itemData": items,
        "credits": STATE.credits(),
    }


def store_info() -> dict[str, Any]:
    pack = {
        "id": 1,
        "packId": 1,
        "packType": "LOCAL_GOLD",
        "packTypeId": 1,
        "name": "Local Gold Pack",
        "description": "12 local player items",
        "coins": int(CFG.get("pack_cost", 7500)),
        "points": 0,
        "numberItems": int(CFG.get("pack_size", 12)),
        "numItems": int(CFG.get("pack_size", 12)),
        "rare": 3,
        "isAvailableInStore": True,
        "isavailableinstore": True,
        "categoryId": 1,
        "packContentInfo": {
            "gold": int(CFG.get("pack_size", 12)),
            "rare": 3,
            "items": int(CFG.get("pack_size", 12)),
        },
    }
    return {
        "storeEnabled": True,
        "cardPackStoreEnabled": True,
        "packList": [pack],
        "packTypes": [pack],
        "credits": STATE.credits(),
    }


def purchased_info() -> dict[str, Any]:
    unopened = len(STATE.unopened_pack_ids())
    return {
        "unopenedPacks": unopened,
        "packList": [],
        "purchasedItems": [],
        "credits": STATE.credits(),
    }


def _pow_sid() -> str:
    return "pow." + hashlib.sha256(("POW-" + STATE.token()).encode("utf-8")).hexdigest()[:32]


def _ut_sid() -> str:
    return "ut." + hashlib.sha256(("UT-" + STATE.sid()).encode("utf-8")).hexdigest()[:32]


def _credits_payload() -> dict[str, Any]:
    credits = STATE.credits()
    unopened = len(STATE.unopened_pack_ids())
    return {
        "currencies": [
            {"name": "coins", "funds": credits, "finalFunds": credits},
            {"name": "points", "funds": 0, "finalFunds": 0},
        ],
        "unopenedPacks": {"preOrderPacks": unopened, "recoveredPacks": 0},
    }


def _active_club_items() -> list[dict[str, Any]]:
    """Small, schema-faithful active club-item set used by FIFA 21 bootstrap.

    FIFA's native /user and /squad/active payloads contain full item objects in
    `actives`/`club`, not IDs.  These objects are deliberately stable and local.
    """
    ts = int(STATE.get("established", now_s()))
    selected_stadium = _selected_stadium_id()
    selected_capacity = int(
        _STADIUM_BY_ID.get(selected_stadium, {}).get("capacity", 45000) or 45000
    )
    items = [
        {
            # Keep the My Stadium item's FUT card identity separate from the
            # physical venue.  FIFA's menu/card factory can resolve the native
            # Sanderson Park card (6200058 / asset 261); feeding it the
            # match-only Bernabeu object makes the Match Day view crash while
            # building its cached item entity.  Match handoffs replace this
            # object with _squad_battle_stadium_item() where venue 2 is needed.
            "family": "stadiums", "table": "fcc_stadium",
            "resourceId": MY_STADIUM_CARD_RESOURCE_ID,
            "carddbid": MY_STADIUM_CARD_RESOURCE_ID,
            "assetId": MY_STADIUM_CARD_ASSET_ID, "cardassetid": 36,
            "itemType": "stadium", "cardsubtypeid": 10, "itemState": "activeStadium",
            "owners": 1, "untradeable": True, "discardValue": 10, "lastSalePrice": 0,
            "stackCount": 1, "untradeableCount": 1, "pile": "club",
            "timestamp": ts, "rating": 64, "rareflag": 0, "value": 64,
            "weightrare": 0, "category": 4,
            "stadiumid": selected_stadium, "capacity": selected_capacity,
            "StadiumId": selected_stadium, "boost": 0, "id": 910000000003,
        },
        {
            "family": "kits", "table": "fcc_kitcards", "resourceId": 6300074,
            "carddbid": 6300074, "assetId": 6300074, "cardassetid": 35,
            "itemType": "kit", "cardsubtypeid": 9, "itemState": "activeHomeKit",
            "owners": 1, "untradeable": True, "discardValue": 10, "lastSalePrice": 0,
            "stackCount": 1, "untradeableCount": 1, "pile": "club", "islocked": 0,
            "timestamp": ts, "rating": 83, "rareflag": 1, "value": 83,
            "weightrare": 10, "category": 2, "teamid": 1,
            "kitType": 0, "teamkittypetechid": 0, "year": 0,
            "resourceGameYear": 2021,
            "id": 910000000001,
        },
        {
            # Temporary catalogue bypass: the old 6400057/asset 15 definition
            # resolves to FIFA 21's locked FC Catalogue shirt. Reuse the known
            # playable Arsenal item as a separately owned away-slot instance.
            "family": "kits", "table": "fcc_kitcards", "resourceId": 6301345,
            "carddbid": 6301345, "assetId": 6301345, "cardassetid": 35,
            "itemType": "kit", "cardsubtypeid": 9, "itemState": "activeAwayKit",
            "owners": 1, "untradeable": True, "discardValue": 10, "lastSalePrice": 0,
            "stackCount": 1, "untradeableCount": 1, "pile": "club", "islocked": 0,
            "timestamp": ts, "rating": 83, "rareflag": 1, "value": 83,
            "weightrare": 10, "category": 3, "teamid": 1,
            "kitType": 1, "teamkittypetechid": 1, "year": 0,
            "resourceGameYear": 2021,
            "id": 910000000002,
        },
        {
            "family": "badges", "table": "fcc_badgecards", "resourceId": 6000057,
            "carddbid": 6000057, "assetId": 1, "cardassetid": 39,
            "itemType": "custom", "cardsubtypeid": 11, "itemState": "activeBadge",
            "owners": 1, "untradeable": True, "discardValue": 10, "lastSalePrice": 0,
            "stackCount": 1, "untradeableCount": 1, "pile": "club",
            "timestamp": ts, "rating": 83, "rareflag": 1, "value": 83,
            "weightrare": 10, "category": 1, "teamid": 1, "id": 5004,
        },
        {
            "family": "balls", "table": "fcc_balls", "resourceId": 8120212,
            "carddbid": 8120212, "assetId": 191, "cardassetid": 37,
            "itemType": "ball", "cardsubtypeid": 30, "itemState": "activeBall",
            "owners": 1, "untradeable": True, "discardValue": 10, "lastSalePrice": 0,
            "stackCount": 1, "untradeableCount": 1, "pile": "club",
            "timestamp": ts, "rating": 74, "rareflag": 0, "value": 74,
            "weightrare": 0, "category": 1, "ballid": 191, "id": 5411,
        },
    ]
    # Replace virtual defaults with any persisted active customisation choices.
    try:
        owned_actives = [
            item for item in STATE.list_items("club")
            if str(item.get("itemState", "")) in (
                "activeHomeKit", "activeAwayKit", "activeBadge", "activeBall",
            )
        ]
    except NameError:
        owned_actives = []
    for owned in owned_actives:
        state = str(owned["itemState"])
        items = [item for item in items
                 if str(item.get("itemState", "")) != state]
        items.append(dict(owned))
    for item in items:
        item["itemId"] = int(item.get("id", 0) or 0)
        if item.get("itemState") == "activeStadium":
            stadium_value = int(item.get("stadiumid", item.get("assetId", 0)) or 0)
            item["stadiumid"] = stadium_value
            item["StadiumId"] = stadium_value
    return items


def grant_all_customisation_assets() -> dict[str, Any]:
    """Grant every locally verified FIFA 21 club-customisation definition.

    Kits are sourced from kits.json. Stadiums are venue selections rather than
    owned cards and are exposed by the stadium catalog/update route. The bundled
    badge and ball are the only definitions currently proven by local native
    data, so absent vanity tables are reported instead of synthetic IDs being
    inserted into a real club database.
    """
    templates = {str(item.get("itemState", "")): dict(item)
                 for item in _active_club_items()}
    existing = STATE.list_items()
    existing_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for item in existing:
        key = (str(item.get("itemType", "")), int(item.get("resourceId", 0) or 0))
        if key not in existing_by_key or str(item.get("pile", "")) == "club":
            existing_by_key[key] = item
    granted: list[dict[str, Any]] = []

    def store(item: dict[str, Any]) -> None:
        key = (str(item.get("itemType", "")), int(item.get("resourceId", 0) or 0))
        owned = existing_by_key.get(key)
        if owned is not None:
            if str(owned.get("pile", "")) != "club":
                STATE.update_item_pile(int(owned["id"]), "club")
                owned = dict(owned, pile="club")
                existing_by_key[key] = owned
                granted.append(owned)
            return
        item = dict(item)
        item["id"] = STATE.next_item_id()
        item["itemId"] = item["id"]
        item["pile"] = "club"
        with STATE.lock:
            STATE.conn.execute(
                "INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                (item["id"], int(item["resourceId"]), "club", json.dumps(item)),
            )
            STATE.conn.commit()
        existing_by_key[key] = item
        granted.append(item)

    for kit in KIT_DB:
        kit_type = int(kit.get("kitType", -1)
                       if kit.get("kitType") is not None else -1)
        resource_id = int(kit["resourceId"])
        store({
            "timestamp": now_s(), "family": "kits", "table": "fcc_kitcards",
            "resourceId": resource_id, "carddbid": resource_id,
            "assetId": resource_id, "cardassetid": 35,
            "itemType": "kit", "cardsubtypeid": 9, "itemState": "free",
            "owners": 1, "untradeable": True, "discardValue": 10,
            "lastSalePrice": 0, "stackCount": 1, "untradeableCount": 1,
            "islocked": 0, "rating": 83, "rareflag": 1, "value": 83,
            "weightrare": 10, "category": 2 + max(0, kit_type),
            "teamid": int(kit.get("teamid", 0) or 0), "kitType": kit_type,
            "teamkittypetechid": kit_type, "year": 0, "resourceGameYear": 2021,
            "name": str(kit.get("name", "FIFA 21 Kit")),
        })

    for state in ("activeBadge", "activeBall"):
        if state in templates:
            store(templates[state])

    available = {
        "kits": len(KIT_DB),
        "stadiums": len(STADIUM_DB),
        "badges": 1 if "activeBadge" in templates else 0,
        "balls": 1 if "activeBall" in templates else 0,
    }
    return {
        "success": True,
        "grantedCount": len(granted),
        "grantedByType": {
            kind: sum(1 for item in granted if str(item.get("itemType", "")) == item_type)
            for kind, item_type in (("kits", "kit"), ("badges", "custom"), ("balls", "ball"))
        },
        "available": available,
        "stadiumEndpoint": "/ut/game/fifa21/stadium",
        "unsupportedVanityCategories": [
            "vanity_audio", "vanity_tifo", "vanity_pyrotechnics",
            "vanity_paint", "vanity_celebration", "vanity_pitch", "vanity_theme",
        ],
    }


def _native_stadium_response() -> dict[str, Any]:
    """Return FIFA 21's native My Stadium document for Match Day slots.

    The client does not consume a flat ``slots`` array. Its My Stadium factory
    walks ``tiers[].slots`` and filters each slot by ``stadiumArea``; area 0 is
    Match Day. Keeping every local slot in tier 1 avoids exposing unfinished
    Crowds, Sidelines, and Structure sections.
    """
    # Field names below are taken verbatim from the FIFA 21 web-app client model
    # (UTMyStadiumFactory.createStadium / processStadiumTier / generateSlot in the
    # March-2021 compiled_2.js):
    #   createStadium      reads  name, lastUsedTierLevel, stadiumId, tiers[]
    #   processStadiumTier reads  tier, stadiumDefinitionId, slots[]
    #   generateSlot       reads  itemData, locked, slotId, clientSlotId, slotName,
    #                             stadiumArea, sortOrder, vanityTypes, emptiable
    actives = _active_club_items()
    active_stadium = next(
        (dict(item) for item in actives if item.get("itemState") == "activeStadium"),
        {},
    )
    stadium_id = int(active_stadium.get("stadiumid", active_stadium.get("assetId", 261)) or 261)
    slot_states = (
        "activeHomeKit", "activeAwayKit", "activeBadge", "activeBall", "activeStadium",
    )
    slot_names = {
        # These are FIFA 21's native localization identifiers from CardsDLL,
        # rather than web-app-only keys that render verbatim in the PC menu.
        "activeHomeKit": "Home_Kit",
        "activeAwayKit": "Away_Kit",
        "activeBadge": "Badge",
        "activeBall": "FUT_STADIUM_BALL",
        "activeStadium": "Stadium",
    }
    slots: list[dict[str, Any]] = []
    for slot_index, item_state in enumerate(slot_states, start=1):
        src = next((dict(entry) for entry in actives
                    if entry.get("itemState") == item_state), None)
        if src is None:
            # createCustomSlotItem: falsy itemData -> UTNullItemEntity. Send an
            # explicit null rather than an empty object the card factory would
            # try (and fail) to resolve.
            item_data: Any = None
        else:
            # createCustomSlotItem reads the reduced camel-case item contract.
            item_data = dict(src)
            item_data["cardSubTypeId"] = int(src.get("cardsubtypeid", 0) or 0)
            item_data["teamId"] = int(src.get("teamId", src.get("teamid", 0)) or 0)
            item_data["authenticity"] = int(src.get("authenticity", 0) or 0)
        slots.append({
            "slotId": slot_index,
            "clientSlotId": slot_index,
            "slotName": slot_names[item_state],
            "stadiumArea": 0,
            "sortOrder": slot_index,
            "vanityTypes": [],
            "emptiable": False,
            # Kits, badge, and ball have complete owned-item search/equip paths.
            # Stadium remains on the selector-pack/PUT path: opening its native
            # RS4 editor still requires unavailable structure/crowd metadata.
            "locked": item_state == "activeStadium",
            "itemData": item_data,
        })
    return {
        "name": str(CFG.get("club_name", "Local FC")),
        "lastUsedTierLevel": 1,
        "stadiumId": stadium_id,
        "tiers": [{
            "tier": 1,
            "stadiumDefinitionId": stadium_id,
            "slots": slots,
        }],
    }


def _onboarding_kit_choices() -> dict[str, list[dict[str, Any]]]:
    """Return native kit preview lists using definition IDs, not owned IDs."""
    home: list[dict[str, Any]] = []
    away: list[dict[str, Any]] = []
    for template in _active_club_items():
        state = str(template.get("itemState", ""))
        if template.get("itemType") != "kit" or state not in ("activeHomeKit", "activeAwayKit"):
            continue
        preview = dict(template)
        preview["id"] = int(preview["resourceId"])
        (home if state == "activeHomeKit" else away).append(preview)
    return {"homeItemDataList": home, "awayItemDataList": away}


def _select_onboarding_kits(payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"reason": "expected paired kit choices"}
    try:
        home_definition = int(payload.get("homeKitId", 0) or 0)
        away_definition = int(payload.get("awayKitId", 0) or 0)
    except Exception:
        return 400, {"reason": "invalid kit definition IDs"}
    choices = _onboarding_kit_choices()
    home = next((item for item in choices["homeItemDataList"]
                 if int(item["resourceId"]) == home_definition), None)
    away = next((item for item in choices["awayItemDataList"]
                 if int(item["resourceId"]) == away_definition), None)
    if home is None or away is None:
        return 404, {"reason": "unknown local kit choice"}
    # The active templates already carry stable local owned-instance IDs.
    active_by_state = {
        str(item.get("itemState", "")): item
        for item in _active_club_items()
        if item.get("itemType") == "kit"
    }
    result = {
        "homeKitId": int(active_by_state["activeHomeKit"]["id"]),
        "awayKitId": int(active_by_state["activeAwayKit"]["id"]),
    }
    STATE.set("onboarding_kit_choices", [home_definition, away_definition])
    STATE.set("onboarding_kit_item_ids", result)
    return 200, result


def _onboarding_badge_choices() -> dict[str, list[dict[str, Any]]]:
    badges = []
    for template in _active_club_items():
        if str(template.get("itemState", "")) != "activeBadge":
            continue
        preview = dict(template)
        preview["id"] = int(preview["resourceId"])
        badges.append(preview)
    return {"badgeItemDataList": badges}


def _display_sort_alias(item: dict[str, Any], rank: int) -> dict[str, Any]:
    """Return a response-only FIFA item with a sortable resourceId alias.

    FIFA 21 PC re-sorts the New Items carousel by resourceId ascending. The
    persistent/local item keeps its real resourceId; only the HTTP response gets
    an alias whose high byte encodes the desired reveal rank while the low 24
    bits keep the real base asset/player id. assetId remains the real base id.
    """
    out = dict(item)
    try:
        asset = int(out.get("assetId", 0) or 0) & 0x00FFFFFF
    except Exception:
        asset = 0
    # rank 0 -> 0x01000000 + asset, rank 1 -> 0x02000000 + asset, etc.
    # This preserves the base-player portion while forcing ascending client sort.
    out["resourceId"] = (((int(rank) + 1) & 0x7F) << 24) | asset
    return out


def _native_player_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize locally stored items to FIFA 21's native player-item shape."""
    out = {
        "assetId": int(item.get("assetId", item.get("resourceId", 0)) or 0),
        "attributeList": item.get("attributeList", []) if isinstance(item.get("attributeList", []), list) else [],
        "cardsubtypeid": int(item.get("cardsubtypeid", 0) or 0),
        "contract": int(item.get("contract", item.get("contracts", 7)) or 0),
        "discardValue": int(item.get("discardValue", 600) or 0),
        "fitness": int(item.get("fitness", 99) or 0),
        "injuryGames": int(item.get("injuryGames", 0) or 0),
        "injuryType": 0,
        "itemState": str(item.get("itemState", "free") or "free"),
        "itemType": "player",
        "lastSalePrice": int(item.get("lastSalePrice", 0) or 0),
        "leagueId": int(item.get("leagueId", 0) or 0),
        "nation": int(item.get("nation", 0) or 0),
        "loans": int(item.get("loans", 0) or 0),
        "morale": int(item.get("morale", 50) or 0),
        "owners": int(item.get("owners", 1) or 1),
        "preferredPosition": str(item.get("preferredPosition", "") or ""),
        "preferredFoot": str(item.get("preferredFoot", "") or ""),
        "weakFoot": int(item.get("weakFoot", 0) or 0),
        "skillMoves": int(item.get("skillMoves", 0) or 0),
        "workRateAttack": str(item.get("workRateAttack", "") or ""),
        "workRateDefense": str(item.get("workRateDefense", "") or ""),
        "rareflag": int(item.get("rareflag", CFG.get("rare_flag", 1)) or 0),
        "rating": int(item.get("rating", 0) or 0),
        "resourceId": int(item.get("resourceId", item.get("assetId", 0)) or 0),
        "suspension": int(item.get("suspension", 0) or 0),
        "teamid": int(item.get("teamid", 0) or 0),
        "timestamp": int(item.get("timestamp", STATE.get("established", now_s())) or 0),
        "training": int(item.get("training", 0) or 0),
        "untradeable": bool(item.get("untradeable", False)),
        "playStyle": int(item.get("playStyle", 250) or 0),
        "posMods": item.get("posMods", []) if isinstance(item.get("posMods", []), list) else [],
        "id": int(item.get("id", 0) or 0),
        "statsList": item.get("statsList", []) if isinstance(item.get("statsList", []), list) else [],
        "lifetimeStats": item.get("lifetimeStats", []) if isinstance(item.get("lifetimeStats", []), list) else [],
        "assists": int(item.get("assists", 0) or 0),
        "lifetimeAssists": int(item.get("lifetimeAssists", 0) or 0),
    }
    # Special cards (Icons, TOTY, TOTS, ...) have no entry in FIFA 21's bundled
    # roster for their assetId, so the client renders them with no name.  Supply
    # the name from the catalogue -- but ONLY for special cards: doing it for
    # every player broke consumable application and Draft squad swaps.  Skill
    # moves / weak foot are left untouched: overriding them made cards like
    # Pelé PIM show wrong star ratings, so the client's own value is kept.
    if _is_special_card(item):
        out.update(_player_name_fields(item))
    return out


def _player_catalogue_meta(item: dict[str, Any]) -> dict[str, Any]:
    """Best-effort resolve a stored player item back to its catalogue meta.

    Tries every id the item might carry (revision-migrated resourceId, the
    pre-migration dynamic-head id, assetId, carddbid), then the prebuilt
    assetId index.
    """
    for key in ("resourceId", "dynamicImageResourceId", "assetId", "carddbid"):
        try:
            v = int(item.get(key, 0) or 0)
        except (TypeError, ValueError):
            v = 0
        if v:
            meta = player_meta(v)
            if meta:
                return meta
    try:
        asset = int(item.get("assetId", 0) or 0)
    except (TypeError, ValueError):
        asset = 0
    return _PLAYER_DB_BY_ASSET.get(asset, {})


def _is_special_card(item: dict[str, Any]) -> bool:
    """A card whose name/skills FIFA 21 cannot resolve from its bundled
    roster -- anything that is not a plain gold/silver/bronze (rareflag 0/1)."""
    try:
        return int(item.get("rareflag", 0) or 0) not in (0, 1)
    except (TypeError, ValueError):
        return False


def _player_name_fields(item: dict[str, Any]) -> dict[str, Any]:
    meta = _player_catalogue_meta(item)

    def pick(*keys: str) -> str:
        for src in (item, meta):
            for key in keys:
                val = src.get(key)
                if val:
                    return str(val)
        return ""

    first = pick("firstName", "first_name")
    last = pick("lastName", "last_name", "surname")
    common = pick("commonName", "commonname", "knownAs", "commonName")
    if not common:
        common = pick("name")
    if not common:
        common = (first + " " + last).strip() or last or first
    if not last:
        last = common
    return {
        "firstName": first,
        "lastName": last,
        "commonName": common,
        "commonname": common,
        "knownAs": pick("knownAs") or common,
        "name": common,
        "playerName": common,
    }

def _native_consumable_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a stored consumable (including position modifiers).

    Every other native item normalizer in this file (_native_manager_item,
    _native_pack_item, _native_player_item) carries stackCount /
    untradeableCount / untradeable / owners / a name -- base fields present
    on every card the client already renders correctly. This one omitted
    all of them; a card list item missing fields every other card type
    always has is a structural gap, not a data-value one.
    """
    source = str(item.get("sourcePosition", "") or "")
    target = str(item.get("targetPosition", item.get("position", "")) or "")
    name = f"{source} to {target}" if source and target else "Position Modifier"
    return {
        "id": int(item.get("id", 0) or 0),
        "assetId": int(item.get("assetId", item.get("resourceId", 0)) or 0),
        "resourceId": int(item.get("resourceId", item.get("assetId", 0)) or 0),
        "carddbid": int(item.get("carddbid", item.get("resourceId", 0)) or 0),
        "cardassetid": int(item.get("cardassetid", 34) or 34),
        "itemType": str(item.get("itemType", "training") or "training"),
        "itemState": str(item.get("itemState", "free") or "free"),
        "cardsubtypeid": int(item.get("cardsubtypeid", 0) or 0),
        "consumableType": int(item.get("consumableType", 3) or 3),
        "consumableId": int(item.get("consumableId", 0) or 0),
        "position": str(item.get("position", "") or ""),
        "sourcePosition": source,
        "targetPosition": target,
        "positionModifier": str(item.get("positionModifier", "") or ""),
        "rareflag": int(item.get("rareflag", 1) or 1),
        "rating": int(item.get("rating", 95) or 95),
        "resourceGameYear": 2021,
        "discardValue": int(item.get("discardValue", 0) or 0),
        "pile": str(item.get("pile", "club") or "club"),
        "stackCount": max(1, int(item.get("stackCount", 1) or 1)),
        "untradeableCount": max(1, int(item.get("untradeableCount", 1) or 1)),
        "untradeable": bool(item.get("untradeable", True)),
        "owners": int(item.get("owners", 1) or 1),
        "name": str(item.get("name", "") or name),
        "amount": int(item.get("amount", 1) or 1),
    }


def _native_manager_item(item: dict[str, Any], in_squad: bool = False) -> dict[str, Any]:
    """Normalize a stored manager to the native FUT manager subtype."""
    meta = manager_meta(int(item.get("resourceId", item.get("assetId", 0)) or 0))
    return {
        "id": int(item.get("id", 0) or 0),
        "assetId": int(item.get("assetId", item.get("resourceId", 0)) or 0),
        "resourceId": int(item.get("resourceId", item.get("assetId", 0)) or 0),
        "carddbid": int(item.get("carddbid", item.get("resourceId", 0)) or 0),
        "stackCount": max(1, int(item.get("stackCount", 1) or 1)),
        "untradeableCount": max(0, int(item.get("untradeableCount", 0) or 0)),
        "resourceGameYear": 2021,
        "itemType": "manager",
        # Squad assignment is represented by the squad's manager reference,
        # not by a synthetic item state/pile. FIFA 21 treats the owned manager
        # as a normal free club item even while it occupies the MGR slot.
        "itemState": str(item.get("itemState", "free") or "free"),
        "cardsubtypeid": 4,
        "category": int(item.get("category", 0) or 0),
        "contract": int(item.get("contract", item.get("contracts", 7)) or 0),
        "negotiation": int(item.get("negotiation", 0) or 0),
        "discardValue": int(item.get("discardValue", 150) or 0),
        "lastSalePrice": int(item.get("lastSalePrice", 0) or 0),
        "leagueId": int(item.get("leagueId", 0) or 0),
        "nation": int(item.get("nation", 0) or 0),
        "teamid": int(item.get("teamid", 0) or 0),
        "owners": int(item.get("owners", 1) or 1),
        "rareflag": int(item.get("rareflag", 0) or 0),
        "rating": int(item.get("rating", 0) or 0),
        "timestamp": int(item.get("timestamp", STATE.get("established", now_s())) or 0),
        "untradeable": bool(item.get("untradeable", False)),
        "pile": str(item.get("pile", "club") or "club"),
        "firstName": str(item.get("firstName", meta.get("firstName", "")) or ""),
        "lastName": str(item.get("lastName", meta.get("lastName", "")) or ""),
        "commonname": str(item.get("commonname", meta.get("commonname", "")) or ""),
    }


def _native_default_manager() -> dict[str, Any]:
    """Return the club's first real owned FIFA 21 manager."""
    managers = [item for item in STATE.list_items()
                if str(item.get("itemType", "")) == "manager"]
    if managers:
        managers.sort(key=lambda item: (-int(item.get("rating", 0) or 0), int(item.get("id", 0) or 0)))
        return _native_manager_item(managers[0], in_squad=True)
    if not MANAGER_DB:
        return {}
    resource_id = min(MANAGER_DB, key=lambda rid: (-int(MANAGER_DB[rid].get("rating", 0) or 0), int(rid)))
    preview = STATE.build_manager_item(resource_id, 0, "none")
    return _native_manager_item(preview, in_squad=True)


LOAN_PLAYER_POSITIONS = ("ST", "LM", "CM", "RM", "LB", "CB", "RB", "GK")
LOAN_PLAYERS_PER_POSITION = 5
LOAN_GAMES = 7
LOAN_OFFER_ID_BASE = 10001


def _loan_player_resource_ids() -> list[int]:
    """Return a stable, native five-player choice set for each onboarding slot."""
    choices: list[int] = []
    for position in LOAN_PLAYER_POSITIONS:
        candidates = sorted(
            (
                (int(meta.get("rating", 0) or 0), int(resource_id))
                for resource_id, meta in PLAYER_DB.items()
                if str(meta.get("preferredPosition", "")) == position
            ),
            key=lambda candidate: (-candidate[0], candidate[1]),
        )
        choices.extend(resource_id for _rating, resource_id in candidates[:LOAN_PLAYERS_PER_POSITION])
    return choices


def _loan_player_choices() -> dict[str, list[dict[str, Any]]]:
    # The native picker requires an offer definition ID and an eligible ranking
    # in addition to itemData. These IDs are stable local-emulator definitions,
    # deliberately separate from resource IDs and owned item-instance IDs.
    loans = []
    for index, resource_id in enumerate(_loan_player_resource_ids()):
        preview = STATE.build_player_item(
            resource_id,
            resource_id,
            "none",
            LOAN_GAMES,
        )
        loans.append({
            "defId": LOAN_OFFER_ID_BASE + index,
            "ranking": index % LOAN_PLAYERS_PER_POSITION + 1,
            "loans": LOAN_GAMES,
            "itemData": _native_player_item(preview),
        })
    return {"loans": loans}


def _native_squad() -> dict[str, Any]:
    squads = STATE.list_squads()
    src = dict(squads[0]) if squads else {"id": 1, "squadName": "Local XI", "formation": "f442", "players": []}
    item_by_id = {int(x.get("id", 0)): x for x in STATE.list_items()}

    positions: dict[int, dict[str, Any]] = {}
    for pos in src.get("players", []) if isinstance(src.get("players"), list) else []:
        if not isinstance(pos, dict):
            continue
        iid = pos.get("itemId")
        if iid is None and isinstance(pos.get("itemData"), dict):
            iid = pos["itemData"].get("id")
        try:
            iid_int = int(iid)
            idx = int(pos.get("index", len(positions)))
        except Exception:
            continue
        item = item_by_id.get(iid_int)
        if item:
            positions[idx] = {
                "index": idx,
                "kitNumber": int(pos.get("kitNumber", idx + 1) or 0),
                "itemData": _native_player_item(item),
            }

    # The native response contains all 23 squad slots, including empty ones.
    players: list[dict[str, Any]] = []
    for idx in range(23):
        players.append(positions.get(idx, {"index": idx, "kitNumber": 0}))

    populated = [x for x in players if isinstance(x.get("itemData"), dict)]
    sid = int(src.get("id") or src.get("squadId") or 1)
    name = str(src.get("squadName") or src.get("name") or "Local XI")
    captain = int(src.get("captain") or (populated[0]["itemData"]["id"] if populated else 0))
    kick_ids = [int(x["itemData"]["id"]) for x in populated[:5]]
    while kick_ids and len(kick_ids) < 5:
        kick_ids.append(kick_ids[-1])
    kicktakers = [{"index": i, "id": iid, "dream": False} for i, iid in enumerate(kick_ids)]

    actives = _active_club_items()
    saved_managers = src.get("manager", []) if isinstance(src.get("manager"), list) else []
    native_managers = []
    for saved_manager in saved_managers:
        if not isinstance(saved_manager, dict):
            continue
        manager_item = saved_manager.get("itemData") if isinstance(saved_manager.get("itemData"), dict) else saved_manager
        if str(manager_item.get("itemType", "")) != "manager":
            continue
        native_managers.append(_native_manager_item(manager_item, in_squad=True))
    if not native_managers:
        default_manager = _native_default_manager()
        if default_manager:
            native_managers = [default_manager]
    return {
        "id": sid,
        "personaId": int(CFG["persona_id"]),
        "squadName": name,
        "formation": str(src.get("formation") or "f442"),
        "captain": captain,
        "chemistry": int(src.get("chemistry", 100) if src.get("chemistry") is not None else 100),
        "changed": 0,
        "starRating": int(src.get("starRating", 5) or 5),
        "custom": str(src.get("custom") or "[0,0,0,0,0,0,0,0,0,0,0]"),
        "players": players,
        "actives": actives,
        # FIFA's pre-match validator requires native manager subtype 4. Older
        # saves may contain the rejected response-only "staff" placeholder;
        # migrate those responses to an actually owned manager definition.
        "manager": native_managers,
        "club": actives,
        "kicktakers": kicktakers,
    }


# ---- FUT Draft ------------------------------------------------------------

_DRAFT_STATES = {
    "INVALID", "PICK_DIFFICULTY", "FORMATION_DRAFT", "CAPTAIN_DRAFT",
    "PLAYER_DRAFT", "MANAGER_DRAFT", "READY_FOR_MATCH", "READY_FOR_REWARDS",
}
_DRAFT_DIFFICULTIES = (
    "BEGINNER", "AMATEUR", "SEMIPRO", "PRO", "WORLDCLASS", "LEGENDARY", "ULTIMATE",
)
DRAFT_ELITE_99_CHANCE = 0.45
_DRAFT_FORMATIONS = (
    "f3412", "f3421", "f343", "f352", "f41212", "f4141", "f4222", "f4231",
    "f4312", "f4321", "f433", "f4411", "f442", "f451", "f5212", "f5221",
    "f532", "f541",
)
_DRAFT_FORMATION_POSITIONS = {
    "f3412": ("GK", "CB", "CB", "CB", "RM", "CM", "CM", "LM", "CAM", "ST", "ST"),
    "f3421": ("GK", "CB", "CB", "CB", "RM", "CM", "CM", "LM", "RF", "ST", "LF"),
    "f343": ("GK", "CB", "CB", "CB", "RM", "CM", "CM", "LM", "RW", "ST", "LW"),
    "f352": ("GK", "CB", "CB", "CB", "RM", "CDM", "CDM", "LM", "CAM", "ST", "ST"),
    "f41212": ("GK", "RB", "CB", "CB", "LB", "CDM", "CM", "CM", "CAM", "ST", "ST"),
    "f4141": ("GK", "RB", "CB", "CB", "LB", "CDM", "RM", "CM", "CM", "LM", "ST"),
    "f4222": ("GK", "RB", "CB", "CB", "LB", "CDM", "CDM", "CAM", "CAM", "ST", "ST"),
    "f4231": ("GK", "RB", "CB", "CB", "LB", "CDM", "CDM", "CAM", "CAM", "CAM", "ST"),
    "f4312": ("GK", "RB", "CB", "CB", "LB", "CM", "CM", "CM", "CAM", "ST", "ST"),
    "f4321": ("GK", "RB", "CB", "CB", "LB", "CM", "CM", "CM", "RF", "LF", "ST"),
    "f433": ("GK", "RB", "CB", "CB", "LB", "CM", "CM", "CM", "RW", "ST", "LW"),
    "f4411": ("GK", "RB", "CB", "CB", "LB", "RM", "CM", "CM", "LM", "CF", "ST"),
    "f442": ("GK", "RB", "CB", "CB", "LB", "RM", "CM", "CM", "LM", "ST", "ST"),
    "f451": ("GK", "RB", "CB", "CB", "LB", "RM", "CM", "LM", "CM", "CAM", "ST"),
    "f5212": ("GK", "RWB", "CB", "CB", "CB", "LWB", "CM", "CM", "CAM", "ST", "ST"),
    "f5221": ("GK", "RWB", "CB", "CB", "CB", "LWB", "CM", "CM", "RW", "ST", "LW"),
    "f532": ("GK", "RWB", "CB", "CB", "CB", "LWB", "CM", "CM", "CM", "ST", "ST"),
    "f541": ("GK", "RWB", "CB", "CB", "CB", "LWB", "RM", "CM", "CM", "LM", "ST"),
}
_draft_lock = threading.RLock()
_DRAFT_99_POSITIONS = frozenset(
    str(meta.get("preferredPosition", "") or "").upper()
    for meta in PLAYER_DB.values()
    if int(meta.get("rating", 0) or 0) == 99
)


def _draft_valid_run(run: Any) -> bool:
    return isinstance(run, dict) and str(run.get("state", "")) in _DRAFT_STATES


def _draft_new_run(mode: str = "SINGLE_PLAYER") -> dict[str, Any]:
    return {
        "mode": "ONLINE" if str(mode).upper() == "ONLINE" else "SINGLE_PLAYER",
        "state": "PICK_DIFFICULTY" if str(mode).upper() != "ONLINE" else "FORMATION_DRAFT",
        "difficulty": -1 if str(mode).upper() != "ONLINE" else 2,
        "formation": "",
        "formationChoices": [],
        "picked": {},
        "manager": None,
        "currentChoices": [],
        "currentChoiceKind": "",
        "currentPositionId": 0,
        "seenAssets": [],
        "choiceSerial": 0,
        "seed": random.SystemRandom().randrange(1, 2_000_000_000),
        "nextItemId": 910_000_000_000,
        "captain": 0,
        "wins": 0,
        "losses": 0,
        "draftsCompleted": 0,
        "pendingCoinReward": 0,
    }


def _draft_elite_99_pick_ordinals(run: dict[str, Any]) -> tuple[int, ...]:
    """Return the four/five pick rounds boosted to contain a 99-rated card.

    The profile is deterministic for a run's seed, so reopening a choice does
    not reroll the 45% chance. Non-elite drafts still use the stronger general
    weighting below and can naturally contain 99-rated choices.
    """
    seed = int(run.get("seed", 1) or 1)
    rng = random.Random(f"project21-draft-elite99:{seed}")
    if rng.random() >= DRAFT_ELITE_99_CHANCE:
        return ()
    count = rng.choice((4, 5))
    # Pick only rounds whose actual pitch/bench slot accepts at least one 99.
    # Ordinal 0 is the positionless captain choice; ordinals 1..10 correspond
    # to starter positions 0..9 because position 10 is filled by the captain.
    eligible_ordinals = []
    for ordinal in range(23):
        if ordinal == 0:
            eligible_ordinals.append(ordinal)
            continue
        position_id = ordinal - 1 if ordinal <= 10 else ordinal
        allowed = _draft_allowed_positions(run, position_id)
        if allowed is None or _DRAFT_99_POSITIONS.intersection(allowed):
            eligible_ordinals.append(ordinal)
    return tuple(sorted(rng.sample(eligible_ordinals, count)))


def _draft_bad_pick_ordinals(run: dict[str, Any]) -> tuple[int, ...]:
    """Return the two/three intentionally weaker rounds for this draft."""
    seed = int(run.get("seed", 1) or 1)
    rng = random.Random(f"project21-draft-bad-picks:{seed}")
    count = rng.choice((2, 3))
    elite = set(_draft_elite_99_pick_ordinals(run))
    available = [ordinal for ordinal in range(23) if ordinal not in elite]
    return tuple(sorted(rng.sample(available, count)))


def _draft_run() -> dict[str, Any] | None:
    run = STATE.draft_run()
    if not _draft_valid_run(run):
        return None
    changed = False
    picked = run.get("picked", {})
    if isinstance(picked, dict):
        for item in picked.values():
            if not isinstance(item, dict):
                continue
            old_resource_id = int(item.get("resourceId", 0) or 0)
            new_resource_id = PLAYER_RESOURCE_ID_MIGRATIONS.get(old_resource_id)
            if new_resource_id is None:
                continue
            item["resourceId"] = new_resource_id
            if int(item.get("carddbid", old_resource_id) or 0) == old_resource_id:
                item["carddbid"] = new_resource_id
            changed = True
    choices = run.get("currentChoices", [])
    if isinstance(choices, list):
        for choice in choices:
            item = choice.get("itemData") if isinstance(choice, dict) else None
            if not isinstance(item, dict):
                continue
            old_resource_id = int(item.get("resourceId", 0) or 0)
            new_resource_id = PLAYER_RESOURCE_ID_MIGRATIONS.get(old_resource_id)
            if new_resource_id is None:
                continue
            item["resourceId"] = new_resource_id
            if int(item.get("carddbid", old_resource_id) or 0) == old_resource_id:
                item["carddbid"] = new_resource_id
            changed = True
    if changed:
        STATE.save_draft_run(run)
    return run


def _draft_save(run: dict[str, Any]) -> None:
    STATE.save_draft_run(run)


def _draft_squad(run: dict[str, Any]) -> dict[str, Any]:
    picked = run.get("picked", {}) if isinstance(run.get("picked"), dict) else {}
    players = []
    for index in range(23):
        item = picked.get(str(index))
        if isinstance(item, dict):
            players.append({"index": index, "kitNumber": index + 1, "itemData": item})
        else:
            players.append({"index": index, "kitNumber": 0})
    manager = run.get("manager")
    manager_list = [manager] if isinstance(manager, dict) else []
    populated = [slot["itemData"] for slot in players if isinstance(slot.get("itemData"), dict)]
    captain = int(run.get("captain", 0) or (populated[0].get("id", 0) if populated else 0))
    # Draft must not inherit the user's selected/custom stadium.  FIFA reads
    # the venue from both the squad-level IDs and the active club-item list at
    # different stages of the flow, so publish Bernabeu consistently in all
    # three places just as the proven Squad Battles handoff does.
    actives = _squad_battle_match_actives(opponent=False)
    return {
        "id": 900001,
        "personaId": int(CFG["persona_id"]),
        "squadName": "Draft Squad",
        "squadType": "DRAFT_SQUAD",
        "active": False,
        "changed": 0,
        "formation": str(run.get("formation") or "f442"),
        "captain": captain,
        "chemistry": 0,
        "starRating": 0,
        "players": players,
        "manager": manager_list,
        "actives": actives,
        "club": [dict(item) for item in actives],
        "stadiumAssetId": SQUAD_BATTLE_STADIUM_ID,
        "stadiumId": SQUAD_BATTLE_STADIUM_ID,
    }


def _draft_native_match_squad(run: dict[str, Any]) -> dict[str, Any]:
    """Project the Draft editor squad into FIFA's playable match contract.

    The Draft editor tolerates zero rating/chemistry and temporary item states.
    The native match loader does not: it expects a valid active squad, five-star
    strength, kick takers, and playable item instances. Keep this normalization
    transient so the user's normal active squad is never replaced by the Draft.
    """
    squad = _draft_squad(run)
    formation = str(squad.get("formation") or "f442")
    players: list[dict[str, Any]] = []
    for index, raw_slot in enumerate(squad.get("players", [])[:23]):
        slot = dict(raw_slot) if isinstance(raw_slot, dict) else {"index": index}
        slot["index"] = int(slot.get("index", index) or index)
        slot["kitNumber"] = int(slot.get("kitNumber", index + 1) or 0)
        raw_item = slot.get("itemData")
        if isinstance(raw_item, dict):
            item = dict(raw_item)
            item["itemState"] = "free"
            item["contract"] = max(1, int(item.get("contract", 7) or 7))
            item["fitness"] = max(1, int(item.get("fitness", 99) or 99))
            item["formation"] = formation
            item["concept"] = False
            item["untradeable"] = True
            item.pop("draftItem", None)
            slot["itemData"] = item
        players.append(slot)
    while len(players) < 23:
        players.append({"index": len(players), "kitNumber": 0})

    starters = [
        slot["itemData"] for slot in players[:11]
        if isinstance(slot.get("itemData"), dict)
    ]
    captain = int(squad.get("captain", 0) or 0)
    starter_ids = [int(item.get("id", 0) or 0) for item in starters]
    if captain not in starter_ids and starter_ids:
        captain = starter_ids[0]
    kick_ids = [item_id for item_id in starter_ids if item_id > 0][:5]
    while kick_ids and len(kick_ids) < 5:
        kick_ids.append(kick_ids[-1])

    manager = []
    for raw_manager in squad.get("manager", []):
        if not isinstance(raw_manager, dict):
            continue
        item = dict(raw_manager.get("itemData", raw_manager))
        item["itemState"] = "free"
        item["concept"] = False
        item["untradeable"] = True
        manager.append(item)

    ratings = [int(item.get("rating", 0) or 0) for item in starters]
    rating = round(sum(ratings) / len(ratings)) if ratings else 75
    actives = _squad_battle_match_actives(opponent=False)
    squad.update({
        "squadId": 900001,
        "name": "Draft Squad",
        "active": True,
        "valid": True,
        "newSquad": 0,
        "newsquad": 0,
        "formation": formation,
        "captain": captain,
        "chemistry": 100,
        "starRating": 5,
        "rating": rating,
        "custom": "[0,0,0,0,0,0,0,0,0,0,0]",
        "players": players,
        "manager": manager,
        "kicktakers": [
            {"index": index, "id": item_id, "dream": False}
            for index, item_id in enumerate(kick_ids)
        ],
        "actives": actives,
        "club": [dict(item) for item in actives],
        "stadiumAssetId": SQUAD_BATTLE_STADIUM_ID,
        "stadiumId": SQUAD_BATTLE_STADIUM_ID,
    })
    return squad


def _draft_state_payload(run: dict[str, Any] | None) -> list[dict[str, Any]]:
    if run is None:
        return [{
            "entranceCriteria": {"COINS": 15000, "DRAFT_TOKEN": 1, "POINTS": 300},
            "gamesWonCurrentMatch": 0,
            "roundsInfo": [],
            "squadState": "INVALID",
            "stateParam1": "INVALID",
            "stateParam2": "0",
        }]
    state = str(run.get("state", "INVALID"))
    position_id = int(run.get("currentPositionId", 0) or 0)
    payload = {
        "entranceCriteria": {"COINS": 15000, "DRAFT_TOKEN": 1, "POINTS": 300},
        "gamesWonCurrentMatch": int(run.get("wins", 0) or 0),
        "roundsInfo": [],
        "squadState": state,
        "stateParam1": "PLAYER" if state == "PLAYER_DRAFT" else "INVALID",
        "stateParam2": "23" if state in ("MANAGER_DRAFT", "READY_FOR_MATCH", "READY_FOR_REWARDS") else str(position_id),
        "squad": _draft_squad(run),
    }
    return [payload]


def _draft_allowed_positions(run: dict[str, Any], position_id: int) -> set[str] | None:
    if position_id == 11:
        return {"GK"}
    if position_id in (12, 13):
        return {"RWB", "RB", "CB", "LB", "LWB"}
    if position_id in (14, 15):
        return {"CDM", "RM", "CM", "LM", "CAM"}
    if position_id == 16:
        return {"CDM", "RM", "CM", "LM", "CAM", "RF", "CF", "LF", "RW", "ST", "LW"}
    if position_id == 17:
        return {"RF", "CF", "LF", "RW", "ST", "LW"}
    if 18 <= position_id <= 22:
        return None
    positions = _DRAFT_FORMATION_POSITIONS.get(str(run.get("formation") or "f442"), ())
    if position_id < 0 or position_id >= len(positions):
        return None
    target = positions[position_id]
    if target == "RF":
        return {"RF", "RW"}
    if target == "LF":
        return {"LF", "LW"}
    if target == "RWB":
        return {"RWB", "RB"}
    if target == "LWB":
        return {"LWB", "LB"}
    return {target}


def _draft_player_choices(run: dict[str, Any], position_id: int, captain: bool = False) -> list[dict[str, Any]]:
    seen_assets = {int(value) for value in run.get("seenAssets", [])}
    allowed = None if captain else _draft_allowed_positions(run, position_id)
    candidates = []
    for resource_id, meta in PLAYER_DB.items():
        rating = int(meta.get("rating", 0) or 0)
        asset_id = int(meta.get("assetId", resource_id) or resource_id)
        position = str(meta.get("preferredPosition", "") or "").upper()
        if rating < 75 or asset_id in seen_assets or not position:
            continue
        if allowed is not None and position not in allowed:
            continue
        candidates.append((int(resource_id), rating, asset_id))
    rng = random.Random(int(run.get("seed", 1)) + int(run.get("choiceSerial", 0)) * 1009 + position_id * 31)
    chosen = []
    used_assets = set()
    pick_ordinal = min(22, len(run.get("picked", {})))
    elite_99_pick = pick_ordinal in _draft_elite_99_pick_ordinals(run)
    bad_pick = pick_ordinal in _draft_bad_pick_ordinals(run)

    # On an elite pick round, guarantee a 99 only when one naturally fits this
    # slot. Never fill an incompatible position merely to satisfy the boost.
    if elite_99_pick:
        elite_candidates = [
            row for row in candidates
            if row[1] == 99 and (allowed is None or
                                 str(PLAYER_DB[row[0]].get("preferredPosition", "")).upper() in allowed)
        ]
        if elite_candidates:
            resource_id, _rating, asset_id = rng.choice(elite_candidates)
            item_id = int(run.get("nextItemId", 910_000_000_000))
            run["nextItemId"] = item_id + 1
            chosen.append(_native_player_item(
                STATE.build_player_item(resource_id, item_id, "none")
            ))
            used_assets.add(asset_id)
            seen_assets.add(asset_id)
            candidates = [row for row in candidates if row[2] != asset_id]

    # Outside the run's two/three weaker rounds, keep the whole choice screen
    # exciting. The floor is applied only when five natural-position options
    # exist, so it can never cause an empty or out-of-position selection.
    if not bad_pick:
        rating_floor = 90 if captain else 87
        strong_candidates = [row for row in candidates if row[1] >= rating_floor]
        if len(strong_candidates) >= (5 - len(chosen)):
            candidates = strong_candidates

    while candidates and len(chosen) < 5:
        weights = []
        for resource_id, rating, _asset_id in candidates:
            rarity = int(PLAYER_DB[resource_id].get("rareflag", 0) or 0)
            exponent = 1.35 if bad_pick else (4.0 if captain else 3.25)
            high_rating_bias = max(1.0, (rating - 69) ** exponent)
            special_bias = (1.35 if bad_pick else 4.5) if rarity > 1 else 1.0
            weights.append(high_rating_bias * special_bias)
        resource_id, _rating, asset_id = rng.choices(candidates, weights=weights, k=1)[0]
        candidates = [row for row in candidates if row[2] != asset_id]
        if asset_id in used_assets:
            continue
        item_id = int(run.get("nextItemId", 910_000_000_000))
        run["nextItemId"] = item_id + 1
        item = _native_player_item(STATE.build_player_item(resource_id, item_id, "none"))
        chosen.append(item)
        used_assets.add(asset_id)
        seen_assets.add(asset_id)
    run["seenAssets"] = sorted(seen_assets)
    run["choiceSerial"] = int(run.get("choiceSerial", 0)) + 1
    log.info(
        "DRAFT WEIGHT pick=%s eliteProfile=%s guaranteed99=%s badPick=%s ratings=%s rarities=%s",
        pick_ordinal, bool(_draft_elite_99_pick_ordinals(run)), elite_99_pick, bad_pick,
        [int(item.get("rating", 0) or 0) for item in chosen],
        [int(item.get("rareflag", 0) or 0) for item in chosen],
    )
    return chosen


def _draft_manager_choices(run: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sorted(MANAGER_DB, key=lambda rid: (-int(MANAGER_DB[rid].get("rating", 0) or 0), int(rid)))
    rng = random.Random(int(run.get("seed", 1)) + 97_531)
    pool = rows[:min(100, len(rows))]
    rng.shuffle(pool)
    result = []
    for resource_id in pool[:5]:
        item_id = int(run.get("nextItemId", 910_000_000_000))
        run["nextItemId"] = item_id + 1
        result.append(_native_manager_item(STATE.build_manager_item(resource_id, item_id, "none")))
    return result


def _draft_choices(run: dict[str, Any], kind: str, position_id: int = 0) -> dict[str, Any]:
    kind = kind.lower()
    if kind == "difficulty":
        choices = [{"choiceIndex": index, "difficulty": index, "difficultyName": name}
                   for index, name in enumerate(_DRAFT_DIFFICULTIES)]
        return {"choices": choices}
    if kind == "formation":
        if len(run.get("formationChoices", [])) != 5:
            rng = random.Random(int(run.get("seed", 1)) + 41)
            run["formationChoices"] = rng.sample(list(_DRAFT_FORMATIONS), 5)
        _draft_save(run)
        return {"choices": [{"formation": formation, "index": index}
                            for index, formation in enumerate(run["formationChoices"])],
                "positionid": 0, "tier": 0}
    if kind == "captain":
        items = _draft_player_choices(run, 0, captain=True)
    elif kind == "player":
        items = _draft_player_choices(run, position_id, captain=False)
    elif kind == "manager":
        items = _draft_manager_choices(run)
    else:
        items = []
    run["currentChoices"] = items
    run["currentChoiceKind"] = kind
    run["currentPositionId"] = position_id
    _draft_save(run)
    return {"choices": [{"index": index, "itemData": item}
                        for index, item in enumerate(items)],
            "positionid": position_id, "tier": 0}


def _draft_choose(run: dict[str, Any], choice_index: int, position_id: int) -> None:
    state = str(run.get("state", ""))
    if state == "FORMATION_DRAFT":
        formations = run.get("formationChoices", [])
        run["formation"] = formations[choice_index] if 0 <= choice_index < len(formations) else (formations[0] if formations else "f442")
        run["state"] = "CAPTAIN_DRAFT"
    elif state in ("CAPTAIN_DRAFT", "PLAYER_DRAFT"):
        choices = run.get("currentChoices", [])
        if choices:
            selected = choices[choice_index] if 0 <= choice_index < len(choices) else choices[0]
            slot = max(0, min(22, int(position_id)))
            run.setdefault("picked", {})[str(slot)] = selected
            if state == "CAPTAIN_DRAFT":
                run["captain"] = int(selected.get("id", 0) or 0)
                run["state"] = "PLAYER_DRAFT"
            elif len(run["picked"]) >= 23:
                run["state"] = "MANAGER_DRAFT"
    elif state == "MANAGER_DRAFT":
        choices = run.get("currentChoices", [])
        if choices:
            run["manager"] = choices[choice_index] if 0 <= choice_index < len(choices) else choices[0]
        run["state"] = "READY_FOR_MATCH"
    run["currentChoices"] = []
    run["currentChoiceKind"] = ""
    run["currentPositionId"] = int(position_id)
    _draft_save(run)


def _native_draft_match_response(run: dict[str, Any]) -> dict[str, Any]:
    squad = _draft_native_match_squad(run)
    # FIFA 21 exposes both aliases in its native match vocabulary. Different
    # stages of the offline-Draft handoff consume different projections, so
    # mark the local Draft XI as away on the squad and the envelope.
    squad["home"] = False
    squad["isHomeTeam"] = False
    round_index = min(3, max(0, int(run.get("wins", 0) or 0)))
    opponent = _native_squad_battle_opponent(round_index)
    opponent["id"] = 1
    opponent["squadId"] = 1
    opponent["squadType"] = "DRAFT_SQUAD"
    opponent["active"] = True
    opponent["valid"] = True
    return {
        "squad": squad,
        "opponentTeamId": 1,
        # Solo Draft can assign either side as the venue owner. Keep the CPU
        # squad in the match handoff so an away fixture resolves the same
        # forced Bernabeu item already used by the local Draft squad.
        "opponentSquad": opponent,
        "awaySquad": opponent,
        "opponent": opponent,
        "startDateTime": now_s(),
        "stadiumAssetId": SQUAD_BATTLE_STADIUM_ID,
        "stadiumId": SQUAD_BATTLE_STADIUM_ID,
        "home": False,
        "isHomeTeam": False,
    }


def _squad_battle_stadium_item(
    template: dict[str, Any], *, item_id: int | None = None
) -> dict[str, Any]:
    """Return a match-only Santiago Bernabeu stadium item."""
    stadium = dict(template)
    stadium.update({
        "resourceId": SQUAD_BATTLE_STADIUM_RESOURCE_ID,
        "carddbid": SQUAD_BATTLE_STADIUM_RESOURCE_ID,
        # FUT club-item asset 1 points at the Bernabeu card definition, while
        # stadiumid/StadiumId 2 is the physical FIFA venue. Conflating those
        # identifiers produces a valid-looking item whose scene is empty.
        "assetId": SQUAD_BATTLE_STADIUM_CARD_ASSET_ID,
        "stadiumid": SQUAD_BATTLE_STADIUM_ID,
        "StadiumId": SQUAD_BATTLE_STADIUM_ID,
        "name": SQUAD_BATTLE_STADIUM_NAME,
        "itemType": "stadium",
        "itemState": "activeStadium",
    })
    if item_id is not None:
        stadium["id"] = int(item_id)
    return stadium


def _squad_battle_match_actives(*, opponent: bool) -> list[dict[str, Any]]:
    """Build consistent local/opponent cosmetic items for a match handoff."""
    actives = [
        dict(item) for item in _active_club_items()
        if item.get("itemState") in ("activeHomeKit", "activeAwayKit", "activeStadium")
    ]
    stadium = next(
        (item for item in actives if item.get("itemState") == "activeStadium"),
        {},
    )
    actives = [item for item in actives if item.get("itemState") != "activeStadium"]
    actives.append(_squad_battle_stadium_item(
        stadium,
        item_id=930000000099 if opponent else None,
    ))

    if opponent:
        for item_state, (resource_id, team_id, kit_type, name) in SQUAD_BATTLE_OPPONENT_KITS.items():
            template = next(
                (dict(item) for item in actives
                 if item.get("itemState") == item_state and item.get("itemType") == "kit"),
                {},
            )
            template.update({
                "id": 930000000000 + kit_type,
                "resourceId": resource_id,
                "carddbid": resource_id,
                "assetId": resource_id,
                "teamid": team_id,
                "kitType": kit_type,
                "teamkittypetechid": kit_type,
                "category": 2 + kit_type,
                "name": name,
                "itemType": "kit",
                "itemState": item_state,
            })
            actives = [item for item in actives if item.get("itemState") != item_state]
            actives.append(template)
    return actives


def _native_match_response(opponent_slot: int | None = None) -> dict[str, Any]:
    """Create the pre-match handoff expected by the native FUT controller.

    FIFA 17's local server did not unlock catalogue kits with an item flag. It
    returned the active squad from the match-creation endpoint and attached
    the active club items to both ``actives`` and ``club``. FIFA 21 consumes
    the same ownership context immediately before its kit-selection screen.
    """
    squad = _native_squad()
    actives = _squad_battle_match_actives(opponent=False)
    squad["actives"] = actives
    squad["club"] = [dict(item) for item in actives]
    squad["stadiumAssetId"] = SQUAD_BATTLE_STADIUM_ID
    squad["stadiumId"] = SQUAD_BATTLE_STADIUM_ID
    response = {
        "squad": squad,
        "homeSquad": squad,
        "isHomeTeam": True,
        "startDateTime": now_s(),
        "stadiumAssetId": SQUAD_BATTLE_STADIUM_ID,
        "stadiumId": SQUAD_BATTLE_STADIUM_ID,
    }
    if opponent_slot is not None:
        opponent = _native_squad_battle_opponent(opponent_slot)
        # Home fixtures consume squad.actives, while away fixtures resolve the
        # venue from the selected opponent cached during the same handoff.
        # Publish the complete forced-stadium squad under the native aliases
        # used by different FIFA 21 PC controller builds.
        response["opponentSquad"] = opponent
        response["awaySquad"] = opponent
        response["opponent"] = opponent
    return response


_SQUAD_BATTLE_PRESETS = (
    ("Local Rovers", "LRO", 80, 82, "AMATEUR"),
    ("Local United", "LUT", 83, 88, "SEMIPRO"),
    ("Local City", "LCY", 86, 94, "PRO"),
    ("Local Legends", "LLG", 90, 100, "WORLDCLASS"),
)

def _squad_battle_cycle_id() -> int:
    """Stable daily generation key shared by hub summaries and squad fetches."""
    return now_s() // (24 * 60 * 60)


def _squad_battle_player_ids(target_rating: int, slot: int = 0) -> list[int]:
    """Build a randomized, positionally valid 4-4-2 matchday squad.

    A seeded generator keeps a day's four hub summaries and their later
    opponentsquad requests consistent while producing fresh teams the next
    day (or after the system date changes).
    """
    slot_positions = (
        ("GK",), ("LB", "LWB"), ("CB",), ("CB",), ("RB", "RWB"),
        ("LM", "LW"), ("CM", "CDM", "CAM"), ("CM", "CAM", "CDM"),
        ("RM", "RW"), ("ST", "CF"), ("ST", "CF"),
        ("GK",), ("LB", "LWB"), ("CB",), ("RB", "RWB"),
        ("LM", "LW"), ("CM", "CAM", "CDM"), ("RM", "RW"),
        ("CAM", "CM", "CF"), ("CDM", "CM"), ("LW", "LM"),
        ("RW", "RM"), ("ST", "CF"),
    )
    used_resources: set[int] = set()
    used_assets: set[int] = set()
    selected: list[int] = []
    rng = random.Random(
        f"project21-squad-battles:{_squad_battle_cycle_id()}:{int(slot)}:{int(target_rating)}"
    )

    def candidates(positions: tuple[str, ...], base_only: bool) -> list[tuple[int, int, int]]:
        result: list[tuple[int, int, int]] = []
        for raw_resource_id, meta in PLAYER_DB.items():
            resource_id = int(raw_resource_id)
            position = str(meta.get("preferredPosition", "") or "").upper()
            rating = int(meta.get("rating", 0) or 0)
            rareflag = int(meta.get("rareflag", 1) or 0)
            asset_id = int(meta.get("assetId", resource_id) or resource_id)
            if position not in positions or rating < 55:
                continue
            if base_only and (rareflag not in (0, 1) or resource_id > 0x00FFFFFF):
                continue
            if resource_id in used_resources or asset_id in used_assets:
                continue
            result.append((abs(rating - target_rating), -rating, resource_id))
        return sorted(result)

    all_positions = tuple({
        str(meta.get("preferredPosition", "") or "").upper()
        for meta in PLAYER_DB.values()
    })
    for positions in slot_positions:
        # Match squads must use the base resource id. Promo resource ids make
        # the native pre-match flow request dynamic player-head assets; FIFA 21
        # waits indefinitely when one of those old CDN assets is unavailable.
        # Specials remain available in packs/club, but AI opponents use their
        # stable base cards so selecting a squad can always reach difficulty.
        pool = candidates(positions, True)
        if not pool:
            pool = candidates(all_positions, True)
        if not pool:
            raise RuntimeError("FIFA 21 player catalog cannot fill a Squad Battles squad")
        shortlist = pool[:min(160, len(pool))]
        # Keep the displayed difficulty tiers meaningful while ensuring two
        # refreshes are not just the same nearest-rated XI in a new order.
        weights = [1.0 / ((1.0 + candidate[0]) ** 1.7) for candidate in shortlist]
        resource_id = int(rng.choices(shortlist, weights=weights, k=1)[0][2])
        selected.append(resource_id)
        used_resources.add(resource_id)
        meta = player_meta(resource_id)
        used_assets.add(int(meta.get("assetId", resource_id) or resource_id))
    return selected


def _native_squad_battle_opponent(slot: int) -> dict[str, Any]:
    """Return one complete playable opponent squad for a hub slot."""
    slot = max(0, min(int(slot), len(_SQUAD_BATTLE_PRESETS) - 1))
    club_name, _club_abbr, target_rating, chemistry, _difficulty = _SQUAD_BATTLE_PRESETS[slot]
    players: list[dict[str, Any]] = []
    for index, resource_id in enumerate(_squad_battle_player_ids(target_rating, slot)):
        item_id = 920000 + slot * 100 + index
        item = STATE.build_player_item(resource_id, item_id, "none")
        players.append({
            "index": index,
            "kitNumber": index + 1,
            "itemData": _native_player_item(item),
        })

    starter_ids = [int(player["itemData"]["id"]) for player in players[:11]]
    kicktakers = [
        {"index": index, "id": item_id, "dream": False}
        for index, item_id in enumerate(starter_ids[1:6])
    ]
    actives = [
        item for item in _active_club_items()
        if item.get("itemState") in ("activeHomeKit", "activeAwayKit", "activeStadium")
    ]
    # Opponents must not reuse the local club's strips: doing so makes the
    # native pre-match selector see two identical kit sets and guarantees a
    # clash.  Use two installed Barcelona definitions for the away side.
    opponent_kit_defs = SQUAD_BATTLE_OPPONENT_KITS
    for item_state, (resource_id, team_id, kit_type, name) in opponent_kit_defs.items():
        template = next(
            (dict(item) for item in actives
             if item.get("itemState") == item_state and item.get("itemType") == "kit"),
            {},
        )
        template.update({
            "id": 930000000000 + slot * 10 + kit_type,
            "resourceId": resource_id,
            "carddbid": resource_id,
            "assetId": resource_id,
            "teamid": team_id,
            "kitType": kit_type,
            "teamkittypetechid": kit_type,
            "category": 2 + kit_type,
            "name": name,
            "itemState": item_state,
        })
        actives = [item for item in actives if item.get("itemState") != item_state]
        actives.append(template)
    stadium = next(
        (item for item in actives if item.get("itemState") == "activeStadium"),
        {},
    )
    actives = [item for item in actives if item.get("itemState") != "activeStadium"]
    # Do not reuse the local club's active-stadium instance ID. CardsDLL caches
    # active items by id, so a duplicate opponent ID retains the first (home)
    # object and drops the away venue definition during match construction.
    actives.append(_squad_battle_stadium_item(
        stadium,
        item_id=930000000090 + slot,
    ))
    return {
        "id": slot,
        "personaId": int(CFG["persona_id"]),
        "squadName": club_name,
        "formation": "f442",
        "captain": starter_ids[1],
        "chemistry": chemistry,
        "changed": 0,
        "starRating": 5 if target_rating >= 83 else 4,
        "custom": "[0,0,0,0,0,0,0,0,0,0,0]",
        "players": players,
        "actives": actives,
        "stadiumAssetId": SQUAD_BATTLE_STADIUM_ID,
        "stadiumId": SQUAD_BATTLE_STADIUM_ID,
        # Away fixtures cannot currently resolve their venue in the retail PC
        # client, so advertise every generated Squad Battles opponent as an
        # away side and keep the local user as the home side.
        "isHomeTeam": True,
        "sqbt2data": {
            "stadiumAssetId": SQUAD_BATTLE_STADIUM_ID,
            "stadiumId": SQUAD_BATTLE_STADIUM_ID,
        },
        "manager": [_native_default_manager()],
        "club": actives,
        "kicktakers": kicktakers,
    }


def _native_squad_battle_event() -> dict[str, Any]:
    """Return an always-active local Squad Battles competition window."""
    current = now_s()
    start = current - 24 * 60 * 60
    end = current + 30 * 24 * 60 * 60
    event_id = current // (7 * 24 * 60 * 60)
    # These aliases are intentional: different FIFA 21 PC builds use the
    # timestamp and duration names interchangeably when rendering the hub.
    return {
        "id": event_id,
        "eventId": event_id,
        "competitionId": event_id,
        "startTime": start,
        "startTimeStamp": start,
        "eventStartTime": start,
        "endTime": end,
        "endTimeStamp": end,
        "eventEndTime": end,
        "currentTime": current,
        "crtTime": current,
        "serverCrtTime": current,
        "serverTimeInSeconds": current,
        "secondsUntilStart": 0,
        "secondsUntilEnd": end - current,
        "timeUntilStart": 0,
        "timeUntilEnd": end - current,
        "untilStartSeconds": 0,
        "untilEndSeconds": end - current,
        "nextEventStartTime": end + 1,
        "gamesPlayed": 0,
        "numberOfMatchesPlayed": 0,
        "matchesPlayed": 0,
        "gamesRemaining": 4,
        "remainingMatches": 4,
        "squadsRemaining": 4,
        "totalGames": 4,
        "maxMatches": 4,
        "maxNumberOfMatches": 4,
        "maxRegularGames": 4,
        "userPoints": 0,
        "pointsWon": 0,
        "squadBattleEnabled": True,
        "weeklyRewardsAutoClaimed": False,
        "active": True,
        "isActive": True,
        "enabled": True,
        "available": True,
        "status": "ACTIVE",
    }


def _native_squad_battle_hub() -> dict[str, Any]:
    """Return the actual CardsDLL Squad Battles hub contract.

    FIFA 21 does not consume a generic ``opponents`` collection for this
    route.  Its GetSquadBattleHub decoder requires a ``sqbtOppSquadList``
    object containing a ``sqbtOppSquads`` array.  Each entry is an opponent
    summary, not a full /opponentsquad response.  Keep the list deliberately
    small and typed; the detailed squad is fetched only after selection.
    """
    refresh_at = now_s() + 24 * 60 * 60
    opponents: list[dict[str, Any]] = []
    for index, (club_name, club_abbr, rating, chemistry, difficulty) in enumerate(_SQUAD_BATTLE_PRESETS):
        squad_id = index
        generated_ids = _squad_battle_player_ids(rating, index)
        generated_ratings = [int(player_meta(resource_id).get("rating", 0) or 0) for resource_id in generated_ids]
        top_player_indices = sorted(
            range(11),
            key=lambda player_index: int(
                player_meta(generated_ids[player_index]).get("rating", 0) or 0
            ),
            reverse=True,
        )[:3]
        # CardsDLL decodes topPlayers as a vector of objects containing an
        # itemData member, not IDs or bare FUT item objects. Either wrong shape
        # creates three empty card models in the hub and leaves the opponent-
        # selection callback waiting indefinitely.
        # Reuse the exact item/resource IDs returned by opponentsquad/{slot} so
        # the selected summary and detailed squad resolve to the same players.
        top_players = [
            {
                "itemData": _native_player_item(STATE.build_player_item(
                    generated_ids[player_index],
                    920000 + index * 100 + player_index,
                    "none",
                ))
            }
            for player_index in top_player_indices
        ]
        opponents.append({
            # Fields and JSON types recovered from the installed FIFA 21
            # CardsDLL opponent-summary decoder at RVA 0x33BC50.
            # CardsDLL treats id and squadId as aliases for the same fixed-map
            # key.  Both therefore have to be the zero-based slot; a later
            # non-zero-based squadId would overwrite id during decoding.
            "id": index,
            "squadId": squad_id,
            "clubName": club_name,
            "clubAbbr": club_abbr,
            "badgeAssetId": "1",
            "rating": int(round(sum(generated_ratings[:11]) / 11.0)),
            "chemistry": chemistry,
            "formation": "f442",
            "difficulty": difficulty,
            "difficultyName": difficulty,
            "isHomeTeam": True,
            "basePointsWin": 400 + index * 100,
            "basePointsLoss": 0,
            # The opponent-summary decoder only reads these from its nested
            # sqbt2data object (topPlayers at this level is silently skipped).
            "sqbt2data": {
                "stadiumAssetId": SQUAD_BATTLE_STADIUM_ID,
                "isHomeTeam": True,
                "topPlayers": top_players,
            },
        })
    event = _native_squad_battle_event()
    return {
        "currentTime": event["currentTime"],
        # Like FIFA 17's activeChampionLeagues record, the native mode
        # controller reads this exact startTime/endTime/currentTime trio from
        # the active hub record. eventStartTime/eventEndTime are presentation
        # aliases and do not open the competition by themselves.
        "startTime": event["startTime"],
        "endTime": event["endTime"],
        # The FIFA 21 hub view model reads these values directly from the
        # response, rather than through sqbtEvent.  Without a top-level
        # maxNumberOfMatches it displays 0/0 and treats every opponent as an
        # already-finished weekly competition.
        "serverCrtTime": event["serverTimeInSeconds"],
        "crtTime": event["crtTime"],
        "eventStartTime": event["eventStartTime"],
        "eventEndTime": event["eventEndTime"],
        "nextEventStartTime": event["nextEventStartTime"],
        "secondsUntilStart": event["secondsUntilStart"],
        "secondsUntilEnd": event["secondsUntilEnd"],
        "timeUntilStart": event["timeUntilStart"],
        "timeUntilEnd": event["timeUntilEnd"],
        "untilStartSeconds": event["untilStartSeconds"],
        "untilEndSeconds": event["untilEndSeconds"],
        "gamesPlayed": event["gamesPlayed"],
        "gamesRemaining": event["gamesRemaining"],
        "matchesPlayed": event["matchesPlayed"],
        "remainingMatches": event["remainingMatches"],
        "numberOfMatchesPlayed": event["numberOfMatchesPlayed"],
        "squadsRemaining": event["squadsRemaining"],
        "totalGames": event["totalGames"],
        "maxMatches": event["maxMatches"],
        "maxNumberOfMatches": event["maxNumberOfMatches"],
        "maxRegularGames": event["maxRegularGames"],
        "sqbtEventId": event["eventId"],
        "sqbtMatchDifficulty": 0,
        "sqbtOpponentSquadId": -1,
        "sqbtOppid": -1,
        "squadBattleEnabled": True,
        "squadBattleCouchPlayEnabled": True,
        "active": True,
        "isActive": True,
        "enabled": True,
        "available": True,
        "status": "ACTIVE",
        "squadBattlesScore": 0,
        "sqbtEvent": event,
        "sqbt2data": event,
        "squadBattle": event,
        "sqbtOppSquadList": {
            "nextSquadRefreshTimeStamp": refresh_at,
            "sqbtOppSquads": opponents,
        },
    }


# ---------------------------------------------------------------------------
# Division Rivals and FUT Champions -- the FIFA 21 "Seasonal" framework
#
# FIFA 21 drives both menus from a weekly seasonal schedule.  When the window
# fields (start/end/current time) are absent or zero the client renders
# "competition closed".  This is the same native behaviour Aurora17 documents
# for FIFA 17's activeChampionLeagues record: startTime/endTime/currentTime
# open the mode, and nothing else does.  Empty eligibility/requirement arrays
# keep the pre-match squad gate disabled.
#
# Captured routes (client build DS 15.1.6.0.4), all previously unhandled and
# answered with 200 {} + X-LocalFUT15-Unknown:
#   GET /fut/seasonal/seasonalconfig.json
#   PUT /ut/game/fifa21/clientdata/rivals    body {"entries":[{key,value}...]}
#   GET /ut/game/fifa21/rivals/user/hub
#   GET /ut/game/fifa21/champion/user/hub
#   GET /ut/game/fifa21/champion/user/stats?save=false&period=curr&type=base
#   GET /ut/game/fifa21/champion/leaderboard?save=false&period=curr&type=base
#   GET /ut/game/fifa21/champion/type/week/period/curr/friends/stats
#
# The response shapes below are a best-effort reconstruction.  They carry
# generous field aliases (same defensive style as _native_squad_battle_hub)
# and are logged on every request as SEASONAL-SCOUT RESPONSE so the exact
# served document can be correlated with client behaviour and tightened.
# ---------------------------------------------------------------------------

_SEASONAL_WEEK_SECONDS = 7 * 24 * 60 * 60
# FIFA 21 Weekend League Finals is a 20-match set; Play-Offs is 15.
_CHAMPION_PLAYOFF_MATCHES = 10
_CHAMPION_FINALS_MATCHES = 30
_RIVALS_WEEKLY_MATCHES = 30


def _seasonal_window(now: int | None = None) -> dict[str, int]:
    """A currently-open weekly competition window.

    The week is anchored to a fixed epoch boundary so the id is stable within a
    week and advances once per week, mirroring retail seasonal rollover.  start
    is therefore up to seven days in the past and end up to seven days ahead --
    the window always contains ``current``.
    """
    current = int(now if now is not None else now_s())
    week_index = current // _SEASONAL_WEEK_SECONDS
    start = week_index * _SEASONAL_WEEK_SECONDS
    end = start + _SEASONAL_WEEK_SECONDS
    return {
        "weekIndex": int(week_index),
        "start": int(start),
        "end": int(end),
        "current": current,
        "untilStart": 0,
        "untilEnd": int(end - current),
    }


def _rivals_progress() -> dict[str, Any]:
    raw = STATE.get("rivals_progress", {}) or {}
    return {
        # 10 is the entry division (lowest); 1 is the top.
        "division": int(raw.get("division", 10) or 10),
        # Rank inside the division: 5..1 with 1 the best, matching retail.
        "rank": int(raw.get("rank", 5) or 5),
        "skillRating": int(raw.get("skillRating", 1000) or 1000),
        "wins": int(raw.get("wins", 0) or 0),
        "draws": int(raw.get("draws", 0) or 0),
        "losses": int(raw.get("losses", 0) or 0),
        "matchesPlayed": int(raw.get("matchesPlayed", 0) or 0),
        "weeklyWins": int(raw.get("weeklyWins", 0) or 0),
        "weeklyGames": int(raw.get("weeklyGames", 0) or 0),
        "titleWins": int(raw.get("titleWins", 0) or 0),
        "placementMatchesPlayed": int(raw.get("placementMatchesPlayed", 5) or 5),
        "seasonId": int(raw.get("seasonId", 0) or 0),
        # Points accumulated toward the next rank inside the current division.
        "rankPoints": int(raw.get("rankPoints", 0) or 0),
        # Points accumulated in the current weekly competition.
        "weeklyPoints": int(raw.get("weeklyPoints", 0) or 0),
    }


def _champion_progress() -> dict[str, Any]:
    raw = STATE.get("champion_progress", {}) or {}
    return {
        # PLAYOFF -> FINALS is the retail two-stage weekend flow.
        "stage": str(raw.get("stage", "PLAYOFF") or "PLAYOFF"),
        "playoffWins": int(raw.get("playoffWins", 0) or 0),
        "playoffLosses": int(raw.get("playoffLosses", 0) or 0),
        "playoffGames": int(raw.get("playoffGames", 0) or 0),
        "finalsWins": int(raw.get("finalsWins", 0) or 0),
        "finalsLosses": int(raw.get("finalsLosses", 0) or 0),
        "finalsGames": int(raw.get("finalsGames", 0) or 0),
        # Points earned in Division Rivals that unlock a Play-Offs entry.
        "qualificationPoints": int(raw.get("qualificationPoints", 2000) or 2000),
        "entryTokens": int(raw.get("entryTokens", 1) or 1),
        "finalsQualified": bool(raw.get("finalsQualified", False)),
    }


# Field names below are recovered from the installed FIFA 21 CardsDLL
# (CardsDLL_Win64_retail.dll, SHA-256 37133BE9...48A49 -- matches
# docs/findings.md).  Extracted string evidence for FutGetRivalsHubServerResponse
# and the shared seasonal prize contract: crtRivalDivision, divisionsList,
# rivalsScore, skillPoints, relegationBuffer, eloThresholdUp/eloThresholdDown,
# rivalsDivisionCoinsReward, prizeSet, prizeLevel, thresholdPoint, awardMappings,
# weekendLeaguePoints/Threshold/PointsForWin, rivalsEventMatchesSoftCap[Max],
# rivalsThresholdBeforeSoftCap, rivalsSkillPointsCapValue,
# rivalsNumberOfSqbtPlacementMatches, rivalsPlacementSqbtGamesPlayed.
# Structure mirrors Aurora17's FIFA 17 SeasonsListJson prizeSet builder, which
# uses the identical PROMOTION/RELEGATION/MAINTENANCE/CHAMPIONSHIP levels.

_RIVALS_ELO_PROMOTE = 250      # rivalsScore gain that promotes a division
_RIVALS_ELO_RELEGATE = -100   # rivalsScore floor before relegation
_RIVALS_WEEKEND_LEAGUE_THRESHOLD = 2000
_RIVALS_WEEKEND_LEAGUE_POINTS_PER_WIN = 200


def _rivals_hub_json() -> dict[str, Any]:
    window = _seasonal_window()
    p = _rivals_progress()
    games_left = max(0, _RIVALS_WEEKLY_MATCHES - p["weeklyGames"])
    weekly_wins = p["weeklyWins"]
    # NOTE: divisionsList / prizeSet / milestones carry real CardsDLL field
    # names but an unverified element shape.  A populated divisionsList hard-
    # freezes the client (RS4 decoder stalls mid-parse), so these stay empty
    # until the FutGetRivalsHubServerResponse descriptor table is read out of
    # the DLL.  Empty arrays render an empty DIVISIONS tab without crashing.
    return {
        "rivalsEventId": window["weekIndex"],
        "seasonId": p["seasonId"],
        # --- Current ladder position (recovered names) ---
        "crtRivalDivision": p["division"],
        "crtDivision": p["division"],
        "currentDivision": p["division"],
        "divisionId": p["division"],
        "division": p["division"],
        "rivalsScore": p["skillRating"],
        "skillPoints": p["skillRating"],
        "skillRatingScore": p["skillRating"],
        "relegationBuffer": 3,
        "eloThresholdUp": _RIVALS_ELO_PROMOTE,
        "eloThresholdDown": _RIVALS_ELO_RELEGATE,
        # --- Schedule: the fields that actually open the mode ---
        "currentTime": window["current"],
        "serverTime": window["current"],
        "serverCrtTime": window["current"],
        "serverTimeInSeconds": window["current"],
        "crtTime": window["current"],
        "startTime": window["start"],
        "endTime": window["end"],
        "startTimeStamp": window["start"],
        "endTimeStamp": window["end"],
        "secondsUntilStart": window["untilStart"],
        "secondsUntilEnd": window["untilEnd"],
        "untilStartSeconds": window["untilStart"],
        "untilEndSeconds": window["untilEnd"],
        "nextReset": window["end"],
        "nextResetTime": window["end"],
        "weeklyResetTime": window["end"],
        "state": "ACTIVE",
        "status": "ACTIVE",
        "active": True,
        "isActive": True,
        "enabled": True,
        "available": True,
        "competitionClosed": False,
        # --- Weekly matches + placement ---
        "gamesPlayed": p["weeklyGames"],
        "rivalGamesPlayed": p["weeklyGames"],
        "numberOfMatchesPlayed": p["weeklyGames"],
        "matchesPlayed": p["weeklyGames"],
        "weeklyGamesPlayed": p["weeklyGames"],
        "weeklyWins": weekly_wins,
        "weeklyDraws": 0,
        "weeklyLosses": max(0, p["weeklyGames"] - weekly_wins),
        "gamesWon": weekly_wins,
        "gamesLost": max(0, p["weeklyGames"] - weekly_wins),
        "gamesDraw": 0,
        "gamesRemaining": games_left,
        "weeklyGamesRemaining": games_left,
        "remainingMatches": games_left,
        "maxMatches": _RIVALS_WEEKLY_MATCHES,
        "maxWeeklyGames": _RIVALS_WEEKLY_MATCHES,
        "maxRegularGames": _RIVALS_WEEKLY_MATCHES,
        "maxNumberOfMatches": _RIVALS_WEEKLY_MATCHES,
        "wins": p["wins"],
        "draws": p["draws"],
        "losses": p["losses"],
        "titleWins": p["titleWins"],
        # --- Placement: reported complete ---
        "inPlacement": False,
        "inPlacementMatches": False,
        "placementComplete": True,
        "placementMatchesRequired": 5,
        "placementMatchesPlayed": p["placementMatchesPlayed"],
        "gamesPlayedPlacement": 5,
        "rivalsNumberOfSqbtPlacementMatches": 5,
        "rivalsPlacementSqbtGamesPlayed": 5,
        "rivalsSqbtPlacementMatchesDisplayThreshold": 5,
        # --- Soft cap ---
        "rivalsEventMatchesSoftCap": _RIVALS_WEEKLY_MATCHES,
        "rivalsEventMatchesSoftCapMax": _RIVALS_WEEKLY_MATCHES,
        "rivalsThresholdBeforeSoftCap": _RIVALS_WEEKLY_MATCHES,
        "rivalsSkillPointsCapValue": 5000,
        # --- Weekend League qualification (FUT Champions entry) ---
        "weekendLeagueEnabled": True,
        "weekendLeaguePoints": p["weeklyPoints"],
        "weekendLeaguePointsForWin": _RIVALS_WEEKEND_LEAGUE_POINTS_PER_WIN,
        "weekendLeagueThreshold": _RIVALS_WEEKEND_LEAGUE_THRESHOLD,
        "weekendLeagueGamesPlayed": 0,
        "qualifiedInWeekendLeague": False,
        # --- Prizes / rewards: nothing pending on a fresh local club ---
        # Shape from the FIFA 21 web app UTRivalsDAO.getPrizeDetails:
        #   prizes.prizeTiers[].categoryAwardsList[].awards[]
        #   award = {assetId|halId, awardCount, untradeable, awardType, awardValue}
        "prizes": {"prizeTiers": []},
        "prizesInError": [],
        "prizesRivalPoints": p["weeklyPoints"],
        "unclaimedPrizeWaiting": False,
        "weeklyRewardsAutoClaimed": False,
        "hasRewardsToClaim": False,
        "needsAutoClaim": False,
        "needsMilestonesAutoClaim": False,
        "milestones": [],
        "unclaimedRewards": [],
        "pendingRewards": [],
        "rewardSummary": {"unclaimed": 0, "claimed": 0},
        "winForm": 1 if weekly_wins else 0,
        "userForm": [],
        "wasRefreshed": False,
        "opponent": None,
        "matchInProgress": False,
    }


# FUT Champions -- field names verified present in CardsDLL_Win64_retail.dll
# (37133BE9…48A49): activeChampionLeagues, previousChampionEvents,
# upcomingChampionEvents, unclaimedPrizesChampionEvents, qualifiedChampionLeagueIds,
# qualifiedChampionEventId, qualifiedInWeekendLeague, currentChampionEvent,
# userRegistration, userStats, minMatchesToRank, maxMatches, qualifierTournaments,
# championEventId, championEventType, prizeTiers, tierType ("NUMBER_OF_WINS_TYPE"),
# tierLevel/tierStart/tierEnd, awards, halId, awardType ("COINS"/"PACK"),
# awardValue, awardCount.  award element shape confirmed by the FIFA 21 web app
# UTRewardFactory.createRewardFromChampionsData:
#   {halId, awardCount, untradeable, awardType, awardValue, itemData?}
# Structure ported from Aurora17's working FIFA 17 ChampionsHubJson (same routes).

_CHAMPION_EVENT_ID = 1

# FIFA 21 FUT Champions -- Weekend League Finals, 30 games, 10 ranks.
# (tierLevel, minWins, maxWins, coins, [packId, ...]); tierLevel 1 is Rank I.
# Win thresholds match retail FIFA 21 (27 / 23 / 20 / 17 / 14 / 11 / 8 / 5 / 4 / 0).
# Pack ids are real FIFA 21 pack template ids from localfut/PACK_ID_REFERENCE.json:
#   405 Ultimate  404 Rare Mega  402 Jumbo Rare Players  401 Rare Players
#   305 Premium Gold Players
# Shape ported from Aurora17's working FIFA 17 ChampionPrizeEntries; awardType
# is lower-case "coin"/"pack".
_CHAMPION_REWARD_BANDS = [
    (1,  27, 30, 65_000, [402, 402, 402, 401, 401, 401]),
    (2,  23, 26, 50_000, [402, 402, 401, 401, 401]),
    (3,  20, 22, 43_000, [402, 402, 401, 401]),
    (4,  17, 19, 36_000, [402, 401, 401, 401]),
    (5,  14, 16, 30_000, [402, 401, 401]),
    (6,  11, 13, 25_000, [401, 401, 401]),
    (7,   8, 10, 20_000, [401, 401]),
    (8,   5,  7, 15_000, [401, 305]),
    (9,   4,  4, 12_000, [305, 305]),
    (10,  0,  3,  7_500, [305]),
]
_CHAMPION_WORST_TIER = _CHAMPION_REWARD_BANDS[-1][0]


def _champion_prize_tiers() -> list[dict[str, Any]]:
    tiers: list[dict[str, Any]] = []
    for level, lo, hi, coins, packs in _CHAMPION_REWARD_BANDS:
        awards = [{
            "halId": 0, "assetId": 0, "awardType": "coin",
            "awardValue": coins, "awardCount": 1,
        }]
        for pid in packs:
            awards.append(_pack_award(pid))
        tiers.append({
            "tierType": "NUMBER_OF_WINS_TYPE",
            "tierLevel": level,
            "tierStart": lo,
            "tierEnd": hi,
            "awards": awards,
        })
    return tiers


def _champion_tier_for_wins(wins: int) -> int:
    for level, lo, hi, _c, _p in _CHAMPION_REWARD_BANDS:
        if lo <= wins <= hi:
            return level
    return _CHAMPION_WORST_TIER


def _champion_event_record() -> dict[str, Any]:
    window = _seasonal_window()
    return {
        "id": _CHAMPION_EVENT_ID,
        "championEventId": _CHAMPION_EVENT_ID,
        "championEventType": 0,
        "type": 0,
        "state": "ACTIVE",
        "startTime": window["start"],
        "endTime": window["end"],
        "currentTime": window["current"],
    }


def _champion_hub_json() -> dict[str, Any]:
    window = _seasonal_window()
    p = _champion_progress()
    played = p["playoffGames"] + p["finalsGames"]
    won = p["playoffWins"] + p["finalsWins"]
    remaining = max(0, _CHAMPION_FINALS_MATCHES - played)

    league = {
        "id": _CHAMPION_EVENT_ID,
        "championEventId": _CHAMPION_EVENT_ID,
        "championEventType": 0,
        "localizedName": "FUT_CHAMPIONS_WKND_LG",
        # Schedule -- this trio is what makes the mode "scheduled".
        "currentTime": window["current"],
        "startTime": window["start"],
        "endTime": window["end"],
        "serverCrtTime": window["current"],
        "secondsUntilStart": window["untilStart"],
        "secondsUntilEnd": window["untilEnd"],
        "untilStartSeconds": window["untilStart"],
        "untilEndSeconds": window["untilEnd"],
        "nextEventStartTime": window["end"] + 1,
        "registrationCloseTime": window["end"],
        "maxMatches": _CHAMPION_FINALS_MATCHES,
        "maxNumberOfMatches": _CHAMPION_FINALS_MATCHES,
        "minMatchesToRank": 1,
        # Entry gate bypassed: empty eligibility list = no requirement.
        "eligibilities": [],
        "elgOperation": "AND",
        "prizeTiers": _champion_prize_tiers(),
        "qualifierTournaments": [],
    }

    projected_tier = _champion_tier_for_wins(won)
    user_stats = {
        "championEventId": _CHAMPION_EVENT_ID,
        "qualifiedChampionEventId": _CHAMPION_EVENT_ID,
        "gamesPlayed": played,
        "gamesWon": won,
        "gamesLost": max(0, played - won),
        "remainingMatches": remaining,
        "rank": 0,
        "ranking": 0,
        "score": won,
        "tierLevel": 0,
        "userTierLevel": 0,
        # Projected finishing rank for the "your rank" reward preview.
        "expectedTierLevel": projected_tier,
        "predictedTierLevel": projected_tier,
        "winForm": 1 if won else 0,
    }

    return {
        "activeChampionLeagues": [league],
        "previousChampionEvents": [],
        "upcomingChampionEvents": [],
        "unclaimedPrizesChampionEvents": [],
        # Bypass -- report the local user as already qualified / registered.
        "qualifiedChampionLeagueIds": [_CHAMPION_EVENT_ID],
        "qualifiedChampionEventId": _CHAMPION_EVENT_ID,
        "qualifiedInWeekendLeague": True,
        "currentChampionEvent": _champion_event_record(),
        "userRegistration": {
            "competitionCountryCode": "KW",
            "competitionRegion": "ME",
        },
        "userStats": [user_stats],
        # Generic aliases some hub views read directly.
        "championEventId": _CHAMPION_EVENT_ID,
        "currentTime": window["current"],
        "startTime": window["start"],
        "endTime": window["end"],
        "secondsUntilEnd": window["untilEnd"],
        "untilEndSeconds": window["untilEnd"],
        "state": "ACTIVE",
        "status": "ACTIVE",
        "active": True,
        "competitionClosed": False,
        "gamesPlayed": played,
        "gamesWon": won,
        "remainingMatches": remaining,
        "maxMatches": _CHAMPION_FINALS_MATCHES,
        "prizeTiers": _champion_prize_tiers(),
        "unclaimedRewards": [],
        "hasRewardsToClaim": False,
    }


def _champion_stats_json() -> dict[str, Any]:
    p = _champion_progress()
    played = p["playoffGames"] + p["finalsGames"]
    won = p["playoffWins"] + p["finalsWins"]
    return {
        "championEventId": _CHAMPION_EVENT_ID,
        "qualifiedChampionEventId": _CHAMPION_EVENT_ID,
        "period": "curr",
        "type": "base",
        "gamesPlayed": played,
        "gamesWon": won,
        "gamesLost": max(0, played - won),
        "gamesDraw": 0,
        "score": won,
        "points": won,
        "rank": 0,
        "ranking": 0,
        "tierLevel": 0,
        "userTierLevel": 0,
        "expectedTierLevel": _champion_tier_for_wins(won),
        "predictedTierLevel": _champion_tier_for_wins(won),
        "remainingMatches": max(0, _CHAMPION_FINALS_MATCHES - p["finalsGames"]),
        "goalsFor": 0,
        "goalsAgainst": 0,
        "winStreak": 0,
        "bestWinStreak": 0,
    }


def _champion_leaderboard_json() -> dict[str, Any]:
    p = _champion_progress()
    won = p["playoffWins"] + p["finalsWins"]
    played = p["playoffGames"] + p["finalsGames"]
    row = {
        "name": str(CFG.get("club_name", "Local FC")),
        "personaId": int(CFG["persona_id"]),
        "rank": 1,
        "ranking": 1,
        "score": won,
        "points": won,
        "gamesPlayed": played,
        "gamesWon": won,
        "gamesLost": max(0, played - won),
        "tierLevel": 0,
        "userTierLevel": 0,
        "championEventId": _CHAMPION_EVENT_ID,
    }
    return {
        "leaderboard": [dict(row)],
        "ranking": [dict(row)],
        "entries": [dict(row)],
        "userEntry": dict(row),
        "totalCount": 1,
        "prizeTiers": _champion_prize_tiers(),
    }


def _seasonal_config_json() -> dict[str, Any]:
    window = _seasonal_window()
    # The retail file is a CDN document defining both competitions' schedules,
    # division/rank tables and reward delivery times.  This local version keeps
    # the current week permanently open.  Provisional until a real copy is
    # captured or the client rejects a field.
    def week_schedule() -> list[dict[str, Any]]:
        return [{
            "weekId": window["weekIndex"],
            "startTime": window["start"],
            "endTime": window["end"],
            "rewardsTime": window["end"] + 3600,
            "active": True,
        }]

    return {
        "version": 1,
        "generatedAt": window["current"],
        "serverTime": window["current"],
        "currentTime": window["current"],
        "divisionRivals": {
            "enabled": True,
            "active": True,
            "seasonId": 0,
            "startTime": window["start"],
            "endTime": window["end"],
            "weeklyResetTime": window["end"],
            "maxWeeklyGames": _RIVALS_WEEKLY_MATCHES,
            "placementMatches": 5,
            "schedule": week_schedule(),
        },
        "futChampions": {
            "enabled": True,
            "active": True,
            "startTime": window["start"],
            "endTime": window["end"],
            "registrationCloseTime": window["end"],
            "playoffMaxGames": _CHAMPION_PLAYOFF_MATCHES,
            "finalsMaxGames": _CHAMPION_FINALS_MATCHES,
            "qualificationPointsRequired": 2000,
            "schedule": week_schedule(),
            "playoffScheduleViewPeriodInMinutes": 10080,
            "finalsScheduleViewPeriodInMinutes": 10080,
        },
    }


# ---------------------------------------------------------------------------
# FUT Objectives -- FIFA 21 "Seasonal Campaign" (SCMP)
#
# The client gates the whole tab on the `enableObjectives` /
# `enableSeasonalCampaigns` settings flags (added to /ut/game/fifa21/settings),
# then reads the campaign from GET /ut/game/fifa21/scmp/campaign/active.
# Field names below are from the FIFA 21 web app UTObjectivesFactory
# (createCampaign / createCategory / createGroup / createObjective /
# createRewardFromObjectiveData) and CardsDLL strings (scmpCategoryProgressList,
# groupsList, awardsList, objectiveId, currentProgress, takeMeThereLink,
# rewardOptionList).  Every response is logged as SCMP-SCOUT RESPONSE.
#
# Objective award shape (createRewardFromObjectiveData):
#   {assetId | halId, count, untradeable, awardType, value, itemDataReduced?}
# ---------------------------------------------------------------------------

_SCMP_CAMPAIGN_ID = 1
_SCMP_CATEGORY_ID = 1
_SCMP_GROUP_ID = 1
_SCMP_OBJECTIVE_ID = 1
# "82 x10": ten guaranteed 82-rated rare gold players.
_SCMP_REWARD_PLAYER_RATING = 82
_SCMP_REWARD_PLAYER_COUNT = 10
_SCMP_REWARD_PACK_ID = 513  # PACK_ID_REFERENCE.json: "82+ RATED RARE PLAYER"


def _scmp_reward_award() -> dict[str, Any]:
    # Objective award: fields from the FIFA 21 web app
    # UTRewardFactory.createRewardFromObjectiveData -- {assetId|halId, count,
    # untradeable, awardType, value}.
    return _pack_award(_SCMP_REWARD_PACK_ID, _SCMP_REWARD_PLAYER_COUNT)


def _scmp_reward_player_ids() -> list[int]:
    ids = [rid for rid, meta in PLAYER_DB.items()
           if int(meta.get("rating", 0) or 0) == _SCMP_REWARD_PLAYER_RATING]
    if len(ids) < _SCMP_REWARD_PLAYER_COUNT:
        ids += [rid for rid, meta in PLAYER_DB.items()
                if _SCMP_REWARD_PLAYER_RATING <= int(meta.get("rating", 0) or 0) <= 84]
    seen: list[int] = []
    for rid in ids:
        if rid not in seen:
            seen.append(int(rid))
        if len(seen) >= _SCMP_REWARD_PLAYER_COUNT:
            break
    return seen


def _scmp_grant_reward() -> list[dict[str, Any]]:
    """Grant the objective reward -- add the players to the unassigned pile
    and advance the season track."""
    granted: list[dict[str, Any]] = []
    for rid in _scmp_reward_player_ids():
        try:
            granted.append(STATE.make_player_item(int(rid), "unassigned"))
        except Exception as exc:  # noqa: BLE001 - best effort grant
            log.warning("SCMP reward grant failed for %s: %s", rid, exc)
    STATE.set("scmp_user_xp",
              int(STATE.get("scmp_user_xp", 0) or 0) + _SCMP_GROUP_XP_AWARD)
    log.info("SCMP objective reward granted: %d player item(s), +%d XP",
             len(granted), _SCMP_GROUP_XP_AWARD)
    return granted


# --- Season Progress (the SCMP campaign XP level track) ---
_SCMP_XP_PER_LEVEL = 500
_SCMP_SEASON_LEVELS = 30
_SCMP_GROUP_XP_AWARD = 3 * _SCMP_XP_PER_LEVEL  # claiming Welcome Objectives
# Level reward pool cycled across the track: (awardType, value).  "coin" pays
# coins; "pack" is a FIFA 21 pack template id from PACK_ID_REFERENCE.json.
_SCMP_LEVEL_REWARD_POOL = [
    ("coin", 5_000), ("pack", 100), ("coin", 7_500), ("pack", 200),
    ("pack", 300), ("coin", 10_000), ("pack", 301), ("pack", 500),
    ("pack", 305), ("coin", 15_000), ("pack", 400), ("pack", 401),
    ("coin", 20_000), ("pack", 402), ("pack", 403), ("coin", 25_000),
    ("pack", 404), ("pack", 305), ("coin", 30_000), ("pack", 401),
    ("pack", 402), ("coin", 40_000), ("pack", 400), ("pack", 403),
    ("pack", 404), ("coin", 50_000), ("pack", 402), ("pack", 405),
    ("coin", 75_000), ("pack", 405),
]


_PACK_NAMES = {
    100: "Bronze Pack", 200: "Silver Pack", 300: "Gold Pack",
    201: "Premium Silver Players Pack",
    301: "Premium Gold Pack", 305: "Premium Gold Players Pack",
    304: "Gold Players Pack", 400: "Rare Gold Pack", 401: "Rare Players Pack",
    306: "Electrum Players Pack", 307: "Prime Electrum Players Pack",
    308: "Prime Gold Players Pack", 309: "Prime Mixed Players Pack",
    402: "Jumbo Rare Players Pack", 403: "Mega Pack", 404: "Rare Mega Pack",
    405: "Ultimate Pack", 406: "Rare Mixed Players Pack",
    500: "Draft Token Pack", 513: "82+ Player Pick",
    514: "84+ Player Pick", 515: "86+ Player Pick",
    516: "88+ Player Pick",
}


# Pack ART is NOT keyed by the store pack id.  The client's reward components
# (futRewardsScreen::LoadPackArt, SBCRewards::LoadPackAssetId, StoreFront,
# DynamicReward) all resolve it as getArtAssetPath("pack/background", <id>)
# where <id> must be one of the game's own EPackAssetID band values --
# PACK_ASSET_BRONZE / SILVER / GOLD / SPECIAL, i.e. 1/2/3/4.  Sending the
# store pack id (300, 402, 513 ...) resolves to nothing, which is exactly why
# every reward tile drew the blank silhouette placeholder.
_PACK_ART_BRONZE, _PACK_ART_SILVER, _PACK_ART_GOLD, _PACK_ART_SPECIAL = 1, 2, 3, 4


def _pack_art_id(pack_id: int) -> int:
    """Map a store pack id to the client's EPackAssetID art band."""
    pack_id = int(pack_id or 0)
    if pack_id < 200:
        return _PACK_ART_BRONZE
    if pack_id < 300:
        return _PACK_ART_SILVER
    if pack_id < 400:
        return _PACK_ART_GOLD
    return _PACK_ART_SPECIAL


def _pack_award(pack_id: int, count: int = 1) -> dict[str, Any]:
    name = _PACK_NAMES.get(pack_id, f"Pack {pack_id}")
    art = _pack_art_id(pack_id)
    # Keep this narrow: fields from the FIFA 21 web app reward decoders
    # (createRewardFromObjectiveData / ChampionsData) plus the pack id and
    # untradeable flag.  Do NOT add item-shaped keys like defId here -- the
    # client indexes items by those and a pack id collides with real cards,
    # crashing squad edits.
    # Member set proven on this UI lineage (Aurora17's FIFA 17 reward object,
    # same ion_fut components).  The two things that decide whether the tile
    # draws a PACK or an item card:
    #   * assetId must be the ART BAND, not the pack id.  With the pack id here
    #     the reward tile treats it as a card asset, fails to resolve it and
    #     draws the blank player-card silhouette.
    #   * isPack/isItem select the pack image clip over the item one.
    # The real pack id still travels in id / packId / value for grants.
    return {
        "type": "pack", "awardType": "pack",
        "id": pack_id, "packId": pack_id,
        "value": pack_id, "awardValue": pack_id,
        "assetId": art, "packAssetId": art, "packAssetID": str(art),
        "isPack": True, "isItem": False,
        "isUntradeable": True, "untradeable": True,
        "count": count, "awardCount": count,
        "coins": 0, "packs": name, "packsAmount": count,
        "items": "", "itemData": [],
        "AWD_PACKS_STRING": name, "AWD_PACKS_AMOUNT": count,
        "AWD_PACKS_ASSET_IDS": str(art),
        "name": name, "packName": name,
    }


def _scmp_award(kind: str, value: int, count: int = 1) -> dict[str, Any]:
    if kind == "coin":
        return {
            "halId": 0, "assetId": 0, "awardType": "coin",
            "value": value, "awardValue": value,
            "count": count, "awardCount": count,
        }
    return _pack_award(value, count)


def _scmp_season_levels() -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    for lvl in range(1, _SCMP_SEASON_LEVELS + 1):
        kind, val = _SCMP_LEVEL_REWARD_POOL[(lvl - 1) % len(_SCMP_LEVEL_REWARD_POOL)]
        levels.append({
            "levelId": lvl,
            "level": lvl,
            "xpThreshold": lvl * _SCMP_XP_PER_LEVEL,
            "xp": lvl * _SCMP_XP_PER_LEVEL,
            # Reward-set shape from web app createRewardSetFromCampaignLevelData:
            # {optionId, defaultOption, hiddenReward, awards:[award]}.
            "rewardOptionList": [{
                "optionId": 0,
                "defaultOption": True,
                "hiddenReward": False,
                "awards": [_scmp_award(kind, val)],
            }],
        })
    return levels


def _scmp_grant_level_award(award: dict[str, Any]) -> list[dict[str, Any]]:
    if str(award.get("awardType")) == "coin":
        STATE.add_credits(int(award.get("value", 0) or 0))
        return []
    # Pack award: grant a handful of mid-rated players to the unassigned pile.
    pool = [rid for rid, meta in PLAYER_DB.items()
            if 78 <= int(meta.get("rating", 0) or 0) <= 86]
    granted: list[dict[str, Any]] = []
    for rid in pool[:5]:
        try:
            granted.append(STATE.make_player_item(int(rid), "unassigned"))
        except Exception as exc:  # noqa: BLE001
            log.warning("SCMP level grant failed for %s: %s", rid, exc)
    return granted


def _scmp_objective(claimed: bool = False) -> dict[str, Any]:
    # Source field names from web app UTObjectivesFactory.createObjective.
    # The objective is served COMPLETED: an IN_PROGRESS objective with no
    # requirement/condition block makes FIFA 21's live objective evaluator
    # (which runs on squad edits) dereference a missing condition and crash
    # the game.  COMPLETED is display-only -- nothing is evaluated.
    return {
        "objectiveId": _SCMP_OBJECTIVE_ID,
        "id": _SCMP_OBJECTIVE_ID,
        "name": "Kick Off",
        "description": "Play a match in FUT.",
        "multiplier": 1,
        "priority": 1,
        "currentProgress": 1,
        "progress": 1,
        "target": 1,
        "targetValue": 1,
        "state": "REWARDED" if claimed else "COMPLETED",
        "takeMeThereLink": "",
        "isWeb": False,
        "awards": [_scmp_reward_award()],
    }


def _scmp_group(claimed: bool = False, with_objectives: bool = True) -> dict[str, Any]:
    # Source field names from web app UTObjectivesFactory.createGroup.
    # The details-response parser reads the objective array from key
    # "objectives"; campaign/active carries only the group summary (no
    # objectives array) so the row is not rendered twice.
    now = now_s()
    claimed_state = "REWARDED" if claimed else "COMPLETED"
    group = {
        "groupId": _SCMP_GROUP_ID,
        "id": _SCMP_GROUP_ID,
        "groupType": 0,
        "groupState": claimed_state,
        "state": claimed_state,
        "priority": 1,
        "title": "Welcome Objectives",
        "subTitle": "Get started in FUT",
        "startTime": now - 30 * 86_400,
        "endTime": now + 365 * 86_400,
        "visibilityTime": 0,
        "objectivesNumber": 1,
        "objectivesCount": 1,
        "objectivesCompleted": 1,
        "groupIdsToUnlock": [],
        "subGroupType": 0,
        "subGroupTypeAttribute": 0,
        "awardsList": [_scmp_reward_award()],
        "rewardOptionList": [],
    }
    if with_objectives:
        group["objectives"] = [_scmp_objective(claimed)]
    return group


def _scmp_category(claimed: bool = False) -> dict[str, Any]:
    # Source field names from web app UTObjectivesFactory.createCategory.
    # campaign/active carries group summaries only; the objective rows come
    # from the /scmp/active/category/{id}/details response.
    grp = [_scmp_group(claimed, with_objectives=False)]
    return {
        "categoryId": _SCMP_CATEGORY_ID,
        "id": _SCMP_CATEGORY_ID,
        "name": "Objectives",
        "priority": 1,
        "groupsList": grp,
        "groups": grp,
    }


def _scmp_campaign_json() -> dict[str, Any]:
    now = now_s()
    claimed = bool(STATE.get("scmp_reward_claimed", False))
    cats = [_scmp_category(claimed)]
    user_xp = int(STATE.get("scmp_user_xp", 0) or 0)
    levels = _scmp_season_levels()
    return {
        "campaignId": _SCMP_CAMPAIGN_ID,
        "id": _SCMP_CAMPAIGN_ID,
        "campaignName": "Local FUT Objectives",
        "name": "Local FUT Objectives",
        "title": "Local FUT Objectives",
        "campaignScore": user_xp,
        "userXp": user_xp,
        "userXpList": [],
        "startTime": now - 30 * 86_400,
        "endTime": now + 365 * 86_400,
        "rewardingTime": now + 365 * 86_400,
        "progressType": 0,
        "subType": 0,
        "needsAutoClaim": False,
        "needsWorldAutoclaim": False,
        "needsGroupsRefresh": False,
        "scmpCategoryProgressList": cats,
        "categoriesList": cats,
        "categories": cats,
        # Season Progress track.
        "levelsList": levels,
        "levels": levels,
    }


def _native_user() -> dict[str, Any]:
    # Match the *types* and nesting of the successful FIFA 21 response.  The
    # client stops issuing requests if fields such as bidTokens/reliability/
    # squadList have the wrong JSON type, even when the HTTP status is 200.
    club_player_items = [x for x in STATE.list_items("club") if str(x.get("itemType", "player")) == "player"]
    club_manager_items = [x for x in STATE.list_items("club") if str(x.get("itemType", "")) == "manager"]
    squad = _native_squad()
    squad_players = sum(1 for x in squad.get("players", []) if isinstance(x.get("itemData"), dict))
    total_players = len(club_player_items)
    loose_players = max(0, total_players - squad_players)
    actives = _active_club_items()
    # FIFA's startup decoder consumes saved-squad summaries from userInfo.
    # Supplying an empty array makes the onboarding squad screen render empty
    # even when the separate full squad payload contains populated itemData.
    squad_summaries = [{
        "id": int(saved.get("id") or saved.get("squadId") or 1),
        "squadName": str(saved.get("squadName") or saved.get("name") or "Local XI"),
        "rating": int(saved.get("rating", 0) or 0),
        "chemistry": int(saved.get("chemistry", 100) if saved.get("chemistry") is not None else 100),
        "formation": str(saved.get("formation") or "f442"),
        "squadType": saved.get("squadType") if saved.get("squadType") in (
            "REGULAR_SQUAD", "DREAM_SQUAD", "DRAFT_SQUAD"
        ) else "REGULAR_SQUAD",
    } for saved in STATE.list_squads()]
    stadium_data = _native_stadium_response()
    return {
        "pileClubPlayers": loose_players,
        "pileSquadPlayers": squad_players,
        "pileConsumables": 0,
        "pileManagers": len(club_manager_items),
        "pileClubItems": len(actives),
        "pileClubTotal": total_players + len(club_manager_items) + len(actives),
        "pilePack": len(STATE.list_items("unassigned")),
        "hasPlayerPicksPending": bool(STATE.pending_player_pick_tokens()),
        "kitCount": 2,
        "badgeCount": 1,
        "stadiumCount": 1,
        "ballCount": 1,
        "clubPlayers": total_players,
        "clubItemCount": len(actives),
        "clubCount": total_players + len(club_manager_items) + len(actives),
        "accountCreatedPlatformName": "pc",
        "personaId": int(CFG["persona_id"]),
        "clubName": str(CFG["club_name"]),
        "clubAbbr": str(CFG["club_abbr"]),
        "clubNameChangeAllowed": 0,
        "established": str(int(STATE.get("established", now_s()))),
        **_credits_payload(),
        "bidTokens": {"count": 0, "updateTime": 0},
        "divisionOffline": 1,
        "divisionOnline": 1,
        "won": 0,
        "draw": 0,
        "loss": 0,
        "trophies": 0,
        "purchased": False,
        "fifaPointsFromLastYear": 0,
        "fifaPointsTransferredStatus": 0,
        "reliability": {"reliability": 100, "matchUnfinishedTime": 0},
        # Mode availability is also cached from the startup user response.
        # Publishing the flags here prevents a valid hub from being displayed
        # under a stale, globally disabled Squad Battles controller state.
        "squadBattleEnabled": True,
        "squadBattleCouchPlayEnabled": True,
        "squadBattlesScore": 0,
        # My Stadium navigation reads its availability from the startup user
        # model, then obtains the same slot collection from GET /stadium.
        "enableMyStadium": True,
        "myStadium": stadium_data,
        "myStadiumConfigMode": 1,
        "myStadiumAdboardMode": 1,
        "myStadium_tier": stadium_data["lastUsedTierLevel"],
        "myStadium_slot": stadium_data["tiers"][0]["slots"],
        "myStadiumInfo": stadium_data,
        "actives": actives,
        "squadList": {"squad": squad_summaries},
    }


def _club_stats() -> dict[str, Any]:
    players = len([x for x in STATE.list_items("club") if str(x.get("itemType", "player")) == "player"])
    managers = len([x for x in STATE.list_items("club") if str(x.get("itemType", "")) == "manager"])
    # /club/stats/consumables hits this same generic handler (matched by the
    # /club/stats/* prefix below). It used to leave every consumables field
    # hardcoded at 0 regardless of what you actually own; fill them from real
    # owned consumables instead of inventing a different response shape for
    # that one path -- the shape below is the one already proven not to
    # crash (used successfully by /club/stats/staff moments earlier).
    owned_consumables = [
        item for item in STATE.list_items("club") + STATE.list_items("unassigned")
        if str(item.get("itemType", "")).lower() in ("consumable", "training", "development")
    ]
    consumable_totals = {"contract": 0, "fitness": 0, "healing": 0,
                         "playstyle": 0, "training": 0, "position": 0,
                         "managerLeagueModifier": 0}
    for item in owned_consumables:
        stack = int(item.get("stackCount", 1) or 1)
        consumable_type = int(item.get("consumableType", 3) or 3)
        consumable_totals["training"] += stack
        if consumable_type == 1:
            consumable_totals["contract"] += stack
        elif consumable_type == 2:
            consumable_totals["healing"] += stack
        elif consumable_type == 3:
            consumable_totals["position"] += stack
        elif consumable_type == 4:
            consumable_totals["playstyle"] += stack
        elif consumable_type == 5:
            consumable_totals["managerLeagueModifier"] += stack
        else:
            consumable_totals["fitness"] += stack
    consumables_owned = len(owned_consumables)
    base = {
        "playersEmployed": players,
        "rarePlayersEmployed": players,
        "bronzePlayersEmployed": 0,
        "silverPlayersEmployed": 0,
        "goldPlayersEmployed": players,
        "staffEmployed": managers,
        "kitsAvailable": 2,
        "badgesAvailable": 1,
        "stadiaOwned": 1,
        "ballsEarned": 1,
        "trophiesWon": 0,
        "consumables": consumables_owned,
        "kits": 2,
        "badges": 1,
        "stadia": 1,
        "balls": 1,
        "trophies": 0,
        "consumablesContract": consumable_totals["contract"],
        "consumablesFitness": consumable_totals["fitness"],
        "consumablesHealing": consumable_totals["healing"],
        "consumablesPlaystyle": consumable_totals["playstyle"],
        "consumablesTraining": consumable_totals["training"],
        "consumablesTrainingPlayerPlayStyle": 0,
        "consumablesTrainingGkPlayStyle": 0,
        "consumablesContractPlayer": consumable_totals["contract"],
        "consumablesContractManager": 0,
        "consumablesFitnessPlayer": consumable_totals["fitness"],
        "consumablesFitnessTeam": 0,
        "consumablesTrainingManager": 0,
        "consumablesTrainingPlayer": consumable_totals["training"],
        "consumablesTrainingGk": 0,
        "consumablesTrainingManagerLeagueModifier": consumable_totals["managerLeagueModifier"],
        "consumablesPosition": consumable_totals["position"],
        "consumablesFormationManager": 0,
        "staffHeadCoach": managers,
        "staffFitnessCoach": 0,
        "staffGKCoach": 0,
        "staffPhysio": 0,
        "trophiesOffline": 0,
        "trophiesOnline": 0,
        "trophiesFeaturedOffline": 0,
        "trophiesFeaturedOnline": 0,
        "trophiesSeasonOffline": 0,
        "trophiesSeasonOnline": 0,
    }
    stat_names = [
        ("FUT_MYCLUB_PLAYERS_EMPLOYED", players),
        ("FUT_MYCLUB_RARE_PLAYERS_EMPLOYED", players),
        ("FUT_MYCLUB_GOLD_PLAYERS_EMPLOYED", players),
        ("FUT_MYCLUB_SILVER_PLAYERS_EMPLOYED", 0),
        ("FUT_MYCLUB_BRONZE_PLAYERS_EMPLOYED", 0),
        ("FUT_MYCLUB_TROPHIES_WON", 0),
        ("FUT_MYCLUB_KITS_AVAILABLE", 2),
        ("FUT_MYCLUB_BADGES_AVAILABLE", 1),
        ("FUT_MYCLUB_STADIA_OWNED", 1),
        ("FUT_MYCLUB_BALLS_EARNED", 1),
        ("FUT_MYCLUB_STAFF_EMPLOYED", managers),
        ("players", players), ("playersEmployed", players),
        ("rarePlayers", players), ("rarePlayersEmployed", players),
        ("playersBronze", 0), ("playersSilver", 0), ("playersGold", players),
        ("staff", 0), ("staffEmployed", 0),
        ("kits", 2), ("kitsAvailable", 2),
        ("badges", 1), ("badgesAvailable", 1),
        ("stadia", 1), ("stadiaOwned", 1),
        ("balls", 1), ("ballsEarned", 1),
        ("trophies", 0), ("trophiesWon", 0),
        ("consumables", consumables_owned), ("clubPlayers", players), ("playerCount", players),
        ("rareQuantity", players), ("staffManager", 0),
        ("kit", 2), ("kitsHome", 1), ("kitsAway", 1),
        ("badge", 1), ("timesWon", 0), ("allofflinetrophy", 0),
        ("allonlinetrophy", 0), ("stadium", 1), ("ball", 1),
    ]
    base["stat"] = [
        {"type": name, "typeValue": value, "contextId": 0, "contextValue": 0}
        for name, value in stat_names
    ]
    return base


def _store_purchasegroups() -> dict[str, Any]:
    credits=STATE.credits()
    settings=load_pack_settings()
    packs=[]
    for pid_s,cfg0 in sorted(settings.get("packs",{}).items(),key=lambda kv:int(kv[0])):
        if not bool(cfg0.get("enabled",True)): continue
        pid=int(pid_s); cfg=dict(cfg0)
        name=str(cfg.get("name",f"Custom Pack {pid}"))
        price=max(0,int(cfg.get("price",0)))
        qty=max(1,min(30,int(cfg.get("size",12))))
        rare=max(0,min(qty,int(cfg.get("rare_slots",0))))
        gw=max(0.0,float(cfg.get("gold_weight",0) or 0))
        sw=max(0.0,float(cfg.get("silver_weight",0) or 0))
        bw=max(0.0,float(cfg.get("bronze_weight",0) or 0))
        total=gw+sw+bw
        if total<=0: gw,sw,bw,total=100.0,0.0,0.0,100.0
        gold=int(round(qty*gw/total)); silver=int(round(qty*sw/total)); bronze=max(0,qty-gold-silver)
        dominant=max((("gold",gw),("silver",sw),("bronze",bw)),key=lambda x:x[1])[0]
        requested_group=str(cfg.get("store_group",dominant) or dominant).lower()
        # These are the exact PurchaseDisplayGroup values used by the FUT
        # client.  "special" is rendered as the Promo Packs tab.  Keep the
        # old "promo" spelling as a migration alias for saved Pack Studio
        # configurations created before category metadata was supported.
        store_group={"promo":"special"}.get(requested_group,requested_group)
        if store_group not in {"bronze","silver","gold","special"}:
            store_group=dominant
        category_id={"bronze":1,"silver":2,"gold":3,"special":4}[store_group]
        bio = str(cfg.get("bio", "") or "")
        packs.append({
            "id":pid,
            "assetId":pid,
            "quantity":0,

            # FIFA 21 store presentation fields. Different PC builds reference
            # slightly different keys, so provide the same content under the
            # common aliases used by the store tile/header/detail panel.
            "name": name,
            "packName": name,
            "displayName": name,
            "title": name,

            # FIFA 21 uses this field for the visible label above the pack tile.
            "description": name,

            # Keep the longer text separate so it cannot blank/replace the title.
            "packDescription": bio,
            "longDescription": bio,
            "shortDescription": bio,
            "bio": bio,

            "label": name,
            "displayLabel": name,
            "storePackName": name,

            "finalPrice":price,"originalPrice":price,
            "currencies":[{"name":"coins","funds":price,"finalFunds":price},
                          {"name":"points","funds":0,"finalFunds":0}],
            "categoryList":{"categoryId":category_id,"orderInCategory":pid},
            "displayGroup":{"value":store_group,"priority":category_id},
            "purchaseGroup":store_group,
            "group":store_group,
            "state":"active","packType":dominant.upper(),"sortPriority":pid,
            "packContentInfo":{"itemQuantity":qty,"goldQuantity":gold,"silverQuantity":silver,
                               "bronzeQuantity":bronze,"rareQuantity":rare},
            "visible":True,"isPurchaseable":credits>=price,
        })

    # Native Draft does not read its admission price from the ordinary pack
    # definitions.  Its toll-booth view looks up one of these fixed store ids
    # after /squad/mode/draft/state reports that no run is active.
    for draft_entry_id in (20024, 20026, 20027):
        packs.append({
            "id": draft_entry_id,
            "assetId": draft_entry_id,
            "quantity": 0,
            "name": "FUT Draft Entry",
            "packName": "FUT Draft Entry",
            "displayName": "FUT Draft Entry",
            "title": "FUT Draft Entry",
            "description": "FUT Draft Entry",
            "packDescription": "Entry to a FUT Draft.",
            "longDescription": "Entry to a FUT Draft.",
            "shortDescription": "Entry to a FUT Draft.",
            "bio": "Entry to a FUT Draft.",
            "label": "FUT Draft Entry",
            "displayLabel": "FUT Draft Entry",
            "storePackName": "FUT Draft Entry",
            "finalPrice": 15000,
            "originalPrice": 15000,
            "currencies": [
                {"name": "coins", "funds": 15000, "finalFunds": 15000},
                {"name": "points", "funds": 300, "finalFunds": 300},
            ],
            "displayGroup": {"value": "draftentry", "priority": draft_entry_id},
            "purchaseGroup": "draftentry",
            "group": "draftentry",
            "state": "active",
            "packType": "DRAFTENTRY",
            "sortPriority": draft_entry_id,
            "packContentInfo": {
                "itemQuantity": 0,
                "goldQuantity": 0,
                "silverQuantity": 0,
                "bronzeQuantity": 0,
                "rareQuantity": 0,
            },
            "visible": True,
            "isPurchaseable": credits >= 15000,
        })

    # Reward packs must also be present in the catch-all response. Some FIFA
    # 21 screens discover My Packs here before requesting the dedicated group.
    owned_counts: dict[int, int] = {}
    for owned_pack_id in STATE.unopened_pack_ids():
        owned_counts[owned_pack_id] = owned_counts.get(owned_pack_id, 0) + 1
    packs.extend(_owned_reward_pack_json(pack_id, count)
                 for pack_id, count in sorted(owned_counts.items())
                 if get_pack_cfg(pack_id))
    return {"purchase":packs,"timestamp":now_s()}


def _owned_reward_pack_json(pack_id: int, quantity: int) -> dict[str, Any]:
    cfg = get_pack_cfg(pack_id)
    name = str(cfg.get("name", _PACK_NAMES.get(pack_id, f"Pack {pack_id}")))
    size = max(1, min(30, int(cfg.get("size", 12) or 12)))
    rare = max(0, min(size, int(cfg.get("rare_slots", 0) or 0)))
    gold_weight = float(cfg.get("gold_weight", 0) or 0)
    silver_weight = float(cfg.get("silver_weight", 0) or 0)
    bronze_weight = float(cfg.get("bronze_weight", 0) or 0)
    total_weight = gold_weight + silver_weight + bronze_weight
    if total_weight <= 0:
        gold_weight, silver_weight, bronze_weight, total_weight = 100.0, 0.0, 0.0, 100.0
    gold = int(round(size * gold_weight / total_weight))
    silver = int(round(size * silver_weight / total_weight))
    bronze = max(0, size - gold - silver)
    art = _pack_art_id(pack_id)
    return {
        "id": pack_id, "packId": pack_id, "assetId": art,
        "quantity": quantity, "count": quantity,
        "name": name, "packName": name, "displayName": name, "title": name,
        "description": name, "packDescription": name, "longDescription": name,
        "shortDescription": name, "bio": name, "label": name,
        "displayLabel": name, "storePackName": name,
        "packType": "CARDPACK", "packTypeId": pack_id,
        "packTier": 3, "tier": 3, "categoryId": 5,
        "numberItems": size, "numItems": size, "rare": rare,
        "displayGroup": {"value": "mypacks", "priority": 5},
        "displayGroupId": 5, "state": "active", "sortPriority": 0,
        "isReward": True, "isPurchased": True, "isOwned": True, "owned": True,
        "unopened": True, "isUnopened": True, "isPreOrder": True,
        "preOrder": True, "usePreOrder": True, "preOrderQuantity": quantity,
        "purchaseType": "PREORDER", "action": "OPEN", "isOpenable": True,
        "isFree": True, "isAvailableInStore": True,
        "isavailableinstore": True, "isPurchaseable": True, "visible": True,
        "finalPrice": 0, "originalPrice": 0, "price": 0, "coins": 0,
        "coinPrice": 0, "points": 0, "pointsPrice": 0, "cost": 0,
        "currencies": [
            {"name": "preorder", "funds": quantity, "finalFunds": quantity},
            {"name": "coins", "funds": 0, "finalFunds": 0},
            {"name": "points", "funds": 0, "finalFunds": 0},
        ],
        "packContentInfo": {
            "itemQuantity": size, "goldQuantity": gold,
            "silverQuantity": silver, "bronzeQuantity": bronze,
            "rareQuantity": rare,
        },
    }


def _store_mypacks() -> dict[str, Any]:
    counts: dict[int, int] = {}
    for pack_id in STATE.unopened_pack_ids():
        counts[pack_id] = counts.get(pack_id, 0) + 1
    packs = [_owned_reward_pack_json(pack_id, count)
             for pack_id, count in sorted(counts.items()) if get_pack_cfg(pack_id)]
    total = sum(counts.values())
    return {
        "purchase": packs, "packList": packs, "unopenedPacks": packs,
        "recoveredPacks": packs, "purchasedPacks": packs,
        "count": total, "total": total, "timestamp": now_s(),
    }


def _purchase_request_document(
    payload: Any,
    headers: dict[str, str],
    body: bytes,
    query: dict[str, list[str]],
) -> dict[str, Any]:
    """Decode the equivalent JSON and form-encoded Store purchase shapes.

    FIFA PC builds have used both JSON and form-encoded request bodies for this
    endpoint.  Keep every received field so the request is logged and a missing
    selector can fail safely instead of purchasing an arbitrary default pack.
    """
    if isinstance(payload, dict):
        request = dict(payload)
    else:
        request = {}

    if not request and body:
        content_type = headers.get("Content-Type", headers.get("content-type", "")).lower()
        if "application/x-www-form-urlencoded" in content_type:
            parsed = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            request = {key: values[-1] if len(values) == 1 else values for key, values in parsed.items()}

    for key, values in query.items():
        if key not in request and values:
            request[key] = values[-1]
    return request


def _purchase_pack_id(request: dict[str, Any]) -> int:
    """Return the catalog pack selector used by the PC Store request."""
    candidates = ("packId", "id", "packType", "serverId", "assetId")
    sources = [request]
    for key in ("purchase", "pack", "item"):
        nested = request.get(key)
        if isinstance(nested, dict):
            sources.append(nested)

    for source in sources:
        for key in candidates:
            value = source.get(key)
            if isinstance(value, list):
                value = value[-1] if value else None
            try:
                if value is not None and str(value).strip() != "":
                    return int(value)
            except (TypeError, ValueError):
                continue
    raise ValueError("purchase request did not include a numeric packId/id/packType/serverId/assetId")


def _next_purchase_transaction_id() -> int:
    """Allocate a monotonic local transaction id for Store purchase receipts."""
    previous = int(STATE.get("last_purchase_transaction_id", 0) or 0)
    transaction_id = max(previous + 1, now_ms())
    STATE.set("last_purchase_transaction_id", transaction_id)
    return transaction_id


def _pack_purchase_response(pack: dict[str, Any], transaction_id: int) -> dict[str, Any]:
    """Build the FUT purchase receipt plus the nested create-pack response."""
    items = [_native_pack_item(item) for item in pack.get("itemData", [])]
    pack_id = int(pack.get("purchasedPackId", pack.get("packId", 0)) or 0)
    duplicates = list(pack.get("duplicateItemIdList", []))
    create_pack_response = {
        "duplicateItemIdList": duplicates,
        "itemList": items,
        "numberItems": len(items),
        "purchasedPackId": pack_id,
        # Keep these aliases for the PC clients that read the outer shape.
        "itemData": items,
        "items": items,
        "packId": pack_id,
    }
    return {
        "packId": pack_id,
        "firstPartyStoreId": 0,
        "purchasePackType": "CARDPACK",
        "state": "PURCHASECOMPLETE",
        "transactionId": transaction_id,
        "useAuth": 0,
        "useCount": 1,
        "useTime": 0,
        "purchasedPackId": pack_id,
        "numberItems": len(items),
        "duplicateItemIdList": duplicates,
        "itemList": items,
        "itemData": items,
        "items": items,
        "createPackResponse": create_pack_response,
        **_credits_payload(),
    }


def route_local_auth(method: str, path: str, raw_query: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
    """Strict loopback-emulator contract; never log or exchange supplied credentials."""
    response_headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}

    def reject(error: str = "invalid_request") -> tuple[int, dict[str, str], bytes]:
        return 400, response_headers, json_bytes({"error": error})

    request_headers = {key.lower(): value for key, value in headers.items()}
    if request_headers.get("authorization"):
        return reject("invalid_client")

    if path == "/localfut/auth/bootstrap":
        if method != "GET" or raw_query or body:
            return reject()
        return 200, response_headers, json_bytes({
            "version": 1,
            "serverSourceSha256": SERVER_SOURCE_SHA256,
            "onboardingSourceSha256": ONBOARDING_SOURCE_SHA256,
            "authCode": LOCAL_AUTH_CODE,
            "clientIds": list(LOCAL_AUTH_CLIENT_IDS),
            "config": local_auth_config(),
        })

    if path == "/localfut/callback":
        if method != "GET" or body:
            return reject()
        return 200, response_headers, b""

    if path == "/connect/token":
        content_type = request_headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if method != "POST" or raw_query or len(body) > 4096 or content_type != "application/x-www-form-urlencoded":
            return reject()
        try:
            form = urllib.parse.parse_qs(
                body.decode("utf-8"), keep_blank_values=True, strict_parsing=True,
                errors="strict", max_num_fields=8,
            )
        except (UnicodeDecodeError, ValueError):
            return reject()
        allowed = {"grant_type", "code", "client_id", "client_secret", "redirect_uri"}
        if set(form) - allowed or any(len(values) != 1 for values in form.values()):
            return reject()
        if form.get("grant_type") != ["authorization_code"]:
            return reject("unsupported_grant_type")
        if form.get("client_id", [""])[0] not in LOCAL_AUTH_CLIENT_IDS or form.get("client_secret", [""])[0]:
            return reject("invalid_client")
        if form.get("code") != [LOCAL_AUTH_CODE]:
            return reject("invalid_grant")
        if "redirect_uri" in form and form["redirect_uri"] != [local_auth_config()["identityRedirectUri"]]:
            return reject()
        return 200, response_headers, json_bytes({
            "access_token": LOCAL_AUTH_ACCESS_TOKEN,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": LOCAL_AUTH_REFRESH_TOKEN,
        })

    if path == "/connect/tokeninfo":
        if method != "GET" or body or len(raw_query) > 4096:
            return reject()
        try:
            query = urllib.parse.parse_qs(
                raw_query, keep_blank_values=True, strict_parsing=True,
                errors="strict", max_num_fields=4,
            )
        except (UnicodeDecodeError, ValueError):
            return reject()
        if set(query) - {"access_token", "client_id"} or any(len(values) != 1 for values in query.values()):
            return reject()
        if query.get("access_token") != [LOCAL_AUTH_ACCESS_TOKEN]:
            return reject("invalid_token")
        client_id = query.get("client_id", [LOCAL_AUTH_SDK_CLIENT_ID])[0]
        if client_id not in LOCAL_AUTH_CLIENT_IDS:
            return reject("invalid_client")
        return 200, response_headers, json_bytes({
            "user_id": str(CFG["nucleus_id"]),
            "persona_id": str(CFG["persona_id"]),
            "client_id": client_id,
            "expires_in": 3600,
        })

    return reject()


# ===================== Squad Building Challenges (SBC) ======================
# The FIFA 21 client gates this screen on the `sbcEnabled` /
# `SquadBuildingChallengesEnabled` settings flags, then drives it entirely
# from the /ut/game/fifa21/sbs/* routes.  Wire contract recovered from the
# FIFA 21 web app (UTSquadBuildingChallengeDAO / UTSBCFactory):
#   GET  /sbs/sets                  -> {categories:[{categoryId,priority,name,sets:[set]}]}
#   GET  /sbs/sets/tag             -> {sets:[]}          (user favourites)
#   GET  /sbs/setId/{id}/challenges -> {challenges:[challenge]}
#   POST /sbs/challenge/{id}        -> {squad, playerRequirements}   (enter)
#   GET  /sbs/challenge/{id}/squad  -> {squad, playerRequirements}
#   PUT  /sbs/challenge/{id}/squad  -> 200                (save; body {players:[...]})
#   PUT  /sbs/challenge/{id}        -> submit; grant rewards on success
# The catalogue is the human-authored sbc.json in this folder; its abstract
# rule/reward vocabulary is translated here to the client's elgReq / awards
# contract.  The structure (sets -> challenges -> requirements -> rewards,
# per-challenge completion tracking, two-step save/submit) follows the FIFA 17
# reference server; none of its code is reused.

SBC_DB_PATH = ROOT / "sbc.json"
try:
    SBC_DB: dict[str, Any] = json.loads(SBC_DB_PATH.read_text(encoding="utf-8"))
except Exception as _sbc_exc:  # noqa: BLE001
    log.warning("SBC catalogue unavailable (%s); SBC screen will be empty", _sbc_exc)
    SBC_DB = {"tabs": [], "sets": []}

# The client keys everything by integer id; sbc.json uses stable strings.
_SBC_SET_INT: dict[str, int] = {}
_SBC_SET_BY_INT: dict[int, dict[str, Any]] = {}
_SBC_CHAL_BY_INT: dict[int, dict[str, Any]] = {}
for _sbc_i, _sbc_set in enumerate(SBC_DB.get("sets", [])):
    _sbc_set_int = 900000 + _sbc_i * 100
    _SBC_SET_INT[str(_sbc_set.get("id"))] = _sbc_set_int
    _SBC_SET_BY_INT[_sbc_set_int] = _sbc_set
    for _sbc_j, _sbc_chal in enumerate(_sbc_set.get("challenges", []), start=1):
        _sbc_chal_int = _sbc_set_int + _sbc_j
        _sbc_chal["_int"] = _sbc_chal_int
        _sbc_chal["_setInt"] = _sbc_set_int
        _sbc_chal["_setStrId"] = str(_sbc_set.get("id"))
        _SBC_CHAL_BY_INT[_sbc_chal_int] = _sbc_chal

# SBCEligibilityKey values.  The FIFA 21 web app groups elgReq entries by
# `eligibilitySlot` exactly as FIFA 17 did, and the key ids are shared.
_SBC_ELG_CHEM = 1
_SBC_ELG_PLAYER_COUNT = 2
_SBC_ELG_QUALITY = 3
_SBC_ELG_SAME_NATION = 4
_SBC_ELG_SAME_LEAGUE = 5
_SBC_ELG_SAME_CLUB = 6
_SBC_ELG_NATION_COUNT = 7
_SBC_ELG_LEAGUE_COUNT = 8
_SBC_ELG_NATION_ID = 10
_SBC_ELG_LEAGUE_ID = 11
_SBC_ELG_SCOPE = 13
_SBC_ELG_RARITY = 18
_SBC_ELG_TEAM_RATING = 19
_SBC_SCOPE_GTE = 0
_SBC_SCOPE_LTE = 1
_SBC_SCOPE_EXACT = 2

# Abstract sbc.json packType id -> a store pack id this server can actually open.
# sbc.json reward pack key -> a store pack id this server can actually open.
# The store id also decides the reward tile's art band via _pack_art_id().
_SBC_PACK_MAP: dict[Any, int] = {
    "bronze": 100, "silver": 200, "premium_silver_players": 201,
    "gold": 300, "premium_gold": 301, "gold_players": 304,
    "premium_gold_players": 305, "electrum_players": 306,
    "prime_electrum_players": 307, "prime_gold_players": 308,
    "prime_mixed_players": 309, "rare_players": 401,
    "rare_mixed_players": 406, "rare_gold": 400,
    "jumbo_rare_players": 402, "mega": 403, "rare_mega": 404,
    "ultimate": 405, "player_pick": 513,
    # legacy numeric keys from the old sbc.json
    1: 300, 2: 301, 3: 305, 4: 304, 5: 400, 6: 401, 7: 402,
    8: 403, 9: 404, 10: 405, 11: 401, 12: 402, 13: 403, 14: 405,
}


def _sbc_pack_id(abstract_type: Any, context: dict[str, Any] | None = None) -> int:
    if str(abstract_type).lower() == "player_pick" and isinstance(context, dict):
        set_id = str(context.get("_setStrId", ""))
        match = re.search(r"(?:^|_)(82|84|86|88)(?:\+|_|$)", set_id)
        if match:
            return {82: 513, 84: 514, 86: 515, 88: 516}[int(match.group(1))]
    return int(_SBC_PACK_MAP.get(abstract_type, _SBC_PACK_MAP.get(
        int(abstract_type) if str(abstract_type).isdigit() else 0, 300)))


def _sbc_completed() -> set[str]:
    raw = STATE.get("sbc_completed", [])
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def _sbc_mark_completed(challenge_str_id: str) -> None:
    done = _sbc_completed()
    done.add(str(challenge_str_id))
    STATE.set("sbc_completed", sorted(done))


def _sbc_normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _sbc_player_resource_id(entry: dict[str, Any]) -> int:
    """Resolve authored SBC player rewards to a real FIFA 21 card."""
    attrs = entry.get("attributes", {}) if isinstance(entry.get("attributes"), dict) else {}
    wanted_name = _sbc_normalize_name(attrs.get("name"))
    wanted_rating = int(attrs.get("rating", 0) or 0)
    wanted_position = str(attrs.get("position", "") or "").upper()
    candidates: list[tuple[int, int]] = []
    for resource_id, meta in PLAYER_DB.items():
        primary_name = _sbc_normalize_name(meta.get("name"))
        first_name = _sbc_normalize_name(meta.get("firstName"))
        last_name = _sbc_normalize_name(meta.get("lastName"))
        common_name = _sbc_normalize_name(meta.get("commonName"))
        full_name = first_name + last_name
        aliases = {primary_name, common_name, full_name}
        aliases.discard("")
        if wanted_name in aliases:
            identity_score = 0
        elif (primary_name and len(primary_name) >= 3
              and (primary_name in wanted_name or wanted_name in primary_name)):
            # Supports authored labels such as "R9 Ronaldo" while refusing a
            # match solely because an unrelated player shares a first name.
            identity_score = 1
        elif (common_name and len(common_name) >= 3
              and (common_name in wanted_name or wanted_name in common_name)):
            identity_score = 2
        else:
            continue
        rating = int(meta.get("rating", 0) or 0)
        position = str(meta.get("preferredPosition", "") or "").upper()
        score = identity_score * 1000 + abs(rating - wanted_rating) * 10
        if wanted_position and position != wanted_position:
            score += 8
        if int(meta.get("rareflag", 0) or 0) <= 1:
            score += 3
        candidates.append((score, int(resource_id)))
    if candidates:
        return min(candidates)[1]
    resource_id = int(attrs.get("resourceId", 0) or 0)
    return resource_id if resource_id in PLAYER_DB else 0


def _sbc_authored_player_item(item: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Overlay the SBC's advertised edition onto the closest native card."""
    attrs = entry.get("attributes", {}) if isinstance(entry.get("attributes"), dict) else {}
    authored_name = str(attrs.get("name", "") or "")
    if authored_name:
        for key in ("name", "playerName", "commonName", "commonname", "knownAs"):
            item[key] = authored_name
    authored_rating = int(attrs.get("rating", 0) or 0)
    if authored_rating:
        item["rating"] = authored_rating
    authored_position = str(attrs.get("position", "") or "").upper()
    if authored_position:
        item["preferredPosition"] = authored_position
    item["untradeable"] = True
    item["untradeableCount"] = max(1, int(item.get("untradeableCount", 0) or 0))
    return item


def _sbc_award_json(
    entry: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """One reward in the shape UTRewardFactory.createRewardFromSbcData reads:
    {halId, count, isUntradeable, type, value, itemData?}."""
    kind = str(entry.get("type", "")).upper()
    attrs = entry.get("attributes", {}) if isinstance(entry.get("attributes"), dict) else {}
    value = int(entry.get("value", attrs.get("value", 0)) or 0)
    if kind == "PACK":
        pid = _sbc_pack_id(attrs.get("pack", attrs.get("type", 1)), context)
        pack_cfg = get_pack_cfg(pid)
        if pack_cfg.get("player_pick_count"):
            min_rating = int(pack_cfg.get("min_rating", 0) or 0)
            choice_count = int(pack_cfg.get("player_pick_count", 4) or 4)
            name = f"{min_rating}+ Player Pick"
            # The literal "pick 1 of N" flow (a misc unlock token + a
            # redeem/select round trip) crashed the client on every
            # confirmation regardless of the response content -- even a bare
            # {"success": true}, which only makes sense if the custom token
            # item type itself is unrecognised client-side. create_pack now
            # grants one random qualifying player directly for these pack
            # ids instead; this is just the SBC tile's display name for that
            # same reward.
            award = _pack_award(pid, int(attrs.get("count", 1) or 1))
            award.update({
                "name": name, "packName": name, "packs": name,
                "AWD_PACKS_STRING": name, "packType": "PLAYER_PICK",
                "isPlayerPick": True, "choiceCount": choice_count,
                "minRating": min_rating,
            })
            return award
        award = _pack_award(pid, int(attrs.get("count", 1) or 1))
        if attrs.get("name"):
            award["name"] = award["packName"] = award["packs"] = str(attrs["name"])
            award["AWD_PACKS_STRING"] = str(attrs["name"])
        return award
    if kind == "COINS":
        return {"halId": 0, "assetId": 0, "count": 1, "isUntradeable": True,
                "type": "coins", "value": value}
    if kind == "XP":
        return {"halId": 0, "assetId": 0, "count": 1, "isUntradeable": True,
                "type": "xp", "value": value}
    if kind == "PLAYER":
        rid = _sbc_player_resource_id(entry)
        name = str(attrs.get("name", player_meta(rid).get("name", "")) or "")
        item = (_native_player_item(_sbc_authored_player_item(
            STATE.build_player_item(rid, 0, "unassigned"), entry)) if rid else {})
        return {"halId": rid, "assetId": rid, "count": 1, "isUntradeable": True,
                "type": "item", "awardType": "item", "value": rid,
                "isPack": False, "isItem": True,
                "name": name, "items": name, "itemsAmount": 1,
                "itemData": item, "rating": int(attrs.get("rating", 0) or 0),
                "position": str(attrs.get("position", "") or "")}
    return None


def _sbc_awards(challenge_or_set: dict[str, Any]) -> list[dict[str, Any]]:
    entries = (challenge_or_set.get("rewards", {}) or {}).get("entries", [])
    out: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        award = _sbc_award_json(entry, challenge_or_set)
        if award:
            out.append(award)
    return out


def _sbc_visual_fields(awards: list[dict[str, Any]], completed: bool) -> dict[str, Any]:
    """The flat reward block the native SBC tiles draw themselves from.

    SBCRewards.big reads `coins`, `packs`, `packsAmount`, `packAssetID`,
    `items`, `itemData` and `isCompleted` off the set/challenge itself -- not
    the nested `awards` array the web app uses.  packAssetID is a
    comma-separated list of EPackAssetID art bands (LoadPackAssetId splits on
    ",") from which SetPackAssetForBiggestPrize picks the highest.
    """
    coins = sum(int(a.get("value", 0) or 0) for a in awards if a.get("type") == "coins")
    packs = [a for a in awards if a.get("type") == "pack"]
    items = [a for a in awards if a.get("type") in ("item", "player")]
    art_ids = [str(a.get("packAssetId", _PACK_ART_GOLD)) for a in packs]
    item_data = [a["itemData"] for a in items
                 if isinstance(a.get("itemData"), dict) and a["itemData"]]
    return {
        "coins": coins,
        "packs": ", ".join(str(a.get("name", "Pack")) for a in packs),
        "packsAmount": sum(int(a.get("count", 1) or 1) for a in packs),
        "packAssetID": ",".join(art_ids),
        "packAssetId": int(art_ids[0]) if art_ids else 0,
        "items": ", ".join(str(a.get("name", "")) for a in items),
        "itemsAmount": len(items),
        "itemData": item_data,
        "isCompleted": bool(completed),
    }


# FIFA rareflag values used as PLAYER_RARITY eligibility values.
_SBC_RARITY_RARE = 1     # any rare (gold/silver/bronze rare)
_SBC_RARITY_TOTW = 3     # Team of the Week / in-form


def _sbc_elg_req(challenge: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate sbc.json requirement rules to the client's elgReq list.

    The client (SquadChallenge / futSbcSquads) groups elgReq entries by
    `eligibilitySlot`; one slot carries the rule key/value plus a SCOPE
    key/value, and -- for the per-player rules PLAYER_RARITY / PLAYER_QUALITY
    -- a PLAYER_COUNT key/value that says how many players must match.  This
    is the same shape FIFA 17 used (Aurora17 SbcCatalog.EligibilityRequirements)
    and the FIFA 21 eligibility-key numbers are identical:
      1 chem  2 playerCount  3 quality  4 sameNation  5 sameLeague
      7 nationCount  8 leagueCount  13 SCOPE  18 rarity  19 teamRating
    SCOPE: 0 >=, 1 <=, 2 ==.
    """
    # Player-pick upgrades intentionally accept any complete XI. FIFA already
    # renders its built-in exact squad-size line, so no extra eligibility rows
    # should advertise rating, chemistry, rarity, league, or nation gates.
    if _sbc_pack_id("player_pick", challenge) in (513, 514, 515, 516) and any(
        str(entry.get("attributes", {}).get("pack", "")).lower() == "player_pick"
        for entry in (challenge.get("rewards", {}) or {}).get("entries", [])
        if isinstance(entry, dict) and isinstance(entry.get("attributes"), dict)
    ):
        return []
    rules = (challenge.get("requirements", {}) or {}).get("rules", [])
    out: list[dict[str, Any]] = []
    slot = 0

    def add(key: int, val: int, scope: int, count: int = 0) -> None:
        nonlocal slot
        slot += 1
        out.append({"eligibilityKey": key, "eligibilitySlot": slot, "eligibilityValue": int(val)})
        if count > 0:
            out.append({"eligibilityKey": _SBC_ELG_PLAYER_COUNT,
                        "eligibilitySlot": slot, "eligibilityValue": int(count)})
        out.append({"eligibilityKey": _SBC_ELG_SCOPE,
                    "eligibilitySlot": slot, "eligibilityValue": scope})

    # No PLAYER_COUNT-only slot: the client already shows a built-in
    # "Number of players in the Squad: 11 (11)" line for squad size.
    for rule in rules if isinstance(rules, list) else []:
        rtype = str(rule.get("type", "")).upper()
        a = rule.get("attributes", {}) if isinstance(rule.get("attributes"), dict) else {}
        mn = int(a.get("min", 0) or 0)
        mx = int(a.get("max", 0) or 0)
        ex = a.get("exact")
        if rtype == "CHEMISTRY" and mn > 0:
            add(_SBC_ELG_CHEM, mn, _SBC_SCOPE_GTE)
        elif rtype == "RATING" and mn > 0:
            add(_SBC_ELG_TEAM_RATING, mn, _SBC_SCOPE_GTE)
        elif rtype == "RARE" and mn > 0:
            add(_SBC_ELG_RARITY, _SBC_RARITY_RARE, _SBC_SCOPE_GTE, count=mn)
        elif rtype == "TOTW" and mn > 0:
            add(_SBC_ELG_RARITY, _SBC_RARITY_TOTW, _SBC_SCOPE_GTE, count=mn)
        elif rtype == "NATIONS":
            # count of distinct nations in the squad
            if ex is not None and 1 <= int(ex) <= 11:
                add(_SBC_ELG_NATION_COUNT, int(ex), _SBC_SCOPE_EXACT)
            elif mn > 0:
                add(_SBC_ELG_NATION_COUNT, mn, _SBC_SCOPE_GTE)
        elif rtype == "LEAGUES":
            if ex is not None and 1 <= int(ex) <= 11:
                add(_SBC_ELG_LEAGUE_COUNT, int(ex), _SBC_SCOPE_EXACT)
            elif mn > 0:
                add(_SBC_ELG_LEAGUE_COUNT, mn, _SBC_SCOPE_GTE)
        elif rtype == "SAME_NATION" and mx > 0:
            add(_SBC_ELG_SAME_NATION, mx, _SBC_SCOPE_LTE)
        elif rtype == "SAME_LEAGUE" and mx > 0:
            add(_SBC_ELG_SAME_LEAGUE, mx, _SBC_SCOPE_LTE)
        elif rtype == "SAME_CLUB" and mx > 0:
            add(_SBC_ELG_SAME_CLUB, mx, _SBC_SCOPE_LTE)
        elif rtype == "QUALITY":
            q = {"bronze": 1, "silver": 2, "gold": 3}.get(str(a.get("value", "")).lower(), 0)
            if q:
                add(_SBC_ELG_QUALITY, q, _SBC_SCOPE_EXACT, count=11)
        # AGE: no stable FIFA 21 eligibility key; carried in the description.
    return out


def _sbc_describe(challenge: dict[str, Any]) -> str:
    """Human-readable requirement summary, used as the challenge bio (sbc.json
    challenges carry no description of their own)."""
    if any(
        str(entry.get("attributes", {}).get("pack", "")).lower() == "player_pick"
        for entry in (challenge.get("rewards", {}) or {}).get("entries", [])
        if isinstance(entry, dict) and isinstance(entry.get("attributes"), dict)
    ):
        return "Number of players in the Squad: Exactly 11"
    parts: list[str] = []
    for rule in (challenge.get("requirements", {}) or {}).get("rules", []) or []:
        rtype = str(rule.get("type", "")).upper()
        a = rule.get("attributes", {}) if isinstance(rule.get("attributes"), dict) else {}
        mn, mx, ex = int(a.get("min", 0) or 0), int(a.get("max", 0) or 0), a.get("exact")
        if rtype == "RATING" and mn > 0:
            parts.append(f"Min Squad Rating: {mn}")
        elif rtype == "CHEMISTRY" and mn > 0:
            parts.append(f"Min Squad Chemistry: {mn}")
        elif rtype == "RARE" and mn > 0:
            parts.append(f"Rare players: Min {mn}")
        elif rtype == "TOTW" and mn > 0:
            parts.append(f"Team of the Week players: Min {mn}")
        elif rtype == "NATIONS":
            if ex is not None and 1 <= int(ex) <= 11:
                parts.append(f"Nationalities in Squad: Exactly {ex}")
            elif mn > 0:
                parts.append(f"Nationalities in Squad: Min {mn}")
        elif rtype == "LEAGUES":
            if ex is not None and 1 <= int(ex) <= 11:
                parts.append(f"Leagues in Squad: Exactly {ex}")
            elif mn > 0:
                parts.append(f"Leagues in Squad: Min {mn}")
        elif rtype == "SAME_NATION" and mx > 0:
            parts.append(f"Max players from same Nation: {mx}")
        elif rtype == "SAME_LEAGUE" and mx > 0:
            parts.append(f"Max players from same League: {mx}")
        elif rtype == "SAME_CLUB" and mx > 0:
            parts.append(f"Max players from same Club: {mx}")
        elif rtype == "QUALITY" and str(a.get("value", "")):
            parts.append(f"Player Quality: {str(a['value']).title()} only")
        elif rtype == "AGE" and mx > 0:
            parts.append(f"Player Age: Max {mx}")
    return "  |  ".join(parts) if parts else "Submit an 11-player squad."


def _sbc_set_description(set_obj: dict[str, Any]) -> str:
    """sbc.json set descriptions are frequently null -- fall back to a line
    built from the set name so the SBC hub never shows the literal 'None'."""
    desc = set_obj.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc
    name = str(set_obj.get("name", "")).strip()
    n = len(set_obj.get("challenges", []) or [])
    plural = "challenge" if n == 1 else "challenges"
    return f"Complete the {name} SBC ({n} {plural})." if name else f"Complete this SBC ({n} {plural})."


def _sbc_required_players(challenge: dict[str, Any]) -> int:
    """The exact squad size an SBC challenge wants.  FIFA 21 SBC squads are a
    full 11 field players; the sbc.json PLAYERS rule's `max` (never its `min`,
    which is only a lower bound for the tutorial) is the real target."""
    for rule in (challenge.get("requirements", {}) or {}).get("rules", []) or []:
        if str(rule.get("type", "")).upper() == "PLAYERS":
            a = rule.get("attributes", {}) or {}
            val = int(a.get("max", a.get("exact", 11)) or 11)
            return val if 1 <= val <= 11 else 11
    return 11


# Back-compat alias for the requirement encoder.
_sbc_min_players = _sbc_required_players


def _sbc_set_json(set_obj: dict[str, Any]) -> dict[str, Any]:
    set_int = _SBC_SET_INT[str(set_obj.get("id"))]
    challenges = set_obj.get("challenges", []) or []
    done = _sbc_completed()
    completed = sum(1 for c in challenges if str(c.get("id")) in done)
    set_done = challenges and completed == len(challenges)
    img = 1000000 + (set_int - 900000)
    # Last challenge's reward doubles as the set tile's reward preview.
    tile_awards = _sbc_awards(challenges[-1]) if challenges else []
    return {
        **_sbc_visual_fields(tile_awards, bool(set_done)),
        "setId": set_int, "id": set_int,
        "name": str(set_obj.get("name", "")),
        "description": _sbc_set_description(set_obj),
        "priority": int(set_obj.get("tabId", 0) or 0) * 100 + 1,
        "hidden": len(challenges) == 1,
        "repeatable": bool(set_obj.get("repeatable", False)),
        "challengesCount": len(challenges),
        "challengesCompletedCount": completed,
        "timesCompleted": 1 if set_done else 0,
        "setImageId": img, "trophyId": img, "assetId": img,
        "rewardPreviewImageId": "",
        "taggedByUser": False, "taggedByProduction": False,
        "endTime": 0, "startTime": 0, "tutorial": False,
        "awards": tile_awards,
    }


def _sbc_challenge_json(challenge: dict[str, Any]) -> dict[str, Any]:
    cid = int(challenge["_int"])
    done = str(challenge.get("id")) in _sbc_completed()
    img = 1000000 + (int(challenge["_setInt"]) - 900000)
    awards = _sbc_awards(challenge)
    return {
        **_sbc_visual_fields(awards, done),
        "challengeId": cid, "id": cid, "setId": int(challenge["_setInt"]),
        "name": str(challenge.get("name", "")),
        "description": _sbc_describe(challenge),
        "formation": "f442",
        "priority": int(challenge.get("order", 1) or 1),
        "repeatable": False,
        "status": "COMPLETED" if done else "NOT_STARTED",
        "timesCompleted": 1 if done else 0,
        "type": 0,
        "challengeImageId": 0, "trophyId": img,
        "endTime": 0, "startTime": 0,
        "elgOperation": 0,
        "elgReq": _sbc_elg_req(challenge),
        "awards": _sbc_awards(challenge),
    }


def _sbc_sets_response() -> dict[str, Any]:
    tabs = {int(t.get("id")): str(t.get("name", f"Tab {t.get('id')}"))
            for t in SBC_DB.get("tabs", []) if isinstance(t, dict)}
    by_tab: dict[int, list[dict[str, Any]]] = {}
    for set_obj in SBC_DB.get("sets", []):
        tab = int(set_obj.get("tabId", 0) or 0)
        by_tab.setdefault(tab, []).append(_sbc_set_json(set_obj))
    categories = []
    for tab_id in sorted(by_tab):
        categories.append({
            "categoryId": tab_id,
            "priority": tab_id,
            "name": tabs.get(tab_id, f"Tab {tab_id}"),
            "sets": by_tab[tab_id],
        })
    return {"categories": categories}


def _sbc_challenges_response(set_int: int) -> dict[str, Any]:
    set_obj = _SBC_SET_BY_INT.get(set_int)
    if not set_obj:
        return {"challenges": []}
    return {"challenges": [_sbc_challenge_json(c) for c in set_obj.get("challenges", [])]}


def _sbc_empty_squad(challenge_int: int) -> dict[str, Any]:
    """Blank SBC squad in the shape generateSBCSquadConstructorOptions reads.

    `playerRequirements` MUST be a full 11-element array: that builder does
    ``c = i[n]; c.playerType`` for every field slot with no guard, so a short
    (or empty) array throws and the squad silently fails to build -- which is
    why placed players were not being counted.  playerType 0 = NONE (a plain
    slot, not a locked brick).
    """
    return {
        "squad": {
            "id": challenge_int,
            "squadId": challenge_int,
            "challengeId": challenge_int,
            "formation": "f442",
            "squadType": "SBC_SQUAD",
            "players": [],
            "rating": 0, "starRating": 0, "chemistry": 0,
            "manager": [], "valid": False,
        },
        "playerRequirements": [
            {"index": i, "playerType": 0, "elgReq": []} for i in range(11)
        ],
    }


def _sbc_grant_rewards(challenge: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply exact rewards, keeping pack awards unopened in My Packs."""
    granted: list[dict[str, Any]] = []
    for entry in (challenge.get("rewards", {}) or {}).get("entries", []) or []:
        kind = str(entry.get("type", "")).upper()
        attrs = entry.get("attributes", {}) if isinstance(entry.get("attributes"), dict) else {}
        value = int(entry.get("value", attrs.get("value", 0)) or 0)
        try:
            if kind == "COINS" and value:
                STATE.add_credits(value)
            elif kind == "XP" and value:
                STATE.set("scmp_user_xp", int(STATE.get("scmp_user_xp", 0) or 0) + value)
            elif kind == "PLAYER":
                rid = _sbc_player_resource_id(entry)
                if rid:
                    item = _sbc_authored_player_item(
                        STATE.make_player_item(rid, "unassigned"), entry)
                    with STATE.lock:
                        STATE.conn.execute("UPDATE items SET data=? WHERE id=?",
                                           (json.dumps(item), int(item["id"])))
                        STATE.conn.commit()
                    granted.append(item)
            elif kind == "PACK":
                key = attrs.get("pack", attrs.get("type", "gold"))
                pack_id = _sbc_pack_id(key, challenge)
                for _ in range(max(1, int(attrs.get("count", 1) or 1))):
                    # FIFA's native player-pick path is also delivered as an
                    # unopened pack. Opening it yields the subtype-237 token,
                    # which then launches the 1-of-N selection screen.
                    STATE.add_unopened_pack(pack_id, "sbc")
                    granted.append({"itemType": "pack", "packId": pack_id})
        except Exception as exc:  # noqa: BLE001
            log.warning("SBC reward grant failed (%s): %s", kind, exc)
    return granted


def _sbc_submit(challenge_int: int) -> tuple[int, dict[str, Any]]:
    challenge = _SBC_CHAL_BY_INT.get(challenge_int)
    if not challenge:
        return 404, {"success": False, "status": "INVALID",
                     "message": "Unknown challenge."}
    str_id = str(challenge.get("id"))
    already = str_id in _sbc_completed()
    # Guard: the previous PUT /squad recorded how many players were placed.
    # The client should never submit a short squad, but if it does, reject
    # rather than pay out a free reward.
    need = _sbc_required_players(challenge)
    placed = int((STATE.get("sbc_saved_counts", {}) or {}).get(str(challenge_int), 0) or 0)
    if not already and placed and placed < need:
        return 200, {
            "id": challenge_int, "challengeId": challenge_int, "setId": int(challenge["_setInt"]),
            "success": False, "valid": False, "completed": False, "status": "INVALID",
            "message": f"Squad needs {need} players ({placed} placed).",
        }
    granted = [] if already else _sbc_grant_rewards(challenge)
    if not already:
        _sbc_mark_completed(str_id)
    set_int = int(challenge["_setInt"])
    set_obj = _SBC_SET_BY_INT.get(set_int, {})
    done = _sbc_completed()
    set_done = all(str(c.get("id")) in done for c in set_obj.get("challenges", []))
    if not already and set_done:
        granted.extend(_sbc_grant_rewards(set_obj))
    awards = _sbc_awards(challenge)
    body = {
        "id": challenge_int, "challengeId": challenge_int, "setId": set_int,
        "success": True, "valid": True, "completed": True, "status": "COMPLETED",
        "timesCompleted": 1,
        "grantedChallengeAwards": awards,
        "grantedSetAwards": _sbc_awards(set_obj) if set_done else [],
        "awards": awards, "rewards": awards,
        "credits": STATE.credits(),
        "grantedItemsCount": len(granted),
        "preOrderPacks": len(STATE.unopened_pack_ids()),
        "recoveredPacks": 0,
        "unopenedPacks": len(STATE.unopened_pack_ids()),
    }
    return 200, body


def _player_pick_token_id(path: str, payload: Any) -> int:
    pending = STATE.pending_player_pick_tokens()
    pending_ids = {int(item.get("id", 0) or 0) for item in pending}
    for match in reversed(re.findall(r"/(\d+)(?:/|$)", path)):
        if int(match) in pending_ids:
            return int(match)
    if isinstance(payload, dict):
        for key in ("playerPickItemId", "playerPickId", "tokenId", "unlockableItemId", "itemId", "id"):
            try:
                candidate = int(payload.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if candidate in pending_ids:
                return candidate
    return next(iter(pending_ids)) if len(pending_ids) == 1 else 0


def _player_pick_selection(payload: Any) -> tuple[bool, Any]:
    """Read all FIFA 21 console/web player-pick confirmation shapes."""
    if not isinstance(payload, dict):
        return False, None
    for key in ("selectedItemId", "selectedPlayerId", "choiceId", "selectedResourceId",
                "choiceIndex", "globalChoiceIndex", "selectedItemPos"):
        if key in payload:
            return True, payload.get(key)
    for key in ("selectedItem", "selection", "selectedPick", "choice"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            for nested_key in ("id", "itemId", "resourceId", "assetId", "index"):
                if nested_key in nested:
                    return True, nested.get(nested_key)
        elif nested is not None:
            return True, nested
    data = payload.get("itemData")
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        for key in ("selectedItemId", "id", "itemId", "resourceId", "assetId", "index"):
            if key in data:
                return True, data.get(key)
    selected_picks = payload.get("selectedPicks")
    if isinstance(selected_picks, list) and selected_picks:
        first = selected_picks[0]
        if isinstance(first, dict):
            for key in ("selectedItemId", "id", "itemId", "resourceId", "assetId", "index"):
                if key in first:
                    return True, first.get(key)
            return _player_pick_selection(first)
        return True, first
    return False, None


def _player_pick_redeem_response(token_id: int) -> dict[str, Any]:
    choices = [_native_pack_item(item) for item in STATE.redeem_player_pick(token_id)]
    group = {"itemId": token_id, "playerPickItemId": token_id,
             "itemData": choices, "choices": choices}
    return {
        "itemIds": [token_id], "playerPicks": choices, "playerPickData": [group],
        "items": choices, "itemData": choices, "choiceData": choices,
        "availablePicks": 1,
    }


def _player_pick_select_response(token_id: int, selection: Any) -> dict[str, Any]:
    """Confirm a FIFA 21 pick and return its item at the JSON root.

    CardsDLL's FutSelectPlayerPickItemServerCall response parser passes the
    response root straight to the generic item deserializer and immediately
    dereferences the resulting item.  An array or an ``itemData`` envelope is
    therefore not a valid FIFA 21 PC response for this call.
    """
    granted = STATE.select_player_pick(token_id, selection)
    return _native_pack_item(granted)


def route_fut(method: str, raw_path: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
    # ProtoHttp sends the POW auth path with a double leading slash in the
    # successful FIFA 21 trace. urlsplit("//pow/auth") would otherwise treat
    # "pow" as a network location and lose the /pow prefix.
    # Local auth URLs use the same slash-terminated base convention.
    if raw_path.startswith(("//pow/", "//connect/", "//localfut/")):
        raw_path = "/" + raw_path.lstrip("/")
    parsed = urllib.parse.urlsplit(raw_path)
    path = "/" + parsed.path.lstrip("/")
    low = path.lower().rstrip("/") or "/"
    if low in LOCAL_AUTH_PATHS:
        return route_local_auth(method, low, parsed.query, headers, body)
    query = urllib.parse.parse_qs(parsed.query)
    payload = safe_json(body)
    if "playerpick" in low or low == "/ut/game/fifa21/item/nontargeted":
        log.warning(
            "PLAYER-PICK-SCOUT REQUEST method=%s path=%s query=%s body=%s",
            method, raw_path, query, body[:2000],
        )
    if low == "/localfut/test/status":
        if not TEST_MODE or method != "GET":
            return 404, {}, json_bytes({"reason": "Not a test runtime"})
        return 200, {}, json_bytes({"version": 1, "testMode": True,
                                    "runtimeRoot": str(RUNTIME_ROOT.resolve()), "dbPath": str(DB_PATH.resolve())})

    if low == "/localfut/customisation/grant-all":
        if method != "POST":
            return 405, {"Allow": "POST"}, json_bytes({"reason": "POST required"})
        result = grant_all_customisation_assets()
        log.info("Granted local customisation assets: %s", json.dumps(result, sort_keys=True))
        return 200, {"Cache-Control": "no-store"}, json_bytes(result)

    # STORE-SCOUT v27: capture the exact endpoints FIFA 21 actually uses for
    # store catalog/title/description data. This is diagnostic only and does
    # not change the working pack/coin/item behavior.
    if any(token in low for token in ("store", "pack", "purchase", "loc")):
        log.warning(
            "STORE-SCOUT REQUEST method=%s path=%s query=%s accept=%s contentType=%s body=%s",
            method,
            raw_path,
            query,
            headers.get("Accept", headers.get("accept", "")),
            headers.get("Content-Type", headers.get("content-type", "")),
            body[:1000],
        )

    if "draft" in low:
        log.warning(
            "DRAFT-SCOUT REQUEST method=%s path=%s query=%s contentType=%s body=%s",
            method,
            raw_path,
            query,
            headers.get("Content-Type", headers.get("content-type", "")),
            body[:2000],
        )

    if ("squadbattle" in low or "squad/battle" in low or "squad_battle" in low or "sqbt" in low):
        log.warning(
            "SQUAD-BATTLES-SCOUT REQUEST method=%s path=%s query=%s contentType=%s body=%s",
            method,
            raw_path,
            query,
            headers.get("Content-Type", headers.get("content-type", "")),
            body[:2000],
        )

    # SCMP-SCOUT: capture the FIFA 21 Objectives / Seasonal Campaign endpoints.
    if any(token in low for token in ("scmp", "objective", "campaign", "milestone")):
        log.warning(
            "SCMP-SCOUT REQUEST method=%s path=%s query=%s contentType=%s body=%s",
            method, raw_path, query,
            headers.get("Content-Type", headers.get("content-type", "")),
            body[:2000],
        )

    # SEASONAL-SCOUT: capture the exact endpoints FIFA 21 uses for Division
    # Rivals and FUT Champions. Both modes are driven by EA's Seasonal
    # framework. This is diagnostic only; it does not build a response.
    if any(token in low for token in (
        "seasonal", "rivals", "champion", "champs", "weekendleague",
        "weekend_league", "/wl/", "seasonalplay", "competition",
    )):
        log.warning(
            "SEASONAL-SCOUT REQUEST method=%s path=%s query=%s accept=%s contentType=%s headers=%s body=%s",
            method,
            raw_path,
            query,
            headers.get("Accept", headers.get("accept", "")),
            headers.get("Content-Type", headers.get("content-type", "")),
            {k: v for k, v in headers.items()
             if k.lower() in ("x-ut-sid", "x-pow-sid", "user-agent", "referer")},
            body[:2000],
        )

    # ---- POW / EASFC bootstrap --------------------------------------------
    if low == "/pow/auth":
        sid = _pow_sid()
        return 200, {"X-POW-SID": sid}, json_bytes({
            "sid": sid,
            "serverTime": now_s(),
            "lastOnlineTime": now_s(),
        })

    if low.startswith("/pow/"):
        if low == "/pow/bank/user/account":
            return 200, {}, json_bytes({
                "currencies": [{
                    "currency": "pow_funds",
                    "funds": 0,
                    "fundsCapInfo": [
                        {"period": "daily", "fundsEarned": 0},
                        {"period": "weekly", "fundsEarned": 0},
                    ],
                }]
            })
        if low == "/pow/store/game/fifa21/catalog/list":
            return 200, {}, json_bytes({
                "catalogs": [
                    {"catalogId": 0, "name": "FUT Store"},
                    {"catalogId": 1, "name": "FIFAPoints"},
                ]
            })
        if "/catalog/0/item/list" in low:
            return 200, {}, json_bytes({"items": [], "endOfList": True})
        return 200, {}, json_bytes({})

    # ---- FUT authentication -----------------------------------------------
    if low == "/ut/auth":
        if method == "DELETE":
            return 200, {}, json_bytes({})
        sid = _ut_sid()
        return 200, {"X-UT-SID": sid}, json_bytes({"sid": sid, "protocol": "http"})

    # ---- FUT Draft --------------------------------------------------------
    if low == "/ut/game/fifa21/squad/mode/draft/state" and method == "GET":
        mode = str(query.get("mode", ["SINGLE_PLAYER"])[0] or "SINGLE_PLAYER").upper()
        state_payload = _draft_state_payload(_draft_run())
        log.warning("DRAFT-SCOUT STATE_RESPONSE mode=%s payload=%s",
                    mode, json.dumps(state_payload, separators=(",", ":")))
        return 200, {"Cache-Control": "no-store"}, json_bytes(state_payload)

    draft_purchase = re.fullmatch(r"/ut/game/fifa21/purchase/mode/(\d+)/draft", low)
    if draft_purchase and method == "POST":
        with _draft_lock:
            run = _draft_run()
            resumed = run is not None
            currency = str(payload.get("currency", "COINS") if isinstance(payload, dict) else "COINS").upper()
            if run is None:
                if currency in ("COINS", "CREDITS"):
                    if STATE.credits() < 15000:
                        return 403, {}, json_bytes({"reason": "insufficientCoins", **_credits_payload()})
                    STATE.add_credits(-15000)
                run = _draft_new_run("SINGLE_PLAYER" if draft_purchase.group(1) != "0" else "ONLINE")
                _draft_save(run)
            response = [{
                "COINS": STATE.credits(),
                "POINTS": 0,
                "DRAFT_TOKEN": 0,
            }]
            log.warning("DRAFT-SCOUT PURCHASE mode=%s currency=%s resumed=%s state=%s balance=%s",
                        draft_purchase.group(1), currency, resumed, run["state"], STATE.credits())
            return 200, {"Cache-Control": "no-store"}, json_bytes(response)

    if low == "/ut/game/fifa21/squad/mode/draft/choose/difficulty" and method in ("PUT", "POST"):
        with _draft_lock:
            run = _draft_run()
            if run is None:
                return 409, {}, json_bytes({"reason": "DRAFT_NOT_ACTIVE"})
            requested = str(payload.get("difficultyName", "BEGINNER") if isinstance(payload, dict) else "BEGINNER").upper()
            aliases = {"PROFESSIONAL": "PRO", "WORLD_CLASS": "WORLDCLASS"}
            requested = aliases.get(requested, requested)
            run["difficulty"] = _DRAFT_DIFFICULTIES.index(requested) if requested in _DRAFT_DIFFICULTIES else 0
            run["state"] = "FORMATION_DRAFT"
            _draft_save(run)
            log.warning("DRAFT-SCOUT CHOOSE_DIFFICULTY value=%s index=%s", requested, run["difficulty"])
            return 200, {"Cache-Control": "no-store"}, json_bytes({})

    draft_choices = re.search(r"/draft/choices/(difficulty|formation|captain|player|manager)$", low)
    if draft_choices and method in ("GET", "PUT", "POST"):
        with _draft_lock:
            run = _draft_run()
            if run is None:
                return 409, {}, json_bytes({"reason": "DRAFT_NOT_ACTIVE"})
            kind = draft_choices.group(1)
            raw_position = payload.get("positionId", payload.get("positionid", 0)) if isinstance(payload, dict) else 0
            try:
                position_id = int(raw_position or 0)
            except (TypeError, ValueError):
                position_id = 0
            response = _draft_choices(run, kind, position_id)
            log.warning("DRAFT-SCOUT CHOICES kind=%s position=%s count=%s state=%s",
                        kind, position_id, len(response.get("choices", [])), run["state"])
            return 200, {"Cache-Control": "no-store"}, json_bytes(response)

    if low.endswith("/draft/choose") and method in ("PUT", "POST"):
        with _draft_lock:
            run = _draft_run()
            if run is None:
                return 409, {}, json_bytes({"reason": "DRAFT_NOT_ACTIVE"})
            try:
                choice_index = int(payload.get("choiceIndex", 0) if isinstance(payload, dict) else 0)
                position_id = int(payload.get("positionId", run.get("currentPositionId", 0))
                                  if isinstance(payload, dict) else run.get("currentPositionId", 0))
            except (TypeError, ValueError):
                choice_index, position_id = 0, int(run.get("currentPositionId", 0) or 0)
            old_state = run["state"]
            _draft_choose(run, choice_index, position_id)
            log.warning("DRAFT-SCOUT CHOOSE oldState=%s choice=%s position=%s newState=%s picks=%s",
                        old_state, choice_index, position_id, run["state"], len(run.get("picked", {})))
            return 200, {"Cache-Control": "no-store"}, json_bytes({})

    if low.endswith("/draft/autocomplete") and method in ("POST", "PUT"):
        with _draft_lock:
            run = _draft_run()
            if run is None:
                return 409, {}, json_bytes({"reason": "DRAFT_NOT_ACTIVE"})
            if not run.get("formation"):
                if len(run.get("formationChoices", [])) != 5:
                    _draft_choices(run, "formation", 0)
                run["formation"] = run["formationChoices"][0]
            for position_id in range(23):
                if str(position_id) in run.get("picked", {}):
                    continue
                choices = _draft_player_choices(run, position_id, captain=False)
                if choices:
                    run.setdefault("picked", {})[str(position_id)] = choices[0]
            if not run.get("manager"):
                managers = _draft_manager_choices(run)
                run["manager"] = managers[0] if managers else None
            run["state"] = "READY_FOR_MATCH"
            _draft_save(run)
            return 200, {"Cache-Control": "no-store"}, json_bytes(_draft_state_payload(run))

    if (low.endswith("/draft/mode/1/stats") or
            ("/squad/mode/" in low and low.endswith("/stats"))) and method == "GET":
        run = _draft_run()
        wins = int(run.get("wins", 0) or 0) if run else 0
        eliminated = bool(run and run.get("state") == "READY_FOR_REWARDS")
        return 200, {"Cache-Control": "no-store"}, json_bytes({
            "bestBuilderScore": 0, "concededGoals": 0,
            "draftChampion": 1 if wins >= 4 else 0,
            "draftsCompleted": 1 if eliminated or wins >= 4 else 0,
            "gamesLost": 1 if eliminated else 0, "gamesWon": wins,
            "passAccuracyTotal": 0, "possessionPercentage": 0,
            "possesionTotal": 0, "possessionTotal": 0, "scoredGoals": 0,
        })

    if "/grant/award" in low and method in ("GET", "POST", "PUT"):
        with _draft_lock:
            run = _draft_run()
            reward = int(run.get("pendingCoinReward", 0) or 0) if run else 0
            if reward:
                STATE.add_credits(reward)
            response = [{"halId": 0, "type": 0, "value": reward}] if reward else []
            STATE.clear_draft_run()
            log.warning("DRAFT-SCOUT AWARD coins=%s", reward)
            return 200, {"Cache-Control": "no-store"}, json_bytes(response)

    if low in ("/ut/shards", "/ut/shards/v2"):
        return 200, {}, json_bytes({
            "shardInfo": [{
                "customdata1": "127.0.0.1",
                "customdata2": str(CFG["fut_port"]),
                "name": "LocalFUT15",
                "platform": "pc",
                "clientFacingIpPort": f"127.0.0.1:{CFG['fut_port']}",
            }]
        })

    if low == "/ut/game/fifa21/resourcemapping":
        # CardsDLL_Win64_retail.dll parses this endpoint as an array of
        # {format, key, resourceType, value} records. No remote mappings are
        # required for the local runtime, but the container type must match.
        return 200, {}, json_bytes([])

    if low == "/ut/game/fifa21/user/accountinfo":
        return 200, {}, json_bytes({
            "userAccountInfo": {
                "dateOfBirth": str(CFG["date_of_birth"]),
                "age": mock_account_age(),
                "isUnderAge": mock_account_is_under_age(),
                "personas": [{
                    "personaId": int(CFG["persona_id"]),
                    "personaName": str(CFG["display_name"]),
                    "userClubList": [{
                        "year": "2015",
                        "platform": "pc",
                        "clubName": str(CFG["club_name"]),
                        "established": now_s(),
                        "assetId": int(CFG["club_id"]),
                        "clubAbbr": str(CFG["club_abbr"]),
                    }],
                    "returningUser": 1,
                }]
            }
        })

    # Static CDN presentation files requested by the Store/New Items screens.
    cached_content = _cached_fut_content(low)
    if cached_content is not None:
        body, content_type = cached_content
        return 200, {"Content-Type": content_type, "Cache-Control": "public, max-age=86400"}, body

    dynamic_head = re.fullmatch(r"/fut/playerheads/g4/single/p(\d+)\.dds", low)
    if dynamic_head and method == "GET":
        resource_id = int(dynamic_head.group(1))
        body = _cached_dynamic_player_head(resource_id)
        if body is None:
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"dynamic player head unavailable\n"
        return 200, {
            "Content-Type": "application/octet-stream",
            "Cache-Control": "public, max-age=31536000, immutable",
        }, body

    # Static client-side localization/config requests observed in FUT.
    if low == "/fut/loc/pc/leaderboards.eng_us.xml":
        return 404, {"Content-Type": "text/plain"}, b"not found"
    if low == "/fut/tutorials/cmn":
        return 404, {"Content-Type": "text/plain"}, b"not found"
    if low in (
        "/fut/packs/loc/storepackdescriptions.en_us.xml",
        "/fut/packs/loc/storepackdescriptions.en_gb.xml",
    ):
        # FUT store pack localization is an XLIFF translation file.
        # The native client resolves the visible title/description through
        # FUT_STORE_PACK_<packId>_NAME / _DESC keys rather than from the
        # human-readable name fields in purchasegroup/all.
        settings = load_pack_settings()
        loc_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<xliff version="1.2">',
            '  <file original="storepackdescriptions" source-language="en-US" datatype="plaintext">',
            '    <body>',
        ]

        exported = []
        for pid_s, cfg in sorted(
            settings.get("packs", {}).items(),
            key=lambda kv: int(kv[0])
        ):
            if not bool(cfg.get("enabled", True)):
                continue

            try:
                pid = int(pid_s)
            except Exception:
                continue

            name = str(cfg.get("name", f"Custom Pack {pid}") or f"Custom Pack {pid}")
            bio = str(cfg.get("bio", "") or "")

            name_xml = html.escape(name, quote=False)
            bio_xml = html.escape(bio, quote=False)

            # Desktop FUT store name.
            key_name = f"FUT_STORE_PACK_{pid}_NAME"
            loc_lines.append(
                f'      <trans-unit id="{key_name}" resname="{key_name}">'
                f'<source>{name_xml}</source></trans-unit>'
            )

            # Pack detail/description panel.
            key_desc = f"FUT_STORE_PACK_{pid}_DESC"
            loc_lines.append(
                f'      <trans-unit id="{key_desc}" resname="{key_desc}">'
                f'<source>{bio_xml}</source></trans-unit>'
            )

            # FIFA web/mobile-era localization files also expose a MOBILE name.
            # Keeping it here is harmless for the PC client and improves
            # compatibility with builds that request the same key family.
            key_mobile = f"FUT_STORE_PACK_{pid}_NAME_MOBILE"
            loc_lines.append(
                f'      <trans-unit id="{key_mobile}" resname="{key_mobile}">'
                f'<source>{name_xml}</source></trans-unit>'
            )
            exported.append((pid, name, bio))

        # Names for the pack ids used as Objectives / FUT Champions / Season
        # Progress rewards.  The client resolves a reward pack's label from
        # FUT_STORE_PACK_<id>_NAME (same key family as the store); without an
        # entry it shows the generic "PACK" text.  (Pack artwork is a separate
        # bundled-asset lookup and is not reachable from here.)
        loc_seen = {pid for pid, _n, _b in exported}
        for reward_pid, reward_name in sorted(_PACK_NAMES.items()):
            if reward_pid in loc_seen:
                continue
            rn = html.escape(str(reward_name), quote=False)
            for key in (
                f"FUT_STORE_PACK_{reward_pid}_NAME",
                f"FUT_STORE_PACK_{reward_pid}_DESC",
                f"FUT_STORE_PACK_{reward_pid}_NAME_MOBILE",
            ):
                loc_lines.append(
                    f'      <trans-unit id="{key}" resname="{key}">'
                    f'<source>{rn}</source></trans-unit>'
                )

        loc_lines.extend([
            '    </body>',
            '  </file>',
            '</xliff>',
        ])
        xml = ("\n".join(loc_lines)).encode("utf-8")

        log.warning("STORE-SCOUT LOC RESPONSE xml=%s", xml.decode("utf-8", errors="replace"))
        log.info("STORE PACK LOC XLIFF packs=%s", exported)

        return 200, {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }, xml

    # ---- Squad Building Challenges --------------------------------------
    if "/sbs/" in low or low.endswith("/sbs"):
        log.warning("SBC-SCOUT method=%s path=%s body=%s", method, raw_path, body[:600])
        if low == "/ut/game/fifa21/sbs/sets" and method == "GET":
            return 200, {"Cache-Control": "no-store"}, json_bytes(_sbc_sets_response())
        if low == "/ut/game/fifa21/sbs/sets/tag":
            return 200, {"Cache-Control": "no-store"}, json_bytes({"sets": []})
        m = re.fullmatch(r"/ut/game/fifa21/sbs/setid/(\d+)/challenges", low)
        if m and method == "GET":
            return 200, {"Cache-Control": "no-store"}, json_bytes(
                _sbc_challenges_response(int(m.group(1))))
        m = re.fullmatch(r"/ut/game/fifa21/sbs/challenge/(\d+)/squad", low)
        if m:
            cid = int(m.group(1))
            if method == "PUT":
                # Save the selected squad. FIFA follows with an empty
                # PUT /sbs/challenge/{id} to actually submit it.  Remember how
                # many real players were placed so the submit step can check it.
                placed = 0
                if isinstance(payload, dict):
                    for p in payload.get("players", []) or []:
                        item = p.get("itemData", p) if isinstance(p, dict) else {}
                        if isinstance(item, dict) and int(item.get("id", 0) or 0) > 0:
                            placed += 1
                saved = dict(STATE.get("sbc_saved_counts", {}) or {})
                saved[str(cid)] = placed
                STATE.set("sbc_saved_counts", saved)
                return 200, {}, json_bytes({})
            return 200, {"Cache-Control": "no-store"}, json_bytes(_sbc_empty_squad(cid))
        m = re.fullmatch(r"/ut/game/fifa21/sbs/challenge/(\d+)", low)
        if m:
            cid = int(m.group(1))
            if method == "PUT":
                status, resp = _sbc_submit(cid)
                return status, {"Cache-Control": "no-store"}, json_bytes(resp)
            # POST (or GET) enters the challenge and returns its blank squad.
            return 200, {"Cache-Control": "no-store"}, json_bytes(_sbc_empty_squad(cid))
        return 200, {}, json_bytes({})

    # Exact FIFA 21 game namespace.
    if low == "/ut/game/fifa21/settings":
        return 200, {}, json_bytes({"configs": [
            {"type": "IsStoreEnabled", "value": "1"},
            {"type": "cardPackStoreEnabled", "value": "1"},
            {"type": "pointsPackStoreEnabled", "value": "1"},
            {"type": "enableMyStadium", "value": "1"},
            {"type": "myStadium", "value": "1"},
            {"type": "myStadiumConfigMode", "value": "1"},
            {"type": "myStadiumAdboardMode", "value": "1"},
            {"type": "myStadium_tier", "value": "1"},
            {"type": "squadBattleEnabled", "value": "1"},
            {"type": "squadBattleCouchPlayEnabled", "value": "1"},
            {"type": "enableDraftMode", "value": "1"},
            {"type": "enableOfflineDraftMode", "value": "1"},
            {"type": "enableSinglePlayerDraftMode", "value": "1"},
            {"type": "enableObjectives", "value": "1"},
            {"type": "enableSeasonalCampaigns", "value": "1"},
            {"type": "enableRivals", "value": "1"},
            {"type": "sbcEnabled", "value": "1"},
            {"type": "SquadBuildingChallengesEnabled", "value": "1"},
            {"type": "enableSBC", "value": "1"},
        ]})

    if low == "/ut/game/fifa21/stadium":
        if method in ("PUT", "POST"):
            log.info("My Stadium update method=%s payload=%s", method,
                     json.dumps(payload, sort_keys=True, separators=(",", ":")))
            # Persist a stadium selection so the chosen venue sticks and is
            # used for home matches.  Accept any of the keys FIFA 21 has used
            # for the venue id in this save.
            chosen = 0
            if isinstance(payload, dict):
                for src in (payload, payload.get("myStadium", {}),
                            payload.get("stadium", {})):
                    if not isinstance(src, dict):
                        continue
                    for key in ("StadiumId", "stadiumId", "stadiumid",
                                "selectedStadiumId", "assetId", "id"):
                        try:
                            v = int(src.get(key, 0) or 0)
                        except (TypeError, ValueError):
                            v = 0
                        if v in _STADIUM_BY_ID:
                            chosen = v
                            break
                    if chosen:
                        break
            if chosen:
                STATE.set("selected_stadium_id", chosen)
                log.info("My Stadium venue set to %d (%s)", chosen,
                         _STADIUM_BY_ID.get(chosen, {}).get("name", "?"))
        # GET opens the native editor. PUT/POST are accepted as a local save.
        return 200, {"Cache-Control": "no-store"}, json_bytes(_native_stadium_response())

    if low == "/ut/game/fifa21/attributes/metadata" and method == "GET":
        requested_ids: list[int] = []
        for raw_value in query.get("defIds", []):
            for raw_id in str(raw_value).split(","):
                try:
                    resource_id = int(raw_id.strip())
                except (TypeError, ValueError):
                    continue
                if resource_id > 0 and resource_id not in requested_ids:
                    requested_ids.append(resource_id)
        # player_attribute_metadata preserves each requested defId while
        # resolving the two pro cards through their base-asset aliases.
        records = [player_attribute_metadata(resource_id) for resource_id in requested_ids]
        log.info("Resolved player attribute metadata requested=%s", requested_ids)
        # CardsDLL's FutGetPlayerAttributesServerResponse reads a top-level
        # array directly (there is no collection-name wrapper).
        return 200, {"Cache-Control": "no-store"}, json_bytes(records)

    if low.startswith("/ut/game/fifa21/phishing/trusteddevice"):
        return 200, {}, json_bytes({
            "trusted": True, "exists": True, "locked": False, "changed": False,
            "token": "fut15-local-phishing-token", "isTrusted": True,
            "completed": True, "answerRequired": False, "attempts": 0,
        })

    if low == "/ut/game/fifa21/match/end" and method in ("POST", "PUT"):
        with _draft_lock:
            run = _draft_run()
            if run is not None:
                reason = str(payload.get("endReason", "LOSS") if isinstance(payload, dict) else "LOSS").upper()
                if reason == "WIN":
                    run["wins"] = min(4, int(run.get("wins", 0) or 0) + 1)
                    run["state"] = "READY_FOR_REWARDS" if run["wins"] >= 4 else "READY_FOR_MATCH"
                    eliminated = run["wins"] >= 4
                    reward = 0
                else:
                    run["losses"] = int(run.get("losses", 0) or 0) + 1
                    run["state"] = "READY_FOR_REWARDS"
                    eliminated = True
                    reward = 15000 if int(run.get("wins", 0) or 0) == 0 else 0
                    run["pendingCoinReward"] = reward
                    reason = "LOSS"
                _draft_save(run)
                balance = STATE.credits()
                response = {
                    "endReason": reason,
                    "secondsPlayed": int(payload.get("secondsPlayed", 0) or 0),
                    "matchDifficulty": int(payload.get("matchDifficulty", run.get("difficulty", 0)) or 0),
                    "opponentTeamId": 1,
                    "items": payload.get("items", []) if isinstance(payload.get("items"), list) else [],
                    "matchData": str(payload.get("matchData", "") or ""),
                    "completionAward": reward,
                    "skillAward": 0,
                    "rewardCoins": reward,
                    "totalCoins": reward,
                    "credits": balance,
                    "coins": balance,
                    "sessionCoinsBankBalance": balance,
                    "dnfModifier": 1.0,
                    "draftWins": int(run.get("wins", 0) or 0),
                    "draftEliminated": eliminated,
                    "currencies": [{"name": "coins", "funds": balance, "finalFunds": balance},
                                   {"name": "points", "funds": 0, "finalFunds": 0}],
                    "record": {"won": 0, "draw": 0, "loss": 0},
                    "gamesWon": 0,
                    "gamesDraw": 0,
                    "gamesLost": 0,
                    "gamesPlayed": 0,
                    "unopenedPacks": {
                        "preOrderPacks": len(STATE.unopened_pack_ids()),
                        "recoveredPacks": 0,
                    },
                    "won": 1 if reason == "WIN" else 0,
                    "draw": 0,
                    "loss": 0 if reason == "WIN" else 1,
                    "matchCoinPartials": None,
                    "matchCoinMultipliers": [{"type": "DIFFICULTY", "value": 1.0}],
                    "boostConis": 0,
                }
                for optional_key in ("myMatchStats", "opponentMatchStats", "myRating", "opponentRating"):
                    if optional_key in payload:
                        response[optional_key] = payload[optional_key]
                PENDING_USERSESSION_REFRESH.set()
                log.warning("DRAFT-SCOUT MATCH_END reason=%s wins=%s state=%s",
                            reason, run["wins"], run["state"])
                return 200, {"Cache-Control": "no-store"}, json_bytes(response)

    if low == "/ut/game/fifa21/match/reset":
        return 200, {}, json_bytes({"reset": True})

    # Match creation must return the user's complete active squad. Returning a
    # generic empty object here lets navigation reach kit selection, but the
    # native controller then treats both kits as unowned catalogue items.
    if low == "/ut/game/fifa21/match" and method in ("POST", "PUT"):
        draft_run = _draft_run()
        if draft_run is not None:
            if draft_run.get("state") != "READY_FOR_MATCH":
                return 409, {}, json_bytes({"reason": "DRAFT_NOT_READY_FOR_MATCH"})
            response = _native_draft_match_response(draft_run)
            log.warning(
                "DRAFT-SCOUT MATCH method=%s players=%s starters=%s chemistry=%s stars=%s opponentTeamId=%s body=%r",
                method,
                len(response["squad"].get("players", [])),
                sum(1 for slot in response["squad"].get("players", [])[:11]
                    if isinstance(slot.get("itemData"), dict)),
                response["squad"].get("chemistry"),
                response["squad"].get("starRating"),
                response.get("opponentTeamId"),
                body[:1024],
            )
            return 200, {"Cache-Control": "no-store"}, json_bytes(response)
        opponent_slot = payload.get("sqbtOpponentSquadId") if isinstance(payload, dict) else None
        try:
            opponent_slot = int(opponent_slot) if opponent_slot is not None else None
        except (TypeError, ValueError):
            opponent_slot = None
        return 200, {"Cache-Control": "no-store"}, json_bytes(_native_match_response(opponent_slot))

    # Single-player Draft asks for this complete CPU squad immediately before
    # POST /match. Returning the unknown-route {} leaves the match loader
    # without an away XI and FIFA terminates while constructing the session.
    offline_squad = re.fullmatch(r"/ut/game/fifa21/offlinesquad/(\d+)", low)
    if offline_squad and method == "GET":
        run = _draft_run()
        round_index = min(3, max(0, int(run.get("wins", 0) or 0))) if run else 0
        squad = _native_squad_battle_opponent(round_index)
        squad["id"] = int(offline_squad.group(1))
        squad["squadId"] = int(offline_squad.group(1))
        squad["squadType"] = "DRAFT_SQUAD"
        squad["active"] = True
        squad["valid"] = True
        # The CPU side owns the Draft venue. Its active Bernabeu item therefore
        # remains authoritative instead of FIFA falling back to My Stadium.
        squad["home"] = True
        squad["isHomeTeam"] = True
        log.warning(
            "DRAFT-SCOUT OFFLINE_SQUAD id=%s round=%s players=%s starters=%s",
            squad["id"], round_index, len(squad.get("players", [])),
            sum(1 for slot in squad.get("players", [])[:11]
                if isinstance(slot.get("itemData"), dict)),
        )
        return 200, {"Cache-Control": "no-store"}, json_bytes(squad)

    # Both summary identifiers are zero-based keys in CardsDLL's fixed map.
    # The same slot is used by the detailed-squad route below.
    if low == "/ut/game/fifa21/sqbt/user/hub" and method == "GET":
        return 200, {"Cache-Control": "no-store"}, json_bytes(_native_squad_battle_hub())

    # ---- Division Rivals + FUT Champions (Seasonal framework) ------------
    def _seasonal_reply(name: str, payload: dict[str, Any]):
        log.warning("SEASONAL-SCOUT RESPONSE %s=%s", name,
                    json.dumps(payload, separators=(",", ":"))[:4000])
        return 200, {"Cache-Control": "no-store"}, json_bytes(payload)

    if low == "/fut/seasonal/seasonalconfig.json" and method == "GET":
        return _seasonal_reply("seasonalconfig", _seasonal_config_json())

    # ---- FUT Objectives (Seasonal Campaign / SCMP) ----------------------
    if low in ("/ut/game/fifa21/scmp/campaign/active",
               "/ut/game/fifa21/scmp/campaign") and method == "GET":
        return _seasonal_reply("scmp_campaign", _scmp_campaign_json())

    # Lazy group-detail fetch: PUT {"groupList":[{groupId,groupType}]}.
    # RS4:FutGetObjectiveGroupsDetailsServerCall -- response carries the full
    # groups (with their objectives) under groupList / scmpGroupProgressList.
    _scmp_details = re.fullmatch(
        r"/ut/game/fifa21/scmp/(?:active|learning|world)/category/(\d+)/details", low)
    if _scmp_details and method in ("PUT", "POST", "GET"):
        claimed = bool(STATE.get("scmp_reward_claimed", False))
        # The details-response parser (CardsDLL sub_1803308c0) reads the group
        # array from key id 498 = "groupsList" (NOT "groupList", which is the
        # *request* body key).  Other keys are skipped.
        cat_id = 1
        m = re.search(r"/category/(\d+)/", low)
        if m:
            cat_id = int(m.group(1))
        return _seasonal_reply("scmp_category_details", {
            "groupsList": [_scmp_group(claimed)],
            "categoryId": cat_id,
        })

    if low == "/ut/game/fifa21/scmp/world/campaign/active" and method == "GET":
        return _seasonal_reply("scmp_world_campaign", {})

    if low in ("/ut/game/fifa21/scmp/campaign/rewards/all",
               "/ut/game/fifa21/scmp/learning/campaign/rewards/all",
               "/ut/game/fifa21/scmp/world/campaign/rewards/all") and method == "GET":
        return _seasonal_reply("scmp_rewards_all", {"rewards": [], "rewardsList": []})

    # Season Progress: claim one level's reward (if enough XP, once).
    _scmp_level = re.fullmatch(
        r"/ut/game/fifa21/scmp/(?:campaign|world/campaign)/level/(\d+)/reward/(\d+)",
        low)
    if _scmp_level and method in ("POST", "PUT"):
        lvl = int(_scmp_level.group(1))
        user_xp = int(STATE.get("scmp_user_xp", 0) or 0)
        claimed_levels = set(STATE.get("scmp_claimed_levels", []) or [])
        by_id = {x["levelId"]: x for x in _scmp_season_levels()}
        level_def = by_id.get(lvl)
        if (level_def is None or lvl in claimed_levels
                or user_xp < level_def["xpThreshold"]):
            return _seasonal_reply("scmp_level_claim", {
                "awards": [], "awardsList": [],
                "alreadyClaimed": lvl in claimed_levels,
            })
        award = level_def["rewardOptionList"][0]["awards"][0]
        granted = _scmp_grant_level_award(award)
        claimed_levels.add(lvl)
        STATE.set("scmp_claimed_levels", sorted(claimed_levels))
        return _seasonal_reply("scmp_level_claim", {
            "awards": [award], "awardsList": [award],
            "grantedItems": [_native_pack_item(x) for x in granted],
            "itemList": [_native_pack_item(x) for x in granted],
        })

    # Objectives: claim the group / campaign-wide reward (the "82 x10"), once.
    _scmp_claim = re.fullmatch(
        r"/ut/game/fifa21/scmp/campaign/(?:group/\d+/(?:objective/\d+/)?rewards"
        r"|rewards/all)", low)
    if _scmp_claim and method in ("POST", "PUT"):
        already = bool(STATE.get("scmp_reward_claimed", False))
        granted = [] if already else _scmp_grant_reward()
        if not already:
            STATE.set("scmp_reward_claimed", True)
        awards = [] if already else [_scmp_reward_award()]
        return _seasonal_reply("scmp_claim", {
            "awards": awards,
            "awardsList": awards,
            "grantedItems": [_native_pack_item(x) for x in granted],
            "itemList": [_native_pack_item(x) for x in granted],
            "alreadyClaimed": already,
        })

    # NOTE: /ut/game/fifa21/clientdata/rivals is deliberately NOT handled here.
    # Persisting rivals client-data markers made the client's bulk client-data
    # sync (which also runs on squad edits) hard-crash the game.  Letting it
    # fall through to the generic 200 {} is what the client expects and Rivals
    # still works.

    if low == "/ut/game/fifa21/rivals/user/hub" and method == "GET":
        return _seasonal_reply("rivals_hub", _rivals_hub_json())

    if low == "/ut/game/fifa21/champion/user/hub" and method == "GET":
        return _seasonal_reply("champion_hub", _champion_hub_json())

    if low == "/ut/game/fifa21/champion/events/upcoming" and method == "GET":
        return _seasonal_reply("champion_upcoming", {"championEvents": []})

    if low == "/ut/game/fifa21/champion/user/prize" and method in ("POST", "PUT"):
        # Claim FUT Champions prizes.  Nothing is pending on a fresh local
        # club, so acknowledge with empty award lists (web app UTChampionsDAO
        # reads awardedPrizes[].awards[] and prizesInError[].eventId).
        return _seasonal_reply("champion_prize_claim", {
            "awardedPrizes": [], "prizesInError": [],
        })

    if low == "/ut/game/fifa21/champion/user/stats" and method == "GET":
        return _seasonal_reply("champion_stats", _champion_stats_json())

    if low == "/ut/game/fifa21/champion/leaderboard" and method == "GET":
        return _seasonal_reply("champion_leaderboard", _champion_leaderboard_json())

    if re.fullmatch(r"/ut/game/fifa21/champion/type/[^/]+/period/[^/]+/friends/stats", low) \
            and method == "GET":
        return _seasonal_reply("champion_friends_stats", {
            "entries": [], "friends": [], "stats": [], "totalCount": 0,
        })

    opponent_body = re.fullmatch(r"/ut/game/fifa21/sqbt/user/opponentsquad/(\d+)", low)
    if opponent_body and method == "GET":
        squad = _native_squad_battle_opponent(int(opponent_body.group(1)))
        return 200, {"Cache-Control": "no-store"}, json_bytes(squad)

    if low.startswith("/ut/game/fifa21/featuredsquad/user/stats") and method == "GET":
        event = _native_squad_battle_event()
        return 200, {"Cache-Control": "no-store"}, json_bytes({
            "wins": 0, "losses": 0, "matchesPlayed": 0, "points": 0,
            "rank": 0, "score": 0, "gamesPlayed": 0,
            "gamesRemaining": event["gamesRemaining"],
            "remainingMatches": event["remainingMatches"],
            "matchesRemaining": event["remainingMatches"],
            "maxMatches": event["maxMatches"],
            "eventStartTime": event["eventStartTime"],
            "eventEndTime": event["eventEndTime"],
            "secondsUntilStart": event["secondsUntilStart"],
            "secondsUntilEnd": event["secondsUntilEnd"],
        })

    # Squad Battles requires a non-empty opponent feed when the mode opens.
    if ("squadbattle" in low or "squad/battle" in low or "squad_battle" in low) and method in ("GET", "POST", "PUT"):
        base = _native_squad()
        # Build a complete 23-slot opponent XI from real local player
        # definitions.  Empty placeholder slots are not selectable by the
        # Squad Battles UI, even when the containing squad object is present.
        source_items = [item for item in STATE.list_items() if str(item.get("itemType", "player")) == "player"]
        if not source_items:
            resource_ids = sorted(PLAYER_DB, key=lambda rid: int(PLAYER_DB[rid].get("rating", 0)), reverse=True)[:23]
            source_items = [STATE.build_player_item(int(rid), 800000 + idx, "none") for idx, rid in enumerate(resource_ids)]
        source_items = (source_items * ((23 + len(source_items) - 1) // len(source_items)))[:23]
        opponents = []
        for idx in range(4):
            opponent = dict(base)
            opponent["id"] = 900000 + idx
            opponent["squadId"] = opponent["id"]
            opponent["squadName"] = f"Squad Battles Opponent {idx + 1}"
            opponent["name"] = opponent["squadName"]
            opponent["difficulty"] = 0
            opponent["rating"] = 75 + idx * 3
            opponent["chemistry"] = 75 + idx * 5
            opponent["players"] = [{
                "index": slot,
                "kitNumber": slot + 1,
                "itemData": _native_player_item(item),
            } for slot, item in enumerate(source_items)]
            opponents.append(opponent)
        return 200, {"Cache-Control": "no-store"}, json_bytes({
            "squads": opponents,
            "opponents": opponents,
            "squadBattles": opponents,
            "opponentSquads": opponents,
            "availableSquads": opponents,
            "squadBattleSquads": opponents,
            "items": opponents,
            "refreshAvailable": True,
            "matchesRemaining": len(opponents),
        })

    if low == "/ut/game/fifa21/user":
        return 200, {}, json_bytes(_native_user())

    if low == "/ut/game/fifa21/userdata":
        return 200, {}, json_bytes({"onlineELORating": 0, "onlineRatedUser": False, "accountResetCount": 0})

    if low == "/ut/v2/game/fifa21/store/transaction":
        return 200, {}, json_bytes({"state": "NOTRANSACTION"})

    if low == "/ut/game/fifa21/clientdata/tutorialpopups":
        return 200, {}, json_bytes({"entries": []})
    if low == "/ut/game/fifa21/clientdata/onboarding":
        if method == "PUT":
            status, result = _save_client_data("onboarding", payload)
            if status != 200:
                return status, {}, json_bytes(result)
        return 200, {}, json_bytes(_completed_onboarding_client_data())
    if low == "/ut/game/fifa21/clientdata/squad":
        if method == "PUT":
            status, result = _save_client_data("squad", payload)
            if status != 200:
                return status, {}, json_bytes(result)
        return 200, {}, json_bytes(_squad_client_data())
    if low == "/ut/game/fifa21/clientdata/pilesize":
        return 200, {}, json_bytes({"entries": [{"key": 2, "value": 100}, {"key": 4, "value": 100}]})
    if low == "/ut/game/fifa21/clientdata/userhubdata":
        return 200, {}, json_bytes({"entries": [{"key": 0, "value": 13}, {"key": 1, "value": 12}]})
    if low == "/ut/game/fifa21/clientdata/managerquest":
        return 200, {}, json_bytes({"entries": [{"key": 0, "value": 148528}, {"key": 1, "value": 148528}, {"key": 2, "value": 0}]})
    if low == "/ut/game/fifa21/clientdata/loanplayerreward":
        if method == "PUT":
            status, result = _save_client_data("loanplayerreward", payload)
            if status != 200:
                return status, {}, json_bytes(result)
        return 200, {}, json_bytes(_completed_loan_client_data())

    if low == "/ut/game/fifa21/onboarding/kits":
        if method == "GET":
            return 200, {}, json_bytes(_onboarding_kit_choices())
        if method == "POST":
            status, result = _select_onboarding_kits(payload)
            return status, {}, json_bytes(result)
        return 405, {}, json_bytes({"reason": "unsupported kit-selection method"})

    if low == "/ut/game/fifa21/onboarding/badges" and method == "GET":
        return 200, {}, json_bytes(_onboarding_badge_choices())

    badge_selection = re.match(r"^/ut/game/fifa21/onboarding/badge/(\d+)$", low)
    if badge_selection and method == "POST":
        resource_id = int(badge_selection.group(1))
        badge = next((item for item in _active_club_items()
                      if str(item.get("itemState", "")) == "activeBadge"
                      and int(item["resourceId"]) == resource_id), None)
        if badge is None:
            return 404, {}, json_bytes({"reason": "unknown local badge choice"})
        STATE.set("onboarding_badge_choice", resource_id)
        STATE.set("onboarding_badge_item_id", int(badge["id"]))
        STATE.set("onboarding_complete", True)
        return 200, {}, json_bytes({"badgeId": int(badge["id"])})

    if low == "/ut/game/fifa21/loan/players":
        return 200, {}, json_bytes({"loans": []} if onboarding_complete() else _loan_player_choices())

    loan_grant = re.match(r"^/ut/game/fifa21/loan/player/(\d+)$", low)
    if loan_grant and method in ("PUT", "POST"):
        offer_id = int(loan_grant.group(1))
        offer = next(
            (candidate for candidate in _loan_player_choices()["loans"]
             if int(candidate["defId"]) == offer_id),
            None,
        )
        if offer is None:
            return 404, {}, json_bytes({"reason": "unknown local loan offer"})
        resource_id = int(offer["itemData"]["resourceId"])

        granted_item = None
        granted_item_id = int(STATE.get("onboarding_loan_item_id", 0) or 0)
        if granted_item_id:
            granted_item = STATE.get_item(granted_item_id)
            previous_offer = int(STATE.get("onboarding_loan_offer_id", 0) or 0)
            if previous_offer and previous_offer != offer_id:
                # FIFA can restart an unfinished onboarding flow and legitimately
                # choose another offer. Keep the old loan alive until the later
                # squad save binds the new one, so an interrupted retry never
                # leaves the active squad pointing at a deleted item.
                stale = [int(value) for value in STATE.get("onboarding_stale_loan_item_ids", [])
                         if int(value) > 0]
                if granted_item is not None and granted_item_id not in stale:
                    stale.append(granted_item_id)
                STATE.set("onboarding_stale_loan_item_ids", stale)
                granted_item = None

        if granted_item is None:
            granted_item = STATE.make_player_item(resource_id, "unassigned", LOAN_GAMES)
            STATE.set("onboarding_loan_offer_id", offer_id)
            STATE.set("onboarding_loan_item_id", int(granted_item["id"]))
            STATE.set("onboarding_loan_resource_id", resource_id)
            # A changed loan reopens the following onboarding selections.
            STATE.set("onboarding_kit_choices", [])
            STATE.set("onboarding_kit_item_ids", {})
            STATE.set("onboarding_badge_choice", 0)
            STATE.set("onboarding_badge_item_id", 0)

        return 200, {}, json_bytes(_native_player_item(granted_item))

    if low == "/ut/game/fifa21/squad/active":
        return 200, {}, json_bytes(_native_squad())

    if low == "/ut/game/fifa21/squad/900001" and method == "GET":
        run = _draft_run()
        if run is None:
            return 404, {}, json_bytes({"reason": "DRAFT_NOT_ACTIVE"})
        return 200, {"Cache-Control": "no-store"}, json_bytes(_draft_squad(run))

    if low == "/ut/game/fifa21/squad/900001" and method in ("PUT", "POST"):
        # Draft rearrangements are transient and must never replace the user's
        # real active squad. Persist the slot layout inside the Draft run only.
        with _draft_lock:
            run = _draft_run()
            if run is None:
                return 404, {}, json_bytes({"reason": "DRAFT_NOT_ACTIVE"})
            supplied = payload.get("players", []) if isinstance(payload, dict) else []
            if isinstance(supplied, list):
                rearranged = {}
                for slot in supplied:
                    if not isinstance(slot, dict) or not isinstance(slot.get("itemData"), dict):
                        continue
                    try:
                        index = max(0, min(22, int(slot.get("index", 0))))
                    except (TypeError, ValueError):
                        continue
                    rearranged[str(index)] = slot["itemData"]
                if rearranged:
                    run["picked"] = rearranged
            if isinstance(payload, dict) and payload.get("formation"):
                run["formation"] = str(payload["formation"])
            _draft_save(run)
            return 200, {"Cache-Control": "no-store"}, json_bytes(_draft_squad(run))

    if low.startswith("/ut/game/fifa21/squad/") and method in ("PUT", "POST"):
        try:
            sid = int(low.rsplit("/", 1)[-1])
        except Exception:
            sid = 1
        src = payload if isinstance(payload, dict) else {}
        src["id"] = sid
        saved_squad = STATE.save_squad(src)
        current_loan_id = int(STATE.get("onboarding_loan_item_id", 0) or 0)
        referenced_ids = set(STATE._squad_item_ids(saved_squad))
        if current_loan_id and current_loan_id in referenced_ids:
            remaining_stale = []
            for stale_id in STATE.get("onboarding_stale_loan_item_ids", []):
                try:
                    stale_id = int(stale_id)
                except Exception:
                    continue
                if stale_id in referenced_ids:
                    remaining_stale.append(stale_id)
                else:
                    STATE.delete_item(stale_id)
            STATE.set("onboarding_stale_loan_item_ids", remaining_stale)
        return 200, {}, json_bytes({"id": sid})

    if low == "/ut/game/fifa21/eventfeed":
        return 200, {}, json_bytes({"event": [], "timestamp": now_s()})

    if low == "/ut/game/fifa21/hub":
        return 200, {}, json_bytes({
            "auctionCount": 0,
            "clubPlayers": len(STATE.list_items("club")) + len(STATE.list_items("squad")),
            "tradePile": {"count": 0, "selling": 0, "sold": 0},
            "watchlist": {"count": 0, "outbid": 0, "winning": 0},
            "squad": _native_squad(),
        })

    if low == "/ut/game/fifa21/clubuser":
        return 200, {}, json_bytes({"user": [{
            "persona": str(CFG["display_name"]),
            "personaId": int(CFG["persona_id"]),
            "public": True,
        }]})

    if low == "/ut/game/fifa21/leaderboards/options":
        return 200, {}, json_bytes({})

    if low.startswith("/ut/game/fifa21/club/stats/"):
        return 200, {}, json_bytes(_club_stats())

    if low == "/ut/game/fifa21/club/consumables/development" and method == "GET":
        # FIFA requests this document when opening the Club > Consumables /
        # Development screen. Returning the generic unknown-route `{}` makes
        # the native consumer panel fail before it can enumerate modifiers.
        # Consumables (the All Consumables Pack, training cards) are granted
        # into "unassigned" just like any fresh pack card, not "club" -- only
        # checking "club" left this screen with nothing to show.
        consumables = [
            item for item in STATE.list_items("club") + STATE.list_items("unassigned")
            if str(item.get("itemType", "")).lower() in ("consumable", "training", "development")
        ]
        native = [_native_consumable_item(item) for item in consumables]
        return 200, {"Cache-Control": "no-store"}, json_bytes({
            "itemData": native,
            "consumables": native,
            "developmentItems": native,
            "positionModifiers": [x for x in native
                                   if int(x.get("consumableType", 0) or 0) == 3],
        })

    if low == "/ut/game/fifa21/club":
        # Active cosmetics are owned club items too. Keeping them only in the
        # startup `actives` vector makes the pre-match kit selector render the
        # shirt but reject it as a locked catalogue item.
        merged_club_items = STATE.list_items("club") + _active_club_items()
        items = list({int(item.get("id", 0) or 0): item
                      for item in merged_club_items}.values())
        all_club_items = list(items)
        club_players = sum(1 for item in all_club_items
                           if str(item.get("itemType", "player")) == "player")
        club_managers = sum(1 for item in all_club_items
                            if str(item.get("itemType", "")) == "manager")
        club_kits = sum(1 for item in all_club_items
                        if str(item.get("itemType", "")) == "kit")
        club_badges = sum(1 for item in all_club_items
                          if str(item.get("itemState", "")) == "activeBadge")
        club_stadiums = sum(1 for item in all_club_items
                            if str(item.get("itemType", "")) == "stadium")
        club_balls = sum(1 for item in all_club_items
                         if str(item.get("itemType", "")) == "ball")
        club_total = len(all_club_items)

        # FIFA 21 PC sends My Club searches as POST requests with the paging
        # document in the JSON body. The web app uses GET query parameters.
        # Treat both representations identically. Ignoring the POST body made
        # every press of Next return page zero, so recently stored lower-rated
        # cards appeared to be missing even though SQLite contained them.
        search = {key: values[-1] for key, values in query.items() if values}
        if isinstance(payload, dict):
            nested = payload.get("searchCriteria") or payload.get("criteria") or payload.get("filters")
            if isinstance(nested, dict):
                search.update(nested)
            search.update({key: value for key, value in payload.items()
                           if not isinstance(value, (dict, list))})

        def _search_value(*names, default=None):
            lowered = {str(key).lower(): value for key, value in search.items()}
            for name in names:
                if name.lower() in lowered:
                    return lowered[name.lower()]
            return default

        def _as_int(value, default=0):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        requested_type = str(_search_value("type", "itemType", "ctype", default="player") or "player").lower()
        requested_type = requested_type.replace("_", "").replace("-", "").replace(" ", "")
        requested_category = str(
            _search_value("category", "cat", "searchCategory", "subType", "cardSubTypeId", default="") or ""
        ).lower()
        requested_category = requested_category.replace("_", "").replace("-", "").replace(" ", "")

        # My Stadium does not search kits as type=kit. FIFA 21 opens the
        # shared Club Info bucket (type=clubInfo) and distinguishes kits from
        # badges with category=kit/badge. Treating clubInfo as a literal item
        # type returned an empty list and the native customization screen then
        # dereferenced its missing first result, terminating the game.
        if requested_type in ("clubinfo", "clubitem", "clubitems"):
            if requested_category in ("badge", "badges", "custom", "11"):
                items = [item for item in items
                         if str(item.get("itemType", "")).lower() == "custom"
                         or _as_int(item.get("cardsubtypeid"), 0) == 11]
            else:
                # KIT is the default category for the Club Info repository in
                # the FIFA 21 client, so an omitted category must still return
                # owned kits.
                items = [item for item in items
                         if str(item.get("itemType", "")).lower() == "kit"
                         or _as_int(item.get("cardsubtypeid"), 0) == 9]
        elif requested_type in ("manager", "managers", "staff"):
            items = [item for item in items if str(item.get("itemType", "")) == "manager"]
        elif requested_type == "player":
            items = [item for item in items if str(item.get("itemType", "player")) == "player"]
        elif requested_type not in ("", "any", "all"):
            items = [item for item in items
                     if str(item.get("itemType", "")).lower() == requested_type]

        level = str(_search_value("level", default="") or "").lower()
        if level in ("gold", "silver", "bronze"):
            def _level_matches(item):
                rating = _as_int(item.get("rating"), 0)
                tier = str(player_meta(_as_int(item.get("resourceId"), 0)).get("tier", "") or "").lower()
                actual = tier if tier in ("gold", "silver", "bronze") else (
                    "gold" if rating >= 75 else "silver" if rating >= 65 else "bronze"
                )
                return actual == level
            items = [item for item in items if _level_matches(item)]

        exact_filters = (
            (("defId", "resourceId"), ("resourceId",)),
            (("maskedDefId", "assetId"), ("assetId",)),
            (("leag", "league", "leagueId"), ("leagueId",)),
            (("team", "club", "teamid"), ("teamid",)),
            (("nat", "nationality", "nation"), ("nation",)),
        )
        for request_names, item_names in exact_filters:
            wanted = _as_int(_search_value(*request_names), 0)
            if wanted:
                items = [item for item in items if any(
                    _as_int(item.get(item_name), 0) == wanted for item_name in item_names
                )]

        position = str(_search_value("pos", "position", default="") or "").upper()
        if position:
            items = [item for item in items
                     if str(item.get("preferredPosition", "") or "").upper() == position]

        rare = str(_search_value("rare", default="") or "").upper()
        if rare in ("SP", "SPECIAL"):
            items = [item for item in items if _as_int(item.get("rareflag"), 0) > 1]

        # My Club should default to highest-rated players first, just like native FUT.
        # Use stable tie-breakers so paging stays deterministic:
        #   1) rating descending
        #   2) rareflag descending
        #   3) resourceId descending
        #   4) item id ascending
        def _club_sort_key(item):
            try:
                rating = int(item.get("rating", 0) or 0)
            except Exception:
                rating = 0
            try:
                rareflag = int(item.get("rareflag", 0) or 0)
            except Exception:
                rareflag = 0
            try:
                rid = int(item.get("resourceId", item.get("assetId", 0)) or 0)
            except Exception:
                rid = 0
            try:
                iid = int(item.get("id", 0) or 0)
            except Exception:
                iid = 0
            return (-rating, -rareflag, -rid, iid)

        items = sorted(items, key=_club_sort_key)
        if str(_search_value("sort", default="desc") or "desc").lower() == "asc":
            items.reverse()

        # FIFA 21 paginates My Club with a start/offset + count query.
        try:
            count = int(_search_value("count", "num", "pageSize", default=len(items)) or len(items))
        except Exception:
            count = len(items)

        start = max(0, _as_int(_search_value("start", "offset", "startIndex", default=0), 0))

        # Some clients use 1-based page numbers rather than an explicit offset.
        if start == 0 and _search_value("page") is not None:
            try:
                page = max(1, int(_search_value("page", default=1) or 1))
                start = (page - 1) * max(1, count)
            except Exception:
                pass

        end = start + max(0, count)
        page_items = items[start:end]

        log.info(
            "CLUB PAGE start=%d count=%d total=%d returned=%d query=%s",
            start, count, len(items), len(page_items), search
        )

        return 200, {}, json_bytes({
            "pileClubPlayers": club_players,
            "pileSquadPlayers": sum(1 for item in STATE.list_items("squad")
                                      if str(item.get("itemType", "player")) == "player"),
            "pileConsumables": sum(1 for item in all_club_items
                                     if str(item.get("itemType", "")).lower()
                                     in ("consumable", "training", "development")),
            "pileManagers": club_managers,
            "pileClubItems": club_total,
            "pileClubTotal": club_total,
            "pilePack": len(STATE.list_items("unassigned")),
            "kitCount": club_kits, "badgeCount": club_badges,
            "stadiumCount": club_stadiums, "ballCount": club_balls,
            "clubPlayers": club_players,
            "clubItemCount": club_total,
            "clubCount": club_total,
            "itemCount": len(items),
            "itemData": page_items,
        })

    if low == "/ut/game/fifa21/user/credits":
        return 200, {}, json_bytes(_credits_payload())

    if ("/ut/game/fifa21/store/purchasegroup/" in low
            and any(token in low for token in
                    ("/cardpack", "/mypacks", "/unopened", "/recovered", "/purchased"))):
        return 200, {"Cache-Control": "no-store"}, json_bytes(_store_mypacks())

    if low == "/ut/game/fifa21/store/purchasegroup/all":
        store_payload = _store_purchasegroups()
        log.warning("STORE-SCOUT PURCHASEGROUP RESPONSE %s", json.dumps(store_payload, ensure_ascii=False))
        return 200, {"Cache-Control": "no-store"}, json_bytes(store_payload)

    if low == "/ut/game/fifa21/purchased/items":
        if method in ("POST", "PUT"):
            request = _purchase_request_document(payload, headers, body, query)
            try:
                pack_id = _purchase_pack_id(request)
                pack_cfg = get_pack_cfg(pack_id)
                if not pack_cfg:
                    raise ValueError(f"unknown pack id {pack_id}")
            except ValueError as error:
                log.warning("PACK_OPEN_REJECTED reason=%s request=%s", error, request)
                return 400, {}, json_bytes({"code": "400", "reason": str(error)})

            owned_reward = pack_id in STATE.unopened_pack_ids()
            price = 0 if owned_reward else max(0, int(pack_cfg.get("price", 0)))
            currency = str(request.get("currency") or request.get("currencyId") or "COINS").upper()
            use_credits = (not owned_reward and
                           str(request.get("useCredits", 1)).strip().lower()
                           not in ("", "0", "false", "no", "off"))
            if not owned_reward and currency not in ("COINS", "CREDITS"):
                log.warning("PACK_OPEN_REJECTED packId=%s reason=unsupported-currency currency=%s", pack_id, currency)
                return 400, {}, json_bytes({"code": "400", "reason": f"unsupported local pack currency {currency}"})
            if use_credits:
                if STATE.credits() < price:
                    log.warning("PACK_OPEN_REJECTED packId=%s reason=insufficient-coins price=%s credits=%s",
                                pack_id, price, STATE.credits())
                    return 200, {}, json_bytes({
                        "packId": pack_id,
                        "purchasePackType": "CARDPACK",
                        "state": "INSUFFICIENTFUNDS",
                        "duplicateItemIdList": [],
                        "itemList": [],
                        "itemData": [],
                        **_credits_payload(),
                    })
                STATE.add_credits(-price)
            log.warning("CARDS-SCOUT PACK_OPEN_BEGIN packId=%s ownedClub=%d ownedSquad=%d",
                        pack_id, len(STATE.list_items("club")), len(STATE.list_items("squad")))
            try:
                pack = STATE.create_pack(pack_id)
                if owned_reward and not STATE.take_unopened_pack(pack_id):
                    raise RuntimeError("owned reward pack is no longer available")
            except Exception as error:
                if use_credits:
                    STATE.add_credits(price)
                log.exception("PACK_OPEN_FAILED packId=%s refunded=%s", pack_id, use_credits)
                return 500, {}, json_bytes({"code": "500", "reason": f"local pack generation failed: {error}"})
            log.warning("CARDS-SCOUT PACK_OPEN_ITEMS ids=%s resources=%s duplicates=%s",
                        [x.get("id") for x in pack.get("itemData", [])],
                        [x.get("resourceId") for x in pack.get("itemData", [])],
                        pack.get("duplicateItemIdList", []))
            ordered_pack_items = [_native_pack_item(x) for x in pack.get("itemData", [])]
            real_resources = [x.get("resourceId") for x in ordered_pack_items]
            # The client resolves player cards by resourceId. Keep it native;
            # reveal order is already persisted in ids/timestamps by create_pack.
            ordered_pack_items = list(ordered_pack_items)
            log.warning("PACK DISPLAY ORDER ratings=%s rareflags=%s ids=%s timestamps=%s realResources=%s aliasResources=%s",
                        [x.get("rating") for x in ordered_pack_items],
                        [x.get("rareflag") for x in ordered_pack_items],
                        [x.get("id") for x in ordered_pack_items],
                        [x.get("timestamp") for x in ordered_pack_items],
                        real_resources,
                        [x.get("resourceId") for x in ordered_pack_items])
            pack["itemData"] = ordered_pack_items
            return 200, {}, json_bytes(_pack_purchase_response(pack, _next_purchase_transaction_id()))
        # When there are unassigned pack cards, preserve their duplicate markers on
        # refreshes of purchased/items. With no unassigned cards this stays exactly
        # the normal empty bootstrap shape used while entering FUT.
        unassigned = STATE.list_items("unassigned")

        # FIFA refreshes /purchased/items after the pack-open POST and uses this
        # refreshed itemData order for the reveal/New Items screen. Keep this
        # order identical to create_pack(): highest rating first.
        def _unassigned_reveal_sort_key(item):
            try:
                rating = int(item.get("rating", 0) or 0)
            except Exception:
                rating = 0
            try:
                rareflag = int(item.get("rareflag", 0) or 0)
            except Exception:
                rareflag = 0
            try:
                rid = int(item.get("resourceId", item.get("assetId", 0)) or 0)
            except Exception:
                rid = 0
            # Same ordering as create_pack(): specials first, then OVR descending.
            is_special = bool(item.get("special", False)) or rareflag > 1
            is_manager = str(item.get("itemType", "player")) == "manager"
            return (2 if is_manager else 0 if is_special else 1, -rating, -rareflag, -rid)

        unassigned = sorted(unassigned, key=_unassigned_reveal_sort_key)

        duplicate_item_list = []
        for item in unassigned:
            if str(item.get("itemType", "player")) != "player":
                continue
            try:
                iid = int(item.get("id", 0) or 0)
                rid = int(item.get("resourceId", item.get("assetId", 0)) or 0)
            except Exception:
                continue
            owned_item = STATE.owned_player_item_by_resource_id(rid)
            owned_iid = int(owned_item.get("id", 0) or 0) if isinstance(owned_item, dict) else 0
            if iid and owned_iid:
                duplicate_item_list.append({"itemId": iid, "duplicateItemId": owned_iid})
        native_unassigned = [_native_pack_item(x) for x in unassigned]
        real_resources = [x.get("resourceId") for x in native_unassigned]
        log.warning("UNASSIGNED DISPLAY ORDER ratings=%s rareflags=%s realResources=%s aliasResources=%s",
                    [x.get("rating") for x in native_unassigned],
                    [x.get("rareflag") for x in native_unassigned],
                    real_resources,
                    [x.get("resourceId") for x in native_unassigned])
        return 200, {}, json_bytes({
            "numberItems": len(native_unassigned),
            "duplicateItemIdList": duplicate_item_list,
            "itemData": native_unassigned,
        })

    # Native player-pick discovery/redeem/selection routes. FIFA 21 console
    # builds use both playerpick(s) URLs and POST /item/<tokenId>, so support
    # the complete family while returning the same persisted choice set.
    if low == "/ut/game/fifa21/item/nontargeted" and method in ("POST", "PUT"):
        # FIFA 21 sends an empty body here and identifies the unlockable in the
        # query string (itemId). Returning the old generic {} leaves the reveal
        # screen waiting forever before it can construct the four choices.
        action_payload = dict(payload) if isinstance(payload, dict) else {}
        for key in ("itemId", "playerPickItemId", "playerPickId", "tokenId",
                    "unlockableItemId", "selectedItemId", "selectedPlayerId",
                    "choiceId", "selectedResourceId", "choiceIndex"):
            values = query.get(key)
            if values and key not in action_payload:
                action_payload[key] = values[0]
        token_id = _player_pick_token_id(low, action_payload)
        token = STATE.get_item(token_id) if token_id else None
        if not (isinstance(token, dict)
                and str(token.get("itemType", "")).lower() == "misc"
                and int(token.get("cardsubtypeid", token.get("subtype", 0)) or 0) == 237):
            return 404, {"Cache-Control": "no-store"}, json_bytes({
                "code": "404", "reason": "Player-pick item not found",
            })
        try:
            has_selection, selection = _player_pick_selection(action_payload)
            response = (_player_pick_select_response(token_id, selection)
                        if has_selection else _player_pick_redeem_response(token_id))
        except (ValueError, RuntimeError) as error:
            log.warning("PLAYER_PICK_REJECTED token=%s reason=%s payload=%s",
                        token_id, error, action_payload)
            return 400, {}, json_bytes({"code": "400", "reason": str(error)})
        return 200, {"Cache-Control": "no-store"}, json_bytes(response)

    # The real FIFA 21 client's confirmation request is
    # POST /playerpicks/item/<chosenId>/select with an EMPTY body -- the id of
    # the player the user tapped travels in the URL, not the body.  The
    # generic "playerpick" handler below only ever looks at the body for a
    # selection, so an empty body always fell through to re-issuing the
    # original 4-choice redeem response instead of granting the pick.  The
    # client then renders that mismatched response as a "reward" and crashes.
    # Match this exact shape first and treat the URL id as the choice.
    select_match = re.fullmatch(r"/ut/game/fifa21/playerpicks/item/(\d+)/select", low)
    if select_match and method in ("POST", "PUT"):
        chosen_id = int(select_match.group(1))
        token_id = _player_pick_token_id(low, payload)
        if not token_id:
            return 200, {"Cache-Control": "no-store"}, json_bytes({
                "items": [], "itemData": [], "playerPicks": [],
                "choiceData": [], "availablePicks": 0,
            })
        try:
            response = _player_pick_select_response(token_id, chosen_id)
        except (ValueError, RuntimeError) as error:
            log.warning("PLAYER_PICK_REJECTED token=%s chosen=%s reason=%s",
                        token_id, chosen_id, error)
            return 400, {}, json_bytes({"code": "400", "reason": str(error)})
        return 200, {"Cache-Control": "no-store"}, json_bytes(response)

    if "playerpick" in low:
        token_id = _player_pick_token_id(low, payload)
        if not token_id:
            return 200, {"Cache-Control": "no-store"}, json_bytes({
                "items": [], "itemData": [], "playerPicks": [],
                "choiceData": [], "availablePicks": 0,
            })
        try:
            has_selection, selection = _player_pick_selection(payload)
            response = (_player_pick_select_response(token_id, selection)
                        if method in ("POST", "PUT") and has_selection
                        else _player_pick_redeem_response(token_id))
        except (ValueError, RuntimeError) as error:
            log.warning("PLAYER_PICK_REJECTED token=%s reason=%s payload=%s",
                        token_id, error, payload)
            return 400, {}, json_bytes({"code": "400", "reason": str(error)})
        return 200, {"Cache-Control": "no-store"}, json_bytes(response)

    player_pick_item_action = re.fullmatch(r"/ut/game/fifa21/item/(\d+)", low)
    if player_pick_item_action and method in ("POST", "PUT"):
        token_id = int(player_pick_item_action.group(1))
        token = STATE.get_item(token_id)
        if (isinstance(token, dict)
                and str(token.get("itemType", "")).lower() == "misc"
                and int(token.get("cardsubtypeid", token.get("subtype", 0)) or 0) == 237):
            try:
                has_selection, selection = _player_pick_selection(payload)
                response = (_player_pick_select_response(token_id, selection)
                            if has_selection else _player_pick_redeem_response(token_id))
            except (ValueError, RuntimeError) as error:
                log.warning("PLAYER_PICK_REJECTED token=%s reason=%s payload=%s",
                            token_id, error, payload)
                return 400, {}, json_bytes({"code": "400", "reason": str(error)})
            return 200, {"Cache-Control": "no-store"}, json_bytes(response)

    if low == "/ut/game/fifa21/tradepile":
        return 200, {}, json_bytes({
            "auctionInfo": [], "credits": STATE.credits(), "total": 0,
            "count": 0, "maxAuctionsAllowed": 100, "offered": 0,
            "selling": 0, "sold": 0, "itemData": [],
        })

    # Native FUT applies consumables by definition ID, not owned instance ID.
    position_apply = re.fullmatch(r"/ut/game/fifa21/item/resource/(\d+)", low)
    if position_apply and method == "POST":
        apply_rows = payload.get("apply", []) if isinstance(payload, dict) else []
        if isinstance(apply_rows, dict):
            apply_rows = [apply_rows]
        target_ids = []
        for row in apply_rows if isinstance(apply_rows, list) else []:
            raw_id = row.get("id", row.get("itemId")) if isinstance(row, dict) else row
            try:
                target_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
        if not target_ids:
            return 400, {}, json_bytes({"reason": "missing position modifier target"})
        try:
            changed = STATE.apply_position_modifier(int(position_apply.group(1)), target_ids[0])
        except ValueError as error:
            log.warning("Position modifier rejected resource=%s target=%s reason=%s",
                        position_apply.group(1), target_ids[0], error)
            return 400, {}, json_bytes({"reason": str(error)})
        log.info("Applied position modifier resource=%s target=%s newPosition=%s",
                 position_apply.group(1), target_ids[0], changed.get("preferredPosition"))
        # Native ApplyCard contract (verified working on Aurora17's FIFA 17
        # client, same CardsDLL lineage): the live apply screen iterates
        # itemData and replaces its in-memory player from each element.  The
        # old bare {} left it nothing to patch, which crashed the client on
        # apply -- the same missing-contract bug the player-pick select had.
        return 200, {"Cache-Control": "no-store"}, json_bytes({
            "itemData": [_native_pack_item(changed)],
            "consume": True,
            "applyTo": int(target_ids[0]),
        })

    # The New Items screen resolves the already-owned side of duplicate cards
    # through this lookup after a pack animation is skipped.  Returning the
    # generic unknown-route payload ({}) leaves the screen waiting forever.
    if low == "/ut/game/fifa21/item" and method == "GET":
        requested_ids = []
        for raw_value in query.get("idList", []):
            for raw_id in str(raw_value).split(","):
                try:
                    item_id = int(raw_id.strip())
                except (TypeError, ValueError):
                    continue
                if item_id > 0 and item_id not in requested_ids:
                    requested_ids.append(item_id)
                if len(requested_ids) >= 200:
                    break
            if len(requested_ids) >= 200:
                break

        items = []
        for item_id in requested_ids:
            item = STATE.get_item(item_id)
            if isinstance(item, dict):
                items.append(_native_pack_item(item))
        log.info("Resolved FUT item lookup requested=%s found=%s",
                 requested_ids, [item.get("id") for item in items])
        return 200, {}, json_bytes({"itemData": items})

    # FIFA 21 resolves the already-owned side of some duplicate pack cards by
    # instance URL instead of idList. Missing this variant makes the native
    # results scene poll the same item about twenty times per second forever.
    single_item_lookup = re.fullmatch(r"/ut/game/fifa21/item/(\d+)", low)
    if single_item_lookup and method == "GET":
        item_id = int(single_item_lookup.group(1))
        item = STATE.get_item(item_id)
        if not isinstance(item, dict):
            log.info("Single FUT item lookup missing id=%d", item_id)
            return 404, {}, json_bytes({"reason": "item not found"})
        native_item = _native_pack_item(item)
        log.info("Resolved single FUT item lookup id=%d resource=%s",
                 item_id, native_item.get("resourceId"))
        return 200, {"Cache-Control": "no-store"}, json_bytes({"itemData": [native_item]})

    # ---- Native FIFA discard / quick-sell routes -------------------------
    # Single item:
    #   DELETE /ut/game/fifa21/item/<itemId>
    # Multiple items ("Quick Sell All"):
    #   POST /ut/delete/game/fifa21/item
    #
    # FIFA expects totalCredits + items[] in the response.
    single_discard = re.match(r"^/ut/game/fifa21/item/(\d+)$", low)
    if single_discard and method == "DELETE":
        iid = int(single_discard.group(1))
        total_credits, deleted_ids = STATE.discard_items([iid])
        return 200, {}, json_bytes({
            "totalCredits": total_credits,
            "credits": total_credits,
            "items": [{"id": x} for x in deleted_ids],
            "currencies": [{
                "name": "coins",
                "funds": total_credits,
                "finalFunds": total_credits,
            }],
        })

    if low == "/ut/delete/game/fifa21/item" and method == "POST":
        ids = []
        if isinstance(payload, dict):
            raw_ids = payload.get("itemId", [])
            if not isinstance(raw_ids, list):
                raw_ids = [raw_ids]
            ids.extend(raw_ids)

            # Be tolerant if this particular PC build sends itemData instead.
            raw_data = payload.get("itemData", [])
            if isinstance(raw_data, dict):
                raw_data = [raw_data]
            if isinstance(raw_data, list):
                for x in raw_data:
                    if isinstance(x, dict):
                        ids.append(x.get("id") or x.get("itemId"))

        total_credits, deleted_ids = STATE.discard_items(ids)
        return 200, {}, json_bytes({
            "totalCredits": total_credits,
            "credits": total_credits,
            "items": [{"id": x} for x in deleted_ids],
            "currencies": [{
                "name": "coins",
                "funds": total_credits,
                "finalFunds": total_credits,
            }],
        })

    if low == "/ut/game/fifa21/item" and method in ("PUT", "POST"):
        log.info("FUT item update method=%s payload=%s", method, json.dumps(payload, sort_keys=True, separators=(",", ":")))
        results = []
        duplicate_results = []
        data_list = payload.get("itemData", []) if isinstance(payload, dict) else []
        if isinstance(data_list, dict):
            data_list = [data_list]
        batch_mode = len(data_list) > 1

        for change in data_list:
            try:
                iid = int(change.get("id") or change.get("itemId"))
            except Exception:
                continue
            pile = str(change.get("pile", "club"))
            moving_item = STATE.get_item(iid)
            is_temporary_loan = bool(moving_item and int(moving_item.get("loans", 0) or 0) > 0)

            requested_state = str(change.get("itemState", "") or "")
            if (moving_item and str(moving_item.get("itemType", "")) == "kit"
                    and requested_state in ("activeHomeKit", "activeAwayKit")):
                try:
                    equipped = STATE.equip_kit(iid, requested_state)
                    results.append({"id": iid, "pile": "club", "itemState": requested_state,
                                    "success": True})
                    log.info("Equipped kit item=%d resource=%s state=%s", iid,
                             equipped.get("resourceId"), requested_state)
                except ValueError as error:
                    results.append({"id": iid, "pile": str(moving_item.get("pile", "club")),
                                    "success": False, "reason": str(error)})
                continue

            if moving_item and requested_state in ("activeBadge", "activeBall"):
                try:
                    equipped = STATE.equip_club_customisation(iid, requested_state)
                    results.append({"id": iid, "pile": "club", "itemState": requested_state,
                                    "success": True})
                    log.info("Equipped customisation item=%d resource=%s state=%s", iid,
                             equipped.get("resourceId"), requested_state)
                except ValueError as error:
                    results.append({"id": iid, "pile": str(moving_item.get("pile", "club")),
                                    "success": False, "reason": str(error)})
                continue

            if (pile in ("club", "squad") and not is_temporary_loan
                    and STATE.would_duplicate_owned_player(iid)):
                # Keep the duplicate physically in Unassigned in every case.
                STATE.mark_item_duplicate(iid, True)
                duplicate_results.append({"itemId": iid})

                if batch_mode:
                    # Store All: explicitly acknowledge the item while telling FIFA
                    # that its resulting pile is still Unassigned.  v5 omitted the
                    # duplicate from itemData entirely; the database was correct but
                    # the native client optimistically animated every submitted card
                    # into the club.  Pairing this result with duplicateItemIdList
                    # gives the client both pieces of state without using success=False
                    # (which made FIFA 21 treat the whole batch as fatal in v3).
                    results.append({
                        "id": iid,
                        "pile": "unassigned",
                        "success": True,
                    })
                    log.info("Store All kept duplicate item %d visibly unassigned", iid)
                    continue

                # Native FIFA 21 PC handles duplicate state in NewItemsScreen itself.
                # Returning a failed /item move causes this build to tear down FUT auth,
                # so acknowledge the request while preserving the card in Unassigned.
                results.append({
                    "id": iid,
                    "pile": "unassigned",
                    "success": True,
                    "IS_DUPLICATE": True,
                })
                log.info("Kept duplicate item %d in unassigned using native UI marker", iid)
                continue

            ok = STATE.update_item_pile(iid, pile)
            results.append({"id": iid, "pile": pile, "success": ok})

        response = {"itemData": results}
        if duplicate_results:
            response["duplicateItemIdList"] = duplicate_results
        return 200, {}, json_bytes(response)

    suffix = low
    m = re.match(r"^/ut/(?:game/)?[^/]+/(.*)$", low)
    if m:
        suffix = "/" + m.group(1)

    if suffix in ("/usermassinfo", "/user/massinfo"):
        return 200, {}, json_bytes(user_mass_info())
    if suffix.startswith("/defid"):
        rid = 0
        try:
            rid = int(query.get("defId", [0])[0])
        except Exception:
            pass
        variants = []
        if rid:
            for vrid, vmeta in PLAYER_DB.items():
                try:
                    asset = int(vmeta.get("assetId", vrid) or vrid)
                except Exception:
                    continue
                if asset != rid and int(vrid) != rid:
                    continue
                variants.append({
                    "id": 0, "assetId": asset, "resourceId": int(vrid), "itemType": "player",
                    "rareflag": int(vmeta.get("rareflag", 1)), "rating": int(vmeta.get("rating", 0)),
                    "discardValue": STATE.player_discard_value(int(vrid)), "preferredPosition": str(vmeta.get("preferredPosition", "")),
                    "leagueId": int(vmeta.get("leagueId", 0)), "teamid": int(vmeta.get("teamid", 0)),
                    "nation": int(vmeta.get("nation", 0)), "attributeList": player_attribute_list(int(vrid)),
                })
            variants.sort(key=lambda x: int(x.get("rating", 0)), reverse=True)
        return 200, {}, json_bytes({"itemData": variants})

    if CFG.get("debug_unknown_http", True):
        log.warning("Unknown FUT/POW request: %s %s body=%s", method, raw_path, body[:500])
    return 200, {"X-LocalFUT15-Unknown": "1"}, json_bytes({})


class FutHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LocalFUT15/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        raw_path = getattr(self, "path", "")
        if raw_path.startswith("//"):
            raw_path = "/" + raw_path.lstrip("/")
        try:
            path = "/" + urllib.parse.urlsplit(raw_path).path.lstrip("/").lower().rstrip("/")
        except ValueError:
            path = ""
        if path in LOCAL_AUTH_PATHS:
            log.info("FUT8080 %s - local auth %s %s", self.client_address[0], self.command, path)
            return
        log.info("FUT8080 %s - %s", self.client_address[0], fmt % args)

    def _go(self) -> None:
        n = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(n) if n else b""
        hdrs = {k: v for k, v in self.headers.items()}
        try:
            status, extra, out = route_fut(self.command, self.path, hdrs, body)
        except Exception:
            log.exception("FUT handler error")
            status, extra, out = 500, {}, json_bytes({"error": "local server exception"})
        self.send_response(status)
        content_type = extra.pop("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Connection", "keep-alive")
        if self.path.startswith("/ut") or self.path.startswith("//ut"):
            self.send_header("X-UT-SID", _ut_sid())
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(out)

    do_GET = _go
    do_POST = _go
    do_PUT = _go
    do_DELETE = _go
    do_OPTIONS = _go


class RedirectHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LocalFUT21Redirector/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("REDIR42231 %s - %s", self.client_address[0], fmt % args)

    def _reply(self) -> None:
        n = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(n) if n else b""
        seq = self.headers.get("X-BLAZE-SEQNO", "0")
        log.info("Redirector request %s %s body=%s", self.command, self.path, raw[:800])
        ip_int = 2130706433  # 127.0.0.1 in host-order integer form used by old redirector XML.
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<serverinstanceinfo>
    <address member="0">
        <valu>
            <hostname>127.0.0.1</hostname>
            <ip>{ip_int}</ip>
            <port>{int(CFG["blaze_port"])}</port>
        </valu>
    </address>
    <secure>0</secure>
</serverinstanceinfo>'''.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(xml)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(xml)

    do_POST = _reply
    do_GET = _reply


QOS_PING_REQUEST_ID = 1
QOS_PING_REQUEST_SECRET = 0
QOS_BANDWIDTH_REQUEST_ID = 1234
QOS_BANDWIDTH_REQUEST_SECRET = 5678
QOS_PING_HEADER_SIZE = 20
QOS_PING_RESPONSE_SIZE = 30
QOS_BANDWIDTH_RESPONSE_SIZE = 1200


def _qos_udp_response(data: bytes, peer: tuple[str, int]) -> tuple[bytes, str] | None:
    """Build the DirtySDK QoS response expected after Blaze PreAuth."""
    try:
        peer_ip = socket.inet_aton(peer[0])
    except OSError:
        return None

    peer_port = int(peer[1])
    if peer_port <= 0 or peer_port > 0xFFFF:
        return None

    if len(data) == 8:
        request_id, request_secret = struct.unpack(">II", data)
        if (request_id, request_secret) == (
            QOS_BANDWIDTH_REQUEST_ID,
            QOS_BANDWIDTH_REQUEST_SECRET,
        ):
            return data, "firewall"
        return None

    if len(data) < 12:
        return None

    request_id, request_secret = struct.unpack_from(">II", data, 4)
    if (
        len(data) in (18, QOS_PING_HEADER_SIZE)
        and request_id == QOS_PING_REQUEST_ID
        and request_secret == QOS_PING_REQUEST_SECRET
    ):
        response = bytearray(QOS_PING_RESPONSE_SIZE)
        response[: min(len(data), QOS_PING_HEADER_SIZE)] = data[:QOS_PING_HEADER_SIZE]
        response[QOS_PING_HEADER_SIZE:QOS_PING_HEADER_SIZE + 4] = peer_ip
        struct.pack_into(">H", response, QOS_PING_HEADER_SIZE + 4, peer_port)
        return bytes(response), "ping"

    if (
        len(data) == QOS_BANDWIDTH_RESPONSE_SIZE
        and request_id == QOS_BANDWIDTH_REQUEST_ID
        and request_secret == QOS_BANDWIDTH_REQUEST_SECRET
    ):
        response = bytearray(QOS_BANDWIDTH_RESPONSE_SIZE)
        response[:12] = data[:12]
        request_count = struct.unpack_from(">I", data, 12)[0]
        total_count = struct.unpack_from(">I", data, 16)[0]
        struct.pack_into(">I", response, 12, (request_count + 1) & 0xFFFFFFFF)
        struct.pack_into("<I", response, 16, total_count)
        struct.pack_into(">I", response, 20, 123456789)
        struct.pack_into(">H", response, 24, 3659)
        return bytes(response), "bandwidth"

    return None


class QosUdpHandler(socketserver.BaseRequestHandler):
    """Validated DirtySDK UDP ping, firewall, and bandwidth responder."""

    def handle(self) -> None:
        data, sock = self.request
        result = _qos_udp_response(data, self.client_address)
        if result is None:
            log.info(
                "QOS17502 UDP dropped unrecognized %dB datagram from %s:%d",
                len(data),
                *self.client_address,
            )
            return

        response, kind = result
        sock.sendto(response, self.client_address)
        log.info(
            "QOS17502 UDP %s %dB -> %dB for %s:%d",
            kind,
            len(data),
            len(response),
            *self.client_address,
        )


class ThreadingUDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True
    daemon_threads = True


class QosHandler(BaseHTTPRequestHandler):
    """FIFA 21 QoS HTTP service learned from the successful V3 capture."""
    protocol_version = "HTTP/1.1"
    server_version = "LocalFUT21QoS/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("QOS17502 %s - %s", self.client_address[0], fmt % args)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        path = parsed.path.lower()
        qos_ip_le = 16777343  # 127.0.0.1 interpreted as little-endian uint32.

        if path == "/qos/qos":
            qtyp = q.get("qtyp", ["1"])[0]
            if qtyp == "2":
                xml = (
                    f'<?xml version="1.0" encoding="utf-8"?><qos>'
                    f'<numprobes>10</numprobes><qosport>{int(CFG["qos_port"])}</qosport>'
                    f'<probesize>1200</probesize><qosip>{qos_ip_le}</qosip>'
                    f'<requestid>1234</requestid><reqsecret>5678</reqsecret></qos>'
                ).encode("utf-8")
            else:
                xml = (
                    f'<?xml version="1.0" encoding="utf-8"?><qos>'
                    f'<numprobes>0</numprobes><qosport>{int(CFG["qos_port"])}</qosport>'
                    f'<probesize>0</probesize><qosip>{qos_ip_le}</qosip>'
                    f'<requestid>1</requestid><reqsecret>0</reqsecret></qos>'
                ).encode("utf-8")
            status = 200
        elif path == "/qos/firewall":
            xml = (
                f'<?xml version="1.0" encoding="utf-8"?><firewall>'
                f'<ips><ips>{qos_ip_le}</ips><ips>{qos_ip_le}</ips></ips>'
                f'<numinterfaces>2</numinterfaces>'
                f'<ports><ports>{int(CFG["qos_port"])}</ports><ports>{int(CFG["qos_port"])}</ports></ports>'
                f'<requestid>1234</requestid><reqsecret>5678</reqsecret></firewall>'
            ).encode("utf-8")
            status = 200
        elif path == "/qos/firetype":
            xml = b'<?xml version="1.0" encoding="utf-8"?><firetype><firetype>2</firetype></firetype>'
            status = 200
        else:
            xml = b"not found"
            status = 404

        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8" if status == 200 else "text/plain")
        self.send_header("Content-Length", str(len(xml)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(xml)


class EaswHandler(BaseHTTPRequestHandler):
    """Legacy EASW service used between Blaze login and FUT /ut/auth."""
    protocol_version = "HTTP/1.1"
    server_version = "LocalFUT15EASW/0.1"

    ROSTER_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<squad>
  <squadInfoSet>
    <squadInfo platform="pc64">
      <dbSchemaCRC>596426fe3525c25b90593b800b9ce5c3c61ccab5</dbSchemaCRC>
      <dbMajor>1</dbMajor><dbMinor>0</dbMinor>
      <dbMajorCRC>0</dbMajorCRC><dbMinorCRC>0</dbMinorCRC>
      <dbMajorLoc></dbMajorLoc><dbMinorLoc></dbMinorLoc>
      <dbFUTVer>1</dbFUTVer><dbFUTCRC>0</dbFUTCRC><dbFUTLoc></dbFUTLoc>
    </squadInfo>
    <squadInfo platform="pc64">
      <dbSchemaCRC>00e20ae6ad44d892ad15ea7f060ce6d97ca9e730</dbSchemaCRC>
      <dbMajor>1</dbMajor><dbMinor>0</dbMinor>
      <dbMajorCRC>0</dbMajorCRC><dbMinorCRC>0</dbMinorCRC>
      <dbMajorLoc></dbMajorLoc><dbMinorLoc></dbMinorLoc>
      <dbFUTVer>1</dbFUTVer><dbFUTCRC>0</dbFUTCRC><dbFUTLoc></dbFUTLoc>
    </squadInfo>
  </squadInfoSet>
</squad>'''

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("EASW42232 %s - %s", self.client_address[0], fmt % args)

    def _send(self, status: int, body: bytes, content_type: str = "text/xml", extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", "0") or "0")
        if n:
            self.rfile.read(n)
        if urllib.parse.urlsplit(self.path).path.lower() == "/extension":
            self._send(
                200,
                b'<?xml version="1.0" encoding="UTF-8"?>\n<result/>\n',
                "text/xml",
                {
                    "EASW-Session": "fut15-local-easw-session",
                    "EASW-Token": "fut15-local-easw-token",
                },
            )
        else:
            self._send(200, b'<?xml version="1.0" encoding="UTF-8"?>\n<result/>\n')

    # OSDK routing table + content stubs, modelled on the proven Aurora17
    # (FIFA 17 private server) wire transcript: the client's GameServices
    # config loader fetches ROUTINGCFGFILE_URL (/routing) after
    # Util.FetchClientConfig, then pulls every network file listed in it from
    # CONTENT_URL. Advertise only resources that are actually available;
    # nonexistent network entries make the client abort initialization and
    # loop FetchClientConfig -> Logout.
    ROUTING_XML = b'''<?xml version="1.0" encoding="utf-8"?>
<routing fileVersion="2" refresh="1800">
  <files>
    <file service="dimecfglocal" type="local" base="data/store/" defaultFile="dimecfg.xml" />
    <file service="dimedesclocal" type="local" base="data/store/" defaultFile="dime_descriptions_%s.xml" modifier="locale" />
    <file service="storecfglocal" type="local" base="data/store/" defaultFile="storecfg.xml" />
  </files>
</routing>
'''
    DIMECFG_XML = b'''<?xml version="1.0" encoding="utf-8"?>
<DimeCfg>
  <version>1</version>
  <enabled>1</enabled>
</DimeCfg>
'''
    STORECFG_XML = b'''<?xml version="1.0" encoding="utf-8"?>
<StoreCfg>
  <version>1</version>
  <enabled>1</enabled>
</StoreCfg>
'''
    RESULT_XML = b'<?xml version="1.0" encoding="UTF-8"?>\n<result/>\n'
    EASW_HEADERS = {"EASW-Session": "fut15-local-easw-session", "EASW-Token": "fut15-local-easw-token"}

    @staticmethod
    def _content_request(path: str) -> tuple[Path | None, str | None]:
        """Resolve only the two content URL forms observed from FIFA."""
        prefixes = ("/contentfifa/", "/content/fifa/")
        relative = next((path[len(prefix):] for prefix in prefixes if path.startswith(prefix)), None)
        if relative is None or not relative or "\\" in relative:
            return None, None
        candidate = (CONTENT_ROOT / relative).resolve()
        root = CONTENT_ROOT.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None, None
        return candidate, relative

    @staticmethod
    def _content_type(path: Path) -> str:
        return {
            ".bin": "application/octet-stream",
            ".big": "application/octet-stream",
            ".csv": "text/csv; charset=utf-8",
            ".xml": "application/xml; charset=utf-8",
        }.get(path.suffix.lower(), "application/octet-stream")

    @staticmethod
    def _is_placeholder(body: bytes) -> bool:
        return not body or b"LOCALFUT_TEST_PLACEHOLDER" in body[:256]

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path.lower()
        if path == "/roster":
            self._send(200, self.ROSTER_XML, "text/xml", self.EASW_HEADERS)
            return
        if path in ("/routing", "/routing/cfgrouting.xml"):
            self._send(200, self.ROUTING_XML, "text/xml; charset=utf-8", self.EASW_HEADERS)
            return
        if path == "/ctl":
            self._send(200, self.RESULT_XML, "text/xml", self.EASW_HEADERS)
            return
        # Never disguise missing binary/XML content as an unrelated success
        # document. The OSDK retries initialization when those fake bodies fail
        # to parse; a real 404 lets optional content remain unavailable.
        resource, relative = self._content_request(path)
        if resource is None:
            self._send(404, b"content route not found\n", "text/plain; charset=utf-8", self.EASW_HEADERS)
            return
        if not resource.is_file():
            log.warning("Missing FIFA content resource: %s", relative)
            self._send(404, b"content resource unavailable\n", "text/plain; charset=utf-8", self.EASW_HEADERS)
            return
        try:
            body = resource.read_bytes()
        except OSError:
            log.exception("Unable to read FIFA content resource: %s", relative)
            self._send(500, b"content resource read failure\n", "text/plain; charset=utf-8", self.EASW_HEADERS)
            return
        if self._is_placeholder(body):
            log.warning("Refusing placeholder FIFA content resource: %s", relative)
            self._send(404, b"content resource unavailable\n", "text/plain; charset=utf-8", self.EASW_HEADERS)
            return
        self._send(200, body, self._content_type(resource), self.EASW_HEADERS)


class SsfHandler(BaseHTTPRequestHandler):
    """FIFA 21 SSF session bootstrap used before the Origin auth-code step."""

    protocol_version = "HTTP/1.1"
    server_version = "LocalFUT21SSF/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("SSF10853 %s - %s", self.client_address[0], fmt % args)

    def _send(self, status: int, body: bytes, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        path = urllib.parse.urlsplit(self.path).path.lower()
        if path != "/sf/auth":
            self._send(404, b'{"error":"route_not_found"}')
            return

        session_id = self.headers.get("X-SF-SID", "") or "localfut21-ssf"
        current_time = now_s()
        body = json.dumps(
            {
                "serverTime": current_time,
                "lastOnlineTime": current_time,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        log.info("SSF auth accepted (session header present=%s)", bool(self.headers.get("X-SF-SID")))
        self._send(200, body, {"X-SF-SID": session_id})

    def do_GET(self) -> None:
        self._send(404, b'{"error":"route_not_found"}')


# ---- Blaze Fire2 / TDF ----------------------------------------------------
# FIFA 21's 44321 connection uses the Fire2 Blaze frame used by later
# BlazeSDK builds. The captured client packet is Util.PreAuth (component 9,
# command 7). 0.1.2 only recorded it; 0.1.3 parses/replies and keeps the
# session alive so later requests can be learned/handled locally.

_TDF_VARINT = 0x0
_TDF_STRING = 0x1
_TDF_BLOB = 0x2
_TDF_GROUP = 0x3
_TDF_LIST = 0x4
_TDF_MAP = 0x5
_TDF_UNION = 0x6
_TDF_VARIABLE = 0x7
_TDF_OBJECT_TYPE = 0x8
_TDF_OBJECT_ID = 0x9


def _tdf_tag(tag: str | bytes, value_type: int) -> bytes:
    """Encode a 1-4 character Blaze TDF tag plus type byte."""
    raw = tag.encode("ascii") if isinstance(tag, str) else bytes(tag)
    if not 1 <= len(raw) <= 4:
        raise ValueError(f"TDF tag must be 1-4 bytes: {raw!r}")
    out = [0, 0, 0, int(value_type) & 0xFF]
    if len(raw) > 0:
        out[0] |= (raw[0] & 0x40) << 1
        out[0] |= (raw[0] & 0x10) << 2
        out[0] |= (raw[0] & 0x0F) << 2
    if len(raw) > 1:
        out[0] |= (raw[1] & 0x40) >> 5
        out[0] |= (raw[1] & 0x10) >> 4
        out[1] |= (raw[1] & 0x0F) << 4
    if len(raw) > 2:
        out[1] |= (raw[2] & 0x40) >> 3
        out[1] |= (raw[2] & 0x10) >> 2
        out[1] |= (raw[2] & 0x0C) >> 2
        out[2] |= (raw[2] & 0x03) << 6
    if len(raw) > 3:
        out[2] |= (raw[3] & 0x40) >> 1
        out[2] |= raw[3] & 0x1F
    return bytes(out)


def _tdf_varint(value: int) -> bytes:
    value = int(value)
    if value < 0:
        # Blaze serializes signed integers through their unsigned bit pattern.
        value &= (1 << 64) - 1
    if value < 0x40:
        return bytes([value])
    out = bytearray([(value & 0x3F) | 0x80])
    value >>= 6
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _tdf_str_value(value: str) -> bytes:
    raw = str(value).encode("utf-8")
    return _tdf_varint(len(raw) + 1) + raw + b"\x00"


def _tdf_field_int(tag: str, value: int) -> bytes:
    return _tdf_tag(tag, _TDF_VARINT) + _tdf_varint(value)


def _tdf_field_str(tag: str, value: str) -> bytes:
    return _tdf_tag(tag, _TDF_STRING) + _tdf_str_value(value)


def _tdf_field_blob(tag: str, value: bytes) -> bytes:
    raw = bytes(value)
    return _tdf_tag(tag, _TDF_BLOB) + _tdf_varint(len(raw)) + raw


def _tdf_field_null_variable(tag: str) -> bytes:
    """Encode an absent Blaze Heat2 variable.

    Variables carry a one-byte presence flag before their type information.
    ResultNotification's DATA member is required by FIFA 21's Blaze schema even
    when the title has no custom result object to return.
    """
    return _tdf_tag(tag, _TDF_VARIABLE) + b"\x00"


def _tdf_field_object_id(tag: str, component: int, entity_type: int, entity_id: int) -> bytes:
    """Encode Blaze Heat2 ObjectId: component, entity type, then 64-bit id as varints."""
    return (
        _tdf_tag(tag, _TDF_OBJECT_ID)
        + _tdf_varint(component)
        + _tdf_varint(entity_type)
        + _tdf_varint(entity_id)
    )


def _tdf_field_group(tag: str, body: bytes) -> bytes:
    return _tdf_tag(tag, _TDF_GROUP) + body + b"\x00"


def _tdf_field_list_int(tag: str, values: list[int] | tuple[int, ...]) -> bytes:
    out = bytearray(_tdf_tag(tag, _TDF_LIST))
    out.append(_TDF_VARINT)
    out.extend(_tdf_varint(len(values)))
    for value in values:
        out.extend(_tdf_varint(value))
    return bytes(out)


def _tdf_field_list_groups(tag: str, groups: list[bytes]) -> bytes:
    out = bytearray(_tdf_tag(tag, _TDF_LIST))
    out.append(_TDF_GROUP)
    out.extend(_tdf_varint(len(groups)))
    for body in groups:
        out.extend(body)
        out.append(0)  # each group body terminator
    return bytes(out)


def _tdf_field_map_str_str(tag: str, values: list[tuple[str, str]]) -> bytes:
    out = bytearray(_tdf_tag(tag, _TDF_MAP))
    out.extend((_TDF_STRING, _TDF_STRING))
    out.extend(_tdf_varint(len(values)))
    for key, value in values:
        out.extend(_tdf_str_value(key))
        out.extend(_tdf_str_value(value))
    return bytes(out)


def _tdf_field_map_str_group(tag: str, values: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(_tdf_tag(tag, _TDF_MAP))
    out.extend((_TDF_STRING, _TDF_GROUP))
    out.extend(_tdf_varint(len(values)))
    for key, body in values:
        out.extend(_tdf_str_value(key))
        out.extend(body)
        out.append(0)  # group terminator
    return bytes(out)


def _tdf_decode_varint(raw: bytes, pos: int = 0) -> tuple[int, int]:
    if pos >= len(raw):
        raise ValueError("truncated TDF varint")
    first = raw[pos]
    pos += 1
    value = first & 0x3F
    shift = 6
    if first & 0x80:
        while True:
            if pos >= len(raw):
                raise ValueError("truncated TDF varint")
            b = raw[pos]
            pos += 1
            value |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
    return value, pos


def _tdf_get_string(raw: bytes, tag: str) -> str | None:
    marker = _tdf_tag(tag, _TDF_STRING)
    p = raw.find(marker)
    if p < 0:
        return None
    p += 4
    try:
        n, p = _tdf_decode_varint(raw, p)
    except ValueError:
        return None
    if n <= 0 or p + n > len(raw):
        return None
    value = raw[p:p+n]
    if value.endswith(b"\x00"):
        value = value[:-1]
    return value.decode("utf-8", errors="replace")


def _tdf_get_int(raw: bytes, tag: str) -> int | None:
    marker = _tdf_tag(tag, _TDF_VARINT)
    p = raw.find(marker)
    if p < 0:
        return None
    try:
        value, _ = _tdf_decode_varint(raw, p + len(marker))
        return value
    except ValueError:
        return None



def _tdf_debug_summary(raw: bytes) -> str:
    """Best-effort non-mutating TDF scout used only for compatibility logging."""
    if not raw:
        return "<empty>"
    # Extract printable ASCII runs; method/adaptor names often survive here.
    strings = []
    cur = bytearray()
    for b in raw:
        if 32 <= b <= 126:
            cur.append(b)
        else:
            if len(cur) >= 3:
                strings.append(cur.decode("ascii", errors="replace"))
            cur.clear()
    if len(cur) >= 3:
        strings.append(cur.decode("ascii", errors="replace"))

    # Best-effort scan for packed Blaze TDF 3-char tags. Do not attempt to parse
    # values recursively; malformed guesses are harmless because this is logging only.
    tags = []
    for i in range(max(0, len(raw) - 3)):
        chunk = raw[i:i+3]
        # Packed TDF tags commonly decode to uppercase alnum/_ names. Keep only
        # plausible candidates to avoid a wall of noise.
        try:
            v = int.from_bytes(chunk, "big")
            chars = [
                ((v >> 18) & 0x3F) + 32,
                ((v >> 12) & 0x3F) + 32,
                ((v >> 6) & 0x3F) + 32,
                (v & 0x3F) + 32,
            ]
            name = ''.join(chr(c) for c in chars).rstrip()
            if 2 <= len(name) <= 4 and all(c.isupper() or c.isdigit() or c == '_' for c in name):
                if name not in tags:
                    tags.append(name)
        except Exception:
            pass
        if len(tags) >= 24:
            break
    return f"hex={raw[:256].hex()} ascii={strings[:20]} tags={tags}"

def _blaze_preauth_payload() -> bytes:
    """FIFA 21 PC PreAuth payload reconstructed from the successful Aurora trace.

    The important part is QOSS: FIFA uses the Blaze reply to discover the QoS
    HTTP service. 0.1.8 conflated that service with Blaze itself; the original
    topology uses Blaze on 44321 and QoS on 17502.
    """
    core_conf = _tdf_field_map_str_str(
        "CONF",
        [
            ("connIdleTimeout", "120s"),
            ("defaultRequestTimeout", "80s"),
            ("pingPeriod", "20s"),
            ("voipHeadsetUpdateRate", "1000"),
            ("xlspConnectionIdleTimeout", "300"),
            *local_auth_config().items(),
        ],
    )

    bwps = bytearray()
    bwps += _tdf_field_str("PSA", "127.0.0.1")
    bwps += _tdf_field_int("PSP", int(CFG["qos_port"]))
    bwps += _tdf_field_str("SNA", "qos")

    latency = bytearray()
    latency += _tdf_field_str("PSA", "127.0.0.1")
    latency += _tdf_field_int("PSP", int(CFG["qos_port"]))
    latency += _tdf_field_str("SNA", "ea-sjc")

    qoss = bytearray()
    qoss += _tdf_field_group("BWPS", bytes(bwps))
    qoss += _tdf_field_int("LNP", 10)
    qoss += _tdf_field_map_str_group("LTPS", [("ea-sjc", bytes(latency))])
    qoss += _tdf_field_int("SVID", 1161889797)
    qoss += _tdf_field_int("TIME", 5000)

    body = bytearray()
    body += _tdf_field_str("ASRC", "303107")
    body += _tdf_field_list_int("CIDS", [1, 9, 35, 30722])
    body += _tdf_field_group("CONF", core_conf)
    body += _tdf_field_int("EEFA", 1)
    body += _tdf_field_str("ESRC", "303107")
    body += _tdf_field_str("INST", "fifa-2015-pc")
    body += _tdf_field_int("MAID", 89)
    body += _tdf_field_int("MINR", 0)
    body += _tdf_field_str("NASP", "cem_ea_id")
    body += _tdf_field_str("PILD", "")
    body += _tdf_field_str("PLAT", "pc")
    body += _tdf_field_group("QOSS", bytes(qoss))
    body += _tdf_field_str("RSRC", "303107")
    body += _tdf_field_str("SVER", "Blaze 14.2.1.1.0")
    return bytes(body)


def _blaze_auth_payload() -> bytes:
    persona = int(CFG["persona_id"])
    nucleus = int(CFG["nucleus_id"])
    session = bytearray()
    session += _tdf_field_int("1CON", 0)
    session += _tdf_field_int("BUID", persona)
    session += _tdf_field_int("FRST", 0)
    session += _tdf_field_str("KEY", STATE.token())
    session += _tdf_field_int("LLOG", now_s())
    session += _tdf_field_str("MAIL", "local@localhost")
    pdtl = bytearray()
    pdtl += _tdf_field_str("DSNM", str(CFG["display_name"]))
    pdtl += _tdf_field_int("LAST", 0)
    pdtl += _tdf_field_int("PID", persona)
    pdtl += _tdf_field_int("PLAT", 4)
    pdtl += _tdf_field_int("STAS", 0)
    pdtl += _tdf_field_int("XREF", nucleus)
    session += _tdf_field_group("PDTL", bytes(pdtl))
    session += _tdf_field_int("UID", nucleus)

    body = bytearray()
    body += _tdf_field_int("CNTX", 0)
    body += _tdf_field_int("ERRC", 0)
    body += _tdf_field_str("SKEY", STATE.token())
    body += _tdf_field_int("ANON", 0)
    body += _tdf_field_int("NTOS", 0)
    body += _tdf_field_group("SESS", bytes(session))
    body += _tdf_field_int("SPAM", 0)
    body += _tdf_field_int("UNDR", 0)
    return bytes(body)


def _blaze_entitlements_payload() -> bytes:
    ent = bytearray()
    ent += _tdf_field_str("DEVI", "")
    ent += _tdf_field_str("GDAY", "2014-09-25T00:00Z")
    ent += _tdf_field_str("GNAM", "FIFA15PC")
    ent += _tdf_field_int("ID", 1024871)
    ent += _tdf_field_int("ISCO", 0)
    ent += _tdf_field_int("PID", 0)
    ent += _tdf_field_str("PJID", "1024871")
    ent += _tdf_field_int("PRCA", 2)
    ent += _tdf_field_str("PRID", "FIFA15_PC_ONLINE_ACCESS")
    ent += _tdf_field_int("STAT", 1)
    ent += _tdf_field_int("STRC", 0)
    ent += _tdf_field_str("TAG", "ONLINE_ACCESS")
    ent += _tdf_field_str("TDAY", "")
    ent += _tdf_field_int("TYPE", 1)
    ent += _tdf_field_int("UCNT", 0)
    ent += _tdf_field_int("VER", 0)
    return _tdf_field_list_groups("NLST", [bytes(ent)])


def _blaze_postauth_payload() -> bytes:
    telemetry = bytearray()
    telemetry += _tdf_field_str("ADRS", "127.0.0.1")
    telemetry += _tdf_field_int("ANON", 0)
    telemetry += _tdf_field_str("DISA", "")
    telemetry += _tdf_field_str("FILT", "-UION/****")
    telemetry += _tdf_field_int("PORT", 0)
    telemetry += _tdf_field_int("SDLY", 15000)
    telemetry += _tdf_field_str("SESS", "LOCALFUT15")
    telemetry += _tdf_field_str("SKEY", "")
    telemetry += _tdf_field_int("SPCT", 0)

    ticker = bytearray()
    ticker += _tdf_field_str("ADRS", "127.0.0.1")
    ticker += _tdf_field_int("PORT", 0)
    ticker += _tdf_field_str("SKEY", "")

    urop = bytearray()
    urop += _tdf_field_int("TMOP", 1)
    urop += _tdf_field_int("UID", int(CFG["persona_id"]))

    body = bytearray()
    body += _tdf_field_group("TELE", bytes(telemetry))
    body += _tdf_field_group("TICK", bytes(ticker))
    body += _tdf_field_group("UROP", bytes(urop))
    return bytes(body)


def _blaze_ping_payload() -> bytes:
    # Different Blaze generations use STIM or TIME. Supplying both is harmless
    # for tag-addressed TDF readers and maximizes compatibility with FIFA 21.
    t = now_s()
    return _tdf_field_int("STIM", t) + _tdf_field_int("TIME", t)


def _blaze_empty_config_payload() -> bytes:
    return _tdf_field_map_str_str("CONF", [])


@dataclass
class Fire2Packet:
    payload_size: int
    metadata_size: int
    component: int
    command: int
    msg_num: int
    msg_type: int
    metadata: bytes
    payload: bytes
    raw: bytes


def _fire2_try_parse(buf: bytearray) -> Fire2Packet | None:
    if len(buf) < 16:
        return None
    payload_size = int.from_bytes(buf[0:4], "big")
    metadata_size = int.from_bytes(buf[4:6], "big")
    total = 16 + metadata_size + payload_size
    if payload_size > 16 * 1024 * 1024 or metadata_size > 1024 * 1024:
        raise ValueError(f"unreasonable Fire2 sizes payload={payload_size} metadata={metadata_size}")
    if len(buf) < total:
        return None
    raw = bytes(buf[:total])
    del buf[:total]
    component = int.from_bytes(raw[6:8], "big")
    command = int.from_bytes(raw[8:10], "big")
    msg_num = int.from_bytes(raw[10:13], "big")
    msg_type = raw[13] >> 5
    metadata = raw[16:16 + metadata_size]
    payload = raw[16 + metadata_size:]
    return Fire2Packet(payload_size, metadata_size, component, command, msg_num, msg_type, metadata, payload, raw)


def _fire2_build(component: int, command: int, msg_num: int, msg_type: int, payload: bytes = b"", metadata: bytes = b"") -> bytes:
    out = bytearray()
    out += len(payload).to_bytes(4, "big")
    out += len(metadata).to_bytes(2, "big")
    out += int(component).to_bytes(2, "big")
    out += int(command).to_bytes(2, "big")
    out += (int(msg_num) & 0xFFFFFF).to_bytes(3, "big")
    out += bytes([(int(msg_type) & 0x07) << 5, 0, 0])
    out += metadata
    out += payload
    return bytes(out)


def _save_fire2(packet: Fire2Packet, peer: tuple[str, int], kind: str = "rx") -> Path:
    stamp = f"{int(time.time()*1000)}-{peer[0].replace(':','_')}-{peer[1]}"
    path = LOGS / f"fire2-{kind}-c{packet.component}-k{packet.command}-m{packet.msg_num}-{stamp}.bin"
    path.write_bytes(packet.raw)
    path.with_suffix(".hex.txt").write_text(packet.raw.hex(" "), encoding="utf-8")
    return path


def _blaze_fifa35_auth_bootstrap_payload() -> bytes:
    """Exact FIFA 21 c35/k10 login-response shape learned from the V3 trace."""
    persona = int(CFG["persona_id"])

    pdtl = bytearray()
    pdtl += _tdf_field_str("DSNM", str(CFG["display_name"]))
    pdtl += _tdf_field_int("PID", persona)
    pdtl += _tdf_field_int("PLAT", 4)

    sess = bytearray()
    sess += _tdf_field_int("1CON", 0)
    sess += _tdf_field_int("BUID", persona)
    sess += _tdf_field_int("FRST", 0)
    sess += _tdf_field_str("KEY", "localfut15-blaze")
    sess += _tdf_field_int("LLOG", now_s())
    sess += _tdf_field_str("MAIL", "localfut15@localhost")
    sess += _tdf_field_group("PDTL", bytes(pdtl))
    sess += _tdf_field_int("UID", persona)

    body = bytearray()
    body += _tdf_field_int("ANON", 0)
    body += _tdf_field_int("NTOS", 0)
    body += _tdf_field_group("SESS", bytes(sess))
    body += _tdf_field_int("SPAM", 1)
    body += _tdf_field_int("UNDR", 0)
    return bytes(body)


def _blaze_qos_data_group() -> bytes:
    body = bytearray()
    body += _tdf_field_int("DBPS", 0)
    body += _tdf_field_int("NATT", 0)
    body += _tdf_field_int("UBPS", 0)
    return bytes(body)


def _blaze_extended_data_minimal() -> bytes:
    """Minimal UserSessionExtendedData used in the captured login notifications."""
    body = bytearray()
    body += _tdf_field_int("HWFG", 0)
    body += _tdf_field_group("QDAT", _blaze_qos_data_group())
    body += _tdf_field_int("UATT", 0)
    return bytes(body)


def _blaze_notify_user_added() -> bytes:
    """UserSessions notification 2 (UserAdded), rebuilt with local identity."""
    persona = int(CFG["persona_id"])
    user = bytearray()
    user += _tdf_field_int("AID", persona)
    user += _tdf_field_int("ALOC", 1701729619)
    user += _tdf_field_blob("EXBB", b"")
    user += _tdf_field_int("EXID", 0)
    user += _tdf_field_int("ID", persona)
    user += _tdf_field_str("NAME", str(CFG["display_name"]))
    # These FIFA-specific additions are present in the successful V3 packet.
    user += _tdf_field_str("NASP", "cem_ea_id")
    user += _tdf_field_int("ORIG", persona)
    user += _tdf_field_int("PIDI", 0)

    body = bytearray()
    body += _tdf_field_group("DATA", _blaze_extended_data_minimal())
    body += _tdf_field_group("USER", bytes(user))
    return bytes(body)


def _blaze_notify_login_session() -> bytes:
    """FIFA 21's UserSessions notification 8 captured immediately after c35/k10."""
    persona = int(CFG["persona_id"])
    body = bytearray()
    body += _tdf_field_int("BUID", persona)
    body += _tdf_field_object_id("CGID", 30722, 2, persona)
    body += _tdf_field_str("DSNM", str(CFG["display_name"]))
    body += _tdf_field_str("KEY", "localfut15-blaze")
    body += _tdf_field_int("LAST", now_s())
    body += _tdf_field_str("MAIL", "localfut15@localhost")
    body += _tdf_field_str("NASP", "cem_ea_id")
    body += _tdf_field_int("PID", persona)
    body += _tdf_field_int("PLAT", 0)
    body += _tdf_field_int("UID", persona)
    body += _tdf_field_int("USTP", 0)
    body += _tdf_field_int("XREF", 0)
    return bytes(body)


def _blaze_notify_extended_data_update() -> bytes:
    """UserSessions notification 1, the small login-time extended-data update."""
    body = bytearray()
    body += _tdf_field_group("DATA", _blaze_extended_data_minimal())
    body += _tdf_field_int("USID", int(CFG["persona_id"]))
    return bytes(body)


def _blaze_notify_user_updated() -> bytes:
    """UserSessions notification 5 (UserUpdated)."""
    body = bytearray()
    body += _tdf_field_int("FLGS", 3)
    body += _tdf_field_int("ID", int(CFG["persona_id"]))
    return bytes(body)


def _blaze_game_reporting_result(report_id: int = 0) -> bytes:
    """Terminal result for GameReporting.SubmitOfflineGameReport.

    The RPC reply only acknowledges receipt. FIFA waits for notification 114
    before tearing down the gameplay session and returning to FUT.
    """
    report_id = max(0, int(report_id or 0))
    body = bytearray()
    body += _tdf_field_null_variable("DATA")
    body += _tdf_field_int("EROR", 0)
    body += _tdf_field_int("FNL", 1)
    body += _tdf_field_int("GHID", report_id)
    body += _tdf_field_int("GRID", report_id)
    return bytes(body)


def _blaze_login_notifications() -> list[tuple[int, int, bytes, str]]:
    # Exact order observed between the c35/k10 reply and Util.PostAuth.
    return [
        (30722, 2, _blaze_notify_user_added(), "UserSessions.UserAdded"),
        (30722, 8, _blaze_notify_login_session(), "UserSessions.LoginSession"),
        (30722, 1, _blaze_notify_extended_data_update(), "UserSessions.ExtendedDataUpdate"),
        (30722, 5, _blaze_notify_user_updated(), "UserSessions.UserUpdated"),
    ]


def _blaze_osdk_core_config() -> list[tuple[str, str]]:
    fut = int(CFG["fut_port"])
    easw = int(CFG["easw_port"])
    ssf = int(CFG["ssf_port"])
    return [
        ("AUTH_TYPE", "NUCLEUS"),
        ("CONTENT_URL", f"http://127.0.0.1:{easw}/content"),
        ("CTL_UPDATE_URL", f"http://127.0.0.1:{easw}/ctl"),
        ("FIFA_POW_CONTENT_SERVER_URL", f"http://127.0.0.1:{fut}/"),
        ("FIFA_POW_NUCLEUS_PROXY_URL", f"http://127.0.0.1:{fut}/"),
        ("FIFA_POW_URL", f"http://127.0.0.1:{fut}/"),
        ("FIFA_SSF_CLIENT_ID", LOCAL_AUTH_SSF_CLIENT_ID),
        ("FIFA_SSF_URL", f"http://127.0.0.1:{ssf}"),
        ("FUT_RS4_BASE_URL", f"http://127.0.0.1:{fut}/"),
        ("FUTDYNAMICMESSAGES_CUSTOMURL", f"127.0.0.1:{fut}"),
        ("FUTDYNAMICMESSAGES_URL_BASE", f"http://127.0.0.1:{fut}"),
        ("NUCLEUS_LOGIN_ENABLED", "1"),
        ("ONLINE/FUTDYNAMICMESSAGES_CUSTOMURL", f"127.0.0.1:{fut}"),
        ("ONLINE/FUTDYNAMICMESSAGES_TUTORIAL_MSG_URL", f"http://127.0.0.1:{fut}/"),
        ("ORIGIN_LOGIN_ENABLED", "1"),
        ("OSDK_AUTH_REQUIRED", "1"),
        ("OSDK_EASW_AUTH_URL", f"http://127.0.0.1:{easw}"),
        ("OSDK_EASW_EVENT_URL", f"http://127.0.0.1:{easw}"),
        ("OSDK_EASW_MEDIA_URL", f"http://127.0.0.1:{easw}"),
        ("OSDK_EASW_REQ_URL", f"http://127.0.0.1:{easw}"),
        ("ROSTERUPDATE_URL", f"http://127.0.0.1:{easw}/roster"),
        ("ROUTINGCFGFILE_URL", f"http://127.0.0.1:{easw}/routing"),
        ("USE_TOKEN_AUTH", "1"),
    ]


def _blaze_fetch_config_payload(cfid: str) -> bytes:
    items = _blaze_osdk_core_config() if cfid == "OSDK_CORE" else []
    body = bytearray()
    body += _tdf_field_map_str_str("CONF", items)
    body += _tdf_field_str("ID", cfid)
    body += _tdf_field_str("ITYP", "")
    body += _tdf_field_str("TOKN", "")
    return bytes(body)


def _blaze_telemetry_payload() -> bytes:
    body = bytearray()
    body += _tdf_field_str("ADRS", "127.0.0.1")
    body += _tdf_field_int("ANON", 0)
    body += _tdf_field_str("DISA", "1")
    body += _tdf_field_str("FILT", "")
    body += _tdf_field_int("LOC", 1701729619)
    body += _tdf_field_str("NOOK", "")
    body += _tdf_field_int("PORT", 9988)
    body += _tdf_field_int("SDLY", 0)
    body += _tdf_field_str("SESS", "")
    body += _tdf_field_str("SKEY", "")
    body += _tdf_field_int("SPCT", 0)
    body += _tdf_field_str("STIM", "")
    return bytes(body)


def _blaze_fifa_postauth_payload() -> bytes:
    pss = bytearray()
    pss += _tdf_field_int("PORT", 0)
    pss += _tdf_field_int("RPRT", 0)
    pss += _tdf_field_int("TIID", 0)

    tick = bytearray()
    tick += _tdf_field_str("ADRS", "127.0.0.1")
    tick += _tdf_field_int("PORT", 8999)
    tick += _tdf_field_str("SKEY", "")

    urop = bytearray()
    urop += _tdf_field_int("TMOP", 0)
    urop += _tdf_field_int("UID", int(CFG["persona_id"]))

    body = bytearray()
    body += _tdf_field_group("PSS", bytes(pss))
    body += _tdf_field_group("TELE", _blaze_telemetry_payload())
    body += _tdf_field_group("TICK", bytes(tick))
    body += _tdf_field_group("UROP", bytes(urop))
    return bytes(body)


def _blaze_entitlements_fifa21_payload() -> bytes:
    groups: list[bytes] = []
    for idx, gnam in enumerate(("FIFA15PCBoxContent", "FIFA14PC"), start=1):
        ent = bytearray()
        ent += _tdf_field_str("DEVI", "")
        ent += _tdf_field_str("GDAY", "")
        ent += _tdf_field_str("GNAM", gnam)
        ent += _tdf_field_int("ID", idx)
        ent += _tdf_field_int("ISCO", 0)
        ent += _tdf_field_int("PID", int(CFG["persona_id"]))
        ent += _tdf_field_str("PJID", "")
        ent += _tdf_field_int("PRCA", 0)
        ent += _tdf_field_str("PRID", "")
        ent += _tdf_field_int("STAT", 1)
        ent += _tdf_field_int("STRC", 0)
        ent += _tdf_field_str("TAG", "ONLINE_ACCESS")
        ent += _tdf_field_str("TDAY", "")
        ent += _tdf_field_int("TYPE", 1)
        ent += _tdf_field_int("UCNT", 0)
        ent += _tdf_field_int("VER", 0)
        groups.append(bytes(ent))
    return _tdf_field_list_groups("NLST", groups)


def _fire2_local_response(packet: Fire2Packet) -> tuple[bytes, str]:
    c, k = packet.component, packet.command

    # Util component - these shapes now mirror the successful FIFA 21 PC trace.
    if (c, k) == (9, 7):
        return _blaze_preauth_payload(), "Util.PreAuth"
    if (c, k) == (9, 2):
        return _tdf_field_int("STIM", now_s()), "Util.Ping"
    if (c, k) == (9, 1):
        cfid = _tdf_get_string(packet.payload, "CFID") or ""
        log.info("Util.FetchClientConfig CFID=%s", cfid)
        return _blaze_fetch_config_payload(cfid), f"Util.FetchClientConfig({cfid})"
    if (c, k) == (9, 8):
        return _blaze_fifa_postauth_payload(), "Util.PostAuth"
    if (c, k) == (9, 5):
        return _blaze_telemetry_payload(), "Util.GetTelemetryServer"
    if (c, k) == (9, 12):
        return _tdf_field_map_str_str("SMAP", [("FirstTimeFlag", "0")]), "Util.GetUserOptions"
    if (c, k) == (9, 10):
        key = _tdf_get_string(packet.payload, "KEY") or ""
        value = "0" if key == "FirstTimeFlag" else ""
        return _tdf_field_str("DATA", value) + _tdf_field_str("KEY", key), f"Util.GetUserOption({key})"
    if (c, k) in ((9, 22), (9, 28)):
        return b"", "Util.ClientState/Metrics"

    # FIFA 21 title-specific login bridge. The V3 capture proved this is not
    # an AUTH/EXTB/EXTI echo: the response is a FullLogin-style session object.
    if (c, k) == (35, 10):
        log.info(
            "FIFA35 login request: payload=%s; replying with captured-compatible local session",
            packet.payload.hex(),
        )
        return _blaze_fifa35_auth_bootstrap_payload(), "FIFA35.LoginSession"

    # Component 10/5 is a repeated subscription-state exchange.  The client
    # sends exactly one RSUB varint (0 or 1); an empty response causes it to
    # retry roughly thirty times per second and can starve the player-details
    # scene.  This request and response share the same one-field shape.
    if (c, k) == (10, 5):
        subscribed = _tdf_get_int(packet.payload, "RSUB")
        return _tdf_field_int("RSUB", 1 if subscribed else 0), "Subscription.SetState"

    # SubmitOfflineGameReport completes asynchronously. The handler emits the
    # required terminal ResultNotification immediately after this empty ack.
    if (c, k) == (28, 2):
        return b"", "GameReporting.SubmitOfflineGameReport"

    # UserSessions / game bootstrap calls that returned no body in the trace.
    if (c, k) in (
        (30722, 20), (30722, 8),
        (21, 10),
        (15, 2),
        (10, 1),
        (25, 6),
        (11, 2600),
        (7, 15), (7, 3),
        (11, 1600),
        (0, 0),
    ):
        return b"", "captured-empty"

    # FIFA ticker/filter support.
    if (c, k) == (2249, 1):
        entry = bytearray()
        entry += _tdf_field_str("ID", "O_TKfilter")
        entry += _tdf_field_int("LOCF", 0)
        entry += _tdf_field_int("TOGG", 0)
        return _tdf_field_list_groups("LSST", [bytes(entry)]), "Ticker.GetSettings"
    if (c, k) == (2249, 2):
        entry = bytearray()
        entry += _tdf_field_str("ID", "O_SG_TCKR")
        # list<string> LSET
        lset = bytearray(_tdf_tag("LSET", _TDF_LIST))
        lset.append(_TDF_STRING)
        lset += _tdf_varint(1)
        lset += _tdf_str_value("O_TKfilter")
        entry += lset
        return _tdf_field_list_groups("LGRP", [bytes(entry)]), "Ticker.GetGroups"

    # Authentication. FIFA 21 asks for ListEntitlements (command 32) after the
    # title-specific login is complete.
    if c == 1 and k == 0x20:
        return _blaze_entitlements_fifa21_payload(), "Authentication.ListEntitlements"
    if c == 1 and k in (0x28, 0x32, 0x3C, 0x98, 0x0A):
        return _blaze_auth_payload(), f"Authentication.LocalLogin(0x{k:X})"
    if c == 1 and k in (0x1D, 0x30):
        return _blaze_entitlements_fifa21_payload(), f"Authentication.Entitlements(0x{k:X})"
    if c == 1 and k == 0x24:
        return _tdf_field_str("AUTH", STATE.token()), "Authentication.GetAuthToken"
    if c == 1 and k in (0x1E, 0x5A, 0x64, 0x6E):
        return _blaze_auth_payload(), f"Authentication.AccountPersona(0x{k:X})"
    if c == 1 and k == 0x46:
        return b"", "Authentication.Logout"

    return b"", "unknown-empty"


class BlazeOrHttpHandler(socketserver.BaseRequestHandler):
    """Port 44321 local Blaze listener (with legacy HTTP fallback)."""

    HTTP_PREFIXES = (b"GET ", b"POST ", b"PUT ", b"DELETE ", b"OPTIONS ", b"HEAD ")

    def handle(self) -> None:
        sock: socket.socket = self.request
        peer = self.client_address
        sock.settimeout(120)
        try:
            first = sock.recv(65536)
        except Exception:
            return
        if not first:
            return
        if first.startswith(self.HTTP_PREFIXES):
            self._http(sock, first)
            return

        # Poll the otherwise-idle persistent Blaze connection so an HTTP
        # /match/end settlement can immediately publish UserUpdated.  The old
        # 120-second blocking recv delayed that notification until FIFA made a
        # later request (commonly only after leaving and reopening Draft).
        sock.settimeout(0.100)
        idle_deadline = time.monotonic() + 120.0
        buf = bytearray(first)
        while True:
            try:
                packet = _fire2_try_parse(buf)
            except Exception as exc:
                stamp = f"{int(time.time()*1000)}-{peer[1]}"
                raw_path = LOGS / f"blaze44321-unparsed-{stamp}.bin"
                raw_path.write_bytes(bytes(buf))
                log.exception("44321 Fire2 framing failed; saved %s: %s", raw_path.name, exc)
                return

            if packet is None:
                try:
                    more = sock.recv(65536)
                except socket.timeout:
                    if PENDING_USERSESSION_REFRESH.is_set():
                        notification = _fire2_build(
                            30722, 5, 0, 2, _blaze_notify_user_updated()
                        )
                        try:
                            sock.sendall(notification)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            return
                        PENDING_USERSESSION_REFRESH.clear()
                        log.warning("FIRE2 TX NOTIFY POST-MATCH UserSessions.UserUpdated (idle push)")
                    if time.monotonic() >= idle_deadline:
                        log.info("44321 client %s:%s timed out", *peer)
                        return
                    continue
                except (ConnectionResetError, ConnectionAbortedError):
                    return
                if not more:
                    return
                idle_deadline = time.monotonic() + 120.0
                buf.extend(more)
                continue

            path = _save_fire2(packet, peer, "rx")
            log.info(
                "FIRE2 RX c=%d cmd=%d(0x%X) msg=%d type=%d payload=%d metadata=%d saved=%s",
                packet.component, packet.command, packet.command, packet.msg_num,
                packet.msg_type, len(packet.payload), len(packet.metadata), path.name,
            )

            # Fire2 Message=0; Reply=1; Ping=4; PingReply=5.
            if packet.msg_type == 4:
                reply = _fire2_build(packet.component, packet.command, packet.msg_num, 5, b"")
                sock.sendall(reply)
                log.info("FIRE2 TX PingReply msg=%d", packet.msg_num)
                continue
            if packet.msg_type != 0:
                continue

            payload, label = _fire2_local_response(packet)
            reply = _fire2_build(packet.component, packet.command, packet.msg_num, 1, payload)
            try:
                sock.sendall(reply)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            log.info(
                "FIRE2 TX %s c=%d cmd=%d msg=%d payload=%d",
                label, packet.component, packet.command, packet.msg_num, len(payload),
            )

            if (packet.component, packet.command) == (28, 2):
                report_id = _tdf_get_int(packet.payload, "GRID") or 0
                # Match the proven local flow: the async completion follows the
                # RPC acknowledgement after a tiny scheduling gap.
                time.sleep(0.020)
                notification = _fire2_build(
                    28, 114, 0, 2, _blaze_game_reporting_result(report_id)
                )
                try:
                    sock.sendall(notification)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                log.warning(
                    "FIRE2 TX NOTIFY GameReporting.ResultNotification reportId=%s",
                    report_id,
                )

            if (PENDING_USERSESSION_REFRESH.is_set() and
                    (packet.component, packet.command) != (28, 2)):
                notification = _fire2_build(
                    30722, 5, 0, 2, _blaze_notify_user_updated()
                )
                try:
                    sock.sendall(notification)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                PENDING_USERSESSION_REFRESH.clear()
                log.warning("FIRE2 TX NOTIFY POST-MATCH UserSessions.UserUpdated")

            # The retail FIFA 21 build can enter the authenticated session through
            # either the title-specific component-35 login or Authentication.LocalLogin.
            # Both successful replies must be followed by the same UserSessions
            # notification sequence or the client waits 20 seconds and logs out.
            if label in ("FIFA35.LoginSession", "Authentication.LocalLogin(0xA)"):
                for ncomp, ncmd, npayload, nlabel in _blaze_login_notifications():
                    nframe = _fire2_build(ncomp, ncmd, 0, 2, npayload)
                    try:
                        sock.sendall(nframe)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                    log.info(
                        "FIRE2 TX NOTIFY %s c=%d cmd=%d payload=%d",
                        nlabel, ncomp, ncmd, len(npayload),
                    )

            if label == "unknown-empty":
                scout = _tdf_debug_summary(packet.payload)
                log.warning(
                    "CARDS-SCOUT Unhandled Blaze request c=%d cmd=%d(0x%X) msg=%d payload=%d :: %s",
                    packet.component, packet.command, packet.command, packet.msg_num, len(packet.payload), scout,
                )
                try:
                    scout_path = LOGS / f"cards-scout-c{packet.component}-k{packet.command}-m{packet.msg_num}-{int(time.time()*1000)}.json"
                    scout_path.write_text(json.dumps({
                        "component": packet.component, "command": packet.command, "commandHex": hex(packet.command),
                        "msgNum": packet.msg_num, "payloadHex": packet.payload.hex(), "summary": scout,
                    }, indent=2), encoding="utf-8")
                    log.warning("CARDS-SCOUT saved %s", scout_path.name)
                except Exception:
                    log.exception("CARDS-SCOUT failed to save diagnostic")

    def _http(self, sock: socket.socket, first: bytes) -> None:
        buf = bytearray(first)
        while b"\r\n\r\n" not in buf and len(buf) < 1024 * 1024:
            more = sock.recv(65536)
            if not more:
                break
            buf.extend(more)
        head, _, rest = bytes(buf).partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
        req = lines[0].split(" ")
        if len(req) < 2:
            return
        method, path = req[0], req[1]
        hdrs: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                hdrs[k.strip()] = v.strip()
        need = int(hdrs.get("Content-Length", "0") or "0")
        body = bytearray(rest)
        while len(body) < need:
            more = sock.recv(min(65536, need - len(body)))
            if not more:
                break
            body.extend(more)
        status, extra, out = route_fut(method, path, hdrs, bytes(body[:need]))
        reason = "OK" if status == 200 else "Error"
        response_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(out)),
            "Connection": "close",
            "X-UT-SID": STATE.sid(),
            **extra,
        }
        raw = [f"HTTP/1.1 {status} {reason}\r\n"] + [f"{k}: {v}\r\n" for k, v in response_headers.items()] + ["\r\n"]
        sock.sendall("".join(raw).encode("ascii") + out)


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---- Local Origin LSX -----------------------------------------------------
# OriginSDK 9.x/10.x uses a server-first challenge, then AES-128-ECB encrypted
# hex frames. 0.1.1 incorrectly waited for a plaintext client request first,
# which deadlocked the SDK and could make FIFA exit on the language screen.


class OriginRandom:
    """Origin/EA SDK deterministic PRNG used for LSX session-key derivation."""

    def __init__(self, seed: int):
        self.seed = int(seed) & 0xFFFFFFFF

    def set_seed(self, seed: int) -> None:
        self.seed = int(seed) & 0xFFFFFFFF

    def next(self) -> int:
        # Same LCG used by the legacy Origin SDK.
        self.seed = (self.seed * 214013 + 2531011) & 0xFFFFFFFF
        return (self.seed >> 16) & 0x7FFF


class OriginCrypto:
    def __init__(self, seed: int = 0):
        self.key = bytes(range(16))
        self.set_key(seed)

    def set_key(self, seed: int) -> None:
        seed = int(seed) & 0xFFFFFFFF
        if seed == 0:
            self.key = bytes(range(16))
            return
        rng = OriginRandom(7)
        new_seed = (rng.next() + seed) & 0xFFFFFFFF
        rng.set_seed(new_seed)
        self.key = bytes((rng.next() & 0xFF) for _ in range(16))

    def encrypt(self, text: str) -> bytes:
        if Cipher is None:
            raise RuntimeError(f"cryptography package is unavailable: {_crypto_import_error}")
        padder = padding.PKCS7(128).padder()
        raw = padder.update(text.encode("utf-8")) + padder.finalize()
        enc = Cipher(algorithms.AES(self.key), modes.ECB()).encryptor()
        return enc.update(raw) + enc.finalize()

    def decrypt(self, ciphertext: bytes) -> str:
        if Cipher is None:
            raise RuntimeError(f"cryptography package is unavailable: {_crypto_import_error}")
        dec = Cipher(algorithms.AES(self.key), modes.ECB()).decryptor()
        padded = dec.update(ciphertext) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        raw = unpadder.update(padded) + unpadder.finalize()
        return raw.decode("utf-8")

    def challenge_response(self, challenge: str) -> str:
        response = self.encrypt(challenge).hex()
        b = response.encode("ascii")
        if len(b) < 2:
            raise ValueError("challenge response was too short")
        seed = (b[0] << 8) | b[1]
        self.set_key(seed)
        return response

    def set_session_from_response(self, response: str) -> int:
        b = response.encode("ascii", errors="strict")
        if len(b) < 2:
            raise ValueError("ChallengeResponse.response is too short")
        seed = (b[0] << 8) | b[1]
        self.set_key(seed)
        return seed


def _xml_attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _lsx_extract_request(request_xml: str) -> tuple[str, str, ET.Element | None]:
    rid = "0"
    name = "Unknown"
    op: ET.Element | None = None
    try:
        root = ET.fromstring(request_xml)
        req = root.find(".//Request")
        if req is not None:
            rid = req.attrib.get("id", rid)
            children = list(req)
            if children:
                op = children[0]
                name = op.tag.split("}")[-1]
    except Exception:
        pass
    return rid, name, op


def _lsx_response_envelope(rid: str, inner: str, sender: str = "EbisuSDK") -> str:
    return f'<LSX><Response id="{_xml_attr(rid)}" sender="{_xml_attr(sender)}">{inner}</Response></LSX>'


def _lsx_xml_response(request_xml: str) -> str:
    rid, name, op = _lsx_extract_request(request_xml)
    player = str(CFG["display_name"])
    nucleus = str(CFG["nucleus_id"])
    persona = str(CFG["persona_id"])

    if name == "GetConfig":
        services = (
            ("EbisuSDK", "SDK"),
            ("EbisuSDK", "PROFILE"),
            ("XMPP", "PRESENCE"),
            ("XMPP", "FRIENDS"),
            ("Commerce", "COMMERCE"),
            ("EbisuSDK", "RECENTPLAYER"),
            ("EbisuSDK", "IGO"),
            ("EbisuSDK", "MISC"),
            ("EALS", "LOGIN"),
            ("EbisuSDK", "UTILITY"),
            ("XMPP", "XMPP"),
            ("XMPP", "CHAT"),
            ("EbisuSDK", "IGO_EVENT"),
            ("EALS", "EALS_EVENTS"),
            ("EbisuSDK", "LOGIN_EVENT"),
            ("XMPP", "INVITE_EVENT"),
            ("EbisuSDK", "PROFILE_EVENT"),
            ("XMPP", "PRESENCE_EVENT"),
            ("XMPP", "FRIENDS_EVENT"),
            ("Commerce", "COMMERCE_EVENT"),
            ("XMPP", "CHAT_EVENT"),
            ("EbisuSDK", "DOWNLOAD_EVENT"),
            ("EbisuSDK", "PERMISSION"),
            ("EbisuSDK", "RESOURCES"),
            ("EbisuSDK", "BLOCKED_USERS"),
            ("EbisuSDK", "BLOCKED_USER_EVENT"),
            ("EbisuSDK", "GET_USERID"),
            ("EbisuSDK", "ONLINE_STATUS_EVENT"),
            ("EbisuSDK", "ACHIEVEMENT"),
            ("EbisuSDK", "ACHIEVEMENT_EVENT"),
            ("EbisuSDK", "BROADCAST_EVENT"),
            ("PI", "PROGRESSIVE_INSTALLATION"),
            ("PI", "PROGRESSIVE_INSTALLATION_EVENT"),
            ("EbisuSDK", "CONTENT"),
        )
        inner = '<GetConfigResponse>' + ''.join(
            f'<Service Name="{name}" Facility="{facility}" />' for name, facility in services
        ) + '</GetConfigResponse>'
    elif name == "GetSettings":
        inner = ('<GetSettingsResponse Language="en_US" Environment="production" '
                 'IsIGOAvailable="false" IsIGOEnabled="false" '
                 'IsTelemetryEnabled="false" IsManualOffline="false" />')
    elif name == "GetSetting":
        setting = ""
        if op is not None:
            setting_id = op.attrib.get("SettingId", "")
            setting = {
                "LANGUAGE": "en_US",
                "ENVIRONMENT": "production",
                "IS_IGO_AVAILABLE": "false",
                "IS_IGO_ENABLED": "false",
                "IS_TELEMETRY_ENABLED": "false",
                "IS_MANUAL_OFFLINE": "false",
            }.get(setting_id, "")
        inner = f'<GetSettingResponse Setting="{_xml_attr(setting)}" />'
    elif name == "GetInternetConnectedState":
        inner = '<InternetConnectedState connected="1" />'
    elif name == "GetUtcTime":
        utc = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        inner = f'<GetUtcTimeResponse Time="{utc}" />'
    elif name == "GetAuthToken":
        inner = f'<AuthToken value="{LOCAL_AUTH_ACCESS_TOKEN}" />'
    elif name == "GetAuthCode":
        inner = f'<AuthCode value="{LOCAL_AUTH_CODE}" />'
    elif name == "GetProfile":
        under_age = "true" if mock_account_is_under_age() else "false"
        inner = (f'<GetProfileResponse UserIndex="0" UserId="{_xml_attr(nucleus)}" '
                 f'PersonaId="{_xml_attr(persona)}" Persona="{_xml_attr(player)}" AvatarId="" '
                 f'Country="PT" IsUnderAge="{under_age}" IsSubscriber="false" '
                 'IsTrialSubscriber="false" SubscriberLevel="0" GeoCountry="PT" '
                 'CommerceCountry="PT" CommerceCurrency="EUR" IsSteamSubscriber="false" />')
    elif name == "GetBlockList":
        inner = '<GetBlockListResponse />'
    elif name == "QueryFriends":
        inner = '<QueryFriendsResponse />'
    elif name == "QueryAreFriends":
        inner = '<QueryAreFriendsResponse />'
    elif name in ("QueryContent",):
        inner = '<QueryContentResponse />'
    elif name in ("QueryEntitlements",):
        inner = '<QueryEntitlementsResponse />'
    elif name == "GetPresence":
        inner = '<GetPresenceResponse />'
    elif name == "QueryPresence":
        inner = '<QueryPresenceResponse />'
    elif name == "GetWalletBalance":
        inner = '<GetWalletBalanceResponse Balance="0" Currency="EUR" />'
    elif name == "GetGameInfo":
        # Legacy OriginSDK/FIFA 21 asks for individual title-state values.
        # Returning an empty GetGameInfoResponse makes the client interpret
        # UPTODATE as false and show the mandatory-update dialog.
        game_info_id = ""
        if op is not None:
            game_info_id = op.attrib.get("GameInfoId", "").upper()
        game_info_values = {
            "UPTODATE": "true",
            "LANGUAGES": "en_US,pt_PT,fr_FR,de_DE,es_ES,it_IT",
            "FREETRIAL": "false",
            "EXPIRATION": "0000-00-00T00:00:00",
            "EXPIRATION_DURATION": "0",
            "INSTALLED_VERSION": "1.0.0.0",
            "INSTALLED_LANGUAGE": "en_US",
            "AVAILABLE_VERSION": "1.0.0.0",
            "DISPLAY_NAME": "EA SPORTS FIFA 21",
            "FULLGAME_PURCHASED": "true",
            "FULLGAME_IS_RELEASED": "true",
            "FULLGAME_RELEASE_DATE": "2014-09-25T00:00:00",
            "MAX_GROUP_SIZE": "22",
            "ENTITLEMENT_SOURCE": "LOCAL",
        }
        game_info = game_info_values.get(game_info_id, "")
        log.info("LSX GetGameInfo %s -> %s", game_info_id or "<missing>", game_info)
        inner = f'<GetGameInfoResponse GameInfo="{_xml_attr(game_info)}" />'
    elif name == "GetAllGameInfo":
        now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        inner = (f'<GetAllGameInfoResponse UpToDate="true" '
                 'Languages="en_US,pt_PT,fr_FR,de_DE,es_ES,it_IT" FreeTrial="false" '
                 'FullGamePurchased="true" FullGameReleased="true" '
                 'FullGameReleaseDate="2014-09-25T00:00:00" Expiration="" '
                 f'SystemTime="{now}" HasExpiration="false" InstalledVersion="1.0.0.0" '
                 'InstalledLanguage="en_US" AvailableVersion="1.0.0.0" '
                 'DisplayName="EA SPORTS FIFA 21" MaxGroupSize="22" EntitlementSource="LOCAL" />')
    elif name == "RequestLicense":
        # RequestLicenseResponse REQUIRES the License attribute (types.rs in Maxima
        # declares it non-optional). Maxima's offline mode returns License="".
        # An attribute-less response makes the SDK treat the license as invalid
        # and FIFA 21 dies ~7s after the LSX answer.
        inner = '<RequestLicenseResponse License="" />'
    else:
        # Most setter/notification calls use ErrorSuccess in OriginSDK. Keeping
        # the response explicit also makes unsupported calls obvious in the log.
        inner = '<ErrorSuccess Code="0" Description="Local FUT success" />'
        log.warning("LSX unmodelled request %s; returning generic ErrorSuccess", name)

    return _lsx_response_envelope(rid, inner)


def _recv_nul_frame(sock: socket.socket, buffer: bytearray) -> bytes | None:
    while b"\x00" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            return None
        buffer.extend(chunk)
    idx = buffer.index(0)
    frame = bytes(buffer[:idx])
    del buffer[: idx + 1]
    return frame


def _send_plain_frame(sock: socket.socket, xml: str) -> None:
    sock.sendall(xml.encode("utf-8") + b"\x00")


def _send_encrypted_frame(sock: socket.socket, crypto: OriginCrypto, xml: str) -> None:
    payload = crypto.encrypt(xml).hex().encode("ascii") + b"\x00"
    sock.sendall(payload)


class LSXHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        sock: socket.socket = self.request
        sock.settimeout(30)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        peer = self.client_address
        log.info("Origin LSX client connected from %s:%s", *peer)
        buffer = bytearray()

        if Cipher is None:
            log.error("Origin LSX requires the Python 'cryptography' package: %s", _crypto_import_error)
            return

        # 1) Server must speak first. This is the critical fix over 0.1.1.
        challenge_key = secrets.token_hex(16).upper()
        challenge = (f'<LSX><Event sender="EALS"><Challenge key="{challenge_key}" '
                     'version="3" build="10.6.1.8" /></Event></LSX>')
        try:
            _send_plain_frame(sock, challenge)
            log.info("LSX challenge sent to %s:%s key=%s", *peer, challenge_key)

            # 2) ChallengeResponse is still plaintext/NUL-delimited.
            frame = _recv_nul_frame(sock, buffer)
            if frame is None:
                log.warning("LSX client disconnected before ChallengeResponse")
                return
            text = frame.decode("utf-8", errors="replace")
            log.info("LSX plaintext handshake request: %s", text[:3000])
            rid, name, op = _lsx_extract_request(text)
            if name != "ChallengeResponse" or op is None:
                log.warning("Expected ChallengeResponse, received %s", name)
                return
            response = op.attrib.get("response", "")
            returned_key = op.attrib.get("key", "")
            if len(response) < 2:
                log.warning("LSX ChallengeResponse had no usable response attribute")
                return

            # Exact legacy LSX challenge behavior, matching the open-source
            # Maxima Origin/LSX implementation:
            #   1) ChallengeResponse.response must be AES-128-ECB/PKCS7 of the
            #      server Challenge.key using the primary key 00..0f.
            #   2) ChallengeResponse.key is a *client-generated* MD5-ish nonce.
            #      The server encrypts THAT value with the primary key and returns
            #      it as ChallengeAccepted.response.
            #   3) Protocol version 2 keeps the primary 00..0f key for every
            #      subsequent encrypted LSX frame. Version 3 derives a secondary
            #      key from the first two ASCII bytes of the accept response.
            protocol_version = op.attrib.get("version", "3")
            primary_crypto = OriginCrypto(0)
            expected_response = primary_crypto.encrypt(challenge_key).hex()
            if response.lower() == expected_response.lower():
                log.info("LSX challenge response validated for protocol %s", protocol_version)
            else:
                log.warning("LSX challenge response did not validate; expected=%s got=%s", expected_response, response)
                return

            if not returned_key:
                log.warning("LSX ChallengeResponse is missing the client key attribute")
                return
            log.info("LSX client key attribute: %s", returned_key)

            # Server-side mutual challenge response. This was the missing piece in
            # 0.1.4/0.1.5: echoing the client's response is not valid for FIFA 21.
            accept_response = primary_crypto.encrypt(returned_key).hex()

            if protocol_version == "2":
                seed = 0
                crypto = OriginCrypto(0)
                log.info("LSX protocol 2 retaining primary OriginSDK AES key")
            elif protocol_version == "3":
                rb = accept_response.encode("ascii")
                seed = (rb[0] << 8) | rb[1]
                crypto = OriginCrypto(seed)
                log.info("LSX protocol 3 using ChallengeAccepted-derived session key (seed=%d)", seed)
            else:
                log.warning("Unsupported LSX ChallengeResponse version %s", protocol_version)
                return

            accepted = _lsx_response_envelope(
                rid,
                f'<ChallengeAccepted response="{_xml_attr(accept_response)}" />',
                sender="EALS",
            )
            _send_plain_frame(sock, accepted)
            log.info("LSX challenge accepted for %s:%s; protocol=%s session seed=%d", *peer, protocol_version, seed)

            # EA Desktop/Origin also exposes current login state as an LSX event.
            # FIFA 21 checks this before it attempts Blaze authentication.
            login_event = (
                '<LSX><Event sender="EbisuSDK">'
                '<Login IsLoggedIn="true" UserIndex="0" LoginReasonCode="ALREADY_ONLINE" />'
                '</Event></LSX>'
            )
            try:
                _send_encrypted_frame(sock, crypto, login_event)
                log.info("LSX local signed-in Login event sent to %s:%s", *peer)
            except Exception:
                log.exception("Unable to send LSX Login event to %s:%s", *peer)

            # 3) Subsequent LSX messages are ASCII hex of AES-128-ECB/PKCS7 XML.
            # Do not turn an idle game window into a false LSX disconnect. The
            # handshake has its own timeout; the established session remains
            # open until FIFA or the runtime closes it.
            sock.settimeout(None)
            log.info("LSX session entering idle-safe receive loop for %s:%s", *peer)
            while True:
                frame = _recv_nul_frame(sock, buffer)
                if frame is None:
                    break
                if not frame:
                    continue
                try:
                    encrypted = bytes.fromhex(frame.decode("ascii"))
                    request_xml = crypto.decrypt(encrypted)
                except Exception as exc:
                    stamp = f"{int(time.time()*1000)}-{peer[1]}"
                    (LOGS / f"lsx-decode-fail-{stamp}.bin").write_bytes(frame)
                    log.exception("LSX encrypted frame could not be decoded/decrypted: %s", exc)
                    break

                log.info("LSX decrypted request: %s", request_xml[:6000])
                response_xml = _lsx_xml_response(request_xml)
                log.info("LSX encrypted response: %s", response_xml[:6000])
                _send_encrypted_frame(sock, crypto, response_xml)

        except socket.timeout:
            log.info("Origin LSX client %s:%s timed out/closed", *peer)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            log.info("Origin LSX client %s:%s disconnected", *peer)
        except Exception:
            log.exception("Origin LSX handler failed for %s:%s", *peer)

def start_server_thread(server: Any, name: str) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, name=name, daemon=True)
    t.start()
    return t


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--grant-card",
        type=int,
        help="Add one catalog player to the stopped local club database, then exit without opening listeners",
    )
    parser.add_argument(
        "--grant-pile",
        choices=("club", "unassigned"),
        default="club",
        help="Destination for --grant-card (default: club)",
    )
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="Permit --grant-card to add another owned copy",
    )
    parser.add_argument(
        "--replace-resource-id",
        type=int,
        help="Explicitly remove non-squad copies of this resource ID before granting --grant-card",
    )
    parser.add_argument("--http-only-test-port", type=int,
                        help="Isolated loopback HTTP tests only; requires separate runtime/config roots")
    parser.add_argument(
        "--lsx-mode",
        choices=("auto", "off", "required"),
        default="auto",
        help="Origin LSX ownership: auto/optional, off for EA App ownership, or required for the local helper",
    )
    args = parser.parse_args()
    if args.grant_card is not None:
        resource_id = int(args.grant_card)
        if resource_id <= 0 or resource_id not in PLAYER_DB:
            parser.error("--grant-card must name a known positive player resource ID")
        replaced = 0
        if args.replace_resource_id is not None:
            old_resource_id = int(args.replace_resource_id)
            if old_resource_id <= 0 or old_resource_id == resource_id:
                parser.error("--replace-resource-id must be a different positive resource ID")
            try:
                replaced = STATE.remove_non_squad_player_items(old_resource_id)
            except ValueError as error:
                parser.error(str(error))
        existing = STATE.owned_player_item_by_resource_id(resource_id)
        if existing is not None and not args.allow_duplicate:
            print(json.dumps({
                "status": "already-owned",
                "resourceId": resource_id,
                "itemId": int(existing.get("id", 0) or 0),
                "name": str(player_meta(resource_id).get("name", "")),
                "replaced": replaced,
            }))
            return 0
        item = STATE.make_player_item(resource_id, pile=args.grant_pile)
        print(json.dumps({
            "status": "granted",
            "resourceId": resource_id,
            "itemId": int(item["id"]),
            "pile": args.grant_pile,
            "name": str(player_meta(resource_id).get("name", "")),
            "replaced": replaced,
        }))
        return 0
    if args.http_only_test_port is not None:
        if not TEST_MODE or args.host != "127.0.0.1" or not 1024 <= args.http_only_test_port <= 65535:
            parser.error("HTTP-only tests require loopback and an unprivileged test port")
        if args.http_only_test_port in (8090, 8081, 42230, 42231, 42232, 44321, 10853, 17502, 3216):
            parser.error("HTTP-only tests cannot use a production runtime port")
        with ThreadingHTTPServer(("127.0.0.1", args.http_only_test_port), FutHandler) as test_server:
            log.info("HTTP-ONLY TEST MODE port=%s state=%s", args.http_only_test_port, RUNTIME_ROOT)
            test_server.serve_forever()
        return 0

    host = args.host
    servers = []
    active = {}

    def required_http(key, handler, label):
        port = int(CFG[key])
        try:
            srv = ThreadingHTTPServer((host, port), handler)
            servers.append(srv)
            active[label] = (srv, port)
            log.info("Bound REQUIRED %s on %s:%d", label, host, port)
            return srv
        except OSError as e:
            log.error("FAILED REQUIRED %s on %s:%d: %s", label, host, port, e)
            return None

    def required_tcp(key, handler, label):
        port = int(CFG[key])
        try:
            srv = ThreadingTCPServer((host, port), handler)
            servers.append(srv)
            active[label] = (srv, port)
            log.info("Bound REQUIRED %s on %s:%d", label, host, port)
            return srv
        except OSError as e:
            log.error("FAILED REQUIRED %s on %s:%d: %s", label, host, port, e)
            return None

    def optional_http(key, handler, label):
        port = int(CFG[key])
        try:
            srv = ThreadingHTTPServer((host, port), handler)
            servers.append(srv)
            active[label] = (srv, port)
            log.info("Bound optional %s on %s:%d", label, host, port)
            return srv
        except OSError as e:
            log.warning("Skipping optional %s on %s:%d: %s", label, host, port, e)
            return None

    def optional_tcp(key, handler, label):
        port = int(CFG[key])
        try:
            srv = ThreadingTCPServer((host, port), handler)
            servers.append(srv)
            active[label] = (srv, port)
            log.info("Bound optional %s on %s:%d", label, host, port)
            return srv
        except OSError as e:
            log.warning("Skipping optional %s on %s:%d: %s", label, host, port, e)
            return None

    def required_udp(key, handler, label):
        port = int(CFG[key])
        try:
            srv = ThreadingUDPServer((host, port), handler)
            servers.append(srv)
            active[label] = (srv, port)
            log.info("Bound REQUIRED %s on %s:%d/udp", label, host, port)
            return srv
        except OSError as e:
            log.error("FAILED REQUIRED %s on %s:%d/udp: %s", label, host, port, e)
            return None

    # FIFA 21 path: Blaze and FUT are required. Older FIFA15 compatibility
    # listeners are optional so EA App/Origin-owned ports do not abort startup.
    fut = required_http("fut_port", FutHandler, "FUT")
    blaze = required_tcp("blaze_port", BlazeOrHttpHandler, "Blaze")
    qos_http = required_http("qos_port", QosHandler, "QoS-HTTP")
    qos_udp = required_udp("qos_port", QosUdpHandler, "QoS-UDP")
    ssf = required_http("ssf_port", SsfHandler, "SSF")
    if fut is None or blaze is None or qos_http is None or qos_udp is None or ssf is None:
        log.error("FIFA 21 cannot continue until the REQUIRED port above is free.")
        log.error("Run CHECK_PORTS.cmd and send the output/screenshot.")
        for srv in servers:
            try:
                srv.server_close()
            except Exception:
                pass
        return 2

    legacy_fut = optional_http("legacy_fut_port", FutHandler, "FUT-compat")
    redir = optional_http("redirect_port", RedirectHandler, "Cleartext-redirector-debug")
    easw = optional_http("easw_port", EaswHandler, "EASW")
    if args.lsx_mode == "off":
        lsx = None
        log.info("Origin-LSX disabled; EA App/Origin is expected to own 127.0.0.1:%d", int(CFG["lsx_port"]))
    elif args.lsx_mode == "required":
        lsx = required_tcp("lsx_port", LSXHandler, "Origin-LSX")
        if lsx is None:
            log.error("Local Origin-LSX was required but port %d could not be bound.", int(CFG["lsx_port"]))
            for srv in servers:
                try:
                    srv.server_close()
                except Exception:
                    pass
            return 2
    else:
        lsx = optional_tcp("lsx_port", LSXHandler, "Origin-LSX")

    thread_names = {
        "FUT": "FUT-8090",
        "Blaze": "Blaze-44321",
        "FUT-compat": "FUT-compat",
        "Cleartext-redirector-debug": "Redirector-debug",
        "QoS-HTTP": "QoS-HTTP",
        "QoS-UDP": "QoS-UDP",
        "SSF": "SSF-10853",
        "EASW": "EASW",
        "Origin-LSX": "OriginLSX",
    }
    for label, (srv, port) in active.items():
        start_server_thread(srv, thread_names.get(label, label))

    print("=" * 70)
    print(f"{APP_NAME} {VERSION} READY")
    print(f" REQUIRED Blaze : {host}:{CFG['blaze_port']}  {'OK' if blaze else 'FAILED'}")
    print(f" REQUIRED FUT   : http://{host}:{CFG['fut_port']}/  {'OK' if fut else 'FAILED'}")
    lsx_label = "DISABLED (EA App owns it)" if args.lsx_mode == "off" else ("OK" if lsx else "SKIPPED")
    print(f" LSX mode       : {args.lsx_mode} / {host}:{CFG['lsx_port']}  {lsx_label}")
    print(f" REQUIRED QoS   : {host}:{CFG['qos_port']} tcp+udp  {'OK' if qos_http and qos_udp else 'FAILED'}")
    print(f" REQUIRED SSF   : http://{host}:{CFG['ssf_port']}/sf/auth  {'OK' if ssf else 'FAILED'}")
    print(f" Optional EASW  : http://{host}:{CFG['easw_port']}/  {'OK' if easw else 'SKIPPED'}")
    print(f" Optional FUTc  : http://{host}:{CFG['legacy_fut_port']}/  {'OK' if legacy_fut else 'SKIPPED'}")
    print(f" Optional RDBG  : {host}:{CFG['redirect_port']}  {'OK' if redir else 'SKIPPED'}")
    print(f" Local club     : {CFG['club_name']} / {CFG['display_name']} / {STATE.credits()} coins")
    print(f" State/logs     : {RUNTIME_ROOT}")
    print("=" * 70)
    log.info("Required FIFA 21 localhost services ready")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for s in servers:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
