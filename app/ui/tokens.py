from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.ui.paths import ui_resources_dir

_TOKENS_PATH = ui_resources_dir() / "tokens.json"


@lru_cache(maxsize=1)
def load_tokens() -> dict[str, Any]:
    with open(_TOKENS_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_token(path: str, default: str = "") -> str:
    """Return a token value by dot path, e.g. ``colors.bg_primary``."""
    node: Any = load_tokens()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return str(node)


def get_token_int(path: str, default: int = 0) -> int:
    """Return a numeric token value, stripping CSS units like ``px``."""
    raw = get_token(path, str(default))
    try:
        return int(raw)
    except ValueError:
        digits = "".join(ch for ch in raw if ch.isdigit())
        return int(digits) if digits else default


def apply_tokens(template: str) -> str:
    """Replace ``{{colors.bg_primary}}`` placeholders with values from tokens.json."""
    result = template
    for group_name, group in load_tokens().items():
        if group_name.startswith("$") or not isinstance(group, dict):
            continue
        for key, value in group.items():
            result = result.replace(f"{{{{{group_name}.{key}}}}}", str(value))
    return result
