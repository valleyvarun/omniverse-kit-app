#include <viewport_light/SettingsScope.h>

#include <stdexcept>

namespace viewport_light
{
namespace
{
// ----------------------------------------------------------------------
// 1. SETTINGS OWNED BY LIGHT MODE
// ----------------------------------------------------------------------

constexpr const char* kPaths[] = {
    "/rtx/debugMaterialType",
    "/rtx/wireframe/mode",
    "/rtx/useViewLightingMode",
    "/rtx/post/backgroundZeroAlpha/enabled",
    "/rtx/post/backgroundZeroAlpha/outputAlphaInComposite",
    "/app/viewport/outline/enabled",
    "/persistent/app/viewport/outline/width",
    "/persistent/app/hydra/delegates/common/omniGlobalLightAdjustment",
    "/persistent/app/viewport/outline/color",
};

// Restore complete values instead of merging saved subtrees.
carb::dictionary::UpdateAction replace(const carb::dictionary::Item*, carb::dictionary::ItemType,
                                      const carb::dictionary::Item*, carb::dictionary::ItemType, void*)
{
    return carb::dictionary::UpdateAction::eReplaceSubtree;
}
} // namespace

// ----------------------------------------------------------------------
// 2. SNAPSHOT LIFECYCLE
// ----------------------------------------------------------------------

SettingsScope::SettingsScope(carb::settings::ISettings& settings, carb::dictionary::IDictionary& dictionary)
    : m_settings(settings), m_dictionary(dictionary)
{
}

// Restore the host automatically when the scope is destroyed.
SettingsScope::~SettingsScope()
{
    restore();
}

// ----------------------------------------------------------------------
// 3. CAPTURE AND APPLY LIGHT SETTINGS
// ----------------------------------------------------------------------

void SettingsScope::activate()
{
    if (m_active)
        return;

    // Capture everything before the first mutation. If allocation fails, leave
    // the host untouched and let local RAII snapshots release their storage.
    std::vector<SavedSetting> saved;
    saved.reserve(sizeof(kPaths) / sizeof(kPaths[0]));
    for (const char* path : kPaths)
    {
        const bool exists = m_settings.getItemType(path) != carb::dictionary::ItemType::eCount;
        Snapshot value(exists ? m_settings.createDictionaryFromSettings(path) : nullptr, m_dictionary.destroyItem);
        if (exists && !value)
            throw std::runtime_error("Viewport Light could not snapshot a renderer setting");
        saved.push_back({ path, std::move(value) });
    }

    // PXR reads the final two entries of Kit's 256-color RGBA palette for
    // hover and selection. Keep every other entry and any additional colors.
    std::vector<double> palette;
    const size_t colorCount = m_settings.getArrayLength(kPaths[8]);
    if (colorCount >= 1024)
    {
        palette.resize(colorCount);
        m_settings.getAsFloat64Array(kPaths[8], palette.data(), palette.size());
        constexpr double orange[] = { 0.95, 0.4, 0.08, 1.0 };
        for (size_t channel = 0; channel < 4; ++channel)
            palette[1016 + channel] = palette[1020 + channel] = orange[channel];
    }
    m_saved = std::move(saved);
    m_active = true;

    // Use raster materials (including paint coverage), alpha compositing, and selection appearance.
    m_settings.setInt(kPaths[0], -1);
    m_settings.setInt(kPaths[1], 0);
    m_settings.setBool(kPaths[2], false);
    m_settings.setBool(kPaths[3], true);
    // PXR's compositor preserves transparent clear pixels only when composite
    // alpha output is disabled. The studio UI backdrop supplies their color.
    m_settings.setBool(kPaths[4], false);
    m_settings.setBool(kPaths[5], true);
    m_settings.setInt(kPaths[6], 2);
    m_settings.setFloat64(kPaths[7], 1.0);
    if (!palette.empty())
        m_settings.setFloat64Array(kPaths[8], palette.data(), palette.size());
}

// ----------------------------------------------------------------------
// 4. RESTORE ORIGINAL HOST SETTINGS
// ----------------------------------------------------------------------

void SettingsScope::restore() noexcept
{
    if (!m_active)
        return;
    m_active = false;
    // Restore in reverse order, removing keys that were originally absent.
    for (auto it = m_saved.rbegin(); it != m_saved.rend(); ++it)
    {
        if (it->value)
            m_settings.update(it->path, it->value.get(), nullptr, replace, nullptr);
        else
            m_settings.destroyItem(it->path);
    }
    m_saved.clear();
}
} // namespace viewport_light
