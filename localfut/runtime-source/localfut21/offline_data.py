"""Seed and validate the local FUT data layer without starting any service.

This module intentionally uses only JSON and SQLite. It does not import the
server, open a socket, touch routing/certificates, or inspect FIFA21.exe.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from user_config import read_user_config

ROOT = Path(__file__).resolve().parent
PLAYERS_PATH = ROOT / "players.json"
_local_app_data = os.environ.get("LOCALAPPDATA")
RUNTIME_ROOT = Path(_local_app_data) / "FIFA21LocalFUT" if _local_app_data else Path.home() / "AppData" / "Local" / "FIFA21LocalFUT"
DB_PATH = RUNTIME_ROOT / "data" / "localfut15.sqlite3"

PLAYER_FIELDS = {"name", "rating", "preferredPosition", "leagueId", "teamid", "nation", "face"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_inputs() -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    config = read_user_config("config.json", {})
    raw_players = load_json(PLAYERS_PATH)
    if not isinstance(config, dict) or not isinstance(raw_players, dict):
        raise ValueError("config.json and players.json must contain JSON objects")
    players: dict[int, dict[str, Any]] = {}
    for raw_id, meta in raw_players.items():
        rid = int(raw_id)
        if not isinstance(meta, dict):
            raise ValueError(f"player {rid} is not an object")
        missing = PLAYER_FIELDS - set(meta)
        if missing:
            raise ValueError(f"player {rid} missing fields: {sorted(missing)}")
        if not 1 <= int(meta["rating"]) <= 99:
            raise ValueError(f"player {rid} has an invalid rating")
        if len(meta["face"]) != 6 or any(not 0 <= int(v) <= 99 for v in meta["face"]):
            raise ValueError(f"player {rid} must have six face values in [0,99]")
        players[rid] = meta
    starters = [int(x) for x in config.get("starter_players", [])]
    reserves = [int(x) for x in config.get("reserve_players", [])]
    if len(starters) != 11 or len(set(starters + reserves)) != len(starters + reserves):
        raise ValueError("config must contain 11 unique starters and unique reserves")
    for rid in starters + reserves:
        if rid not in players:
            raise ValueError(f"configured roster player {rid} is not defined")
    return config, players


def _item(rid: int, meta: dict[str, Any], item_id: int, timestamp: int, pile: str = "club") -> dict[str, Any]:
    return {
        "id": item_id, "timestamp": timestamp, "resourceId": rid,
        "assetId": int(meta.get("assetId", rid)), "rating": int(meta["rating"]),
        "itemState": "free", "rareflag": int(meta.get("rareflag", 1)),
        "formation": "f442", "leagueId": int(meta["leagueId"]),
        "injuryType": "none", "injuryGames": 0, "lastSalePrice": 0,
        "fitness": 99, "training": 0, "suspension": 0, "contract": 7,
        "contracts": 7, "preferredPosition": str(meta["preferredPosition"]),
        "playStyle": 0, "discardValue": 700, "itemType": "player",
        "cardsubtypeid": 1, "owners": 1, "untradeable": True,
        "morale": 50, "statsList": [], "lifetimeStats": [],
        "attributeList": [{"index": i, "value": int(v)} for i, v in enumerate(meta["face"])],
        "teamid": int(meta["teamid"]), "assists": 0, "lifetimeAssists": 0,
        "loyaltyBonus": 1, "pile": pile, "nation": int(meta["nation"]),
        "resourceGameYear": 2015, "marketDataMinPrice": 150,
        "marketDataMaxPrice": 15000000,
    }


def seed() -> dict[str, Any]:
    config, players = load_inputs()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    now = int(time.time())
    starters = [int(x) for x in config["starter_players"]]
    reserves = [int(x) for x in config.get("reserve_players", [])]
    roster = starters + reserves

    conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL,
            pile TEXT NOT NULL DEFAULT 'club', data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS squads(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS unopened_packs(id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER NOT NULL, data TEXT NOT NULL);
    """)

    def set_kv(key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    with conn:
        # Remove prior player instances while retaining any future non-player
        # records the local data model may add.
        old = conn.execute("SELECT id, data FROM items").fetchall()
        old_player_ids = []
        for row in old:
            try:
                if json.loads(row["data"]).get("itemType") == "player":
                    old_player_ids.append(int(row["id"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if old_player_ids:
            conn.executemany("DELETE FROM items WHERE id=?", [(x,) for x in old_player_ids])

        item_ids: dict[int, int] = {}
        next_id = max(900000000000, int(conn.execute("SELECT COALESCE(MAX(id),0) FROM items").fetchone()[0]))
        for rid in roster:
            next_id += 1
            item_ids[rid] = next_id
            item = _item(rid, players[rid], next_id, now)
            conn.execute("INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
                         (next_id, rid, "club", json.dumps(item, separators=(",", ":"))))

        squad_id = int(config.get("squad_id", 1))
        squad_name = str(config.get("squad_name", "Local Test XI"))
        lineup = [{"index": i, "itemId": item_ids[rid], "loyaltyBonus": 1}
                  for i, rid in enumerate(starters)]
        squad = {
            "id": squad_id, "squadId": squad_id, "name": squad_name,
            "squadName": squad_name, "formation": "f442", "rating": round(sum(players[r]["rating"] for r in starters) / 11),
            "chemistry": 100, "active": True, "players": lineup, "kicktakers": [],
        }
        conn.execute("DELETE FROM squads")
        conn.execute("INSERT INTO squads(id,name,active,data) VALUES(?,?,1,?)",
                     (squad_id, squad_name, json.dumps(squad, separators=(",", ":"))))
        conn.execute("DELETE FROM unopened_packs")

        set_kv("credits", int(config.get("credits", 0)))
        set_kv("next_item_id", next_id)
        set_kv("account_id", str(config.get("test_account_id", "local-test-account-001")))
        set_kv("nucleus_id", int(config.get("nucleus_id", 1000000001)))
        set_kv("persona_id", int(config.get("persona_id", 1000000002)))
        set_kv("club_id", int(config.get("club_id", 1)))
        set_kv("club_name", str(config.get("club_name", "Local Test FC")))
        set_kv("club_abbr", str(config.get("club_abbr", "LTF")))
        set_kv("squad_id", squad_id)
        set_kv("squad_name", squad_name)
        set_kv("owned_player_resource_ids", roster)

    conn.close()
    return {"database": str(DB_PATH), "players": len(players), "owned": len(roster),
            "starters": len(starters), "reserves": len(reserves), "squad_id": squad_id}


if __name__ == "__main__":
    print(json.dumps(seed(), indent=2))
