# Viewport Light architecture

Viewport Light is the default solid USD viewport in `varun.launcher.usd_viewport.v.01`, with **Light · Grey Studio** and **Dark** appearances. New Launcher automatically adds RTX choices on eligible NVIDIA hardware and uses only Viewport Light otherwise. Viewport implementation and build output live inside the existing `usd_viewport` extension; the separate `varun.launcher.gpu` extension selects graphics capabilities before Kit initializes its renderer.

The geometry renderer is the C++ OpenUSD **Storm** rasterizer already supplied by Kit's `omni.hydra.pxr` extension. The new C++ Carbonite plugin owns the appearance policy and temporary renderer settings. Small Python adapters connect that policy to Kit's existing viewport window, menu, GPU texture presentation, and USD stage. This deliberately avoids a second scene graph, a custom input system, or a copy of Blender's drawing engine.

This is a lightweight raster viewport, not a new ray-tracing engine. It reuses a mature native renderer rather than introducing a new render delegate. The menu's `Viewport Light` label maps to the existing `HdStormRendererPlugin` identifier.

## Folder hierarchy

```text
usd_viewport/v/01/
├── extension.py                    Existing extension startup/shutdown integration
└── viewport_light/
    ├── ARCHITECTURE.md              This document
    ├── .gitignore                  Excludes generated builds, binaries and caches
    ├── CMakeLists.txt               Native library, plugin and test build
    ├── __init__.py                  Public Python entry point
    ├── settings.py                 Shared setting paths and renderer identifiers
    ├── controller.py               Viewport/window lifecycle adapter
    ├── gpu_policy.py               New Launcher optional RTX activation
    ├── menu.py                     Renderer name and appearance menu adapter
    ├── display.py                  GPU viewport presentation and backdrop
    ├── depth_background.py         Temporary depth support for translucent strokes
    ├── grid.py                     Camera-linked CAD ground grid and axes
    ├── studio.py                   Temporary USD studio lighting layer
    ├── native/
    │   ├── include/viewport_light/
    │   │   ├── Controller.h        Native lifecycle and settings contract
    │   │   ├── SettingsScope.h     Scoped renderer-setting ownership
    │   │   └── Theme.h             Theme data and theme lookup declaration
    │   ├── src/
    │   │   ├── Controller.cpp      Settings subscriptions and theme application
    │   │   ├── SettingsScope.cpp   Snapshot, activate and restore renderer flags
    │   │   ├── Theme.cpp           Original light/dark palette definitions
    │   │   └── Plugin.cpp          Carbonite/Kit native extension entry point
    │   └── tests/
    │       └── SettingsScopeTests.cpp  Native state and lifecycle regression tests
    ├── scripts/
    │   ├── build_native.py         Configure, compile and run native tests
    │   └── run_compatibility.py    Launch Kit with raster-only startup settings
    ├── tests/
    │   ├── smoke.kit              Isolated Kit integration-test application
    │   ├── smoke.py               Stage, theme and renderer-switch smoke test
    │   ├── compatibility.py       Raster-only GPU and frame-production test
    │   ├── gpu_selection.py       New Launcher GPU policy and renderer-switch test
    │   ├── interactions.kit       Isolated application with UI input-test helpers
    │   └── interactions.py        Actual menu, creation and navigation input tests
    ├── bin/                       Generated loadable native plugin
    │   ├── windows-x86_64/
    │   │   └── viewport_light.plugin.dll
    │   └── linux-x86_64/
    │       └── libviewport_light.plugin.so
    └── _build/                    Generated build files and validation output
        ├── native/                CMake project, object files, CTest output
        ├── validation/            Integration-test report and screen captures
        ├── interactions/          Input-test report, captures and portable runtime
        └── compatibility_gpu_*/   Per-device raster-only reports and captures
```

Only the current platform's binary is produced when building. Generated Python `__pycache__` directories and build logs are disposable runtime artifacts, not source modules.

## Responsibilities of each file

### Python integration

