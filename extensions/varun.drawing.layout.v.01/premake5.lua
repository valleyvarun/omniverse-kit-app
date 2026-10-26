local ext = get_current_extension_info()
project_ext(ext)
repo_build.prebuild_link {
    { "drawing_layout", ext.target_dir.."/drawing_layout" },
    { "layouts", ext.target_dir.."/layouts" },
    { "docs", ext.target_dir.."/docs" },
    { "tests", ext.target_dir.."/tests" },
}
