#pragma once

#include <array>
#include <string_view>

namespace viewport_light
{
// ----------------------------------------------------------------------
// 1. THEME DATA
// ----------------------------------------------------------------------

struct Theme
{
    // Store the theme name, studio identifier, and RGBA backdrop.
    const char* name;
    const char* studio;
    std::array<float, 4> background;
};

// ----------------------------------------------------------------------
// 2. THEME LOOKUP
// ----------------------------------------------------------------------

// Original studio palettes; no Blender source or assets are incorporated.
const Theme& themeFor(std::string_view name) noexcept;
} // namespace viewport_light
