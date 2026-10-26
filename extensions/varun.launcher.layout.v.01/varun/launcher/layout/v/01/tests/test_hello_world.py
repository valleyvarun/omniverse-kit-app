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
from importlib import import_module

# Omniverse & USD imports
import omni.kit.test

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

    # Check that the layout extension entry point is available.
    async def test_layout_module(self):
        module = import_module("varun.launcher.layout.v.01")
        self.assertEqual(module.ACTION_EXTENSION_ID, "varun.launcher.layout.v.01")

    # Check that layout assignments remove saved screen positions.
    async def test_layout_coordinates_are_removed(self):
        module = import_module("varun.launcher.layout.v.01.assign_window")
        assigner = module.WindowAssigner.__new__(module.WindowAssigner)
        assigner.assignments = {"top_window": "toolbar"}
        assigner.main_window_name = "home"
        layout = {
            "position_x": 0.0,
            "position_y": 36.0,
            "children": [
                {
                    "title": "top_window",
                    "position_x": 10.0,
                    "position_y": 50.0,
                    "height": 75.0,
                }
            ],
        }

        assigner.apply_layout_assignments(layout)

        self.assertNotIn("position_x", layout)
        self.assertNotIn("position_y", layout)
        child = layout["children"][0]
        self.assertNotIn("position_x", child)
        self.assertNotIn("position_y", child)
        self.assertEqual(child["title"], "toolbar")
        self.assertEqual(child["height"], 75.0)
