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
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from typing import Any as Any
from typing import cast as cast

import carb as carb
import omni as omni
import omni.ui as ui
from omni.kit.viewport.utility import (
    get_viewport_from_window_name as get_viewport_from_window_name,
)
from omni.kit.widget.prompt import PromptButtonInfo as PromptButtonInfo
from omni.kit.widget.prompt import PromptManager as PromptManager
from omni.kit.window.file_exporter import get_file_exporter as get_file_exporter
from pxr import Usd as Usd
from pxr import UsdUtils as UsdUtils

from .extension import (
    ACTIVE_TAB_STYLE as ACTIVE_TAB_STYLE,
)
from .extension import (
    ADD_VIEWPORT_TAB_ACTION_ID as ADD_VIEWPORT_TAB_ACTION_ID,
)
from .extension import (
    AUTO_FRAME_MODE as AUTO_FRAME_MODE,
)
from .extension import (
    CLOSE_BUTTON_WIDTH as CLOSE_BUTTON_WIDTH,
)
from .extension import (
    DEFAULT_LIGHT_RIG as DEFAULT_LIGHT_RIG,
)
from .extension import (
    HOME_TAB_ID as HOME_TAB_ID,
)
from .extension import (
    INACTIVE_TAB_STYLE as INACTIVE_TAB_STYLE,
)
from .extension import (
    LAYOUT_EXTENSION_ID as LAYOUT_EXTENSION_ID,
)
from .extension import (
    LIGHTING_MODE_KEY as LIGHTING_MODE_KEY,
)
from .extension import (
    SAMPLE_USD_PATH as SAMPLE_USD_PATH,
)
from .extension import (
    SHOW_MAIN_WINDOW_ACTION_ID as SHOW_MAIN_WINDOW_ACTION_ID,
)
from .extension import (
    TAB_HEIGHT as TAB_HEIGHT,
)
from .extension import (
    TAB_WIDTH as TAB_WIDTH,
)
from .extension import (
    VIEWPORT_WINDOW_NAME as VIEWPORT_WINDOW_NAME,
)
from .extension import (
    WORKSPACE_EXTENSION_ID as WORKSPACE_EXTENSION_ID,
)
from .extension import (
    MyExtension as MyExtension,
)
from .extension import (
    WorkspaceTab as WorkspaceTab,
)

# Public names retained from the original package exports.
__all__ = [
    "ACTIVE_TAB_STYLE",
    "ADD_VIEWPORT_TAB_ACTION_ID",
    "AUTO_FRAME_MODE",
    "CLOSE_BUTTON_WIDTH",
    "DEFAULT_LIGHT_RIG",
    "HOME_TAB_ID",
    "INACTIVE_TAB_STYLE",
    "LAYOUT_EXTENSION_ID",
    "LIGHTING_MODE_KEY",
    "SAMPLE_USD_PATH",
    "SHOW_MAIN_WINDOW_ACTION_ID",
    "TAB_HEIGHT",
    "TAB_WIDTH",
    "VIEWPORT_WINDOW_NAME",
    "WORKSPACE_EXTENSION_ID",
    "Any",
    "MyExtension",
    "Path",
    "PromptButtonInfo",
    "PromptManager",
    "Usd",
    "UsdUtils",
    "WorkspaceTab",
    "asyncio",
    "carb",
    "cast",
    "dataclass",
    "get_file_exporter",
    "get_viewport_from_window_name",
    "omni",
    "ui",
]
