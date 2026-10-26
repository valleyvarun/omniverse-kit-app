# 3d-drawing

A small drawing test app using the existing USD viewport, toolbox, cLayers, and Kit Stage extensions.

- Left: Tool Box, with controls arranged for a sidebar.
- Center: one permanent Viewport Light view using OpenUSD Storm.
- Right: cLayers above Stage, both visible together.

The app excludes the launcher workspace, tabs, Home, Explorer, command line, Plexus, and launcher layout. RTX is disabled even on NVIDIA adapters. The existing viewport extension creates the initial empty stage.

Build with `repo.bat build`, then launch `_build\windows-x86_64\release\3d-drawing.kit.bat`.
Press Escape to leave a drawing tool and restore the tool chooser. The standard File menu opens and saves USD stages.

The layout action `varun.drawing.layout.v.01 / reset_layout` restores the three columns.

Sidebar widths are remembered in `source/extensions/varun.drawing.layout.v.01/layouts/sidebar-widths.json`. Commit this file to share the saved widths through GitHub. The build links the extension's layouts directory, so running the built app updates this same project file. No user-specific absolute paths are stored.

The file contains `left` and `right` widths in logical pixels. Resizing saves after a short pause, and quitting flushes the latest widths. Startup restores them, keeping room for the viewport on smaller displays. Missing or invalid files use the default widths of 170 and 255; Reset Layout also saves these defaults.

## Validation

Run the built app with `--enable omni.kit.renderer.capture --exec C:/kit-app-my-launcher/source/extensions/varun.drawing.layout.v.01/tests/smoke.py` to exercise layout, renderer selection, transforms, drawing, Escape, layout reset, and stage replacement. This uses disposable stages and exits without saving; use a separate app instance. Reports and screenshots go to `_build/drawing_checks` by default.

Pass `--/app/drawing/testWidthsFile=C:/kit-app-my-launcher/_build/drawing_width_checks/sidebar-widths.json` during tests to preserve the project's saved widths. The persistence test requires this override and runs twice with `/app/drawing/testPhase=record` and then `restore`.

The Kit 109.0.3 smoke run passes. During stage replacement, Kit's OpenGL scene-view integration logs a missing `UsdRender.Product` error and USD reports a remaining stage reference. The single viewport and layout checks still pass after replacement; these SDK diagnostics remain unresolved.
