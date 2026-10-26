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
from typing import Any as Any
from typing import cast as cast

import carb as carb
import omni as omni
import omni.kit.stage_templates as stage_templates
import omni.ui as ui
from omni.kit.viewport.window import ViewportWindow as ViewportWindow
from pxr import UsdUtils as UsdUtils

from .extension import (
    LIGHT_RIG as LIGHT_RIG,
)
from .extension import (
    LIGHTING_MODE_KEY as LIGHTING_MODE_KEY,
)
from .extension import (
    MyExtension as MyExtension,
)
from .hud import HudToggleButton as HudToggleButton
from .viewport_light import ViewportLight as ViewportLight

# Public names retained from the original package exports.
__all__ = [
    "LIGHTING_MODE_KEY",
    "LIGHT_RIG",
    "Any",
    "HudToggleButton",
    "MyExtension",
    "UsdUtils",
    "ViewportLight",
    "ViewportWindow",
    "asyncio",
    "carb",
    "cast",
    "omni",
    "stage_templates",
    "ui",
]
