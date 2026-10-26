# ----------------------------------------------------------------------
# 1. PROJECT & TOOLCHAIN
# ----------------------------------------------------------------------
cmake_minimum_required(VERSION 3.20)
project(viewport_light VERSION 1.0.0 LANGUAGES CXX)

# Kit can ship an older MSVC runtime than the local compiler. Keep our STL
# runtime private; all Carbonite allocations are freed through their own API.
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

# ----------------------------------------------------------------------
# 2. SDK & PLATFORM VALIDATION
# ----------------------------------------------------------------------
set(KIT_SDK_ROOT "" CACHE PATH "Kit SDK root containing dev/include")
if(NOT EXISTS "${KIT_SDK_ROOT}/dev/include/carb/PluginUtils.h")
    message(FATAL_ERROR "Set KIT_SDK_ROOT to the matching Kit SDK root (containing dev/include)")
endif()
if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "Viewport Light requires a 64-bit Kit SDK and compiler")
endif()
if(WIN32)
    set(VIEWPORT_LIGHT_PLATFORM windows-x86_64)
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64|AMD64)$")
    set(VIEWPORT_LIGHT_PLATFORM linux-x86_64)
else()
    message(FATAL_ERROR "Supported targets: Windows x86_64 and Linux x86_64")
endif()

# ----------------------------------------------------------------------
# 3. SHARED CONTROLLER LIBRARY
# ----------------------------------------------------------------------
add_library(viewport_light_core STATIC native/src/Theme.cpp native/src/SettingsScope.cpp native/src/Controller.cpp)
find_package(Threads REQUIRED)
target_link_libraries(viewport_light_core PUBLIC Threads::Threads)
target_compile_features(viewport_light_core PUBLIC cxx_std_17)
target_include_directories(viewport_light_core PUBLIC native/include)
target_include_directories(viewport_light_core SYSTEM PUBLIC "${KIT_SDK_ROOT}/dev/include")
set_target_properties(viewport_light_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(MSVC)
    target_compile_options(viewport_light_core PRIVATE /W4 /permissive-)
else()
    target_compile_options(viewport_light_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

# ----------------------------------------------------------------------
# 4. KIT PLUGIN TARGET
# ----------------------------------------------------------------------
add_library(viewport_light_plugin MODULE native/src/Plugin.cpp)
target_link_libraries(viewport_light_plugin PRIVATE viewport_light_core ${CMAKE_DL_LIBS})
set_target_properties(viewport_light_plugin PROPERTIES
    OUTPUT_NAME viewport_light.plugin
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin/${VIEWPORT_LIGHT_PLATFORM}"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin/${VIEWPORT_LIGHT_PLATFORM}")
foreach(config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(viewport_light_plugin PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/bin/${VIEWPORT_LIGHT_PLATFORM}"
        LIBRARY_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/bin/${VIEWPORT_LIGHT_PLATFORM}")
endforeach()

# ----------------------------------------------------------------------
# 5. NATIVE TEST TARGET
# ----------------------------------------------------------------------
include(CTest)
if(BUILD_TESTING)
    add_executable(viewport_light_native_tests native/tests/SettingsScopeTests.cpp)
    target_link_libraries(viewport_light_native_tests PRIVATE viewport_light_core)
    add_test(NAME viewport_light_native_tests COMMAND viewport_light_native_tests)
endif()
