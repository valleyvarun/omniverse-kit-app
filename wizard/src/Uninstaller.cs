using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Uninstall
    // ----------------------------------------------------------------------
    // Shared caches (C:\packman-repo, %LOCALAPPDATA%\ov) stay: other Kit apps use them too.
    internal static class Uninstaller
    {
        private const string Title = AppInfo.DisplayName + " Uninstall";

        public static int Run(Options options)
        {
            string self = Assembly.GetExecutingAssembly().Location;
            string root = Path.GetFullPath(options.InstallDir ?? Path.GetDirectoryName(self)).TrimEnd('\\');

            // --- 1) CONFIRM ---
            if (!FileOps.IsInstallFolder(root))
            {
                Report(options, "No 3D Drawing installation was found in " + root + ".", true);
                return 2;
            }
            if (!options.Silent && !options.Confirmed)
            {
                DialogResult answer = MessageBox.Show(
                    "Remove " + AppInfo.DisplayName + " from " + root + "?",
                    Title, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (answer != DialogResult.Yes)
                {
                    return 1;
                }
            }

            // --- 2) FINISH FROM A TEMPORARY COPY ---
            // Windows cannot delete a running program or a folder that is a working directory.
            string temp = Path.GetTempPath();
            Environment.CurrentDirectory = temp;
            if (FileOps.IsInside(self, root))
            {
                string copy = Path.Combine(temp, "3D-Drawing-Uninstall-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".exe");
                File.WriteAllBytes(copy, File.ReadAllBytes(self));
                ProcessStartInfo info = new ProcessStartInfo(copy,
                    "/uninstall /confirmed /dir=\"" + root + "\"" + (options.Silent ? " /silent" : ""));
                info.UseShellExecute = false;
                info.WorkingDirectory = temp;
                using (Process.Start(info))
                {
                }
                return 0;
            }

            // --- 3) REMOVE ---
            // Files go first; the marker and uninstaller stay until the end so a failed run can be retried.
            int exitCode = 0;
            try
            {
                Integration.EnsureNotRunning(root);
                FileOps.DeleteContents(root, new[] { AppInfo.MarkerFile, AppInfo.UninstallerName });
                Integration.Remove(root);
                FileOps.DeleteTree(root);
                Report(options, AppInfo.DisplayName + " was removed.", false);
            }
            catch (Exception ex)
            {
                if (!(ex is InstallException || ex is IOException || ex is UnauthorizedAccessException))
                {
                    throw;
                }
                Report(options, "Some files could not be removed: " + ex.Message +
                    "\n\nClose 3D Drawing and any files open from " + root + ", then uninstall again.", true);
                exitCode = 3;
            }

            // --- 4) DELETE THE TEMPORARY COPY AFTER EXIT ---
            if (FileOps.IsInside(self, temp))
            {
                DeleteAfterExit(self);
            }
            return exitCode;
        }

        private static void Report(Options options, string message, bool isError)
        {
            if (!options.Silent)
            {
                MessageBox.Show(message, Title, MessageBoxButtons.OK,
                    isError ? MessageBoxIcon.Warning : MessageBoxIcon.Information);
            }
        }

        private static void DeleteAfterExit(string path)
        {
            try
            {
                ProcessStartInfo info = new ProcessStartInfo(
                    Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe",
                    "/d /c ping -n 3 127.0.0.1 >nul & del /f /q \"" + path + "\"");
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                using (Process.Start(info))
                {
                }
            }
            catch (Win32Exception)
            {
            }
        }
    }
}
