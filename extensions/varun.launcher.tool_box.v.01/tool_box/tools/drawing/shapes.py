"""Unit brush outlines shared by cursor, paint stamps, and pipe sections."""

import math
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray

from tool_box.tools.drawing.brush import BrushShape


@lru_cache(maxsize=3)
def outline(shape: BrushShape) -> NDArray[np.float64]:
    if shape == "square":
        return np.asarray([(1, 1), (-1, 1), (-1, -1), (1, -1)], dtype=np.float64)
    angles: NDArray[np.float64] = np.arange(3 if shape == "triangle" else 24, dtype=np.float64)
    angles = angles * (2.0 * math.pi / len(angles)) + math.pi * 0.5
    return np.stack((np.cos(angles), np.sin(angles)), axis=1)
