-- Use folder name to build extension name and tag.
-- ----------------------------------------------------------------------
-- 1. EXTENSION DETAILS
-- ----------------------------------------------------------------------

local ext = get_current_extension_info()

-- ----------------------------------------------------------------------
-- 2. BUILD PROJECT
-- ----------------------------------------------------------------------

project_ext (ext)

-- Link only those files and folders into the extension target directory
-- ----------------------------------------------------------------------
-- 3. LINK EXTENSION FILES
-- ----------------------------------------------------------------------

repo_build.prebuild_link {
    { "data", ext.target_dir.."/data" },
    { "layouts", ext.target_dir.."/layouts" },
    { "my_company", ext.target_dir.."/my_company" },
}
