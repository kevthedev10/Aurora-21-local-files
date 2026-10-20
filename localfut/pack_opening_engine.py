"""Deterministic local FUT pack-opening state machine.

The engine only classifies server-owned FUT item instances. It does not emit
undocumented FIFA asset IDs or pretend that native client cinematics can be
invoked over HTTP. Native pack/item fields remain the authoritative payload.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PackOpeningState(str, Enum):
    PACK_SELECTED = "PACK_SELECTED"
    PACK_PURCHASED = "PACK_PURCHASED"
    PACK_OPENING = "PACK_OPENING"
    ITEMS_GENERATED = "ITEMS_GENERATED"
    REVEAL_SEQUENCE = "REVEAL_SEQUENCE"
    WALKOUT_EVALUATION = "WALKOUT_EVALUATION"
    CLAIM_READY = "CLAIM_READY"


def reveal_tier(item: dict[str, Any]) -> str:
    """Use the established local FUT classification, without changing item data."""
    try:
        rarity = int(item.get("rareflag", 0) or 0)
    except (TypeError, ValueError):
        rarity = 0
    try:
        rating = int(item.get("rating", item.get("PlayerOverall", 0)) or 0)
    except (TypeError, ValueError):
        rating = 0
    if rating >= 86 or (rarity >= 5 and rating >= 85):
        return "WALKOUT"
    if rating >= 83:
        return "BOARD"
    if rarity == 1 and rating >= 75:
        return "RARE"
    return "COMMON"


def get_reveal_tier(rating: int, rareflag: int) -> str:
    """Classify a FUT item using the server's documented reveal thresholds."""
    return reveal_tier({"rating": rating, "rareflag": rareflag})


def calculate_pack_probabilities(cfg: dict[str, Any]) -> dict[str, float]:
    """Return configured probabilities without inventing native odds fields."""
    try:
        special = float(cfg.get("special_chance", 0) or 0) * 100.0
        size = max(1, int(cfg.get("size", 12) or 12))
        rare = max(0, min(size, int(cfg.get("rare_slots", 0) or 0))) / size * 100.0
        return {"any_special%": special, "board%": 0.0, "walkout%": 0.0,
                "configuredRareSlots%": rare}
    except (AttributeError, TypeError, ValueError):
        return {"any_special%": 0.0, "board%": 0.0, "walkout%": 0.0,
                "configuredRareSlots%": 0.0}


def create_enhanced_pack(state: Any, pack_id: int) -> dict[str, Any]:
    """Create a normal server pack while retaining reveal metadata/state."""
    try:
        pack = state.create_pack(int(pack_id))
        items = pack.get("itemData", []) if isinstance(pack, dict) else []
        pack["revealTiers"] = [reveal_tier(item) for item in items if isinstance(item, dict)]
        return pack
    except Exception:
        return {"packId": int(pack_id), "numberItems": 0, "itemData": [], "revealTiers": []}


@dataclass
class PackOpeningEngine:
    pack_id: int
    quantity: int = 0
    state: PackOpeningState = PackOpeningState.PACK_SELECTED
    items: list[dict[str, Any]] = field(default_factory=list)
    revealed: int = 0
    purchased: bool = False

    def transition(self, state: PackOpeningState) -> dict[str, Any]:
        allowed = {
            PackOpeningState.PACK_SELECTED: {PackOpeningState.PACK_PURCHASED},
            PackOpeningState.PACK_PURCHASED: {PackOpeningState.PACK_OPENING},
            PackOpeningState.PACK_OPENING: {PackOpeningState.ITEMS_GENERATED},
            PackOpeningState.ITEMS_GENERATED: {PackOpeningState.REVEAL_SEQUENCE},
            PackOpeningState.REVEAL_SEQUENCE: {PackOpeningState.WALKOUT_EVALUATION, PackOpeningState.CLAIM_READY},
            PackOpeningState.WALKOUT_EVALUATION: {PackOpeningState.REVEAL_SEQUENCE, PackOpeningState.CLAIM_READY},
            PackOpeningState.CLAIM_READY: set(),
        }
        if state not in allowed[self.state]:
            raise ValueError(f"invalid pack opening transition {self.state} -> {state}")
        self.state = state
        return self.snapshot()

    def purchase(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if self.state == PackOpeningState.PACK_SELECTED:
            self.transition(PackOpeningState.PACK_PURCHASED)
        self.transition(PackOpeningState.PACK_OPENING)
        self.items = [dict(item) for item in items]
        self.quantity = len(self.items)
        self.purchased = True
        self.transition(PackOpeningState.ITEMS_GENERATED)
        self.transition(PackOpeningState.REVEAL_SEQUENCE)
        return self.snapshot()

    def reveal_next(self) -> dict[str, Any]:
        if self.state not in (PackOpeningState.REVEAL_SEQUENCE, PackOpeningState.WALKOUT_EVALUATION):
            raise ValueError(f"cannot reveal in state {self.state}")
        if self.revealed >= len(self.items):
            self.state = PackOpeningState.CLAIM_READY
            return self.snapshot()
        item = self.items[self.revealed]
        tier = reveal_tier(item)
        self.revealed += 1
        self.state = PackOpeningState.CLAIM_READY if self.revealed >= len(self.items) else PackOpeningState.REVEAL_SEQUENCE
        return {"state": self.state.value, "index": self.revealed - 1, "revealTier": tier,
                "itemId": item.get("id"), "resourceId": item.get("resourceId"),
                "localPresentation": item.get("localPresentation", {})}

    def snapshot(self) -> dict[str, Any]:
        return {"packId": self.pack_id, "state": self.state.value, "quantity": self.quantity,
                "revealed": self.revealed, "claimReady": self.state == PackOpeningState.CLAIM_READY,
                "revealTiers": [reveal_tier(item) for item in self.items]}
