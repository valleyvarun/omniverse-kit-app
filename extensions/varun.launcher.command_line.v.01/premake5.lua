local ext = get_current_extension_info()
project_ext(ext)
repo_build.prebuild_link {
    { "command_line", ext.target_dir.."/command_line" },
    { "tests", ext.target_dir.."/tests" },
}
