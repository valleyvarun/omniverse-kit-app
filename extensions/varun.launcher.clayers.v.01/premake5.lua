local ext = get_current_extension_info()
project_ext(ext)
repo_build.prebuild_link {
    { "clayers", ext.target_dir.."/clayers" },
    { "tests", ext.target_dir.."/tests" },
}
