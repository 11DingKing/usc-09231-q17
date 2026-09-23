"""Where to click to select each scene, and what scene a point selects."""
from __future__ import annotations

from tests.goldens import scenes

SCREEN = scenes.SIZE
KEYS = tuple(sorted(scenes.SCENES))
COLUMNS = 3
ROWS = 2


def _cell(key: str) -> tuple[int, int]:
    index = KEYS.index(key)
    return index % COLUMNS, index // COLUMNS


def click_target(key: str) -> tuple[int, int]:
    """The centre of the cell that selects ``key``."""
    column, row = _cell(key)
    width, height = SCREEN
    return (
        (2 * column + 1) * width // (2 * COLUMNS),
        (2 * row + 1) * height // (2 * ROWS),
    )


def scene_at(x: int, y: int) -> "str | None":
    """The scene a click at ``(x, y)`` selects, or None off any live cell."""
    width, height = SCREEN
    if not (0 <= x < width and 0 <= y < height):
        return None
    column = min(x * COLUMNS // width, COLUMNS - 1)
    row = min(y * ROWS // height, ROWS - 1)
    index = row * COLUMNS + column
    return KEYS[index] if index < len(KEYS) else None
