// Host-independent lifecycle tests using the actual Carbonite interface ABI.
#include <viewport_light/Controller.h>
#include <viewport_light/Theme.h>

#include <algorithm>
#include <cstring>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

namespace
{
// ----------------------------------------------------------------------
// 1. FAKE HOST SETTINGS
// ----------------------------------------------------------------------

using Value = std::variant<bool, int64_t, double, std::string, std::vector<double>>;
using Item = carb::dictionary::Item;
using SubscriptionId = carb::dictionary::SubscriptionId;

// Fail the current test with a descriptive message.
void require(bool condition, const char* message)
{
    if (!condition)
        throw std::runtime_error(message);
}

// Emulate the Carbonite interfaces used by the controller.
struct FakeSettings
{
    struct Subscription
    {
        std::string path;
        carb::dictionary::OnNodeChangeEventFn callback;
        void* userData;
    };

    inline static FakeSettings* current = nullptr;
    carb::settings::ISettings settings{};
    carb::dictionary::IDictionary dictionary{};
    std::map<std::string, Value> values;
    std::vector<Subscription*> subscriptions;
    size_t snapshots = 0;
    size_t writes = 0;
    std::string failSnapshotOn;
    bool failThemeCopy = false;

    // Wire the fake settings functions to the current test host.
    FakeSettings()
    {
        current = this;
        // Report the stored type or the missing-key sentinel.
        settings.getItemType = [](const char* path) {
            const auto it = current->values.find(path);
            if (it == current->values.end())
                return carb::dictionary::ItemType::eCount;
            return static_cast<carb::dictionary::ItemType>(it->second.index());
        };
        // Copy settings into owned snapshots and track their lifetime.
        settings.createDictionaryFromSettings = [](const char* path) -> Item* {
            const auto it = current->values.find(path);
            if (it == current->values.end() || current->failSnapshotOn == path)
                return nullptr;
            ++current->snapshots;
            return reinterpret_cast<Item*>(new Value(it->second));
        };
        dictionary.destroyItem = [](Item* item) {
            delete reinterpret_cast<Value*>(item);
            --current->snapshots;
        };
        // Restore whole values and notify subscribers synchronously.
        settings.update = [](const char* path, const Item* item, const char*,
                             carb::dictionary::OnUpdateItemFn update, void* userData) {
            require(update(nullptr, carb::dictionary::ItemType::eCount, item,
                           carb::dictionary::ItemType::eDictionary, userData) ==
                        carb::dictionary::UpdateAction::eReplaceSubtree,
                    "Restoration must replace arrays/subtrees, not merge them");
            current->put(path, *reinterpret_cast<const Value*>(item));
        };
        settings.destroyItem = [](const char* path) {
            current->values.erase(path);
            ++current->writes;
            current->notify(path);
        };
        // Emulate scalar and array writes with change notifications.
        settings.setBool = [](const char* path, bool value) { current->put(path, value); };
        settings.setInt64 = [](const char* path, int64_t value) { current->put(path, value); };
        settings.setFloat64 = [](const char* path, double value) { current->put(path, value); };
        settings.setFloatArray = [](const char* path, const float* values, size_t count) {
            current->put(path, std::vector<double>(values, values + count));
        };
        settings.setFloat64Array = [](const char* path, const double* values, size_t count) {
            current->put(path, std::vector<double>(values, values + count));
        };
        // Expose array length and contents for palette updates.
        settings.getArrayLength = [](const char* path) -> size_t {
            const auto it = current->values.find(path);
            if (it == current->values.end() || !std::holds_alternative<std::vector<double>>(it->second))
                return 0;
            return std::get<std::vector<double>>(it->second).size();
        };
        settings.getAsFloat64Array = [](const char* path, double* output, size_t count) {
            const auto& values = std::get<std::vector<double>>(current->values.at(path));
            std::copy_n(values.begin(), std::min(count, values.size()), output);
        };
        // Copy string values and expose boolean reads.
        settings.internalSetString = [](const char* path, const char* value, size_t length) {
            current->put(path, std::string(value, length == size_t(-1) ? std::strlen(value) : length));
        };
        settings.getAsBool = [](const char* path) {
            const auto it = current->values.find(path);
            return it != current->values.end() && std::holds_alternative<bool>(it->second) &&
                   std::get<bool>(it->second);
        };
        // Emulate owned string buffers, including allocation failures.
        settings.internalCreateStringBufferFromItemValue = [](const char* path, size_t*) -> const char* {
            if (current->failThemeCopy)
                throw std::bad_alloc();
            const auto it = current->values.find(path);
            if (it == current->values.end() || !std::holds_alternative<std::string>(it->second))
                return nullptr;
            const std::string& text = std::get<std::string>(it->second);
            char* buffer = new char[text.size() + 1];
            std::memcpy(buffer, text.c_str(), text.size() + 1);
            return buffer;
        };
        settings.destroyStringBuffer = [](const char* buffer) { delete[] buffer; };
        // Track subscriptions so shutdown can be checked for leaks.
        settings.subscribeToNodeChangeEvents = [](const char* path,
                                                  carb::dictionary::OnNodeChangeEventFn callback,
                                                  void* userData) -> SubscriptionId* {
            auto* subscription = new Subscription{ path, callback, userData };
            current->subscriptions.push_back(subscription);
            return reinterpret_cast<SubscriptionId*>(subscription);
        };
        settings.unsubscribeToChangeEvents = [](SubscriptionId* id) {
            auto* subscription = reinterpret_cast<Subscription*>(id);
            auto& list = current->subscriptions;
            list.erase(std::remove(list.begin(), list.end(), subscription), list.end());
            delete subscription;
        };
    }

