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
import asyncio as asyncio
from pathlib import Path as Path
from typing import Any as Any
from typing import cast as cast

import omni as omni
import omni.ui as ui

from .assign_window import WindowAssigner as WindowAssigner
from .extension import (
    ACTION_EXTENSION_ID as ACTION_EXTENSION_ID,
)
from .extension import (
    BOTTOM_WINDOW_HEIGHT as BOTTOM_WINDOW_HEIGHT,
)
from .extension import (
    LAYOUT_PATH as LAYOUT_PATH,
)
from .extension import (
    LEFT_WINDOW_HEIGHT as LEFT_WINDOW_HEIGHT,
)
from .extension import (
    LEFT_WINDOW_WIDTH as LEFT_WINDOW_WIDTH,
)
from .extension import (
    MAIN_COLUMN_WIDTH as MAIN_COLUMN_WIDTH,
)
from .extension import (
    MAIN_WINDOW_HEIGHT as MAIN_WINDOW_HEIGHT,
)
from .extension import (
    RESET_LAYOUT_ACTION_ID as RESET_LAYOUT_ACTION_ID,
)
from .extension import (
    RIGHT_BOTTOM_WINDOW_HEIGHT as RIGHT_BOTTOM_WINDOW_HEIGHT,
)
from .extension import (
    RIGHT_COLUMN_WIDTH as RIGHT_COLUMN_WIDTH,
)
from .extension import (
    RIGHT_TOP_WINDOW_HEIGHT as RIGHT_TOP_WINDOW_HEIGHT,
)
from .extension import (
    SHOW_MAIN_WINDOW_ACTION_ID as SHOW_MAIN_WINDOW_ACTION_ID,
)
from .extension import (
    TABS_WINDOW_HEIGHT as TABS_WINDOW_HEIGHT,
)
from .extension import (
    TOP_WINDOW_HEIGHT as TOP_WINDOW_HEIGHT,
)
from .extension import (
    TOP_WINDOW_WIDTH as TOP_WINDOW_WIDTH,
)
from .extension import (
    WINDOW_SIZES as WINDOW_SIZES,
)
from .extension import (
    MyExtension as MyExtension,
)

# Public names retained from the original package exports.
__all__ = [
    "ACTION_EXTENSION_ID",
    "BOTTOM_WINDOW_HEIGHT",
    "LAYOUT_PATH",
    "LEFT_WINDOW_HEIGHT",
    "LEFT_WINDOW_WIDTH",
    "MAIN_COLUMN_WIDTH",
    "MAIN_WINDOW_HEIGHT",
    "RESET_LAYOUT_ACTION_ID",
    "RIGHT_BOTTOM_WINDOW_HEIGHT",
    "RIGHT_COLUMN_WIDTH",
    "RIGHT_TOP_WINDOW_HEIGHT",
    "SHOW_MAIN_WINDOW_ACTION_ID",
    "TABS_WINDOW_HEIGHT",
    "TOP_WINDOW_HEIGHT",
    "TOP_WINDOW_WIDTH",
    "WINDOW_SIZES",
    "Any",
    "MyExtension",
    "Path",
    "WindowAssigner",
    "asyncio",
    "cast",
    "omni",
    "ui",
]