| File | Responsibility |
| --- | --- |
| `__init__.py` | Exports `ViewportLight` for the parent extension. |
| `settings.py` | Defines the extension setting namespace, persistent theme key, Storm plugin ID and menu label. Keep these paths consistent with `native/include/viewport_light/Controller.h`. |
| `controller.py` | Attaches to the existing `ViewportWindow`; observes render-setting and stage events; coalesces appearance updates to the next Kit update; activates native policy; installs/removes the display delegate and studio layer; cancels subscriptions/tasks on shutdown. Cleans up the appearance on failed activation/native readiness, and notifies the parent extension to restore its saved/default Grey Studio lighting on an RTX transition. |
| `menu.py` | Renames the Storm entry to `Viewport Light`, including the collapsed renderer-menu title, and adds the two appearance choices. Uses the renderer menu's custom item factory. A small private-API adapter accesses Kit 109's renderer-name list because the SDK does not expose a public naming registry. Restores names and unregisters its adapter on teardown. |
| `display.py` | Extends Kit's `ViewportDisplayDelegate` with a solid studio backdrop and grid below the normal GPU texture output. Updates the grid from the current camera before presenting each texture. Keeps Kit's texture presentation and input handling; performs no per-frame CPU image readback. |
| `depth_background.py` | Owns two invisible triangles in a removable session sublayer. A small GPU shader writes depth just inside far clip and discards color, allowing Storm transparency through Kit's final depth blit. Empty bounds exclude the helper from framing; picking is disabled. Camera movement requires no geometry updates. |
| `grid.py` | Creates native `omni.ui.scene` lines on the stage's Y-up or Z-up ground plane. Uses cached decade spacing and recentering so camera motion does not recreate lines each frame. Draws subtle major/minor lines with fading edges and colored axes; respects the existing grid visibility setting for the viewport ID. Removes its SceneView on teardown. |
| `studio.py` | Builds an anonymous USD layer containing three neutral key/fill/rim distant lights, adapts them to the stage up axis, and hides scene lights through temporary visibility opinions. Observes USD structural/metadata change notices to handle new/deleted lights and stage-up-axis changes. Coalesces refreshes onto the next Kit update and ignores its own edits. Inserts only this layer into the session layer, and removes it and its notice subscription when leaving Viewport Light or closing a stage. |

### Native C++ policy

| File | Responsibility |
| --- | --- |
| `native/include/viewport_light/Controller.h` | Declares the policy controller, settings paths, subscription handles and synchronization guards. |
| `native/src/Controller.cpp` | Starts/stops settings subscriptions, validates the theme, publishes background/studio values, and activates/restores shared viewport settings. Uses a recursion guard for synchronous settings callbacks and unregisters callbacks before releasing state. There is no frame-update callback. |
| `native/include/viewport_light/SettingsScope.h` | Defines scoped ownership of settings snapshots using RAII. Each snapshot retains the original value, type and existence. |
| `native/src/SettingsScope.cpp` | Captures nine shared settings before mutation; enables raster material shading, alpha compositing and selection policy; restores snapshots in reverse order when the viewport deactivates. For PXR presentation, `backgroundZeroAlpha/enabled=true` and `backgroundZeroAlpha/outputAlphaInComposite=false` preserve the clear pixels needed by the UI backdrop. Changes the two Kit palette entries used for hover/selection to warm orange while preserving the remainder of the palette. Repeated activation does not overwrite the original snapshot. Settings that were originally absent are removed again. |
| `native/include/viewport_light/Theme.h` | Declares the compact `Theme` structure and lookup function. |
| `native/src/Theme.cpp` | Defines the neutral Grey Studio and dark studio backgrounds. Unknown theme values normalize to light. No texture assets are required. |
| `native/src/Plugin.cpp` | Registers the native Carbonite plugin, acquires settings/dictionary interfaces, and owns the controller for the extension lifetime. |
| `native/tests/SettingsScopeTests.cpp` | Tests snapshot/restoration of types and missing values, repeated activation, safe failure before settings mutation, theme changes, callback recursion/failure and lifecycle cleanup using a small settings test double. |

### Build and validation

