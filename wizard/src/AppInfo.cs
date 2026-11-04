using System;
using System.IO;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Product identity, download sources, and install layout
    // ----------------------------------------------------------------------
    internal static class AppInfo
    {
        // --- 1) PRODUCT ---
        public const string DisplayName = "3D Drawing (WIP)";
        public const string Version = "0.1.0";
        public const string Publisher = "valleyvarun";
        public const string FolderName = "3D-Drawing";
        public const string KitFile = "3d-drawing.kit";

        // --- 2) DOWNLOADS ---
        // kit-app-template branch 109.0.3 (Kit SDK 109.0.3), pinned so every install builds the same template.
        public const string TemplateCommit = "a02748f24d142c2abfeddd7f153631f9443fd843";
        public const string TemplateVersion = "109.0.3";
        public const string TemplateZipUrl =
            "https://github.com/NVIDIA-Omniverse/kit-app-template/archive/" + TemplateCommit + ".zip";
        // The app's source tree; main is always the latest published version.
        public const string SourceZipUrl = "https://github.com/valleyvarun/omniverse-kit-app/archive/refs/heads/main.zip";
        public const string RepoUrl = "https://github.com/valleyvarun/omniverse-kit-app";
        public const string LicenseUrl =
            "https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/";
        public const string ProductTermsUrl =
            "https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-omniverse/";

        // Website and installer files in the app repo that do not belong in the Kit source folder.
        public static readonly string[] SourceExcludes = { "index.html", "css", "js", "imgs", "wizard", ".nojekyll" };

        // --- 3) INSTALL LAYOUT ---
        public const string MarkerFile = ".3d-drawing-install";
        public const string UninstallerName = "uninstall.exe";
        public const string IconName = "3d-drawing.ico";
        public const string LogName = "install.log";
        public const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\valleyvarun.3d-drawing";

        // First install estimate: Kit SDK, extensions, and repo tools land in shared caches.
        public const long RequiredBytes = 4L * 1024 * 1024 * 1024;
        // The build's deepest relative path is ~150 characters; keep the root short enough for MAX_PATH.
        public const int MaxInstallPathLength = 100;

        // Per-user Programs folder, unless the user profile path has spaces or other characters the
        // Kit build cannot handle; then a top-level folder like Kit's own C:\packman-repo.
        public static string DefaultInstallDir
        {
            get
            {
                string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string perUser = Path.Combine(local, "Programs", FolderName);
                if (FileOps.IsBuildSafePath(perUser))
                {
                    return perUser;
                }
                string drive = Environment.GetEnvironmentVariable("SystemDrive") ?? "C:";
                return Path.Combine(drive + "\\", FolderName);
            }
        }

        public static string BuildDir(string root)
        {
            return Path.Combine(root, "_build", "windows-x86_64", "release");
        }

        public static string LaunchScript(string root)
        {
            return Path.Combine(BuildDir(root), KitFile + ".bat");
        }

        public static string DesktopShortcut
        {
            get
            {
                string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                return Path.Combine(desktop, DisplayName + ".lnk");
            }
        }

        public static string StartMenuShortcut
        {
            get
            {
                string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                return Path.Combine(programs, DisplayName + ".lnk");
            }
        }
    }
}
