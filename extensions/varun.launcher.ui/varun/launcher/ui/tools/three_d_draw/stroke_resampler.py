"""Pure-math helper that walks a polyline and emits stamps at uniform arc-length spacing.

Used by the stroke emitter to convert the raw cursor-sample polyline of a
single tick into evenly spaced brush-stamp centers, with a `carry` value
that preserves spacing continuity across ticks.
"""

from __future__ import annotations


# Resample a polyline into uniformly spaced stamps along its arc length.
#
# Args:
#   polyline:    ordered cursor samples (x, y, z) for this tick.
#   spacing:     desired world-space distance between consecutive stamps.
#   carry:       distance walked since the previous tick's last stamp (so
#                spacing stays uniform across ticks).
#   seed_first:  emit a stamp exactly at `polyline[0]` (used for the very
#                first tick of a stroke).
#
# Returns:
#   stamps:      list of (x, y, z) stamp centers for this tick.
#   last_stamp:  the final stamp position (or polyline tail if no stamps).
#   carry:       remaining distance to carry into the next tick.
def resample_polyline(
    polyline: list[tuple[float, float, float]],
    spacing: float,
    carry: float,
    seed_first: bool,
) -> tuple[list[tuple[float, float, float]], tuple[float, float, float], float]:
    stamps: list[tuple[float, float, float]] = []
    # Optionally seed the very first stamp at the polyline start.
    if seed_first and polyline:
        stamps.append(polyline[0])
        carry = 0.0
    # Distance walked since the last stamp (carries across calls).
    dist_since = carry
    # Walk each segment, emitting stamps every `spacing` units.
    for i in range(len(polyline) - 1):
        ax, ay, az = polyline[i]
        bx, by, bz = polyline[i + 1]
        dx, dy, dz = bx - ax, by - ay, bz - az
        seg_len = (dx * dx + dy * dy + dz * dz) ** 0.5
        # Skip degenerate segments.
        if seg_len <= 1e-9:
            continue
        remaining = seg_len
        # Drop stamps along this segment until the next one would overshoot.
        while dist_since + remaining >= spacing - 1e-9:
            advance = spacing - dist_since
            offset = (seg_len - remaining) + advance
            t = offset / seg_len
            stamps.append((ax + dx * t, ay + dy * t, az + dz * t))
            remaining -= advance
            dist_since = 0.0
        dist_since += remaining
    # Track the last stamp position for spacing continuity across ticks.
    last_stamp = stamps[-1] if stamps else (polyline[-1] if polyline else (0.0, 0.0, 0.0))
    return stamps, last_stamp, dist_since