| File/directory | Responsibility |
| --- | --- |
| `.gitignore` | Excludes `_build/`, `bin/`, bytecode and Python caches from source control. Distribute the generated native binary through the extension packaging process after building. |
| `CMakeLists.txt` | Builds a C++17 static policy library, the loadable Carbonite module and the native test executable. Links the platform thread library, validates the matching Kit SDK include directory and limits targets to Windows/Linux x86_64. Uses the static MSVC runtime on Windows to isolate compiler/runtime version differences from the host. |
| `scripts/build_native.py` | Finds CMake, including Visual Studio's bundled CMake on Windows; locates the repository's Kit SDK or accepts an explicit one; configures and builds in `_build/native`; runs CTest. |
| `scripts/run_compatibility.py` | Starts an existing `.kit` app with Vulkan, Kit compatibility mode, PXR/Storm only, and single-GPU synchronous rendering. Adds this extension's search folder and enables it. Accepts an explicit Kit SDK, additional Kit arguments and `--dry-run`. Does not rewrite the application's files or install graphics drivers. |
| `tests/smoke.kit` | Starts an isolated test app with this extension and capture support. Disables persistent-user-config loading/saving so the validation process does not overwrite the normal app's preferences. |
| `tests/smoke.py` | Creates simple USD geometry and a scene light; checks native readiness, default renderer, menu choices, both themes, RTX/Storm round-trip, camera/selection preservation and stage replacement. Asserts that theme changes preserve the complete root layer and RTX round-trip preserves the authored `/World` asset. Kit's RTX bridge may add its own `/Render` settings. Writes JSON results and captures into `_build/validation`. It runs inside Kit, not ordinary Python. |
| `tests/compatibility.py` | Runs through the compatibility launcher and checks that RTX is not loaded, only PXR is enabled, the native policy is ready, both themes capture and Storm produces advancing frames. Saves results to `_build/compatibility_gpu_<activeGpu>/`. |
| `tests/gpu_selection.py` | Runs inside New Launcher and checks GPU policy, renderer menu entries, Light frames, and NVIDIA RTX frames followed by switching back to Light. The forced-Light run checks that RTX is absent. |
| `tests/interactions.kit` | Starts a separate isolated app with `omni.kit.ui_test`, creation commands and capture support. Disables registry access and persistent user configuration, and provides brief UI timing delays for submenu interaction. |
| `tests/interactions.py` | Clicks the rendered renderer menu, verifies Viewport Light and RTX Real-Time 2.0 choices, clicks both appearance presets, opens the viewport's right-click Create menu and creates a sphere mesh. Sends right-drag/WASDQE input, checks nonzero camera translations and opposing movement for W/S, A/D and Q/E, and verifies the same viewport window remains. Saves reports/captures to `_build/interactions`, separate from smoke outputs. |
| `bin/<platform>/` | Contains the binary loaded by the extension manifest's `[[native.plugin]]` entry. Build this before enabling the extension. |
| `_build/` | Contains generated compilation and validation artifacts. Do not use these as source inputs or commit generated machine-specific project files. |

## Runtime flow

1. The extension manifest loads `omni.hydra.pxr`, the existing viewport dependencies, and the native plugin. Defaults select `pxr` / `HdStormRendererPlugin` while retaining RTX in the renderer choices. Under compatibility mode, this extension's RTX dependencies become optional and the renderer list is restricted to PXR before startup.
2. The native plugin publishes readiness and subscribes to the active/theme settings. The parent `extension.py` creates its normal viewport window and passes it to `ViewportLight`.
3. The Python adapter recognizes the active Storm viewport, marks the policy active, adds its backdrop delegate, and attaches the temporary studio layer.
4. Native code snapshots and applies the small set of shared presentation flags and orange selection palette. The existing USD-to-Hydra bridge and Storm rasterizer render geometry; Kit presents the resulting GPU texture above the background and cached CAD grid.
5. A theme change updates native palette values and the backdrop. It does not rebuild geometry or reload the USD stage.
6. Selecting an RTX mode deactivates the policy, restores the original display delegate and settings, and removes the temporary studio layer. The parent extension then reapplies its saved/default Grey Studio lighting. The existing window, camera and selection remain in place.
7. USD structural/metadata change notices schedule one deferred studio refresh. New scene lights receive temporary visibility overrides, stale light overrides are removed, and stage-up-axis changes update the rig orientation. Camera motion and ordinary geometry attribute changes do not cause this scan.
8. Stage replacement and extension shutdown detach temporary state and release event subscriptions.

