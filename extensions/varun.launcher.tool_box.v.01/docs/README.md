# Tool Box

A dockable tool window for Omniverse Kit apps with viewports. Enable the
`varun.launcher.tool_box.v.01` extension; reopen its window through **Window > Tool Box**.
The new launcher assigns it to `bottom_window`, with tools and properties side by
side. Other hosts retain the floating layout. The Python package remains
`tool_box` so shared tool types use one consistent import namespace.

## Tools and shortcuts

| Tool | Default shortcut |
| --- | --- |
| Move | M |
| Rotate | R |
| Scale | S |
| 3D paint | B |
| 3D pipe | Shift+D |
| Drawing Plane | Button only |
| Cancel active tool | Esc |

Edit `tool_box/shortcuts.py`, then reload the extension. Set a binding to `None`
to leave it unassigned. Shortcuts apply over viewport windows and Tool Box.
Existing host bindings for the same window are preserved; host global shortcuts
remain available outside these windows.

The properties panel exposes transform values or the active brush settings. The drawing-plane picker
selects Ground or a plane in the current stage. Creating a plane selects it.
Move and rotate plane prims using the ordinary transform tools. Drawing strokes
remain on the stage of the viewport where the drag began.

Drawing contains 3D paint (teal brush/oval) and 3D pipe (orange brush/sphere).
Canvas follows Drawing and remains visible when a tool is selected. Its three
buttons each have a checkbox underneath. PNG and colored SVG icons live in `data/icons`.
Selecting a drawing or transform tool collapses the other tool buttons.

Brush Size is the radius in stage units. 3D pipe creates a smooth-shaded, capped
mesh at that radius, using the selected circle, square, or triangle cross section. Midpoint curves smooth cursor samples, and completed
mesh sections stay unchanged while drawing. This works in Viewport Light without
RTX or global curve-rendering overrides. Each stroke is one selectable Xform with
small mesh sections underneath it. Existing saved strokes are left unchanged.

## Brush dynamics

Both drawing tools offer Circle (default), Square, and Triangle shapes. The white
pen-and-rings icon beside Brush Size toggles pressure; paint also has independent
pressure toggles beside Hardness and Opacity. Blue means enabled. The slider value is the maximum at full pressure;
mouse input keeps the slider value. Pressure toggles start disabled.

While a drawing tool is active, hold **Ctrl+Alt+LMB** and drag up/down to increase/decrease
size. In paint, drag right/left to increase/decrease hardness. Adjustment does not draw;
releasing the modifiers resumes drawing. Narrow sidebars stack labels above controls.

Windows pressure uses Windows Ink pointer messages, without a device-specific SDK or
additional Python packages. The driver must expose Windows Ink pressure. A remote
screen connection must forward pen pressure; mouse-only forwarding cannot provide it.

Touch never draws, places the 3D cursor, or adjusts the brush; use a pen or mouse. In the
new launcher, touch navigates the viewport instead.

Paint follows Grease Pencil's connected-stroke approach: a smoothed centerline becomes
a thin ribbon of triangles, with shared joins and caps only at the ends. It does not
create spheres or repeated mesh stamps. Radius, hardness, and opacity travel along the
stroke; the Storm shader draws the soft edges and transparency. Clicks retain the selected
brush footprint; moving strokes use its width and cap shape.

The analytic paint material targets Viewport Light (Storm). Its shader is embedded in
the saved USD material, with no Blender dependency or machine-specific asset path.
Centerline samples and brush values remain alongside the rendering geometry in USD.
Completed paint and pipe chunks stay unchanged while only a bounded live tail is updated.

Both tools accept samples only after movement of at least 20% of the smaller adjacent
brush radius (minimum 0.025 stage units). The same gap applies after smoothing, so slow
input and stationary holds cannot pile up points. Paint trims the inside of tight bends,
rounds their outside corners, chooses a nonoverlapping triangle split, and caps sharp
reversals within the same stroke.

## Structure

```text
tool_box/
  extension.py               Extension lifecycle and Window menu
  shortcuts.py               Editable keys and action registration
  core/
    controller.py            Tool ownership and cancellation
    tool.py                  Shared button and activation model
    viewport.py              Host viewport and stage routing
    resources.py             Packaged icon paths
  ui/
    window.py                Dockable window and plane picker
    properties.py            Shared property controls
  tools/
    transform/               Move, rotate, scale, and XYZ controls
    drawing/
      points/tool.py         3D paint input and stroke lifecycle
      points/mesh.py         Connected paint ribbons and brush sample storage
      pen.py                 Windows Ink pressure input
      shapes.py              Shared brush outlines
      curves/tool.py         3D pipe input and stroke lifecycle
      curves/mesh.py         Incremental smooth tube geometry
      plane/                 Plane creation, selection, and grid
      brush.py               Independent paint and pipe brush profiles
      cursor.py              Viewport gestures and brush ring
      properties.py          Brush controls
      settings.py            Drawing defaults
      stroke_resampler.py    Stroke spacing
      viewport_suppression.py  Temporary input-layer changes
```