    // Store one value and dispatch its change notification.
    void put(const char* path, Value value)
    {
        values.insert_or_assign(path, std::move(value));
        ++writes;
        notify(path);
    }

    void notify(const char* path)
    {
        // Deliberately synchronous: this exercises callback re-entry when the
        // controller canonicalizes an invalid theme during a theme callback.
        for (auto* subscription : subscriptions)
            if (subscription->path == path)
                subscription->callback(nullptr, carb::dictionary::ChangeEventType::eChanged, subscription->userData);
    }
};

// ----------------------------------------------------------------------
// 2. SETTING RESTORATION TESTS
// ----------------------------------------------------------------------

void restorationAndRepeatedActivation()
{
    FakeSettings host;
    host.values = {
        { "/rtx/debugMaterialType", int64_t(7) },
        { "/rtx/useViewLightingMode", true },
        { "/rtx/post/backgroundZeroAlpha/enabled", std::string("custom") },
        { "/persistent/app/viewport/outline/width", std::vector<double>{ 1, 2, 3 } },
        { "/unrelated/user/preference", std::string("keep") },
    };
    const auto original = host.values;
    // Verify activation, repeated calls, and exact restoration.
    viewport_light::SettingsScope scope(host.settings, host.dictionary);
    scope.activate();
    require(scope.active(), "Activation failed");
    require(std::get<int64_t>(host.values.at("/rtx/debugMaterialType")) == -1, "Raster material mode not applied");
    require(std::get<bool>(host.values.at("/rtx/post/backgroundZeroAlpha/enabled")), "Transparent background not enabled");
    require(!std::get<bool>(host.values.at("/rtx/post/backgroundZeroAlpha/outputAlphaInComposite")),
            "PXR compositor must preserve clear pixels for the studio backdrop");
    const auto writes = host.writes;
    scope.activate();
    require(host.writes == writes, "Repeated activation must not overwrite the saved host state");
    scope.restore();
    require(host.values == original, "Values, types, missing keys, or unrelated settings were not restored");
    require(host.snapshots == 0, "Snapshots leaked after restoration");
    const auto restoredWrites = host.writes;
    scope.restore();
    require(host.writes == restoredWrites, "Repeated restore must be a no-op");
    host.values["/rtx/debugMaterialType"] = int64_t(3);
    scope.activate();
    scope.restore();
    require(std::get<int64_t>(host.values.at("/rtx/debugMaterialType")) == 3, "Next activation reused a stale snapshot");
}

// Reject failed snapshots without changing host settings.
void failedCaptureDoesNotMutateHost()
{
    FakeSettings host;
    host.values = { { "/rtx/debugMaterialType", int64_t(7) }, { "/rtx/wireframe/mode", int64_t(2) } };
    const auto original = host.values;
    host.failSnapshotOn = "/rtx/wireframe/mode";
    viewport_light::SettingsScope scope(host.settings, host.dictionary);
    bool failed = false;
    try { scope.activate(); }
    catch (const std::runtime_error&) { failed = true; }
    require(failed && !scope.active(), "Snapshot failure must reject activation");
    require(host.values == original && host.writes == 0, "Capture failure changed host settings");
    require(host.snapshots == 0, "Partial snapshots leaked on failed activation");
}

// Change only hover/selection colors and preserve custom palettes.
void selectionPaletteIsScopedAndPreservesOtherColors()
{
    FakeSettings host;
    constexpr const char* path = "/persistent/app/viewport/outline/color";
    std::vector<double> original(1040);
    for (size_t index = 0; index < original.size(); ++index)
        original[index] = double(index % 13) / 13.0;
    host.values[path] = original;
    {
        viewport_light::SettingsScope scope(host.settings, host.dictionary);
        scope.activate();
        const auto& colors = std::get<std::vector<double>>(host.values.at(path));
        require(colors.size() == original.size(), "Selection palette length changed");
        require(std::equal(colors.begin(), colors.begin() + 1016, original.begin()), "Unrelated palette colors changed");
        require(std::equal(colors.begin() + 1024, colors.end(), original.begin() + 1024), "Extra palette colors changed");
        const std::vector<double> orange{ 0.95, 0.4, 0.08, 1.0 };
        require(std::equal(orange.begin(), orange.end(), colors.begin() + 1016), "Hover palette color not applied");
        require(std::equal(orange.begin(), orange.end(), colors.begin() + 1020), "Selection palette color not applied");
    }
    require(std::get<std::vector<double>>(host.values.at(path)) == original, "Original palette was not restored exactly");
    host.values[path] = std::vector<double>{ 0.1, 0.2, 0.3, 0.4 };
    const auto shortPalette = host.values.at(path);
    {
        viewport_light::SettingsScope scope(host.settings, host.dictionary);
        scope.activate();
        require(host.values.at(path) == shortPalette, "A short custom palette must be left untouched");
    }
    host.values.erase(path);
    {
        viewport_light::SettingsScope scope(host.settings, host.dictionary);
        scope.activate();
        require(host.values.count(path) == 0, "A missing palette must not be manufactured");
    }
    require(host.snapshots == 0, "Palette snapshots leaked");
}

// ----------------------------------------------------------------------
// 3. CONTROLLER LIFECYCLE TESTS
// ----------------------------------------------------------------------

void controllerLifecycleAndReentry()
{
    FakeSettings host;
    host.values["/rtx/debugMaterialType"] = int64_t(9);
    {
        // Check repeated startup, theme updates, and active shutdown.
        viewport_light::Controller controller(host.settings, host.dictionary);
        controller.start();
        controller.start();
        require(host.subscriptions.size() == 2, "Startup should create exactly two subscriptions");
        require(host.settings.getAsBool(viewport_light::kReady), "Startup did not publish readiness");
        require(std::get<std::string>(host.values.at(viewport_light::kTheme)) == "light", "Default is not Grey Studio");
        require(std::get<int64_t>(host.values.at("/rtx/debugMaterialType")) == 9, "Inactive startup touched renderer settings");
        host.settings.setBool(viewport_light::kActive, true);
        host.settings.setString(viewport_light::kTheme, "dark");
        require(std::get<std::string>(host.values.at(viewport_light::kStudio)) == "dark_studio", "Dark theme not published");
        host.settings.setString(viewport_light::kTheme, "invalid");
        require(std::get<std::string>(host.values.at(viewport_light::kTheme)) == "light", "Invalid theme did not canonicalize");
        host.settings.setBool(viewport_light::kActive, false);
        require(std::get<int64_t>(host.values.at("/rtx/debugMaterialType")) == 9, "Renderer switch did not restore host");
        host.settings.setBool(viewport_light::kActive, true);
        // Destruction while active must also restore and unsubscribe.
    }
    require(!host.settings.getAsBool(viewport_light::kReady), "Shutdown left ready=true");
    require(host.subscriptions.empty() && host.snapshots == 0, "Shutdown leaked subscriptions or snapshots");
    require(std::get<int64_t>(host.values.at("/rtx/debugMaterialType")) == 9, "Active shutdown did not restore settings");
}

// Restore the host when a setting callback fails.
void callbackFailureRestoresWithoutRecursiveFailure()
{
    FakeSettings host;
    host.values["/rtx/debugMaterialType"] = int64_t(11);
    viewport_light::Controller controller(host.settings, host.dictionary);
    controller.start();
    host.settings.setBool(viewport_light::kActive, true);
    host.failThemeCopy = true;
    host.settings.setString(viewport_light::kTheme, "dark");
    require(!host.settings.getAsBool(viewport_light::kActive), "Callback failure did not deactivate");
    require(!host.settings.getAsBool(viewport_light::kReady), "Callback failure did not publish unavailable state");
    require(std::get<int64_t>(host.values.at("/rtx/debugMaterialType")) == 11, "Callback failure did not restore host state");
    require(host.snapshots == 0, "Callback failure leaked settings snapshots");
}
} // namespace

// ----------------------------------------------------------------------
// 4. TEST RUNNER
// ----------------------------------------------------------------------

int main()
{
    try
    {
        restorationAndRepeatedActivation();
        failedCaptureDoesNotMutateHost();
        selectionPaletteIsScopedAndPreservesOtherColors();
        controllerLifecycleAndReentry();
        callbackFailureRestoresWithoutRecursiveFailure();
        std::cout << "Viewport Light: restoration, failed capture, re-entry, and lifecycle tests passed\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
