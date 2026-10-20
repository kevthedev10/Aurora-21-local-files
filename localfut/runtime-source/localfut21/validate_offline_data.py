"""Read-only consistency check for config, players.json, and local SQLite."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from offline_data import DB_PATH, load_inputs


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    config, players = load_inputs()
    errors: list[str] = []
    if not DB_PATH.exists():
        fail(errors, f"database does not exist: {DB_PATH}")
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        expected_tables = {"kv", "items", "squads", "unopened_packs"}
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        if not expected_tables.issubset(tables):
            fail(errors, f"missing tables: {sorted(expected_tables - tables)}")

        def kv(key: str):
            row = conn.execute("select value from kv where key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

        if kv("account_id") != config.get("test_account_id"):
            fail(errors, "SQLite account_id does not match config")
        if kv("club_id") != int(config["club_id"]) or kv("club_name") != config["club_name"]:
            fail(errors, "SQLite club identity does not match config")

        rows = conn.execute("select id,resource_id,pile,data from items order by id").fetchall()
        player_rows = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except json.JSONDecodeError:
                fail(errors, f"item {row['id']} contains invalid JSON")
                continue
            if data.get("itemType") == "player":
                player_rows.append((row, data))
                rid = int(data.get("resourceId", 0))
                if rid not in players:
                    fail(errors, f"owned player {rid} is not defined in players.json")
                if row["pile"] != "club" or data.get("pile") != "club":
                    fail(errors, f"player item {row['id']} is not in the club pile")
                if int(data.get("owners", 0)) < 1:
                    fail(errors, f"player item {row['id']} has no owner")
                if int(data.get("rating", 0)) != int(players.get(rid, {}).get("rating", -1)):
                    fail(errors, f"player item {row['id']} rating does not match definition")

        expected_owned = set(int(x) for x in config["starter_players"] + config.get("reserve_players", []))
        actual_owned = {int(data.get("resourceId", 0)) for _, data in player_rows}
        if actual_owned != expected_owned:
            fail(errors, f"owned resource IDs differ; expected {sorted(expected_owned)}, got {sorted(actual_owned)}")

        squads = conn.execute("select id,name,active,data from squads order by id").fetchall()
        if len(squads) != 1:
            fail(errors, f"expected exactly one squad, found {len(squads)}")
        else:
            squad_row = squads[0]
            squad = json.loads(squad_row["data"])
            refs = squad.get("players", [])
            item_ids = {int(row["id"]) for row, _ in player_rows}
            referenced = {int(ref.get("itemId", 0)) for ref in refs}
            starter_ids = {int(x) for x in config["starter_players"]}
            ref_resources = {
                int(data.get("resourceId", 0))
                for row, data in player_rows
                if int(row["id"]) in referenced
            }
            if int(squad_row["id"]) != int(config.get("squad_id", 1)) or squad_row["name"] != config.get("squad_name"):
                fail(errors, "SQLite squad identity does not match config")
            if int(squad_row["active"]) != 1 or squad.get("active") is not True:
                fail(errors, "configured squad is not active")
            if len(refs) != 11 or referenced - item_ids or ref_resources != starter_ids:
                fail(errors, "squad does not reference exactly the 11 owned starter cards")

        if conn.execute("select count(*) from unopened_packs").fetchone()[0] != 0:
            fail(errors, "unexpected unopened packs in deterministic fixture")
        conn.close()

        # Reopen the file to prove the relationship survived a real SQLite
        # close/reopen boundary, rather than only existing in one connection.
        reopened = sqlite3.connect(DB_PATH)
        persisted_player_count = reopened.execute(
            "select count(*) from items where json_extract(data, '$.itemType')='player'"
        ).fetchone()[0]
        persisted_squad_count = reopened.execute("select count(*) from squads where active=1").fetchone()[0]
        if persisted_player_count != len(expected_owned) or persisted_squad_count != 1:
            fail(errors, "SQLite close/reopen persistence check failed")
        reopened.close()

    if errors:
        print("INVALID")
        print("\n".join(f"- {error}" for error in errors))
        return 1

    print(json.dumps({
        "status": "VALID",
        "players_defined": len(players),
        "player_items_owned": len(expected_owned),
        "club": {"id": int(config["club_id"]), "name": config["club_name"]},
        "squad": {"id": int(config.get("squad_id", 1)), "name": config.get("squad_name")},
        "database": str(DB_PATH),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
