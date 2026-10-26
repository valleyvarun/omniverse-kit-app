"""Read and atomically save the drawing app's sidebar widths."""

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SidebarWidths:
    left: float
    right: float


def load_widths(path: Path) -> SidebarWidths | None:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        left: Any = data.get("left")
        right: Any = data.get("right")
        value: Any
        for value in (left, right):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            if not math.isfinite(value) or value < 80:
                return None
        return SidebarWidths(float(left), float(right))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, OverflowError):
        LOGGER.warning("Could not read sidebar widths from %s; using defaults", path, exc_info=True)
        return None


def save_widths(path: Path, widths: SidebarWidths) -> bool:
    temporary: Path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps({"left": widths.left, "right": widths.right}, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
        return True
    except OSError:
        LOGGER.warning("Could not save sidebar widths to %s", path, exc_info=True)
        return False
