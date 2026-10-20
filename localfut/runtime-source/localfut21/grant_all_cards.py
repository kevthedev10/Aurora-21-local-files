"""Grant one copy of every player card to a stopped LocalFUT club."""

from __future__ import annotations

import json
import sys

import server


def main() -> int:
    owned = set(server.STATE.owned_player_resource_ids())
    for item in server.STATE.list_items("unassigned"):
        if str(item.get("itemType", "")).lower() == "player":
            owned.add(int(item.get("resourceId", item.get("assetId", 0)) or 0))

    resource_ids = sorted(int(resource_id) for resource_id in server.PLAYER_DB)
    pending = [resource_id for resource_id in resource_ids if resource_id not in owned]
    granted = 0
    failed: list[dict[str, object]] = []

    print(json.dumps({"status": "starting", "catalog": len(resource_ids), "pending": len(pending)}), flush=True)
    for resource_id in pending:
        try:
            server.STATE.make_player_item(resource_id, pile="club")
            granted += 1
        except Exception as error:  # Keep the bulk operation useful if one bad definition exists.
            failed.append({"resourceId": resource_id, "error": str(error)})
        if granted and granted % 250 == 0:
            print(json.dumps({"status": "progress", "granted": granted, "total": len(pending)}), flush=True)

    result = {
        "status": "complete" if not failed else "complete-with-errors",
        "catalog": len(resource_ids),
        "granted": granted,
        "alreadyOwned": len(resource_ids) - len(pending),
        "failed": len(failed),
        "failures": failed[:20],
    }
    print(json.dumps(result), flush=True)
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
