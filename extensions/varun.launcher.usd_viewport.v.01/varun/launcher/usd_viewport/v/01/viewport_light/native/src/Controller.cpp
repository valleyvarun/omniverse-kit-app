#include <viewport_light/Controller.h>
#include <viewport_light/Theme.h>

#include <memory>
#include <stdexcept>
#include <string>

namespace viewport_light
{
// ----------------------------------------------------------------------
// 1. CONTROLLER LIFECYCLE
// ----------------------------------------------------------------------

Controller::Controller(carb::settings::ISettings& settings, carb::dictionary::IDictionary& dictionary)
    : m_settings(settings), m_scope(settings, dictionary)
{
}

// Restore settings and release subscriptions on destruction.
Controller::~Controller()
{
    stop();
}

// Subscribe to active/theme changes and publish readiness.
void Controller::start()
{
    if (m_running)
        return;
    m_settings.setBool(kReady, false);
    // Create defaults only for settings that do not already exist.
    if (m_settings.getItemType(kActive) == carb::dictionary::ItemType::eCount)
        m_settings.setBool(kActive, false);
    if (m_settings.getItemType(kTheme) == carb::dictionary::ItemType::eCount)
        m_settings.setString(kTheme, "light");
    m_running = true;
    try
    {
        // Mark readiness only after both subscriptions and the first sync succeed.
        m_activeSubscription = m_settings.subscribeToNodeChangeEvents(kActive, changed, this);
        m_themeSubscription = m_settings.subscribeToNodeChangeEvents(kTheme, changed, this);
        if (!m_activeSubscription || !m_themeSubscription)
            throw std::runtime_error("Viewport Light could not subscribe to settings");
        synchronize();
        m_settings.setBool(kReady, true);
    }
    catch (...)
    {
        stop();
        throw;
    }
}

// Unsubscribe and restore host settings during shutdown.
void Controller::stop() noexcept
{
    // Unsubscribe before taking our mutex: another thread's callback may be
    // waiting on it, and Carbonite waits for in-flight callbacks here.
    if (m_activeSubscription)
        m_settings.unsubscribeToChangeEvents(m_activeSubscription);
    if (m_themeSubscription)
        m_settings.unsubscribeToChangeEvents(m_themeSubscription);
    m_activeSubscription = m_themeSubscription = nullptr;
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (!m_running)
        return;
    m_running = false;
    m_scope.restore();
    m_settings.setBool(kReady, false);
}

// ----------------------------------------------------------------------
// 2. SETTING CHANGE CALLBACK
// ----------------------------------------------------------------------

void Controller::changed(const carb::dictionary::Item*, carb::dictionary::ChangeEventType, void* userData)
{
    auto& self = *static_cast<Controller*>(userData);
    try
    {
        self.synchronize();
    }
    catch (...)
    {
        // No exception may escape the Carbonite callback ABI. Deactivation
        // restores the host; Python observes ready=false and reports failure.
        std::lock_guard<std::recursive_mutex> lock(self.m_mutex);
        self.m_updating = true;
        self.m_scope.restore();
        self.m_settings.setBool(kActive, false);
        self.m_settings.setBool(kReady, false);
        self.m_updating = false;
    }
}

// ----------------------------------------------------------------------
// 3. SYNCHRONIZE THE ACTIVE THEME
// ----------------------------------------------------------------------

void Controller::synchronize()
{
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_updating || !m_running)
        return;
    // Reset the re-entry guard automatically on every exit path.
    struct UpdatingGuard
    {
        bool& value;
        explicit UpdatingGuard(bool& flag) : value(flag) { value = true; }
        ~UpdatingGuard() { value = false; }
    } guard(m_updating);

    // Copy through Carbonite's owned-buffer API, so a concurrent settings write
    // cannot invalidate the string while the profile is being selected.
    std::unique_ptr<const char, void (*)(const char*)> buffer(
        m_settings.createStringBufferFromItemValue(kTheme), m_settings.destroyStringBuffer);
    const std::string name = buffer ? buffer.get() : "light";
    const Theme& theme = themeFor(name);
    // Canonicalize the theme and publish its appearance values.
    if (name != theme.name)
        m_settings.setString(kTheme, theme.name);
    m_settings.setFloatArray(kBackground, theme.background.data(), theme.background.size());
    m_settings.setString(kStudio, theme.studio);

    // Apply scoped overrides only while Light mode is active.
    if (m_settings.getAsBool(kActive))
        m_scope.activate();
    else
        m_scope.restore();
}
} // namespace viewport_light
