# New Launcher graphics startup

`varun.new_launcher.kit` and `3d-drawing.kit` use this extension. Its startup order
(-200000) precedes Kit's renderer initialization (-100000). It has no GUI or
renderer dependencies and cannot be hot reloaded.

`varun_launcher_gpu/adapters.py` enumerates functioning Windows display adapters
using Plug and Play through a hidden PowerShell/CIM query, or Linux DRM card
vendor IDs through sysfs. It does not load graphics drivers into Kit to detect
them. A failed or empty detection uses the raster compatibility path.

`varun_launcher_gpu/__init__.py` selects compatibility/ray-tracing capabilities
before Kit creates the device. NVIDIA vendor ID 0x10DE enables RTX eligibility;
other vendors use compatibility mode. The viewport's `gpu_policy.py` checks the
Kit's graphics-device enumeration before loading the optional RTX extensions.
Viewport Light remains the default in both cases. RTX still requires an NVIDIA
GPU/driver supported by Kit's RTX renderer; vendor detection is not certification
of older NVIDIA hardware.

New Launcher bundles both Windows foundation shader caches and initially selects
DirectX 12 on Windows so cached dependency filters match the NVIDIA backend.
The launcher defers optional RTX packages through startup exclusions; the GPU
bootstrap releases those exclusions only for eligible NVIDIA devices. Before device
creation the bootstrap selects DirectX 12 for NVIDIA on Windows, and Vulkan for
Linux and raster compatibility mode. On NVIDIA the viewport enables the
optional RTX extensions and adds RTX choices; without NVIDIA it leaves only
Storm/Viewport Light. This is evaluated every launch, not saved as a machine
capability in persistent preferences.

Use `--/app/launcher/forceLightViewport=true` to opt into Light-only mode even on
NVIDIA. This also supports testing the fallback path. The separate compatibility
launcher supplies that flag automatically. This is not a simulated Intel driver
test: actual Intel/AMD rendering still needs compatible Vulkan/OpenGL drivers on
the same GPU, and target-hardware testing.

Files: `config/extension.toml` registers the early startup module;
`premake5.lua` includes it in normal extension builds; `adapters.py` detects
hardware; `__init__.py` applies startup settings. `tests/test_adapters.py` checks
hardware-selection decisions without requiring Kit.

Build/package the New Launcher with this extension included. Also bundle the
optional `omni.hydra.rtx`, `omni.kit.viewport.rtx`, and their platform-specific
dependencies (including the matching DirectX 12/Vulkan shader caches) for offline NVIDIA installations.
They may otherwise be obtained from the configured Kit registry when first
enabled. Do not ship just the raster dependency closure if offline RTX is needed.
