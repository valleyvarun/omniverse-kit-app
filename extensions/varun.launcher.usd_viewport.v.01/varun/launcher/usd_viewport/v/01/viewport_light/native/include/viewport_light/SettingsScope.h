#pragma once

#include <carb/settings/ISettings.h>

#include <memory>
#include <vector>

namespace viewport_light
{
// ----------------------------------------------------------------------
// 1. SCOPED SETTINGS OWNER
// ----------------------------------------------------------------------

// Owns only the settings changed by the lightweight viewport. Snapshots preserve
// type, value, array contents, and absence across repeated renderer switches.
class SettingsScope
{
public:
    // Bind the host interfaces without changing any settings.
    SettingsScope(carb::settings::ISettings& settings, carb::dictionary::IDictionary& dictionary);
    ~SettingsScope();
    SettingsScope(const SettingsScope&) = delete;
    SettingsScope& operator=(const SettingsScope&) = delete;

    // Capture and apply Light settings, then restore them on exit.
    void activate();
    void restore() noexcept;
    bool active() const noexcept { return m_active; }

private:
    // ----------------------------------------------------------------------
    // 2. SAVED SETTING DATA
    // ----------------------------------------------------------------------

    using Snapshot = std::unique_ptr<carb::dictionary::Item, void (*)(carb::dictionary::Item*)>;
    struct SavedSetting
    {
        const char* path;
        Snapshot value;
    };

    // Retain the original host values until Light mode ends.
    carb::settings::ISettings& m_settings;
    carb::dictionary::IDictionary& m_dictionary;
    std::vector<SavedSetting> m_saved;
    bool m_active = false;
};
} // namespace viewport_light
