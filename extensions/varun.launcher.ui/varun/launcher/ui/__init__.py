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
import logging as logging
from pathlib import Path as Path

import omni as omni

from .active_context import install_global_context_shim as install_global_context_shim
from .active_context import (
    uninstall_global_context_shim as uninstall_global_context_shim,
)
from .extension import (
    EXTENSION_PATH as EXTENSION_PATH,
)
from .extension import (
    LOGGER as LOGGER,
)
from .extension import (
    MyExtension as MyExtension,
)
from .kit_panels.kit_panel_rebinder import KitPanelRebinder as KitPanelRebinder
from .layers.layers import GroupRegistry as GroupRegistry
from .layers.layers import LayerRegistry as LayerRegistry
from .layers.layers import VisibilityManager as VisibilityManager
from .layers.stage import StageLockColumn as StageLockColumn
from .startup.hotkeys import HotkeyManager as HotkeyManager
from .startup.layout_docking import LayoutDocking as LayoutDocking
from .startup.render_mode import RenderModeMenu as RenderModeMenu
from .startup.startup_defaults import StartupDefaults as StartupDefaults
from .tools.hud.hud_toggle_button import HudToggleButton as HudToggleButton
from .tools.selection.selection import LockManager as LockManager
from .tools.selection.selection import (
    SelectionDirectionFilter as SelectionDirectionFilter,
)
from .tools.selection.selection import SelectionStyle as SelectionStyle
from .tools.selection.selection import SelectionSync as SelectionSync
from .tools.selection.selection import ViewportXformFilter as ViewportXformFilter
from .tools.three_d_draw.drawing_plane import PlaneGridManager as PlaneGridManager
from .tools.tool_properties import ToolPropertiesWindow as ToolPropertiesWindow
from .windows.bottom_window import BottomWindow as BottomWindow
from .windows.left_window import LeftWindow as LeftWindow
from .windows.main_window import MainWindow as MainWindow
from .windows.right_window import ClayersWindow as ClayersWindow
from .windows.top_window import TopWindow as TopWindow

# Public names retained from the original package exports.
__all__ = [
    "EXTENSION_PATH",
    "LOGGER",
    "BottomWindow",
    "ClayersWindow",
    "GroupRegistry",
    "HotkeyManager",
    "HudToggleButton",
    "KitPanelRebinder",
    "LayerRegistry",
    "LayoutDocking",
    "LeftWindow",
    "LockManager",
    "MainWindow",
    "MyExtension",
    "Path",
    "PlaneGridManager",
    "RenderModeMenu",
    "SelectionDirectionFilter",
    "SelectionStyle",
    "SelectionSync",
    "StageLockColumn",
    "StartupDefaults",
    "ToolPropertiesWindow",
    "TopWindow",
    "ViewportXformFilter",
    "VisibilityManager",
    "asyncio",
    "install_global_context_shim",
    "logging",
    "omni",
    "uninstall_global_context_shim",
]