```text
Existing ViewportWindow + keyboard/mouse/context menu
                 │
                 ├── Python lifecycle/menu adapter
                 │          │
                 │          ├── Carbonite settings ↔ native C++ policy
                 │          └── anonymous USD studio layer
                 │
                 └── USD → Kit Hydra bridge → C++ Storm → GPU texture
                                                          │
                                           existing display delegate
                                           + CAD grid + studio background
```

## Controls and data ownership

Viewport Light does not register replacement keyboard or pointer handlers. WASDQE navigation, orbit/pan/dolly, picking, manipulators, shortcuts and the right-click Create menu continue to come from the existing viewport and its enabled extensions. Renderer changes do not recreate the viewport window. This preserves the host's controls instead of approximating them with a second implementation.

The studio rig does not author into the root layer or the user's edit target. Its anonymous session sublayer is removed by identity, preserving other session layers and edits. Ordinary root-layer saving does not write this temporary rig. A deliberate export of the entire composed/flattened stage while the rig is active can include session opinions, so disable Viewport Light before exporting a flattened presentation-independent stage.

Switching to RTX can cause Kit's own bridge to author `/Render` products/settings into the root layer. The isolation guarantee applies to this extension's studio layer and authored assets; it is not a guarantee that the host RTX engine will never change the root layer.

The policy touches a small set of application-level renderer settings, which are shared across viewports. It is designed for this extension's single existing viewport window; independent per-window appearance ownership in multiple simultaneous windows would require a shared ownership manager. The USD lighting layer also affects views sharing the same stage while it is attached.

## Appearance and performance

The light preset provides a neutral grey backdrop and simple matte studio illumination; dark uses a restrained charcoal backdrop with the same rig. Studio lights disable their specular contribution. The grid is a lightweight camera-linked native-line layer below the scene texture; existing Kit orientation widgets and manipulators remain. Storm evaluates authored raster materials, including the drawing tools' lightweight paint coverage shader. This does not enable RTX or modify authored materials. The studio lights are world-aligned with stage-up-axis adaptation, rather than following the camera.

Hover and selection use a warm orange palette. The shipped PXR compositor can tint an entire selected object, rather than drawing Blender's outline-only selection. OpenUSD's implicit primitive tessellation also remains in effect: for example, its default implicit cylinder can appear faceted with ten radial segments. For smoother curved silhouettes use appropriately tessellated meshes. This integration does not replace those native renderer behaviors and is not a complete Blender Workbench emulation.

The implementation adds no ray-traced shadows, HDR image loading, CPU frame copying or Python geometry traversal per frame. The grid reads a small camera matrix and visibility/up-axis state on display updates; it rebuilds its lines only when spacing, center, stage orientation or theme changes. The stage-light scan occurs on rig attachment and after coalesced structural/metadata changes. It traverses the stage, so applications creating/deleting large numbers of prims continuously should measure this cost on representative scenes. Rendering, GPU buffer management and USD scene synchronization remain the responsibilities of the supplied native Hydra/Storm stack. No frame-rate or large-scene performance guarantee is implied without measurements on the target scene and hardware.

The background and grid belong to the viewport UI, not the raw renderer output. Swapchain/window captures include them; a direct renderer-texture capture such as `capture_viewport_to_file` can have a black/transparent clear background instead. Use the full viewport/window capture when the studio backdrop and grid are part of the intended image. Because the guide is underneath the scene texture, it is a viewport guide rather than a depth-tested ground object in the USD stage.

## Build

Requirements: Python 3.10+, CMake 3.20+, a C++17 compiler, and the **matching Kit SDK** containing `dev/include/carb/PluginUtils.h`. Windows builds use Visual Studio 2022 with the C++ tools and x64 support. Linux builds use the compiler and system dependencies compatible with the chosen Linux Kit distribution. No standalone OpenUSD SDK, Blender build, CUDA SDK or new GPU-rendering library is linked by this plugin.

