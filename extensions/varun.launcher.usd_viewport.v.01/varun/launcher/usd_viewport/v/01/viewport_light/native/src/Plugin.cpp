#define CARB_EXPORTS

#include <carb/PluginUtils.h>
#include <carb/logging/Log.h>
#include <omni/ext/IExt.h>
#include <viewport_light/Controller.h>

#include <exception>
#include <memory>

// ----------------------------------------------------------------------
// 1. PLUGIN IDENTITY & DEPENDENCIES
// ----------------------------------------------------------------------

const carb::PluginImplDesc kPluginImpl{
    "varun.launcher.viewport_light.plugin",
    "Native studio viewport settings and lifecycle for the OpenUSD Storm delegate.",
    "Varun Launcher",
    carb::PluginHotReload::eDisabled,
    "1.0.0"
};

CARB_PLUGIN_IMPL_DEPS(carb::settings::ISettings, carb::dictionary::IDictionary, carb::logging::ILogging)

// ----------------------------------------------------------------------
// 2. EXTENSION LIFECYCLE
// ----------------------------------------------------------------------

class ViewportLightExtension : public omni::ext::IExt
{
public:
    void onStartup(const char*) override
    {
        // Resolve the host interfaces before creating the controller.
        auto* settings = carb::getCachedInterface<carb::settings::ISettings>();
        auto* dictionary = carb::getCachedInterface<carb::dictionary::IDictionary>();
        if (!settings || !dictionary)
        {
            CARB_LOG_ERROR("Viewport Light: Carbonite settings/dictionary interfaces unavailable");
            return;
        }
        // Start the controller and report initialization failures.
        try
        {
            m_controller = std::make_unique<viewport_light::Controller>(*settings, *dictionary);
            m_controller->start();
        }
        catch (const std::exception& error)
        {
            m_controller.reset();
            CARB_LOG_ERROR("Viewport Light: %s", error.what());
        }
    }

    // Destroy the controller to restore settings and unsubscribe.
    void onShutdown() override { m_controller.reset(); }

private:
    std::unique_ptr<viewport_light::Controller> m_controller;
};

// ----------------------------------------------------------------------
// 3. CARBONITE PLUGIN REGISTRATION
// ----------------------------------------------------------------------

CARB_PLUGIN_IMPL(kPluginImpl, ViewportLightExtension)

void fillInterface(ViewportLightExtension&) {}
