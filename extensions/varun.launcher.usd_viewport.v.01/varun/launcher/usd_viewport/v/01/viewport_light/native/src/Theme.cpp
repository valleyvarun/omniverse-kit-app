#include <viewport_light/Theme.h>

namespace viewport_light
{
// Choose Dark explicitly; use Grey Studio for all other names.
const Theme& themeFor(std::string_view name) noexcept
{
    // Theme identifiers and RGBA backdrop colors.
    static constexpr Theme light{ "light", "grey_studio", { 0.48f, 0.48f, 0.48f, 1.0f } };
    static constexpr Theme dark{ "dark", "dark_studio", { 0.105f, 0.115f, 0.13f, 1.0f } };
    return name == "dark" ? dark : light;
}
} // namespace viewport_light
