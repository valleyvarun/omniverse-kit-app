local ext = get_current_extension_info()
project_ext(ext)
repo_build.prebuild_link {
    { "explorer", ext.target_dir.."/explorer" },
    { "tests", ext.target_dir.."/tests" },
}
