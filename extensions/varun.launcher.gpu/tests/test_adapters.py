"""Run with standard Python; no Kit or GPU required."""

# Python & library imports
import importlib.util
import unittest
from pathlib import Path

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

spec = importlib.util.spec_from_file_location("adapters", Path(__file__).parents[1] / "varun_launcher_gpu/adapters.py")
if spec is None or spec.loader is None:
    raise ImportError("Could not load the GPU adapter module")
adapters = importlib.util.module_from_spec(spec)
# ----------------------------------------------------------------------
# 2. MODULE SETUP
# ----------------------------------------------------------------------

spec.loader.exec_module(adapters)


# ----------------------------------------------------------------------
# 3. SELECTION TESTS
# ----------------------------------------------------------------------


class SelectionTests(unittest.TestCase):
    # Check RTX eligibility with NVIDIA and hybrid adapters.
    def test_nvidia_and_hybrid(self):
        self.assertTrue(adapters.wants_rtx([(0x10DE, "NVIDIA")]))
        self.assertTrue(adapters.wants_rtx([(0x8086, "Intel"), (0x10DE, "NVIDIA")]))

    # Use the light viewport without an NVIDIA adapter.
    def test_other_vendors_and_unknown(self):
        for devices in ([], [(0x8086, "Intel")], [(0x1002, "AMD")], [(0x1234, "Unknown")]):
            self.assertFalse(adapters.wants_rtx(devices))

    # Honor forced light mode even when NVIDIA is available.
    def test_explicit_light_overrides_nvidia(self):
        self.assertFalse(adapters.wants_rtx([(0x10DE, "NVIDIA")], force_light=True))


# ----------------------------------------------------------------------
# 4. SCRIPT ENTRY POINT
# ----------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
