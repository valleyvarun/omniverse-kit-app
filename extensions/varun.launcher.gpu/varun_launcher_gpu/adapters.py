"""Read physical graphics adapters without initializing Kit's graphics device."""

# Python & library imports
import json
import re
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------
# 1. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Read working Windows adapters and decode their PCI vendor IDs.
def _windows_adapters():
    # Query Plug and Play out of process. Loading DXGI here can select an
    # integrated GPU's driver before Kit creates its OpenGL interop context.
    command = "Get-CimInstance Win32_VideoController | Where-Object {$_.ConfigManagerErrorCode -eq 0} | Select-Object Name,PNPDeviceID | ConvertTo-Json -Compress"
    output = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout
    # Normalize PowerShell's single-object and array JSON outputs.
    rows = json.loads(output) if output.strip() else []
    if isinstance(rows, dict):
        rows = [rows]
    # Read the PCI vendor code from each device ID.
    result = []
    for row in rows:
        vendor = re.search(r"VEN_([0-9A-Fa-f]{4})", row.get("PNPDeviceID") or "")
        if vendor:
            result.append((int(vendor[1], 16), row["Name"]))
    return result


# List physical display adapters for the current platform.
def graphics_adapters():
    if sys.platform == "win32":
        return _windows_adapters()
    if sys.platform == "linux":
        # DRM card nodes represent display devices with a bound graphics driver.
        result = []
        for card in Path("/sys/class/drm").glob("card[0-9]*"):
            if not card.name[4:].isdigit():
                continue
            vendor = card / "device/vendor"
            if vendor.is_file():
                result.append((int(vendor.read_text().strip(), 16), card.name))
        return result
    return []


# Enable RTX only for NVIDIA adapters unless light mode is forced.
def wants_rtx(adapters, force_light=False):
    return not force_light and any(vendor == 0x10DE for vendor, _ in adapters)