The native build is an explicit step: neither Python module import nor extension startup compiles C++. This extension-local CMake project is not automatically added to the repository's top-level build. Run the helper below when native sources change, and package its resulting platform binary with the extension.

On Windows the native library/module use `/MT` (`/MTd` in Debug). Kit's bundled MSVC runtime can predate the local compiler; a newer mutex implementation cannot safely assume the bundled older runtime provides the same initialization behavior. Keeping the C++ runtime private avoids replacing Kit's runtime DLLs. No STL objects cross the plugin ABI, and Carbonite-owned buffers/snapshots are freed through the matching Carbonite API.

From this `viewport_light` directory:

```powershell
python scripts/build_native.py --kit-sdk C:/kit-app-my-launcher/_build/windows-x86_64/release/kit
```

The SDK argument can be omitted when the helper can find the repository's `_build/<platform>/release/kit` directory. The helper runs configure, compile and CTest, and places the plugin in `bin/windows-x86_64/`.

On Linux, from this directory:

```bash
python3 scripts/build_native.py --kit-sdk /path/to/matching/kit
```

This produces `bin/linux-x86_64/libviewport_light.plugin.so`. Include the correct platform binary when packaging the extension. Build and test Windows and Linux separately; a Windows DLL cannot be reused on Linux.

Equivalent direct CMake commands, useful in CI:

```bash
cmake -S . -B _build/native -DKIT_SDK_ROOT=/path/to/matching/kit -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON
cmake --build _build/native --config Release --parallel
ctest --test-dir _build/native -C Release --output-on-failure
```

For Windows direct configuration, add `-G "Visual Studio 17 2022" -A x64`. Stop the app before replacing a native plugin binary; this plugin does not support native hot reload.

The integration test must be launched using the same Kit distribution and extension search paths as the application. Supply `tests/smoke.kit` as the app and `--exec <absolute-path-to-tests/smoke.py>`. Inspect `_build/validation/report.json` and the captures; merely compiling Python does not validate GPU presentation or mouse/keyboard interaction.

## Raster-only compatibility launch

New Launcher now selects its GPU mode automatically; the separate script below
is only needed to force raster-only mode or launch another host configuration.
`source/apps/varun.new_launcher.kit` depends on `varun.launcher.gpu`, a small
extension with no UI/renderer dependencies that runs before Kit renderer init.
It queries Windows Plug and Play display adapters or Linux DRM vendor IDs and
sets graphics compatibility/ray-tracing capabilities before device creation.
The app initially requires only PXR. NVIDIA Windows startup uses DirectX 12;
Linux and raster compatibility startup use Vulkan. After device creation,
`gpu_policy.py` checks Kit's GPU enumeration and loads optional RTX
extensions on eligible NVIDIA hardware. Without NVIDIA, only Viewport Light
is enabled. Viewport Light remains the default on NVIDIA too. Detection or
optional extension activation failures keep the raster renderer selected.

The startup decision is recomputed each launch. An explicit
`--/app/launcher/forceLightViewport=true` overrides NVIDIA detection; the
compatibility script supplies that switch. The bootstrap extension's README
documents its files, detection and offline packaging requirements. Offline
distributions must bundle optional RTX extensions and their DirectX 12/Vulkan shader
caches as well as the raster dependencies. Other launcher apps do not depend
on this bootstrap and retain their existing startup policy.

`tests/gpu_selection.py` runs inside New Launcher to check enabled extensions,
renderer menu entries, advancing Storm frames, and (on NVIDIA) advancing RTX
frames and a switch back to Storm. Run with `forceLightViewport` to check the
raster-only path. Reports are `_build/gpu_auto.json` and `_build/gpu_light.json`.
Forced mode on NVIDIA is configuration testing, not evidence of a working
Intel/AMD graphics driver. Early hardware-selection decisions also have
standard-Python tests in `varun.launcher.gpu/tests/test_adapters.py`.

