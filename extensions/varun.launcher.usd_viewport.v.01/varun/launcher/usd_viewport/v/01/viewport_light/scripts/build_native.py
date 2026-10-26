"""Build and test the native controller without modifying the host application."""

# Python & library imports
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------
# 1. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Find CMake on PATH or in a Visual Studio installation.
def find_cmake() -> str:
    found = shutil.which("cmake")
    if found:
        return found
    if sys.platform == "win32":
        visual_studio = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Microsoft Visual Studio"
        # Editions such as Community and BuildTools have no fixed installation name.
        matches = sorted(visual_studio.glob("*/*/Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe"))
        if matches:
            return str(matches[-1])
    raise SystemExit("CMake 3.20 or later is required; install CMake or Visual Studio's C++ CMake tools.")


# Search parent build folders for a Kit SDK.
def find_sdk(root: Path, platform: str) -> Path | None:
    for parent in root.parents:
        candidate = parent / "_build" / platform / "release" / "kit"
        if (candidate / "dev/include/carb/PluginUtils.h").is_file():
            return candidate
    return None


# Configure, build, and test the native plugin against the selected Kit SDK.
def main() -> None:

    # --- 1) RESOLVE THE PLATFORM AND SDK ---

    root = Path(__file__).resolve().parents[1]
    platform = {"win32": "windows-x86_64", "linux": "linux-x86_64"}.get(sys.platform)
    if platform is None:
        raise SystemExit("Supported platforms: Windows x86_64 and Linux x86_64.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kit-sdk", type=Path, default=find_sdk(root, platform), help="Kit root containing dev/include"
    )
    parser.add_argument("--config", choices=("Release", "Debug", "RelWithDebInfo"), default="Release")
    args = parser.parse_args()
    if args.kit_sdk is None:
        parser.error("Pass --kit-sdk pointing to the matching Kit SDK root.")

    # --- 2) PREPARE THE NATIVE BUILD DIRECTORY ---

    cmake = find_cmake()
    build = root / "_build" / "native"
    temporary = build / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, TMP=str(temporary), TEMP=str(temporary))

    # --- 3) CONFIGURE AND BUILD THE PLUGIN ---

    configure = [
        cmake,
        "-S",
        str(root),
        "-B",
        str(build),
        f"-DKIT_SDK_ROOT={args.kit_sdk.resolve()}",
        f"-DCMAKE_BUILD_TYPE={args.config}",
        "-DBUILD_TESTING=ON",
    ]
    if sys.platform == "win32":
        configure += ["-G", "Visual Studio 17 2022", "-A", "x64"]
    subprocess.run(configure, check=True, env=environment)
    subprocess.run([cmake, "--build", str(build), "--config", args.config, "--parallel"], check=True, env=environment)

    # --- 4) RUN THE NATIVE TESTS ---

    ctest = Path(cmake).with_name("ctest.exe" if sys.platform == "win32" else "ctest")
    subprocess.run(
        [str(ctest), "--test-dir", str(build), "-C", args.config, "--output-on-failure"], check=True, env=environment
    )
    print(f"Native plugin ready: {root / 'bin' / platform}")


# ----------------------------------------------------------------------
# 2. SCRIPT ENTRY POINT
# ----------------------------------------------------------------------

if __name__ == "__main__":
    main()
