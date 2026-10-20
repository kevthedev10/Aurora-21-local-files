"""Local first-run policy over the recovered FIFA 21 wire contracts.

This is not EA's historical reward generator. Keep grants atomic/retry-safe,
and keep owned item IDs separate from preview/offer IDs and progress values.
"""
import json
import random


NATIONS = (14, 54, 18, 52, 21, 27, 45, 38, 34)
STARTER_POSITIONS = (
    "GK", "RB", "CB", "CB", "LB", "RM", "CM", "CM", "LM", "ST", "ST",
    "GK", "CB", "RB", "LB", "CM", "RM", "ST", "LM", "CB", "CM", "ST", "GK",
)
CLIENT_DATA = frozenset(("onboarding", "loanplayerreward", "squad", "tutorialpopups"))


class OnboardingError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def integer(value, name, minimum=0, maximum=2**53 - 1):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise OnboardingError(400, f"Invalid {name}")
    return value


class Onboarding:
    def __init__(self, state, players, config, cosmetic_templates):
        self.state = state
        self.players = players
        self.config = config
        self.cosmetic_templates = cosmetic_templates

    def created(self):
        return bool(self.state.get("onboarding_created", False))

    def managed(self):
        return bool(self.state.get("onboarding_managed", False))

    def _set(self, key, value):
        # Caller owns the State lock/transaction; unlike State.set, no commit.
        self.state.conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def _store_item(self, item):
        self.state.conn.execute(
            "INSERT INTO items(id,resource_id,pile,data) VALUES(?,?,?,?)",
            (item["id"], item["resourceId"], item["pile"], json.dumps(item)),
        )

    def _next_id(self):
        item_id = int(self.state.get("next_item_id", 900000000000)) + 1
        self._set("next_item_id", item_id)
        return item_id

    def _require_club(self):
        if not self.created():
            raise OnboardingError(409, "Choose a nationality and create the club first")

    def select_country(self, ordinal):
        integer(ordinal, "country ordinal", 1, len(NATIONS))
        nation = NATIONS[ordinal - 1]
        with self.state.lock, self.state.conn:
            if self.created() and self.state.get("onboarding_nation") != nation:
                raise OnboardingError(409, "The starter club has already been created")
            self._set("onboarding_nation", nation)

    def _starter_resources(self, nation):
        base = [(rid, meta) for rid, meta in self.players.items()
                if 0 < rid < 0x1000000 and not meta.get("special")
                and int(meta.get("rareflag", 0)) <= 1
                and 55 <= int(meta.get("rating", 0)) < 75]
        rng = random.Random(nation)
        selected = []
        for index, position in enumerate(STARTER_POSITIONS):
            pool = [(rid, meta) for rid, meta in base
                    if rid not in selected and meta.get("preferredPosition") == position]
            if not pool:
                raise OnboardingError(503, f"Mined starter catalog has no eligible {position}")
            # Local policy: emphasize the selected nation in roughly half the slots.
            national = [(rid, meta) for rid, meta in pool if int(meta.get("nation", 0)) == nation]
            if index % 2 == 0 and national:
                pool = national
            pool.sort(key=lambda entry: (abs(int(entry[1]["rating"]) - 63), entry[0]))
            selected.append(rng.choice(pool[:20])[0])
        return selected

    def create_club(self, payload):
        if not isinstance(payload, dict):
            raise OnboardingError(400, "Expected a create-user object")
        name = payload.get("clubName", self.config["club_name"])
        abbr = payload.get("clubAbbr", self.config["club_abbr"])
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32:
            raise OnboardingError(400, "Invalid clubName")
        if not isinstance(abbr, str) or not 1 <= len(abbr.strip()) <= 5:
            raise OnboardingError(400, "Invalid clubAbbr")
        for key in ("useFut1Data", "purchased"):
            if key in payload and not isinstance(payload[key], bool):
                raise OnboardingError(400, f"Invalid {key}")
        with self.state.lock, self.state.conn:
            if self.created():
                return self.starter_items()
            nation = self.state.get("onboarding_nation")
            if nation not in NATIONS:
                raise OnboardingError(409, "Country selection is required before club creation")
            resources = self._starter_resources(nation)
            items = []
            for rid in resources:
                item = self.state.build_player_item(rid, self._next_id(), "club")
                item["untradeable"] = True
                item["discardValue"] = 0
                self._store_item(item)
                items.append(item)
            cosmetic_ids = {}
            for template in self.cosmetic_templates():
                item = dict(template, id=self._next_id(), pile="club", untradeable=True, discardValue=0)
                self._store_item(item)
                cosmetic_ids[item["itemState"]] = item["id"]
            squad = {
                "id": 1, "squadId": 1, "name": name.strip(), "squadName": name.strip(),
                "formation": "f442", "active": True, "rating": 0, "chemistry": 0,
                "squadType": "REGULAR_SQUAD", "starRating": 1,
                "players": [{"index": index, "itemId": item["id"], "kitNumber": index + 1}
                            for index, item in enumerate(items)],
            }
            self.state.conn.execute("INSERT INTO squads(id,name,active,data) VALUES(1,?,1,?)",
                                    (name.strip(), json.dumps(squad)))
            for key, value in {
                "club_name": name.strip(), "club_abbr": abbr.strip(),
                "onboarding_created": True, "onboarding_managed": True,
                "onboarding_starter_item_ids": [item["id"] for item in items],
                "onboarding_active_items": cosmetic_ids,
            }.items():
                self._set(key, value)
            return items

    def starter_items(self):
        return [item for iid in self.state.get("onboarding_starter_item_ids", [])
                if (item := self.state.get_item(iid)) is not None]

    def client_data(self, kind):
        saved = self.state.get("clientdata:" + kind, {})
        return {"entries": [{"key": int(key), "value": value}
                            for key, value in sorted(saved.items(), key=lambda entry: int(entry[0]))]}

    def _progress(self, kind, entries):
        saved = dict(self.state.get("clientdata:" + kind, {}))
        saved.update({str(key): value for key, value in entries.items()})
        self._set("clientdata:" + kind, saved)

    def write_client_data(self, kind, payload):
        if kind not in CLIENT_DATA or not isinstance(payload, dict):
            raise OnboardingError(400, "Unsupported client-data request")
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) > 256:
            raise OnboardingError(400, "Expected bounded client-data entries")
        updates = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise OnboardingError(400, "Invalid client-data entry")
            key = integer(entry.get("key"), "client-data key", 0, 4096)
            value = integer(entry.get("value"), "client-data value", -(2**31), 2**31 - 1)
            if key in updates:
                raise OnboardingError(400, "Duplicate client-data key")
            updates[key] = value
        with self.state.lock, self.state.conn:
            self._require_club()
            if kind == "loanplayerreward":
                # Selection progress is earned by a persisted squad save, not client assertion.
                current = {entry["key"]: entry["value"] for entry in self.client_data(kind)["entries"]}
                if any(current.get(key) != value for key, value in updates.items()):
                    raise OnboardingError(409, "Loan progress does not match the saved squad")
            if kind == "onboarding":
                if any(key != 0 or value not in (2, 3) for key, value in updates.items()):
                    raise OnboardingError(400, "Only native quit/completion acknowledgements may be written")
                if updates.get(0) == 3 and not self.state.get("onboarding_badge_choice"):
                    raise OnboardingError(409, "No completed onboarding selection to acknowledge")
            self._progress(kind, updates)
            return self.client_data(kind)

    def sign_loan(self, offer_id, offers, games):
        integer(offer_id, "loan offer", 1)
        offer = next((offer for offer in offers if offer["defId"] == offer_id), None)
        if offer is None:
            raise OnboardingError(404, "Unknown loan offer")
        with self.state.lock, self.state.conn:
            self._require_club()
            previous = self.state.get("onboarding_loan_offer_id")
            if previous is not None:
                if previous != offer_id:
                    raise OnboardingError(409, "A different onboarding loan was already selected")
                item = self.state.get_item(self.state.get("onboarding_loan_item_id"))
                if item is None:
                    raise OnboardingError(409, "The selected loan item no longer exists")
                return item
            rid = offer["itemData"]["resourceId"]
            item = self.state.build_player_item(rid, self._next_id(), "unassigned", games)
            self._store_item(item)
            self._set("onboarding_loan_offer_id", offer_id)
            self._set("onboarding_loan_item_id", item["id"])
            self._set("onboarding_loan_resource_id", rid)
            return item

    def save_squad(self, payload, sid, onboarding=False):
        if not isinstance(payload, dict):
            raise OnboardingError(400, "Expected squad object")
        payload = payload.get("squad", payload)
        if not isinstance(payload, dict) or not isinstance(payload.get("players"), list):
            raise OnboardingError(400, "Expected squad players")
        integer(sid, "squad id", 1)
        with self.state.lock, self.state.conn:
            self._require_club()
            row = self.state.conn.execute("SELECT data FROM squads WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise OnboardingError(404, "Unknown squad")
            squad = json.loads(row[0])
            positions = {entry["index"]: dict(entry) for entry in squad.get("players", [])}
            seen = set()
            for entry in payload["players"]:
                if not isinstance(entry, dict):
                    raise OnboardingError(400, "Invalid squad position")
                index = integer(entry.get("index"), "squad index", 0, 22)
                if index in seen:
                    raise OnboardingError(400, "Duplicate squad position")
                seen.add(index)
                iid = entry.get("itemId", entry.get("id"))
                if iid is None and isinstance(entry.get("itemData"), dict):
                    iid = entry["itemData"].get("id")
                iid = integer(iid, "owned player id")
                if not iid:
                    positions[index] = {"index": index, "kitNumber": 0}
                    continue
                item = self.state.get_item(iid)
                if not item or item.get("itemType") != "player" or item.get("pile") not in ("club", "squad"):
                    raise OnboardingError(400, "Squad item must be an owned club player")
                positions[index] = {"index": index, "itemId": iid,
                                    "kitNumber": integer(entry.get("kitNumber", index + 1), "kit number", 0, 99)}
            owned_ids = [entry["itemId"] for entry in positions.values() if entry.get("itemId")]
            if len(set(owned_ids)) != len(owned_ids):
                raise OnboardingError(400, "One owned player cannot occupy two squad positions")
            loan_id = self.state.get("onboarding_loan_item_id")
            if onboarding and (not loan_id or loan_id not in [positions.get(i, {}).get("itemId") for i in range(11)]):
                raise OnboardingError(409, "Onboarding squad must contain the selected loan in the starting eleven")
            # Preserve identity/formation/untouched bench when the client sends a partial update.
            if "formation" in payload:
                if not isinstance(payload["formation"], str) or len(payload["formation"]) > 20:
                    raise OnboardingError(400, "Invalid formation")
                squad["formation"] = payload["formation"]
            squad["players"] = [positions[index] for index in sorted(positions)]
            self.state.conn.execute("UPDATE squads SET data=? WHERE id=?", (json.dumps(squad), sid))
            if onboarding:
                self._progress("loanplayerreward", {0: int(self.state.get("onboarding_loan_resource_id")) & 0xFFFFFF})
            return squad

    def cosmetic_choices(self, kind):
        templates = self.cosmetic_templates()
        if kind == "kits":
            return {
                "homeItemDataList": [dict(item, id=item["resourceId"]) for item in templates if item["itemType"] == "kit" and item["itemState"] == "activeHomeKit"],
                "awayItemDataList": [dict(item, id=item["resourceId"]) for item in templates if item["itemType"] == "kit" and item["itemState"] == "activeAwayKit"],
            }
        return {"badgeItemDataList": [dict(item, id=item["resourceId"]) for item in templates if item["itemState"] == "activeBadge"]}

    def _select_cosmetic(self, rid, candidates, state_name):
        template = next((item for item in candidates if item["resourceId"] == rid), None)
        if template is None:
            raise OnboardingError(404, "Unknown cosmetic choice")
        active = dict(self.state.get("onboarding_active_items", {}))
        existing = self.state.get_item(active.get(state_name, 0))
        if existing and existing["resourceId"] == rid:
            return existing["id"]
        if existing:
            existing["itemState"] = "free"
            self.state.conn.execute("UPDATE items SET data=? WHERE id=?", (json.dumps(existing), existing["id"]))
        item = dict(template, id=self._next_id(), pile="club", itemState=state_name, untradeable=True, discardValue=0)
        self._store_item(item)
        active[state_name] = item["id"]
        self._set("onboarding_active_items", active)
        return item["id"]

    def select_kits(self, payload):
        if not isinstance(payload, dict):
            raise OnboardingError(400, "Expected paired kit choices")
        home = integer(payload.get("homeKitId"), "home kit", 1)
        away = integer(payload.get("awayKitId"), "away kit", 1)
        choices = self.cosmetic_choices("kits")
        with self.state.lock, self.state.conn:
            self._require_club()
            if not self.client_data("loanplayerreward")["entries"]:
                raise OnboardingError(409, "Complete the loan squad before selecting kits")
            previous = self.state.get("onboarding_kit_choices")
            if previous is not None and previous != [home, away]:
                raise OnboardingError(409, "Onboarding kits have already been selected")
            result = {
                "homeKitId": self._select_cosmetic(home, choices["homeItemDataList"], "activeHomeKit"),
                "awayKitId": self._select_cosmetic(away, choices["awayItemDataList"], "activeAwayKit"),
            }
            self._set("onboarding_kit_choices", [home, away])
            self._progress("onboarding", {1: home, 2: away})
            return result

    def select_badge(self, rid):
        integer(rid, "badge", 1)
        with self.state.lock, self.state.conn:
            self._require_club()
            if not self.state.get("onboarding_kit_choices"):
                raise OnboardingError(409, "Select the kit pair before the badge")
            previous = self.state.get("onboarding_badge_choice")
            if previous is not None and previous != rid:
                raise OnboardingError(409, "Onboarding badge has already been selected")
            iid = self._select_cosmetic(rid, self.cosmetic_choices("badges")["badgeItemDataList"], "activeBadge")
            self._set("onboarding_badge_choice", rid)
            if previous is None:
                self._progress("onboarding", {0: 1, 3: rid})
            return {"badgeId": iid}

    def active_items(self):
        active = self.state.get("onboarding_active_items", {})
        return [item for iid in active.values() if (item := self.state.get_item(iid)) is not None]
