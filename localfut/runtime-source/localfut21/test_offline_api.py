"""Direct, in-process API tests; no HTTP listener or game process is started."""
from __future__ import annotations

import json
import os
import tempfile
import atexit
import logging

# Keep API checks isolated from the user's persistent local club/database.
_test_runtime = tempfile.TemporaryDirectory(prefix="localfut21-api-")
os.environ["LOCALFUT_RUNTIME_ROOT"] = _test_runtime.name
os.environ["LOCALFUT21_USER_CONFIG_ROOT"] = os.path.join(_test_runtime.name, "user-config")

import server


def _cleanup_test_runtime() -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()
    server.STATE.conn.close()
    _test_runtime.cleanup()


atexit.register(_cleanup_test_runtime)


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
    status, _headers, raw = server.route_fut(method, path, {}, body)
    value = json.loads(raw.decode("utf-8"))
    assert status == 200, (method, path, status, value)
    return status, value


def call_raw(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
    status, _headers, raw = server.route_fut(method, path, {}, body)
    return status, json.loads(raw.decode("utf-8"))


def main() -> int:
    assert os.path.commonpath((str(server.PACK_SETTINGS_PATH), _test_runtime.name)) == _test_runtime.name
    # The imported FIFA 21 catalog is the baseline; locally authored cards may
    # extend it without invalidating the database contract.
    assert len(server.PLAYER_DB) >= 22500
    assert len(server.MANAGER_DB) == 311
    assert min(server.MANAGER_DB) >= 1_000_000
    assert server.MANAGER_DB[1001067]["name"] == "Antonio Conte"
    summer_stars_messi = server.PLAYER_DB[184707399]
    assert summer_stars_messi["assetId"] == 158023
    assert summer_stars_messi["rating"] == 99
    assert summer_stars_messi["rareflag"] == 61
    assert summer_stars_messi["rarityName"] == "Summer Stars"
    assert summer_stars_messi["wefutCard"]["resourceIdSource"] == "wefut-custom-avatar"
    # Zero-revision custom avatar 246525 collides with base player Allsop in
    # FIFA 21. Icon Moments must retain that custom ID only in the low 24 bits.
    moments_pele_id = server._RESOURCE_REVISION_SIZE + 246525
    assert 246525 not in server.PLAYER_DB
    moments_pele = server.PLAYER_DB[moments_pele_id]
    assert moments_pele["name"] == "Pelé"
    assert moments_pele["nation"] == 54
    assert moments_pele["preferredPosition"] == "CF"
    assert moments_pele["rareflag"] == 84
    assert moments_pele["dynamicImageResourceId"] == 246525
    assert server.PLAYER_RESOURCE_ID_MIGRATIONS[246525] == moments_pele_id
    assert all(int(resource_id) >= server._RESOURCE_REVISION_SIZE
               for resource_id, meta in server.PLAYER_DB.items()
               if int(meta.get("rareflag", 0) or 0) == 84)
    assert len(server._special_pool_counts) >= 59
    assert server._special_pool_counts[3] == 807
    assert server._special_pool_counts[61] == 36

    rating_examples = {}
    for resource_id, meta in server.PLAYER_DB.items():
        rating_examples.setdefault(int(meta.get("rating", 0) or 0), int(resource_id))
    assert server.high_rating_pack_weight(rating_examples[89]) == 1.0
    assert 1.0 < server.high_rating_pack_weight(rating_examples[90]) < 2.0
    assert server.high_rating_pack_weight(rating_examples[92]) > 2.0
    assert server.high_rating_pack_weight(rating_examples[99]) == 3.5

    # Elite Draft profiles are stable per run: approximately 45% contain four
    # or five guaranteed pick rounds with a selectable 99-rated player.
    elite_profiles = []
    for seed in range(1, 10_001):
        ordinals = server._draft_elite_99_pick_ordinals({"seed": seed})
        assert len(ordinals) in (0, 4, 5)
        assert len(set(ordinals)) == len(ordinals)
        assert all(0 <= ordinal < 23 for ordinal in ordinals)
        elite_profiles.append(bool(ordinals))
    elite_rate = sum(elite_profiles) / len(elite_profiles)
    assert 0.43 <= elite_rate <= 0.47, elite_rate
    for seed in range(1, 101):
        profile = {"seed": seed, "formation": "f442"}
        bad_picks = server._draft_bad_pick_ordinals(profile)
        assert len(bad_picks) in (2, 3)
        assert set(bad_picks).isdisjoint(server._draft_elite_99_pick_ordinals(profile))

    # Every imported special edition must be reachable from at least one of
    # the three default pack tiers, with its rarity enabled at a positive
    # weight. This audits every special card, including locally authored ones,
    # rather than relying on a catalog-size constant.
    reachable_specials = set()
    for pack in server.DEFAULT_PACK_SETTINGS["packs"].values():
        enabled_tiers = {
            tier for tier in ("gold", "silver", "bronze")
            if float(pack.get(f"{tier}_weight", 0) or 0) > 0
        }
        enabled_rarities = {
            int(rarity_id) for rarity_id, weight in pack["special_weights"].items()
            if float(weight or 0) > 0
        }
        for resource_id, meta in server.PLAYER_DB.items():
            rarity_id = int(meta.get("rareflag", 0) or 0)
            if rarity_id <= 1 or rarity_id not in enabled_rarities:
                continue
            rating = int(meta.get("rating", 0) or 0)
            tier = str(meta.get("tier", "") or "").lower()
            if tier not in ("gold", "silver", "bronze"):
                tier = "gold" if rating >= 75 else "silver" if rating >= 65 else "bronze"
            if tier in enabled_tiers:
                reachable_specials.add(int(resource_id))
    all_specials = {
        int(resource_id) for resource_id, meta in server.PLAYER_DB.items()
        if int(meta.get("rareflag", 0) or 0) > 1
    }
    assert len(all_specials) >= 4598
    assert reachable_specials == all_specials

    # Old saved packs should inherit newly imported rarity pools as enabled;
    # explicit zeros for rarities already present remain user-controlled.
    migrated = server._merge_pack_settings({"packs": {
        "3": {"special_weights": {"3": 0}}
    }})
    assert migrated["packs"]["3"]["special_weights"]["3"] == 0
    assert migrated["packs"]["3"]["special_weights"]["61"] > 0
    assert migrated["packs"]["4"]["name"] == "Elite Ten 82+ Pack"
    assert migrated["packs"]["4"]["price"] == 50000
    assert migrated["next_pack_id"] == server.DEFAULT_PACK_SETTINGS["next_pack_id"]

    _, auth = call("GET", "/ut/auth")
    assert auth["protocol"] == "http"

    _, account = call("GET", "/ut/game/fifa21/user/accountinfo")
    club = account["userAccountInfo"]["personas"][0]["userClubList"][0]
    assert club["clubName"] == server.CFG["club_name"]
    assert club["assetId"] == int(server.CFG["club_id"])

    _, settings = call("GET", "/ut/game/fifa21/settings")
    config = {entry["type"]: entry["value"] for entry in settings["configs"]}
    assert config["enableMyStadium"] == "1"
    assert config["myStadium"] == "1"
    assert config["enableDraftMode"] == "1"
    assert config["enableOfflineDraftMode"] == "1"
    assert config["enableSinglePlayerDraftMode"] == "1"

    # CardsDLL's FutGetDraftCurrentStateServerResponse expects a list.  The
    # INVALID state is the native no-active-run contract that opens the entry
    # payment screen; null wrapper fields leave the retail UI spinning.
    _, draft_state = call(
        "GET", "/ut/game/fifa21/squad/mode/draft/state?mode=SINGLE_PLAYER"
    )
    assert draft_state == [{
        "entranceCriteria": {"COINS": 15000, "DRAFT_TOKEN": 1, "POINTS": 300},
        "gamesWonCurrentMatch": 0,
        "roundsInfo": [],
        "squadState": "INVALID",
        "stateParam1": "INVALID",
        "stateParam2": "0",
    }]

    credits_before_draft = server.STATE.credits()
    _, draft_purchase = call("POST", "/ut/game/fifa21/purchase/mode/1/draft", {
        "currency": "COINS", "usePreOrder": 0,
    })
    assert draft_purchase == [{
        "COINS": credits_before_draft - 15000, "POINTS": 0, "DRAFT_TOKEN": 0,
    }]
    # Pin this integration run to an elite seed so every planned 99 round is
    # verified through the same native choice route used by the game.
    test_run = server.STATE.draft_run()
    elite_seed = next(seed for seed in range(1, 1000)
                      if server._draft_elite_99_pick_ordinals({"seed": seed}))
    test_run["seed"] = elite_seed
    server.STATE.save_draft_run(test_run)
    _, difficulty_choices = call(
        "GET", "/ut/game/fifa21/squad/mode/draft/choices/difficulty"
    )
    assert [choice["difficultyName"] for choice in difficulty_choices["choices"]] == [
        "BEGINNER", "AMATEUR", "SEMIPRO", "PRO", "WORLDCLASS", "LEGENDARY", "ULTIMATE",
    ]
    call("PUT", "/ut/game/fifa21/squad/mode/draft/choose/difficulty", {
        "difficultyName": "PRO",
    })
    _, formation_choices = call(
        "GET", "/ut/game/fifa21/squad/mode/draft/choices/formation"
    )
    assert len(formation_choices["choices"]) == 5
    call("PUT", "/ut/game/fifa21/squad/mode/1/draft/choose", {
        "choiceIndex": 0, "positionId": 0,
    })
    elite_ordinals = set(server._draft_elite_99_pick_ordinals(server.STATE.draft_run()))
    _, captain_choices = call(
        "GET", "/ut/game/fifa21/squad/mode/draft/choices/captain"
    )
    assert len(captain_choices["choices"]) == 5
    if 0 in elite_ordinals:
        assert any(choice["itemData"]["rating"] == 99
                   for choice in captain_choices["choices"])
    call("PUT", "/ut/game/fifa21/squad/mode/1/draft/choose", {
        "choiceIndex": 0, "positionId": 10,
    })
    for draft_position in range(23):
        if draft_position == 10:
            continue
        active_run = server.STATE.draft_run()
        allowed = server._draft_allowed_positions(active_run, draft_position)
        seen_assets = {int(value) for value in active_run.get("seenAssets", [])}
        matching_99_available = any(
            int(meta.get("rating", 0) or 0) == 99
            and int(meta.get("assetId", resource_id) or resource_id) not in seen_assets
            and (allowed is None or
                 str(meta.get("preferredPosition", "") or "").upper() in allowed)
            for resource_id, meta in server.PLAYER_DB.items()
        )
        _, player_choices = call(
            "PUT", "/ut/game/fifa21/squad/mode/draft/choices/player", {
                "positionId": draft_position,
            })
        assert len(player_choices["choices"]) == 5, draft_position
        if allowed is not None:
            assert all(choice["itemData"]["preferredPosition"].upper() in allowed
                       for choice in player_choices["choices"]), draft_position
        pick_ordinal = len(server.STATE.draft_run().get("picked", {}))
        if pick_ordinal in elite_ordinals and matching_99_available:
            assert any(choice["itemData"]["rating"] == 99
                       for choice in player_choices["choices"]), pick_ordinal
        call("PUT", "/ut/game/fifa21/squad/mode/1/draft/choose", {
            "choiceIndex": 0, "positionId": draft_position,
        })
    _, manager_choices = call(
        "GET", "/ut/game/fifa21/squad/mode/draft/choices/manager"
    )
    assert len(manager_choices["choices"]) == 5
    assert all(choice["itemData"]["itemType"] == "manager"
               for choice in manager_choices["choices"])
    call("PUT", "/ut/game/fifa21/squad/mode/1/draft/choose", {
        "choiceIndex": 0, "positionId": 0,
    })
    _, ready_draft = call(
        "GET", "/ut/game/fifa21/squad/mode/draft/state?mode=SINGLE_PLAYER"
    )
    assert ready_draft[0]["squadState"] == "READY_FOR_MATCH"
    assert len([slot for slot in ready_draft[0]["squad"]["players"]
                if isinstance(slot.get("itemData"), dict)]) == 23
    assert len(ready_draft[0]["squad"]["manager"]) == 1
    _, draft_squad = call("GET", "/ut/game/fifa21/squad/900001")
    assert draft_squad["squadType"] == "DRAFT_SQUAD"
    assert draft_squad["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert draft_squad["stadiumId"] == server.SQUAD_BATTLE_STADIUM_ID
    for collection_name in ("actives", "club"):
        draft_stadium = next(item for item in draft_squad[collection_name]
                             if item.get("itemType") == "stadium")
        assert draft_stadium["resourceId"] == server.SQUAD_BATTLE_STADIUM_RESOURCE_ID
        assert draft_stadium["assetId"] == server.SQUAD_BATTLE_STADIUM_CARD_ASSET_ID
        assert draft_stadium["stadiumid"] == server.SQUAD_BATTLE_STADIUM_ID
    _, draft_opponent = call("GET", "/ut/game/fifa21/offlinesquad/1")
    assert draft_opponent["id"] == 1
    assert draft_opponent["squadId"] == 1
    assert draft_opponent["valid"] is True
    assert draft_opponent["home"] is True
    assert draft_opponent["isHomeTeam"] is True
    assert draft_opponent["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert draft_opponent["stadiumId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert len(draft_opponent["players"]) == 23
    assert len([slot for slot in draft_opponent["players"][:11]
                if isinstance(slot.get("itemData"), dict)]) == 11
    _, draft_match = call("POST", "/ut/game/fifa21/match", {})
    assert draft_match["squad"]["squadType"] == "DRAFT_SQUAD"
    assert draft_match["opponentTeamId"] == 1
    assert draft_match["home"] is False
    assert draft_match["isHomeTeam"] is False
    assert draft_match["squad"]["home"] is False
    assert draft_match["squad"]["isHomeTeam"] is False
    assert draft_match["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert draft_match["stadiumId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert draft_match["opponentSquad"] == draft_match["awaySquad"] == draft_match["opponent"]
    assert draft_match["opponentSquad"]["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert draft_match["opponentSquad"]["stadiumId"] == server.SQUAD_BATTLE_STADIUM_ID
    for collection_name in ("actives", "club"):
        draft_away_stadium = next(
            item for item in draft_match["opponentSquad"][collection_name]
            if item.get("itemType") == "stadium"
        )
        assert draft_away_stadium["resourceId"] == server.SQUAD_BATTLE_STADIUM_RESOURCE_ID
        assert draft_away_stadium["assetId"] == server.SQUAD_BATTLE_STADIUM_CARD_ASSET_ID
    assert draft_match["squad"]["active"] is True
    assert draft_match["squad"]["valid"] is True
    assert draft_match["squad"]["chemistry"] == 100
    assert draft_match["squad"]["starRating"] == 5
    assert len(draft_match["squad"]["kicktakers"]) == 5
    assert all(slot["itemData"]["itemState"] == "free"
               for slot in draft_match["squad"]["players"]
               if isinstance(slot.get("itemData"), dict))
    _, draft_result = call("POST", "/ut/game/fifa21/match/end", {"endReason": "LOSS"})
    assert draft_result["draftEliminated"] is True
    assert draft_result["rewardCoins"] == 15000
    assert draft_result["sessionCoinsBankBalance"] == server.STATE.credits()
    assert draft_result["opponentTeamId"] == 1
    assert server.PENDING_USERSESSION_REFRESH.is_set()
    server.PENDING_USERSESSION_REFRESH.clear()
    report_notification = server._blaze_game_reporting_result(12345)
    assert report_notification.startswith(
        server._tdf_tag("DATA", server._TDF_VARIABLE) + b"\x00"
    )
    assert server._tdf_get_int(report_notification, "EROR") == 0
    assert server._tdf_get_int(report_notification, "FNL") == 1
    assert server._tdf_get_int(report_notification, "GRID") == 12345
    _, draft_award = call("POST", "/ut/game/fifa21/squad/mode/1/draft/grant/award", {})
    assert draft_award == [{"halId": 0, "type": 0, "value": 15000}]
    assert server.STATE.credits() == credits_before_draft
    assert server.STATE.draft_run() is None

    _, stadium = call("GET", "/ut/game/fifa21/stadium")
    assert stadium["stadiumId"] == 261
    assert stadium["lastUsedTierLevel"] == 1
    assert len(stadium["tiers"]) == 1
    assert stadium["tiers"][0]["tier"] == 1
    assert stadium["tiers"][0]["stadiumDefinitionId"] == 261
    matchday_slots = stadium["tiers"][0]["slots"]
    assert len(matchday_slots) == 5
    assert all(slot["stadiumArea"] == 0 for slot in matchday_slots)
    locked_by_state = {
        slot["itemData"]["itemState"]: slot["locked"] for slot in matchday_slots
    }
    assert locked_by_state == {
        "activeHomeKit": False,
        "activeAwayKit": False,
        "activeBadge": False,
        "activeBall": False,
        "activeStadium": True,
    }
    assert [slot["slotName"] for slot in matchday_slots] == [
        "Home_Kit", "Away_Kit", "Badge", "FUT_STADIUM_BALL", "Stadium",
    ]
    assert all(slot["itemData"]["id"] > 0 for slot in matchday_slots)
    assert {slot["itemData"]["itemState"] for slot in matchday_slots} == {
        "activeHomeKit", "activeAwayKit", "activeBadge", "activeBall", "activeStadium",
    }
    assert all("cardSubTypeId" in slot["itemData"] for slot in matchday_slots)
    stadium_item = next(slot["itemData"] for slot in matchday_slots
                        if slot["itemData"]["itemState"] == "activeStadium")
    assert stadium_item["resourceId"] == server.MY_STADIUM_CARD_RESOURCE_ID
    assert stadium_item["carddbid"] == server.MY_STADIUM_CARD_RESOURCE_ID
    assert stadium_item["assetId"] == server.MY_STADIUM_CARD_ASSET_ID
    match_stadium = next(item for item in server._squad_battle_match_actives(opponent=False)
                         if item["itemState"] == "activeStadium")
    assert match_stadium["resourceId"] == server.SQUAD_BATTLE_STADIUM_RESOURCE_ID
    assert match_stadium["assetId"] == server.SQUAD_BATTLE_STADIUM_CARD_ASSET_ID
    assert match_stadium["stadiumid"] == server.SQUAD_BATTLE_STADIUM_ID
    _, saved_stadium = call("PUT", "/ut/game/fifa21/stadium", {
        "myStadium_tier": 1,
        "selectedStadiumId": server.SQUAD_BATTLE_STADIUM_ID,
    })
    assert saved_stadium["lastUsedTierLevel"] == 1
    assert saved_stadium["stadiumId"] == server.SQUAD_BATTLE_STADIUM_ID
    saved_stadium_item = next(
        slot["itemData"] for slot in saved_stadium["tiers"][0]["slots"]
        if slot["itemData"]["itemState"] == "activeStadium"
    )
    assert saved_stadium_item["resourceId"] == server.MY_STADIUM_CARD_RESOURCE_ID
    assert saved_stadium_item["assetId"] == server.MY_STADIUM_CARD_ASSET_ID
    assert saved_stadium_item["stadiumid"] == server.SQUAD_BATTLE_STADIUM_ID

    _, user = call("GET", "/ut/game/fifa21/user")
    assert user["clubPlayers"] == 250
    assert user["pileManagers"] == 1
    assert user["squadBattleEnabled"] is True
    assert user["squadBattleCouchPlayEnabled"] is True
    assert user["enableMyStadium"] is True
    assert len(user["myStadium_slot"]) == 5
    assert user["pileSquadPlayers"] == 11
    active_kits = [item for item in user["actives"] if item.get("itemType") == "kit"]
    assert len(active_kits) == 2
    assert all(item["stackCount"] == 1 and item["islocked"] == 0
               and item["pile"] == "club" for item in active_kits)
    assert user["clubName"] == server.CFG["club_name"]
    assert len(user["squadList"]["squad"]) == 1
    assert user["squadList"]["squad"][0]["squadType"] == "REGULAR_SQUAD"

    _, squad = call("GET", "/ut/game/fifa21/squad/active")
    occupied = [x for x in squad["players"] if isinstance(x.get("itemData"), dict)]
    assert squad["id"] == 1 and squad["squadName"] == server.CFG["club_name"].replace("FC", "XI")
    assert len(occupied) == 11
    assert all(x["itemData"]["resourceId"] in server.PLAYER_DB for x in occupied)
    assert len(squad["manager"]) == 1
    assert squad["manager"][0]["itemType"] == "manager"
    assert squad["manager"][0]["cardsubtypeid"] == 4
    assert squad["manager"][0]["resourceId"] in server.MANAGER_DB
    assert squad["manager"][0]["resourceId"] == squad["manager"][0]["carddbid"]
    assert squad["manager"][0]["stackCount"] == 1
    assert squad["manager"][0]["itemState"] == "free"
    assert squad["manager"][0]["pile"] == "club"
    assert squad["manager"][0]["contract"] > 0

    _, mass = call("GET", "/ut/game/fifa21/userMassInfo?screen=futlivemsggen4")
    assert mass["userInfo"]["squadList"] == user["squadList"]
    assert mass["squad"] == squad
    assert len(mass["loanPlayers"]["loans"]) == 40
    offer_ids = {offer["defId"] for offer in mass["loanPlayers"]["loans"]}
    assert len(offer_ids) == 40
    assert all(1 <= offer["ranking"] <= 5 and offer["loans"] == 7
               for offer in mass["loanPlayers"]["loans"])
    selected_offer = mass["loanPlayers"]["loans"][0]
    _, signed_loan = call("PUT", f"/ut/game/fifa21/loan/player/{selected_offer['defId']}")
    assert signed_loan["resourceId"] == selected_offer["itemData"]["resourceId"]
    assert signed_loan["id"] != selected_offer["defId"]

    replacement_offer = mass["loanPlayers"]["loans"][1]
    _, replacement_loan = call("PUT", f"/ut/game/fifa21/loan/player/{replacement_offer['defId']}")
    assert replacement_loan["id"] != signed_loan["id"]
    assert server.STATE.get_item(signed_loan["id"]) is not None
    call("PUT", "/ut/game/fifa21/item", {
        "itemData": [{"id": replacement_loan["id"], "pile": "squad"}],
    })
    replacement_squad = server.STATE.list_squads()[0]
    replacement_squad["players"][0]["itemId"] = replacement_loan["id"]
    call("PUT", "/ut/game/fifa21/squad/1?onboarding=true", replacement_squad)
    assert server.STATE.get_item(signed_loan["id"]) is None
    assert server.STATE.get_item(replacement_loan["id"])["pile"] == "squad"

    _, kit_choices = call("GET", "/ut/game/fifa21/onboarding/kits")
    assert len(kit_choices["homeItemDataList"]) == 1
    assert len(kit_choices["awayItemDataList"]) == 1
    home = kit_choices["homeItemDataList"][0]
    away = kit_choices["awayItemDataList"][0]
    _, selected_kits = call("POST", "/ut/game/fifa21/onboarding/kits", {
        "homeKitId": home["resourceId"], "awayKitId": away["resourceId"],
    })
    assert selected_kits["homeKitId"] > 0
    assert selected_kits["awayKitId"] > 0
    assert selected_kits["homeKitId"] != selected_kits["awayKitId"]

    _, badge_choices = call("GET", "/ut/game/fifa21/onboarding/badges")
    assert len(badge_choices["badgeItemDataList"]) == 1
    badge = badge_choices["badgeItemDataList"][0]
    _, selected_badge = call("POST", f"/ut/game/fifa21/onboarding/badge/{badge['resourceId']}")
    assert selected_badge == {"badgeId": 5004}
    assert server.onboarding_complete()
    _, completed_mass = call("GET", "/ut/game/fifa21/userMassInfo?screen=futlivemsggen4")
    assert completed_mass["loanPlayers"] == {"loans": []}
    _, completed_loans = call("GET", "/ut/game/fifa21/loan/players")
    assert completed_loans == {"loans": []}
    _, onboarding_progress = call("GET", "/ut/game/fifa21/clientdata/onboarding")
    assert onboarding_progress == {"entries": [
        {"key": 0, "value": 3},
        {"key": 1, "value": home["resourceId"]},
        {"key": 2, "value": away["resourceId"]},
        {"key": 3, "value": badge["resourceId"]},
    ]}
    _, loan_progress = call("GET", "/ut/game/fifa21/clientdata/loanplayerreward")
    assert loan_progress["entries"][0]["value"] == replacement_loan["assetId"]
    assert completed_mass["onboardingClientData"] == onboarding_progress
    assert completed_mass["loanPlayerClientData"] == loan_progress
    _, squad_progress = call("PUT", "/ut/game/fifa21/clientdata/squad", {
        "entries": [{"key": 5, "value": 0}],
    })
    assert squad_progress == {"entries": [{"key": 5, "value": 0}]}
    _, saved_squad_progress = call("GET", "/ut/game/fifa21/clientdata/squad")
    assert saved_squad_progress == squad_progress

    # Dynamic special-card images are binary DDS assets, cached per resource.
    dynamic_resource = 184707399
    dynamic_path = (server.CONTENT_CACHE /
                    f"fut/playerheads/g4/single/p{dynamic_resource}.dds")
    dynamic_path.parent.mkdir(parents=True, exist_ok=True)
    expected_dds = b"DDS " + bytes(124)
    dynamic_path.write_bytes(expected_dds)
    dynamic_status, dynamic_headers, dynamic_body = server.route_fut(
        "GET", f"/fut/playerheads/g4/single/p{dynamic_resource}.dds", {}, b"",
    )
    assert dynamic_status == 200
    assert dynamic_headers["Content-Type"] == "application/octet-stream"
    assert dynamic_body == expected_dds

    _, club_page = call("GET", "/ut/game/fifa21/club?count=20")
    assert club_page["clubPlayers"] == 250
    assert len(club_page["itemData"]) == 20
    assert {x["resourceId"] for x in club_page["itemData"]}.issubset(server.PLAYER_DB)

    # The native MGR tab asks the same Club endpoint for type=manager. It must
    # receive only real manager subtype-4 items, never the default player page.
    _, manager_page = call("POST", "/ut/game/fifa21/club", {
        "type": "manager", "sort": "desc", "start": 0, "count": 91,
    })
    assert manager_page["pileManagers"] == 1
    assert manager_page["itemCount"] == 1
    assert len(manager_page["itemData"]) == 1
    assert manager_page["itemData"][0]["itemType"] == "manager"
    assert manager_page["itemData"][0]["cardsubtypeid"] == 4
    assert manager_page["itemData"][0]["resourceId"] in server.MANAGER_DB
    assert manager_page["itemData"][0]["resourceId"] >= 1_000_000
    assert manager_page["itemData"][0]["stackCount"] == 1
    owned_manager_id = manager_page["itemData"][0]["id"]
    assert manager_page["itemData"][0]["firstName"]
    assert manager_page["itemData"][0]["lastName"]

    _, kit_page = call("POST", "/ut/game/fifa21/club", {
        "type": "kit", "start": 0, "count": 20,
    })
    assert kit_page["kitCount"] == 2
    assert len(kit_page["itemData"]) == 2
    assert {item["itemState"] for item in kit_page["itemData"]} == {
        "activeHomeKit", "activeAwayKit",
    }
    assert all(item["stackCount"] == 1 and item["islocked"] == 0
               for item in kit_page["itemData"])

    # My Stadium's Match Day kit row uses the Club Info search bucket rather
    # than type=kit. This response must never be empty: the native screen
    # assumes at least one result and otherwise exits while opening the row.
    _, stadium_kit_page = call("POST", "/ut/game/fifa21/club", {
        "searchCriteria": {
            "type": "clubInfo", "category": "kit", "start": 0, "count": 20,
        },
    })
    assert stadium_kit_page["itemCount"] == 2
    assert len(stadium_kit_page["itemData"]) == 2
    assert all(item["itemType"] == "kit" and item["cardsubtypeid"] == 9
               for item in stadium_kit_page["itemData"])

    _, stadium_badge_page = call("GET", "/ut/game/fifa21/club?type=clubInfo&cat=badge&count=20")
    assert stadium_badge_page["itemCount"] >= 1
    assert all(item["itemType"] == "custom" and item["cardsubtypeid"] == 11
               for item in stadium_badge_page["itemData"])

    # Upgrade the exact placeholder shape written by the earlier career-mode
    # implementation without changing its instance id (squads reference it).
    legacy_manager = dict(server.STATE.get_item(owned_manager_id))
    legacy_manager.update({
        "resourceId": 42, "assetId": 42, "carddbid": 42,
        "firstName": "Gareth", "lastName": "Southgate",
    })
    with server.STATE.lock:
        server.STATE.conn.execute(
            "UPDATE items SET resource_id=?, data=? WHERE id=?",
            (42, json.dumps(legacy_manager), owned_manager_id),
        )
        server.STATE.conn.commit()
    server.STATE._migrate_legacy_manager_items()
    migrated_manager = server.STATE.get_item(owned_manager_id)
    assert migrated_manager["id"] == owned_manager_id
    assert migrated_manager["resourceId"] in server.MANAGER_DB
    assert migrated_manager["resourceId"] != 42
    assert migrated_manager["stackCount"] == 1

    # The retail client can save the visible manager as id=0. The server must
    # repair that NullItem reference to the club's owned manager instance.
    squad_with_null_manager = dict(server.STATE.list_squads()[0])
    squad_with_null_manager["manager"] = [{"id": 0, "dream": False}]
    saved_with_manager = server.STATE.save_squad(squad_with_null_manager)
    assert saved_with_manager["manager"] == [{"id": owned_manager_id, "dream": False}]
    _, active_with_manager = call("GET", "/ut/game/fifa21/squad/active")
    assert active_with_manager["manager"][0]["id"] == owned_manager_id
    assert active_with_manager["manager"][0]["itemType"] == "manager"
    assert active_with_manager["manager"][0]["cardsubtypeid"] == 4
    assert active_with_manager["manager"][0]["itemState"] == "free"
    assert active_with_manager["manager"][0]["pile"] == "club"

    # FIFA 21 PC posts its My Club paging request in the JSON body. Each page
    # must advance instead of silently repeating page zero forever.
    _, posted_club_page_1 = call("POST", "/ut/game/fifa21/club", {
        "type": "player", "sort": "desc", "start": 0, "count": 10,
    })
    _, posted_club_page_2 = call("POST", "/ut/game/fifa21/club", {
        "type": "player", "sort": "desc", "start": 10, "count": 10,
    })
    assert posted_club_page_1["itemCount"] == 250
    assert len(posted_club_page_1["itemData"]) == 10
    assert len(posted_club_page_2["itemData"]) == 10
    assert {item["id"] for item in posted_club_page_1["itemData"]}.isdisjoint(
        item["id"] for item in posted_club_page_2["itemData"]
    )

    # A card moved from Unassigned is immediately discoverable through the
    # same filtered Club POST route used by the native squad Club browser.
    candidate_resource = next(
        resource_id for resource_id in server.PLAYER_DB
        if resource_id not in server.STATE.owned_player_resource_ids()
    )
    candidate = server.STATE.make_player_item(candidate_resource, "unassigned")
    _, moved_to_club = call("PUT", "/ut/game/fifa21/item", {
        "itemData": [{"id": candidate["id"], "pile": "club"}],
    })
    assert moved_to_club["itemData"] == [{
        "id": candidate["id"], "pile": "club", "success": True,
    }]
    _, found_in_club = call("POST", "/ut/game/fifa21/club", {
        "maskedDefId": candidate["assetId"], "start": 0, "count": 91,
    })
    assert candidate["id"] in {item["id"] for item in found_in_club["itemData"]}

    # Pack duplicates make the native New Items screen resolve the existing
    # club cards by idList, especially after the reveal animation is skipped.
    lookup_ids = [item["id"] for item in club_page["itemData"][:2]]
    _, item_lookup = call(
        "GET",
        f"/ut/game/fifa21/item?idList={lookup_ids[0]},{lookup_ids[1]},999999999999",
    )
    assert [item["id"] for item in item_lookup["itemData"]] == lookup_ids
    assert all(item["resourceId"] in server.PLAYER_DB for item in item_lookup["itemData"])
    assert all("pile" not in item for item in item_lookup["itemData"])
    _, single_lookup = call("GET", f"/ut/game/fifa21/item/{lookup_ids[0]}")
    assert [item["id"] for item in single_lookup["itemData"]] == [lookup_ids[0]]
    missing_lookup_status, missing_lookup = call_raw(
        "GET", "/ut/game/fifa21/item/999999999999",
    )
    assert missing_lookup_status == 404
    assert missing_lookup == {"reason": "item not found"}

    # Existing installs can contain a persisted but completely empty squad.
    # Startup repairs that state without replacing any non-empty lineup.
    empty_squad = dict(server.STATE.list_squads()[0])
    empty_squad["players"] = []
    server.STATE.save_squad(empty_squad)
    server.STATE._repair_empty_active_squad()
    repaired = server.STATE.list_squads()[0]
    assert len(server.STATE._squad_item_ids(repaired)) == 11

    _, credits = call("GET", "/ut/game/fifa21/user/credits")
    assert credits["currencies"][0]["funds"] == 70000000

    # Squad Battles consumes match limits from the hub root.  Keeping these
    # only inside sqbtEvent makes the retail client show 0/0 matches and reject
    # every selected opponent as an ended competition.
    _, squad_battle_hub = call("GET", "/ut/game/fifa21/sqbt/user/hub")
    assert squad_battle_hub["numberOfMatchesPlayed"] == 0
    assert squad_battle_hub["maxNumberOfMatches"] == 4
    assert squad_battle_hub["gamesRemaining"] == 4
    assert squad_battle_hub["secondsUntilStart"] == 0
    assert squad_battle_hub["secondsUntilEnd"] > 0
    assert squad_battle_hub["serverCrtTime"] == squad_battle_hub["currentTime"]
    assert squad_battle_hub["startTime"] < squad_battle_hub["currentTime"]
    assert squad_battle_hub["endTime"] > squad_battle_hub["currentTime"]
    assert squad_battle_hub["active"] is True
    assert squad_battle_hub["status"] == "ACTIVE"
    opponent_summaries = squad_battle_hub["sqbtOppSquadList"]["sqbtOppSquads"]
    assert len(opponent_summaries) == 4
    assert all(opponent["sqbt2data"]["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
               for opponent in opponent_summaries)
    assert all(opponent["isHomeTeam"] is True for opponent in opponent_summaries)
    assert all(opponent["sqbt2data"]["isHomeTeam"] is True for opponent in opponent_summaries)
    assert all("topPlayers" not in opponent for opponent in opponent_summaries)
    assert all(len(opponent["sqbt2data"]["topPlayers"]) == 3 for opponent in opponent_summaries)
    generated_lineups = []
    for summary in opponent_summaries:
        _, squad_battle_opponent = call(
            "GET", f"/ut/game/fifa21/sqbt/user/opponentsquad/{summary['id']}"
        )
        assert len(squad_battle_opponent["players"]) == 23
        assert len({player["index"] for player in squad_battle_opponent["players"]}) == 23
        assert squad_battle_opponent["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
        assert squad_battle_opponent["stadiumId"] == server.SQUAD_BATTLE_STADIUM_ID
        assert squad_battle_opponent["isHomeTeam"] is True
        opponent_kits = [item for item in squad_battle_opponent["actives"]
                         if item.get("itemType") == "kit"]
        assert {item["resourceId"] for item in opponent_kits} == {6301491, 6301492}
        opponent_stadium = next(item for item in squad_battle_opponent["actives"]
                                if item.get("itemType") == "stadium")
        assert opponent_stadium["resourceId"] == server.SQUAD_BATTLE_STADIUM_RESOURCE_ID
        assert opponent_stadium["assetId"] == server.SQUAD_BATTLE_STADIUM_CARD_ASSET_ID
        assert opponent_stadium["stadiumid"] == server.SQUAD_BATTLE_STADIUM_ID
        assert opponent_stadium["id"] == 930000000090 + summary["id"]
        lineup = tuple(player["itemData"]["resourceId"] for player in squad_battle_opponent["players"])
        assert all(resource_id <= 0x00FFFFFF for resource_id in lineup)
        assert all(
            int(server.PLAYER_DB[resource_id].get("rareflag", 1)) in (0, 1)
            for resource_id in lineup
        )
        top_player_items = summary["sqbt2data"]["topPlayers"]
        assert all(set(player) == {"itemData"} for player in top_player_items)
        assert all(player["itemData"]["itemType"] == "player" for player in top_player_items)
        assert all(player["itemData"]["id"] > 0 for player in top_player_items)
        assert tuple(player["itemData"]["resourceId"] for player in top_player_items) == tuple(sorted(
            lineup[:11],
            key=lambda resource_id: server.PLAYER_DB[resource_id]["rating"],
            reverse=True,
        )[:3])
        opponent_items = {
            player["itemData"]["id"]: player["itemData"]
            for player in squad_battle_opponent["players"]
        }
        assert all(
            opponent_items[player["itemData"]["id"]]["resourceId"]
            == player["itemData"]["resourceId"]
            for player in top_player_items
        )
        generated_lineups.append(lineup)
    assert len(set(generated_lineups)) == 4
    repeated_opponent = call("GET", "/ut/game/fifa21/sqbt/user/opponentsquad/0")[1]
    assert tuple(
        player["itemData"]["resourceId"] for player in repeated_opponent["players"]
    ) == generated_lineups[0]

    # Selecting an opponent immediately asks for these 23 attribute records.
    # The retail CardsDLL parser consumes a bare array with integer arrays;
    # an object wrapper or web-style {index, value} entries leaves the UI
    # waiting forever instead of navigating to the active-squad screen.
    opponent_def_ids = [
        player["itemData"]["resourceId"] for player in repeated_opponent["players"]
    ]
    _, attribute_metadata = call(
        "GET",
        "/ut/game/fifa21/attributes/metadata?defIds="
        + ",".join(str(resource_id) for resource_id in opponent_def_ids),
    )
    assert isinstance(attribute_metadata, list)
    assert [record["defId"] for record in attribute_metadata] == opponent_def_ids
    assert all(set(record) == {
        "defId", "ingameattribs", "attributeArray", "rating",
        "trait1", "trait2", "version",
    } for record in attribute_metadata)
    assert all(len(record["ingameattribs"]) == 34 for record in attribute_metadata)
    assert all(len(record["attributeArray"]) == 6 for record in attribute_metadata)
    assert all(
        isinstance(value, int)
        for record in attribute_metadata
        for key in ("ingameattribs", "attributeArray")
        for value in record[key]
    )

    # Aqua pro cards must remain maxed whether CardsDLL requests their custom
    # definition IDs or the underlying gold asset IDs.
    aqua_ids = [167963031, 167996618, 190871, 224458]
    _, aqua_metadata = call(
        "GET",
        "/ut/game/fifa21/attributes/metadata?defIds="
        + ",".join(str(resource_id) for resource_id in aqua_ids),
    )
    assert [record["defId"] for record in aqua_metadata] == aqua_ids
    assert all(record["rating"] == 99 for record in aqua_metadata)
    assert all(record["attributeArray"] == [99] * 6 for record in aqua_metadata)
    assert all(record["ingameattribs"] == [99] * 34 for record in aqua_metadata)
    for resource_id in (167963031, 167996618):
        aqua_item = server.STATE.build_player_item(resource_id, 999000 + resource_id, "club")
        assert aqua_item["weakFoot"] == 5
        assert aqua_item["skillMoves"] == 5
    neymar_aqua = server.STATE.build_player_item(167963031, 167963031, "club")
    assert neymar_aqua["preferredPosition"] == "ST"
    assert aqua_item["attributeList"] == [
        {"index": index, "value": 99} for index in range(6)
    ]

    # FIFA's pre-match kit picker derives ownership from the squad returned by
    # match creation. This mirrors Aurora17's working handoff: active kits are
    # present in both the actives and club arrays rather than unlocked through
    # a catalogue/islocked flag.
    _, match = call("POST", "/ut/game/fifa21/match", {
        "squadId": 1,
        "type": "OFFLINE",
        "sqbtEventId": squad_battle_hub["sqbtEvent"]["id"],
        "sqbtOpponentSquadId": 0,
        "sqbtMatchDifficulty": 3,
    })
    assert match["startDateTime"] > 0
    assert match["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert match["stadiumId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert match["isHomeTeam"] is True
    assert match["homeSquad"] == match["squad"]
    assert match["opponentSquad"] == match["awaySquad"] == match["opponent"]
    assert match["opponentSquad"]["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert match["opponentSquad"]["stadiumId"] == server.SQUAD_BATTLE_STADIUM_ID
    assert match["opponentSquad"]["sqbt2data"]["stadiumAssetId"] == server.SQUAD_BATTLE_STADIUM_ID
    for collection_name in ("actives", "club"):
        away_stadium = next(
            item for item in match["opponentSquad"][collection_name]
            if item.get("itemType") == "stadium"
        )
        assert away_stadium["resourceId"] == server.SQUAD_BATTLE_STADIUM_RESOURCE_ID
        assert away_stadium["assetId"] == server.SQUAD_BATTLE_STADIUM_CARD_ASSET_ID
        home_stadium_ids = {
            item["id"] for item in match["squad"][collection_name]
            if item.get("itemType") == "stadium"
        }
        assert away_stadium["id"] not in home_stadium_ids
    assert match["squad"]["id"] == server.STATE.list_squads()[0]["id"]
    for collection_name in ("actives", "club"):
        match_kits = [
            item for item in match["squad"][collection_name]
            if item.get("itemType") == "kit"
        ]
        assert len(match_kits) == 2
        assert {item["resourceId"] for item in match_kits} == {6300074, 6301345}
        assert len({item["id"] for item in match_kits}) == 2
        assert {item["assetId"] for item in match_kits} == {6300074, 6301345}
        assert {item["year"] for item in match_kits} == {0}
        assert {item["resourceGameYear"] for item in match_kits} == {2021}
        assert {item["pile"] for item in match_kits} == {"club"}
        assert all(item["stackCount"] == 1 for item in match_kits)
        match_stadium = next(item for item in match["squad"][collection_name]
                             if item.get("itemType") == "stadium")
        assert match_stadium["resourceId"] == server.SQUAD_BATTLE_STADIUM_RESOURCE_ID
        assert match_stadium["assetId"] == server.SQUAD_BATTLE_STADIUM_CARD_ASSET_ID
        assert match_stadium["stadiumid"] == server.SQUAD_BATTLE_STADIUM_ID

    _, store = call("GET", "/ut/game/fifa21/store/purchasegroup/all?ppInfo=true")
    draft_entries = [entry for entry in store["purchase"]
                     if entry.get("packType") == "DRAFTENTRY"]
    assert {entry["id"] for entry in draft_entries} == {20024, 20026, 20027}
    assert all(entry["finalPrice"] == 15000 for entry in draft_entries)
    promo_store_pack = next(pack for pack in store["purchase"] if pack["id"] == 4)
    assert promo_store_pack["name"] == "Elite Ten 82+ Pack"
    assert promo_store_pack["finalPrice"] == 50000
    assert promo_store_pack["displayGroup"] == {"value": "special", "priority": 4}
    assert promo_store_pack["purchaseGroup"] == "special"
    assert promo_store_pack["categoryList"] == {"categoryId": 4, "orderInCategory": 4}
    assert promo_store_pack["packContentInfo"] == {
        "itemQuantity": 10, "goldQuantity": 10, "silverQuantity": 0,
        "bronzeQuantity": 0, "rareQuantity": 10,
    }

    # Audit every authored SBC reward, not just one example. Each named pack
    # has its own openable profile, and every player reward resolves to the
    # intended footballer rather than trusting the catalog's obsolete IDs.
    expected_sbc_packs = {
        "bronze": 100, "silver": 200, "premium_silver_players": 201,
        "gold": 300, "premium_gold": 301, "gold_players": 304,
        "premium_gold_players": 305, "electrum_players": 306,
        "prime_electrum_players": 307, "prime_gold_players": 308,
        "prime_mixed_players": 309, "rare_players": 401,
        "rare_mixed_players": 406, "rare_gold": 400,
        "jumbo_rare_players": 402, "mega": 403, "rare_mega": 404,
        "ultimate": 405,
    }
    assert {key: server._sbc_pack_id(key) for key in expected_sbc_packs} == expected_sbc_packs
    assert all(server.get_pack_cfg(pack_id) for pack_id in expected_sbc_packs.values())
    player_reward_count = 0
    for challenge in server._SBC_CHAL_BY_INT.values():
        for reward in challenge.get("rewards", {}).get("entries", []):
            kind = str(reward.get("type", "")).upper()
            attrs = reward.get("attributes", {})
            if kind == "PACK":
                pack_id = server._sbc_pack_id(attrs.get("pack"), challenge)
                assert server.get_pack_cfg(pack_id), (challenge["id"], attrs)
            elif kind == "PLAYER":
                player_reward_count += 1
                resource_id = server._sbc_player_resource_id(reward)
                meta = server.PLAYER_DB[resource_id]
                intended = server._sbc_normalize_name(attrs.get("name"))
                actual = server._sbc_normalize_name(meta.get("name"))
                assert actual and (actual in intended or intended in actual), (
                    challenge["id"], attrs, resource_id, meta.get("name")
                )
                award = server._sbc_award_json(reward, challenge)
                assert award["rating"] == int(attrs["rating"])
                assert award["position"] == attrs["position"]
                assert award["itemData"]["rating"] == int(attrs["rating"])
                assert award["itemData"]["preferredPosition"] == attrs["position"]
    assert player_reward_count == 15

    # Upgrade SBCs must award FIFA's native misc player-pick token, retain one
    # stable set of four choices, and persist only the selected 82+/84+/86+/88+
    # player in New Items.
    assert server._sbc_pack_id("player_pick", {"_setStrId": "SBC_UP_82"}) == 513
    assert server._sbc_pack_id("player_pick", {"_setStrId": "SBC_UP_84"}) == 514
    assert server._sbc_pack_id("player_pick", {"_setStrId": "SBC_UP_86"}) == 515
    assert server._sbc_pack_id("player_pick", {"_setStrId": "SBC_UP_88"}) == 516
    for set_id in ("UP_82", "UP_84", "UP_86", "UP_88"):
        upgrade_set = next(value for value in server.SBC_DB["sets"] if value["id"] == set_id)
        upgrade = upgrade_set["challenges"][0]
        assert upgrade["requirements"]["rules"] == [{
            "type": "PLAYERS", "attributes": {"min": 11, "max": 11},
        }]
        assert server._sbc_elg_req(upgrade) == []
        upgrade_json = server._sbc_challenge_json(upgrade)
        assert upgrade_json["packsAmount"] == 1
        assert upgrade_json["itemsAmount"] == 0
        assert upgrade_json["awards"][0]["type"] == "pack"
        assert upgrade_json["awards"][0]["isPlayerPick"] is True
        assert upgrade_json["awards"][0]["packType"] == "PLAYER_PICK"
        assert upgrade_json["awards"][0]["itemData"] == []
    pick_upgrade = next(value for value in server.SBC_DB["sets"] if value["id"] == "UP_82")["challenges"][0]
    pick_packs_before = server.STATE.unopened_pack_ids().count(513)
    granted_pick_pack = server._sbc_grant_rewards(pick_upgrade)
    assert granted_pick_pack == [{"itemType": "pack", "packId": 513}]
    assert server.STATE.unopened_pack_ids().count(513) == pick_packs_before + 1
    assert server.STATE.pending_player_pick_tokens() == []
    _, owned_pick_packs = call("GET", "/ut/game/fifa21/store/purchasegroup/mypacks")
    owned_pick_pack = next(pack for pack in owned_pick_packs["purchase"] if pack["id"] == 513)
    assert owned_pick_pack["name"] == "82+ Player Pick"
    assert server.STATE.take_unopened_pack(513)
    pick_token = server.STATE.make_player_pick_item(86, 4)
    assert pick_token["itemType"] == "misc"
    assert pick_token["cardsubtypeid"] == 237
    assert pick_token["CARD_TYPE"] == "UNLOCKS"
    assert pick_token["UNLOCKABLE_TYPE"] == "PLAYER_PICK"
    assert pick_token["name"] == "1 of 4 86+ Player Pick"
    # This is the exact native FIFA 21 request emitted by RedeemPlayerPickItem:
    # the token ID is a query parameter and the request body is empty.
    _, redeemed_pick = call(
        "POST", f"/ut/game/fifa21/item/nontargeted?itemId={pick_token['id']}"
    )
    assert redeemed_pick["availablePicks"] == 1
    assert len(redeemed_pick["choiceData"]) == 4
    assert len({item["id"] for item in redeemed_pick["choiceData"]}) == 4
    assert len({item["resourceId"] for item in redeemed_pick["choiceData"]}) == 4
    assert all(item["rating"] >= 86 for item in redeemed_pick["choiceData"])
    assert [item["globalChoiceIndex"] for item in redeemed_pick["choiceData"]] == list(range(4))
    assert [item["localChoiceIndex"] for item in redeemed_pick["choiceData"]] == list(range(4))
    _, pending_pick = call("GET", "/ut/game/fifa21/playerpicks")
    assert [item["id"] for item in pending_pick["choiceData"]] == [
        item["id"] for item in redeemed_pick["choiceData"]
    ]
    selected_pick = redeemed_pick["choiceData"][2]
    # FIFA 21 PC sends the chosen resource id in the URL and no request body.
    # The granted item must retain the already-rendered choice's instance id,
    # while the unlock token is consumed.
    _, native_confirmed_pick = call(
        "POST",
        f"/ut/game/fifa21/playerpicks/item/{selected_pick['resourceId']}/select",
    )
    assert native_confirmed_pick["id"] == selected_pick["id"]
    assert native_confirmed_pick["resourceId"] == selected_pick["resourceId"]
    assert server.STATE.get_item(pick_token["id"]) is None
    stored_pick = server.STATE.get_item(selected_pick["id"])
    assert stored_pick["untradeable"] is True
    assert stored_pick["pile"] == "unassigned"

    # Keep the body/index compatibility route covered with a separate token.
    pick_token = server.STATE.make_player_pick_item(86, 4)
    _, redeemed_pick = call(
        "POST", f"/ut/game/fifa21/item/nontargeted?itemId={pick_token['id']}"
    )
    selected_pick = redeemed_pick["choiceData"][2]
    _, confirmed_pick = call("POST", f"/ut/game/fifa21/playerpicks/{pick_token['id']}", {
        "globalChoiceIndex": 2,
    })
    assert confirmed_pick["resourceId"] == selected_pick["resourceId"]
    assert server.STATE.get_item(pick_token["id"]) is None
    stored_pick = server.STATE.get_item(selected_pick["id"])
    assert stored_pick["untradeable"] is True
    assert stored_pick["pile"] == "unassigned"
    assert "playerPickItemId" not in stored_pick
    assert "globalChoiceIndex" not in stored_pick

    queued_pick = server.STATE.create_pack(515)
    assert queued_pick["packType"] == "PLAYER_PICK"
    assert len(queued_pick["itemData"]) == 1
    assert queued_pick["itemData"][0]["cardsubtypeid"] == 237
    queued_choices = server.STATE.redeem_player_pick(queued_pick["itemData"][0]["id"])
    server.STATE.select_player_pick(queued_pick["itemData"][0]["id"], queued_choices[0]["id"])

    legacy_direct_pick = server.STATE.make_player_pick_item(82, 4)
    recovered_before = server.STATE.unopened_pack_ids().count(513)
    server.STATE._migrate_direct_player_pick_tokens()
    assert server.STATE.get_item(legacy_direct_pick["id"]) is None
    assert server.STATE.unopened_pack_ids().count(513) == recovered_before + 1
    assert server.STATE.take_unopened_pack(513)

    # Completing an SBC places its pack in My Packs, reports the live count,
    # opens it for free, consumes exactly one owned copy, and cannot pay twice.
    first_exchange = next(
        challenge for challenge in server._SBC_CHAL_BY_INT.values()
        if challenge.get("id") == "CH_1"
    )
    first_exchange_id = int(first_exchange["_int"])
    unopened_before = len(server.STATE.unopened_pack_ids())
    sbc_credits_before = server.STATE.credits()
    call("PUT", f"/ut/game/fifa21/sbs/challenge/{first_exchange_id}/squad", {
        "players": [{"itemData": {"id": 999001}}],
    })
    _, submitted = call("PUT", f"/ut/game/fifa21/sbs/challenge/{first_exchange_id}", {})
    assert submitted["success"] is True
    assert submitted["unopenedPacks"] == unopened_before + 1
    assert server.STATE.unopened_pack_ids().count(100) == 1
    _, my_packs = call("GET", "/ut/game/fifa21/store/purchasegroup/mypacks")
    bronze_reward = next(pack for pack in my_packs["purchase"] if pack["id"] == 100)
    assert bronze_reward["quantity"] == 1
    assert bronze_reward["packContentInfo"] == {
        "itemQuantity": 12, "goldQuantity": 0, "silverQuantity": 0,
        "bronzeQuantity": 12, "rareQuantity": 1,
    }
    _, opened_reward = call("POST", "/ut/game/fifa21/purchased/items", {
        "packId": 100, "useCredits": 0, "usePreOrder": 1,
        "currency": "PREORDER",
    })
    assert len(opened_reward["itemList"]) == 12
    assert server.STATE.credits() == sbc_credits_before
    assert len(server.STATE.unopened_pack_ids()) == unopened_before
    call("PUT", f"/ut/game/fifa21/sbs/challenge/{first_exchange_id}", {})
    assert len(server.STATE.unopened_pack_ids()) == unopened_before

    _, stats = call("GET", "/ut/game/fifa21/club/stats/summary")
    assert isinstance(stats, dict)

    _, rarities = call("GET", "/fut/items/images/backgrounds/itemBGs/futitemraritytunables.json")
    rarity_ids = {int(entry["id"]) for entry in rarities["rarities"]}
    used_rarity_ids = {int(meta.get("rareflag", 0)) for meta in server.PLAYER_DB.values()}
    assert rarities["version"] == 1
    assert {0, 1, 999}.issubset(rarity_ids)
    assert used_rarity_ids.issubset(rarity_ids)

    # The FIFA 21 reveal contract has fourteen timed events (including the
    # nation/club/league panels); the legacy FIFA 19 file had only nine. Also
    # ensure TOTW and Summer Stars retain distinct flare palettes.
    _, opening = call("GET", "/fut/packs/packopening/packopeningsetting.json")
    assert all(len(profile["timingSec"]) == 14 for profile in opening["revealItemTimingList"])
    opening_by_rarity = {int(entry["rarity"]): entry for entry in opening["itemList"]}
    totw_opening = opening_by_rarity[3]
    summer_stars_opening = opening_by_rarity[61]
    assert totw_opening["walkOutBucketId"][-1] == 2
    assert summer_stars_opening["walkOutBucketId"][-1] == 1
    assert totw_opening["sparkJetsColor1"][-1] != summer_stars_opening["sparkJetsColor1"][-1]
    assert all("shell" in entry for entry in rarities["rarities"]
               if int(entry.get("type", 1)) != 0)

    current = server.STATE.list_squads()[0]
    _, saved = call("PUT", "/ut/game/fifa21/squad/1", current)
    assert saved["id"] == 1
    persisted = server.STATE.list_squads()[0]
    assert persisted["squadName"] == current["squadName"]
    assert len(persisted["players"]) == 11

    before_credits = server.STATE.credits()
    manager_pack_settings = json.loads(json.dumps(server.DEFAULT_PACK_SETTINGS))
    manager_pack_settings["packs"]["3"]["manager_chance"] = 1.0
    server.PACK_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    server.PACK_SETTINGS_PATH.write_text(json.dumps(manager_pack_settings), encoding="utf-8")
    _, purchased = call("POST", "/ut/game/fifa21/purchased/items", {
        "packId": 3, "useCredits": 1, "usePreOrder": 0, "currency": "COINS",
    })
    assert purchased["state"] == "PURCHASECOMPLETE"
    assert purchased["purchasePackType"] == "CARDPACK"
    assert purchased["transactionId"] > 0
    assert purchased["createPackResponse"]["purchasedPackId"] == 3
    assert len(purchased["createPackResponse"]["itemList"]) == 12
    packed_managers = [item for item in purchased["itemList"]
                       if item.get("itemType") == "manager"]
    assert len(packed_managers) == 1
    assert packed_managers[0]["cardsubtypeid"] == 4
    assert packed_managers[0]["resourceId"] in server.MANAGER_DB
    assert all(item["resourceId"] in server.PLAYER_DB
               for item in purchased["itemList"] if item.get("itemType") == "player")
    assert server.STATE.credits() == before_credits - server.get_pack_cfg(3)["price"]

    _, unassigned = call("GET", "/ut/game/fifa21/purchased/items")
    assert unassigned["numberItems"] == 12
    assert sum(1 for item in unassigned["itemData"] if item.get("itemType") == "manager") == 1
    packed_manager = next(item for item in unassigned["itemData"]
                          if item.get("itemType") == "manager")
    _, stored_manager = call("PUT", "/ut/game/fifa21/item", {
        "itemData": [{"id": packed_manager["id"], "pile": "club"}],
    })
    assert stored_manager["itemData"] == [{
        "id": packed_manager["id"], "pile": "club", "success": True,
    }]
    _, managers_after_pack = call("POST", "/ut/game/fifa21/club", {
        "type": "manager", "start": 0, "count": 91,
    })
    assert packed_manager["id"] in {
        item["id"] for item in managers_after_pack["itemData"]
    }

    promo_credits_before = server.STATE.credits()
    _, promo_pack = call("POST", "/ut/game/fifa21/purchased/items", {
        "packId": 4, "useCredits": 1, "usePreOrder": 0, "currency": "COINS",
    })
    assert len(promo_pack["itemList"]) == 10
    assert all(item["rating"] >= 82 for item in promo_pack["itemList"])
    assert sum(1 for item in promo_pack["itemList"] if item["rareflag"] > 1) >= 1
    assert server.STATE.credits() == promo_credits_before - 50000

    # Every imported promotion can be selected as a pack pool. Force one of the
    # late FIFA 21 rarities to prove this is wired beyond catalog loading.
    forced_settings = json.loads(json.dumps(server.DEFAULT_PACK_SETTINGS))
    forced_pack = forced_settings["packs"]["3"]
    forced_pack["special_chance"] = 1.0
    forced_pack["special_weights"] = {
        rarity_id: (1 if rarity_id == "61" else 0)
        for rarity_id in server.DEFAULT_SPECIAL_WEIGHTS
    }
    server.PACK_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    server.PACK_SETTINGS_PATH.write_text(json.dumps(forced_settings), encoding="utf-8")
    _, summer_pack = call("POST", "/ut/game/fifa21/purchased/items", {
        "packId": 3, "useCredits": 1, "usePreOrder": 0, "currency": "COINS",
    })
    summer_specials = [item for item in summer_pack["itemList"] if item["rareflag"] == 61]
    assert len(summer_specials) == 3
    assert all(server.PLAYER_DB[item["resourceId"]]["rarityName"] == "Summer Stars"
               for item in summer_specials)

    feature_settings = json.loads(json.dumps(server.DEFAULT_PACK_SETTINGS))
    feature_settings["packs"]["8"] = {
        "enabled": True, "name": "All Consumables Pack", "bio": "Native modifiers",
        "price": 1, "size": 20, "all_consumables": True, "store_group": "special",
    }
    feature_settings["packs"]["9"] = {
        "enabled": True, "name": "All Kits Pack", "bio": "Native kits",
        "price": 1, "size": 30, "all_kits": True, "store_group": "special",
    }
    server.PACK_SETTINGS_PATH.write_text(json.dumps(feature_settings), encoding="utf-8")

    modifier_pack = server.STATE.create_pack(8)
    assert len(modifier_pack["itemData"]) == 20
    assert {item["resourceId"] for item in modifier_pack["itemData"]} == set(
        server._POSITION_MODIFIER_BY_RESOURCE
    )
    assert all(item["itemType"] == "training" for item in modifier_pack["itemData"])
    target_player = next(
        item for item in server.STATE.list_items()
        if item.get("itemType", "player") == "player"
        and any(definition["source"] == item.get("preferredPosition")
                for definition in server._POSITION_MODIFIER_BY_RESOURCE.values())
    )
    modifier = next(
        item for item in modifier_pack["itemData"]
        if server._POSITION_MODIFIER_BY_RESOURCE[item["resourceId"]]["source"]
        == target_player["preferredPosition"]
    )
    assert server.STATE.update_item_pile(modifier["id"], "club")
    expected_position = server._POSITION_MODIFIER_BY_RESOURCE[modifier["resourceId"]]["target"]
    status, _ = call_raw(
        "POST", f"/ut/game/fifa21/item/resource/{modifier['resourceId']}",
        {"apply": [{"id": target_player["id"]}]},
    )
    assert status == 200
    assert server.STATE.get_item(target_player["id"])["preferredPosition"] == expected_position
    assert server.STATE.get_item(modifier["id"]) is None

    kit_pack = server.STATE.create_pack(9)
    assert len(kit_pack["itemData"]) == 30
    assert len({item["resourceId"] for item in kit_pack["itemData"]}) == 30
    assert all(item["itemType"] == "kit" and item["cardsubtypeid"] == 9
               for item in kit_pack["itemData"])
    equipped_kit = kit_pack["itemData"][0]
    assert server.STATE.update_item_pile(equipped_kit["id"], "club")
    _, equip_result = call("PUT", "/ut/game/fifa21/item", {
        "itemData": [{"id": equipped_kit["id"], "pile": "club",
                      "itemState": "activeHomeKit"}],
    })
    assert equip_result["itemData"][0]["success"] is True
    assert any(item.get("id") == equipped_kit["id"]
               and item.get("itemState") == "activeHomeKit"
               for item in server._active_club_items())

    _, customisation_grant = call("POST", "/localfut/customisation/grant-all", {})
    assert customisation_grant["success"] is True
    assert customisation_grant["available"]["kits"] == len(server.KIT_DB)
    assert customisation_grant["available"]["stadiums"] == len(server.STADIUM_DB)
    owned_kit_resources = {
        item["resourceId"] for item in server.STATE.list_items("club")
        if item.get("itemType") == "kit"
    }
    assert {int(item["resourceId"]) for item in server.KIT_DB}.issubset(owned_kit_resources)
    _, repeated_grant = call("POST", "/localfut/customisation/grant-all", {})
    assert repeated_grant["grantedCount"] == 0
    for item_type, item_state in (("custom", "activeBadge"), ("ball", "activeBall")):
        cosmetic = next(item for item in server.STATE.list_items("club")
                        if item.get("itemType") == item_type)
        _, equipped = call("PUT", "/ut/game/fifa21/item", {
            "itemData": [{"id": cosmetic["id"], "pile": "club",
                          "itemState": item_state}],
        })
        assert equipped["itemData"] == [{
            "id": cosmetic["id"], "pile": "club", "itemState": item_state,
            "success": True,
        }]

    rejected_status, rejected = call_raw("POST", "/ut/game/fifa21/purchased/items", {"useCredits": 1})
    assert rejected_status == 400
    assert rejected["code"] == "400"

    print("VALID: direct local API checks passed (no listener started)")
    print("  endpoints: /ut/auth, accountinfo, user, squad/active, club, item lookup, credits, rarity content, squad PUT, pack purchase")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
