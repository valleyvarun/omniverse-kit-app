local ext = get_current_extension_info()
project_ext(ext)

repo_build.prebuild_link {
    { "plexus", ext.target_dir .. "/plexus" },
    { "tests", ext.target_dir .. "/tests" },
}
