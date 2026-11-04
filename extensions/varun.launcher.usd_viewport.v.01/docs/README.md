# usd viewport [varun.launcher.usd_viewport.v.01]

A simple python UI extension example. Use it as a starting point for your extensions.

## Touch navigation

On a Windows touchscreen, fingers that land on the viewport move the camera:

| Gesture | Action |
| --- | --- |
| Drag one finger | Pan |
| Pinch with two fingers | Zoom toward the point between the fingers |
| Move two fingers together | Orbit around the point the camera is looking at |

A two-finger gesture zooms or orbits, depending on its first movement; lift both fingers to switch or to pan again.
Touch never selects or draws; use a pen or mouse for that. Orthographic views zoom and pan but do not
orbit, and locked cameras ignore touch. Touches elsewhere in the window work as usual.

## 3D ruler and units

A bright yellow 3D ruler crosses the grid at the point the camera is looking at: one line along each
ground axis and one along the vertical axis, each numbered at the major grid lines. The spacing follows
the zoom in powers of ten, for example 0, 1, 2 or 0, 10, 20 or 0, 0.1, 0.2. The ruler button (ruler
icon) in the viewport menubar shows or hides it; it starts on. The **Units** button chooses metres
(default) or millimetres. The numbers are a display reference; the stage's own units are unchanged.

## Z up

New stages and viewport tabs are Z-up (Kit's default up axis is set to Z). Files saved Y-up keep
their own up axis.

## Idle frame pacing

Like Blender, the app only redraws while something happens. Input, camera moves, scene edits,
playback, and RTX rendering keep frames at 60 per second (Kit's "60" pacing preset). About one second
after the last change, each frame waits for the next window event instead of redrawing, at most ten
times per second. Pick a different preset from the viewport's pacing menu if needed.
