# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

# NOTE:
#   omni.kit.test - std python's unittest module with additional wrapping to add
#   suport for async/await tests
#   For most things refer to unittest docs:
#   https://docs.python.org/3/library/unittest.html
# Import extension python module we are testing with absolute import path,
# as if we are external user (other extension)
from importlib import import_module

import omni.kit.test

# Inspect the extension window registered with Kit.
import omni.ui as ui

# ----------------------------------------------------------------------
# 1. TEST
# ----------------------------------------------------------------------


# Having a test class dervived from omni.kit.test.AsyncTestCase declared on the
# root of module will make it auto-discoverable by omni.kit.test
class Test(omni.kit.test.AsyncTestCase):
    # Before running each test
    async def setUp(self):
        pass

    # After running each test
    async def tearDown(self):
        pass

    # Check the extension entry point exposed to Kit.
    async def test_extension_entry_point(self):
        module = import_module("varun.launcher.home.v.01")
        self.assertTrue(callable(module.MyExtension.on_startup))
        self.assertTrue(callable(module.MyExtension.on_shutdown))

    # Check that startup registered the expected window.
    async def test_extension_window(self):
        self.assertIsNotNone(ui.Workspace.get_window("home"))
