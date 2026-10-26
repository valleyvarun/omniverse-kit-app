-- Link the reusable extension and its packaged resources.
local ext = get_current_extension_info()
project_ext(ext)
repo_build.prebuild_link {
    { "tool_box", ext.target_dir.."/tool_box" },
    { "data", ext.target_dir.."/data" },
    { "docs", ext.target_dir.."/docs" },
    { "tests", ext.target_dir.."/tests" },
}
