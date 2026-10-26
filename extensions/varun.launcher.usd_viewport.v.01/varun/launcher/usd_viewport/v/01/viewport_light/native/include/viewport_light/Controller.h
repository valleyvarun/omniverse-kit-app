#pragma once

#include <viewport_light/SettingsScope.h>

#include <mutex>

namespace viewport_light
{
// ----------------------------------------------------------------------
// 1. SHARED SETTING PATHS
// ----------------------------------------------------------------------

inline constexpr char kRoot[] = "/exts/varun.launcher.usd_viewport.v.01/viewportLight";
inline constexpr char kActive[] = "/exts/varun.launcher.usd_viewport.v.01/viewportLight/active";
inline constexpr char kReady[] = "/exts/varun.launcher.usd_viewport.v.01/viewportLight/ready";
inline constexpr char kBackground[] = "/exts/varun.launcher.usd_viewport.v.01/viewportLight/background";
inline constexpr char kStudio[] = "/exts/varun.launcher.usd_viewport.v.01/viewportLight/studio";
inline constexpr char kTheme[] = "/persistent/exts/varun.launcher.usd_viewport.v.01/viewportLight/theme";

// ----------------------------------------------------------------------
// 2. CONTROLLER LIFECYCLE
// ----------------------------------------------------------------------

class Controller
{
public:
    // Own scoped renderer settings and release them on destruction.
    Controller(carb::settings::ISettings& settings, carb::dictionary::IDictionary& dictionary);
    ~Controller();
    Controller(const Controller&) = delete;
    Controller& operator=(const Controller&) = delete;
    // Subscribe on start; restore host settings on stop.
    void start();
    void stop() noexcept;

private:
    // Translate setting callbacks into a synchronized policy update.
    static void changed(const carb::dictionary::Item*, carb::dictionary::ChangeEventType, void* userData);
    void synchronize();

    // Keep subscription and re-entry state with the scoped settings owner.
    carb::settings::ISettings& m_settings;
    SettingsScope m_scope;
    carb::dictionary::SubscriptionId* m_activeSubscription = nullptr;
    carb::dictionary::SubscriptionId* m_themeSubscription = nullptr;
    std::recursive_mutex m_mutex;
    bool m_updating = false;
    bool m_running = false;
};
} // namespace viewport_light
