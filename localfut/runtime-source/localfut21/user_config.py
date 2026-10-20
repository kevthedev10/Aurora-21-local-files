"""Per-Windows-user preferences; shared catalogs and saved clubs stay separate."""
import json
import os
from pathlib import Path


def user_config_root() -> Path:
    explicit = os.environ.get("LOCALFUT21_USER_CONFIG_ROOT")
    if explicit:
        root = Path(explicit)
        if not root.is_absolute():
            raise ValueError("LOCALFUT21_USER_CONFIG_ROOT must be an absolute path")
        return root
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / "AppData" / "Local") / "Project21" / "LocalFUT"


def read_user_config(name: str, defaults: dict) -> dict:
    path = user_config_root() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        initial = dict(defaults)
        example = Path(__file__).with_name(Path(name).stem + ".example.json")
        if example.exists():
            supplied = json.loads(example.read_text(encoding="utf-8-sig"))
            if not isinstance(supplied, dict):
                raise ValueError(f"{example} must contain a JSON object")
            initial.update(supplied)
        try:
            with path.open("x", encoding="utf-8") as output:
                json.dump(initial, output, indent=2)
        except FileExistsError:
            pass
    try:
        supplied = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(supplied, dict):
            raise ValueError("Expected a JSON object")
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {path}: {error}. The file was not overwritten.") from error
    return dict(defaults, **supplied)
