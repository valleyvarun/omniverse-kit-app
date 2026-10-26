"""Launch a Kit app with the raster-only Vulkan compatibility path.

Usage: python run_compatibility.py [--kit-sdk PATH] path/to/app.kit [Kit arguments]
The app must not independently require RTX; this extension omits its RTX
dependencies in compatibility mode. GPU/driver support remains Kit-dependent.
Kit's OpenGL and Vulkan contexts must use the same GPU. Hybrid laptops may
require selecting that GPU for Kit in the operating system's graphics settings.
"""

# Python & library imports
import argparse
import json
import subprocess
import sys
from pathlib import Path

from build_native import find_sdk

# ----------------------------------------------------------------------
# 1. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Validate launch paths and run Kit with raster compatibility settings.
def main() -> int:

    # --- 1) PARSE THE PLATFORM AND LAUNCH OPTIONS ---

    root = Path(__file__).resolve().parents[1]
    platform = {"win32": "windows-x86_64", "linux": "linux-x86_64"}.get(sys.platform)
    if platform is None:
        raise SystemExit("Supported platforms: Windows x86_64 and Linux x86_64.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit-sdk", type=Path, default=find_sdk(root, platform))
    parser.add_argument("--dry-run", action="store_true", help="Print the command without launching Kit")
    parser.add_argument("app", type=Path, help="Path to the Kit application .kit file")
    parser.add_argument("kit_args", nargs=argparse.REMAINDER, help="Additional Kit arguments")
    args = parser.parse_args()
    if args.kit_sdk is None:
        parser.error("Pass --kit-sdk pointing to the matching Kit SDK root.")

    # --- 2) VALIDATE THE KIT AND APP PATHS ---

    executable = args.kit_sdk.resolve() / ("kit.exe" if sys.platform == "win32" else "kit")
    if not executable.is_file():
        parser.error(f"Kit executable not found: {executable}")
    app = args.app.resolve()
    if not app.is_file() or app.suffix != ".kit":
        parser.error(f"Expected an existing .kit application: {app}")

    # --- 3) ASSEMBLE THE COMPATIBILITY COMMAND ---

    extension = root.parents[5]
    extra = args.kit_args[1:] if args.kit_args[:1] == ["--"] else args.kit_args
    command = [
        str(executable),
        str(app),
        "--ext-folder",
        str(extension.parent),
        *extra,
        "--enable",
        "varun.launcher.usd_viewport.v.01",
        "--vulkan",
        "--/app/launcher/forceLightViewport=true",
        "--/exts/omni.kit.renderer.core/compatibilityMode=true",
        "--/renderer/enabled=pxr",
        "--/renderer/active=pxr",
        "--/pxr/rendermode=HdStormRendererPlugin",
        "--/renderer/multiGpu/enabled=false",
        "--/renderer/multiGpu/autoEnable=false",
        "--/renderer/multiGpu/maxGpuCount=1",
        "--/app/asyncRendering=false",
        "--/app/useFabricSceneDelegate=false",
    ]

    # --- 4) PRINT OR EXECUTE THE COMMAND ---

    if args.dry_run:
        print(json.dumps(command, indent=2))
        return 0
    return subprocess.call(command, cwd=str(executable.parent))


# ----------------------------------------------------------------------
# 2. SCRIPT ENTRY POINT
# ----------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(main())