New Launcher validation on the Windows RTX 3070 Ti laptop passed both automatic
NVIDIA startup (Light default, RTX menu entries, advancing RTX frames and return
to Light) and forced-Light startup (only Light in the menu, RTX extension not
loaded, Storm frame produced). The three GPU-selection unit tests also passed.
This does not supersede the Intel/AMD/Linux testing limitations below.

GPU startup and the native controller require a full application restart.
Development hot reload is disabled for these extensions.

Build the native plugin first. From this directory, a Windows launch looks like:

```powershell
python scripts/run_compatibility.py --kit-sdk C:/kit-app-my-launcher/_build/windows-x86_64/release/kit C:/path/to/app.kit
```

On Linux:

```bash
python3 scripts/run_compatibility.py --kit-sdk /path/to/matching/kit /path/to/app.kit
```

Add `--dry-run` before the app path to print the exact argument list without starting Kit. Additional Kit arguments follow the app path; the launcher's required compatibility flags are appended last. It selects `--vulkan`, sets `/exts/omni.kit.renderer.core/compatibilityMode=true`, restricts `/renderer/enabled` to `pxr`, selects Storm, and forces single-GPU synchronous rendering. These settings must be available before graphics initialization, which is why changing the renderer menu after startup is not equivalent.

The chosen app must not independently require RTX through other extensions. Making this extension's RTX dependencies optional cannot remove another extension's requirements. Compatibility mode intentionally provides the raster-only menu: to use RTX choices again, restart the normal application without the compatibility launcher. The launcher provides a configuration path for compatible hardware; it is not a proof of AMD/Intel driver support.

## Platform and compatibility limits

- Supported source/build targets are **Windows x86_64 and Linux x86_64**. macOS and a standalone application are outside this integration's scope.
- Storm is a rasterization backend and this plugin adds no NVIDIA-specific compute dependency. Nevertheless, the surrounding Kit app, its required extensions and its graphics interoperability can impose GPU and driver requirements. Non-NVIDIA operation of the complete app must be verified on actual AMD/Intel hardware; selecting Storm alone is not proof of that support.
- Kit's PXR/GL interoperability uses a single GPU in this integration. The manifest disables automatic multi-GPU use, asynchronous rendering and the Fabric scene delegate, keeping USD scene synchronization on the compatible path. Compatibility mode additionally starts the host through Vulkan. These are host configuration constraints, not new renderer features.
- The menu adapter targets Kit 109 internals. Recheck `menu.py`, renderer discovery and GPU presentation when updating Kit.
- The Windows native build and native CTest were exercised with MSVC 19.44 and Kit SDK `109.0.3+production.263267.62477c11.gl`. Windows smoke validation covers themes, RTX round-trip, camera/selection preservation, asset preservation and stage replacement. The independent interaction suite passed real appearance clicks, right-click mesh creation and all six flight keys with opposing directions. Consult each current report and captured images for the tested revision.
- Raster-only Vulkan compatibility produced frames and both theme captures on the NVIDIA GPU with the RTX extension absent. The attempted Intel device on this hybrid laptop did **not** produce a Storm frame because of the host's graphics interoperability path. This is a failed non-NVIDIA validation, not confirmed Intel support. AMD hardware and Linux execution have not been tested. Production-scene performance also remains unmeasured.

## Blender reference

The requested local Blender checkout at `C:\blender` was inspected for visual and architectural reference. Useful files are:

- `source/blender/draw/engines/workbench/workbench_mesh_passes.cc`: separation of geometry and shading passes, reused native GPU resources.
- `source/blender/draw/engines/workbench/shaders/workbench_world_light.bsl.hh`: studio-style directional illumination.
- `source/blender/draw/engines/workbench/shaders/workbench_matcap.bsl.hh`: economical view-dependent solid shading reference.
- `source/blender/draw/engines/workbench/workbench_state.cc`: update-driven viewport state.
- `source/blender/draw/engines/workbench/workbench_effect_antialiasing.cc`: viewport antialiasing and navigation behavior.
- `source/blender/draw/engines/overlay/shaders/overlay_grid_frag.glsl`: subtle grid and axis presentation.

The resulting palettes, rig and integration code are original. No Blender source code, shaders, matcaps or other assets are copied or linked. Blender's full Workbench engine and feature set are not included.
