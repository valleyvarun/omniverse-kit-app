# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

# Expose the extension entry point to Kit.
from collections.abc import Callable as Callable
from pathlib import Path as Path

import omni as omni
import omni.ui as ui

from .apps import AppsWindow as AppsWindow
from .extension import (
    CLICKABLE_STYLE as CLICKABLE_STYLE,
)
from .extension import (
    GRID_HEIGHT as GRID_HEIGHT,
)
from .extension import (
    GRID_WIDTH as GRID_WIDTH,
)
from .extension import (
    IMAGE_SIZE as IMAGE_SIZE,
)
from .extension import (
    ITEM_HEIGHT as ITEM_HEIGHT,
)
from .extension import (
    ITEM_WIDTH as ITEM_WIDTH,
)
from .extension import (
    LOGOS_PATH as LOGOS_PATH,
)
from .extension import (
    MyExtension as MyExtension,
)

# Public names retained from the original package exports.
__all__ = [
    "CLICKABLE_STYLE",
    "GRID_HEIGHT",
    "GRID_WIDTH",
    "IMAGE_SIZE",
    "ITEM_HEIGHT",
    "ITEM_WIDTH",
    "LOGOS_PATH",
    "AppsWindow",
    "Callable",
    "MyExtension",
    "Path",
    "omni",
    "ui",
]