## Shared canvas

Brush size, shape, pressure, hardness, and opacity remain independent for each tool.
Canvas choices are shared, always available, and absent from individual brush settings.
There is no Methods category in the toolbox.

- **Drawing plane**: its violet button creates a plane; its checkbox enables the
  selected plane and shows the plane picker.
- **Draw on object**: the colored brush/cube button or its checkbox enables drawing
  on visible USD surfaces. With Drawing plane enabled too, the nearest hit wins.
- **3D cursor**: click the colored cursor button, then click in the viewport to place
  its construction origin. Placement works without a brush selected and does not
  paint. The checkbox enables/disables drawing through that origin. Placement uses
  the ground, enabled drawing plane, or enabled object surface. Esc cancels placement.

With no canvas checked, drawing uses the ground plane. With 3D cursor checked, the
viewport keys `1`, `2`, and `3` select planes through its position perpendicular to
X, Y, and Z. Repeat a key to view the opposite side; `0` restores perspective and
keeps the last construction plane. Shared buttons provide the same view actions.

Object projection and ray queries run locally against USD geometry. Construction
cameras live only in the session layer. These features do not require RTX, a server,
or downloaded packages. Existing flat paint remeshing and pipe geometry remain the
default paths; surface drawing uses adapters around them.

Clayers collects each group's strokes under a collapsed **Stroke Layer**. Expand
it to select individual strokes, or select the layer to select all its strokes.
Layer visibility and locking also apply to strokes subsequently added to that group.
The layer is an organizational row; it does not move or rename saved USD strokes.

## Add to another app

Copy this entire extension folder, including `config`, `data`, `docs`, and the
Python package, to an extension search directory. Keep the folder named
`varun.launcher.tool_box.v.01`. Add `"varun.launcher.tool_box.v.01" = {}` to the
host application's `[dependencies]`, or enable it through Extension Manager.
Kit resolves the dependencies declared in `config/extension.toml`. The host
provides its viewport and renderer. Named USD contexts and multiple viewports
are supported without replacing `omni.usd.get_context`.

The implementation uses the Kit viewport registry and viewport window APIs.
It targets the Kit 109 SDK used by this repository; custom viewport frameworks
must expose those APIs. Test a different Kit release before distributing an app.

## Validation

`tests/viewport_host.kit` is a small host without launcher dependencies.
Run it with `--ext-folder <extensions-directory>` and
`--exec <this-extension>/tests/smoke.py`. The script checks tool registration,
floating UI, transforms, drawing, named contexts, and extension reload. Set
`/exts/tool_box/testReport` to the desired JSON report path.

`tests/input_smoke.py` also exercises the real shortcut and mouse input path.
Run it with `--enable omni.kit.ui_test` in the test host. It saves a JSON result
and a screenshot beside that report. Both tests use temporary, in-memory stages.

`tests/pipe_radius.py` runs in a disposable `3d-drawing` instance with
`omni.kit.renderer.capture` and `omni.kit.ui_test` enabled. It checks tool names,
field and slider edits, live/final radii, and a curved stroke made with actual
mouse events. It captures top, oblique, and side views, checks round sections,
end caps and chunk seams, and times updates for a 1,500-sample stroke. It also
asserts that the viewport uses Storm with the RTX rendering extensions disabled.
Set `/exts/tool_box/pipeTestOutput` for the output directory (default:
`_build/pipe_geometry_checks`).

`tests/brush_dynamics.py` checks pressure mapping and mouse fallback, three brush shapes,
tapered pipes, paint alpha values, bounded uploads, tool-specific UI, and real
Ctrl+Alt mouse gestures in a disposable `3d-drawing` app. Enable `omni.kit.ui_test` and
`omni.kit.renderer.capture`; set `/exts/tool_box/brushTestOutput` for artifacts.
Its Windows callback test supplies controlled packets; physical stylus verification
is separate from these automated checks.

`tests/slow_strokes.py` checks slow jitter, self-intersections, retracing, and tight
loops in all three brush shapes. It also injects slow mouse gestures and stationary
holds into a disposable app without moving the desktop cursor. Enable `omni.kit.ui_test`
and `omni.kit.renderer.capture`; results and captures go to `_build/slow_stroke_checks/final`.

`tests/drawing_features.py` exercises actual mouse drags and number-key input for
independent brushes, axes spokes, cursor placement, surface drawing, temporary-camera
cleanup, and narrow/wide controls. Run it in a disposable app capped at 30 FPS with
`omni.kit.ui_test` and `omni.kit.renderer.capture`; artifacts go to
`_build/shared_canvas_checks/kit`. The CPU tests `brush_profiles.py`,
`drawing_methods.py`, `surface_queries.py`, and `surface_paint.py` cover the numerical
and USD behavior without a renderer. Clayers includes `tests/stroke_layers.py`.
