using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.RegularExpressions;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Install steps
    // ----------------------------------------------------------------------
    // Runs on a worker thread. Events fire on that thread; the wizard marshals them to the UI.
    internal sealed class Installer
    {
        public const int StepCount = 6;
        private static readonly Regex AnsiCodes = new Regex("\u001B\\[[0-?]*[ -/]*[@-~]");

        private readonly string _root;
        private readonly Options _options;
        private readonly InstallLog _log;
        private readonly object _buildLock = new object();
        private Process _build;
        private long _lastReported;
        private volatile bool _cancelled;

        public event Action<int, string> StepStarted;
        // Percent 0-100, or -1 for work without a measurable size.
        public event Action<int> ProgressChanged;
        public event Action<string> DetailChanged;

        public Installer(string root, Options options, InstallLog log)
        {
            _root = root;
            _options = options;
            _log = log;
        }

        public bool Cancelled
        {
            get { return _cancelled; }
        }

        // Safe to call from the UI thread; stops the build process tree if it is running.
        public void Cancel()
        {
            _cancelled = true;
            lock (_buildLock)
            {
                if (_build != null)
                {
                    KillTree(_build);
                }
            }
        }

        public static string Describe(Exception ex)
        {
            if (ex is OperationCanceledException)
            {
                return "Installation cancelled.";
            }
            if (ex is InstallException)
            {
                return ex.Message;
            }
            return "Unexpected error: " + ex.Message;
        }

        // ----------------------------------------------------------------------
        // Main sequence
        // ----------------------------------------------------------------------
        public void Run()
        {
            string temp = Path.Combine(_root, ".tmp");
            _log.Write(AppInfo.DisplayName + " setup " + AppInfo.Version + " -> " + _root);

            // --- 1) PREPARE THE FOLDER ---
            Begin(1, "Preparing the install folder");
            PrepareFolder();
            _log.AttachFile(Path.Combine(_root, AppInfo.LogName));
            Directory.CreateDirectory(temp);

            // --- 2) KIT APP TEMPLATE ---
            Begin(2, "Downloading NVIDIA's Kit App Template " + AppInfo.TemplateVersion);
            string templateTop = FetchAndExtract(_options.TemplateZip, temp, "t");
            FileOps.MoveContents(templateTop, _root, null);
            RequireFile(Path.Combine(_root, "repo.bat"), "The template download is missing repo.bat.");

            // --- 3) APP SOURCE REPLACES THE TEMPLATE'S source FOLDER ---
            Begin(3, "Downloading 3D Drawing from GitHub");
            string sourceTop = FetchAndExtract(_options.SourceZip, temp, "s");
            string sourceDir = Path.Combine(_root, "source");
            FileOps.DeleteTree(sourceDir);
            FileOps.MoveContents(sourceTop, sourceDir, AppInfo.SourceExcludes);
            RequireFile(Path.Combine(sourceDir, "apps", AppInfo.KitFile),
                "The app download is missing apps/" + AppInfo.KitFile + ".");

            // --- 4) CONFIGURE ---
            Begin(4, "Configuring the build");
            TemplatePatcher.AddApp(_root, AppInfo.KitFile);
            // Breadcrumb the template tools use to record license acceptance.
            File.WriteAllText(Path.Combine(_root, ".omniverse_eula_accepted.txt"), "");
            FileOps.DeleteTree(temp);
            _log.Write("Registered " + AppInfo.KitFile + " in premake5.lua and repo.toml.");

            // --- 5) BUILD ---
            Begin(5, "Building the app");
            Detail("Downloads the Kit SDK and extensions. The first install takes about 10-30 minutes.");
            RunBuild();

            // --- 6) SHORTCUTS AND UNINSTALL ENTRY ---
            Begin(6, "Creating shortcuts");
            Integration.Install(_root, _options.DesktopShortcut, _log);
            Progress(100);
            _log.Write("Installation complete.");
        }

        // ----------------------------------------------------------------------
        // Steps
        // ----------------------------------------------------------------------
        private void PrepareFolder()
        {
            if (Directory.Exists(_root) && FileOps.IsInstallFolder(_root))
            {
                // Previous install: keep the marker until the end so an interrupted run can still be replaced.
                Integration.EnsureNotRunning(_root);
                _log.Write("Replacing the previous installation.");
                FileOps.DeleteContents(_root, new[] { AppInfo.MarkerFile });
            }
            else
            {
                string fullPath;
                string error = FileOps.ValidateTarget(_root, out fullPath);
                if (error != null)
                {
                    throw new InstallException(error);
                }
                Directory.CreateDirectory(_root);
            }
            File.WriteAllText(Path.Combine(_root, AppInfo.MarkerFile),
                AppInfo.DisplayName + " " + AppInfo.Version + Environment.NewLine);
        }

        // Downloads (or copies) a zip, extracts it, and returns its single top-level folder.
        private string FetchAndExtract(string source, string temp, string name)
        {
            string zip = Path.Combine(temp, name + ".zip");
            string target = Path.Combine(temp, name);
            _lastReported = -1;
            Downloader.Fetch(source, zip, _log, ReportDownload, delegate { return _cancelled; });
            ThrowIfCancelled();

            Detail("Unpacking");
            Progress(-1);
            FileOps.DeleteTree(target);
            ZipFile.ExtractToDirectory(zip, target);
            File.Delete(zip);
            ThrowIfCancelled();

            // GitHub archives wrap everything in one folder, e.g. kit-app-template-<commit>.
            string[] entries = Directory.GetFileSystemEntries(target);
            return entries.Length == 1 && Directory.Exists(entries[0]) ? entries[0] : target;
        }

        private void RunBuild()
        {
            // --- 1) START repo.bat build -r ---
            string repoBat = Path.Combine(_root, "repo.bat");
            string shell = Environment.GetEnvironmentVariable("ComSpec");
            if (string.IsNullOrEmpty(shell))
            {
                shell = "cmd.exe";
            }
            ProcessStartInfo info = new ProcessStartInfo(shell, "/d /s /c \"\"" + repoBat + "\" build -r\"");
            info.WorkingDirectory = _root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardInput = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            // Stream Python tool output line by line, as UTF-8.
            info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            // `repo build_number` asks git for the branch and hash and exits when git isn't installed.
            // A complete version (one containing '+') is used as-is, without git.
            info.EnvironmentVariables["BUILD_NUMBER"] = AppInfo.TemplateVersion + "+main.0.setup.local";
            _log.Write("Running: repo.bat build -r");

            // --- 2) STREAM OUTPUT UNTIL EXIT ---
            int exitCode;
            using (Process process = new Process())
            {
                process.StartInfo = info;
                process.OutputDataReceived += OnBuildOutput;
                process.ErrorDataReceived += OnBuildOutput;
                lock (_buildLock)
                {
                    ThrowIfCancelled();
                    process.Start();
                    _build = process;
                }
                // Any prompt reads end-of-input instead of waiting forever.
                process.StandardInput.Close();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                process.WaitForExit();
                lock (_buildLock)
                {
                    _build = null;
                }
                exitCode = process.ExitCode;
            }
            ThrowIfCancelled();

            // --- 3) VERIFY ---
            if (exitCode != 0)
            {
                throw new InstallException("The build failed (exit code " + exitCode + "). The install log has the details.");
            }
            RequireFile(AppInfo.LaunchScript(_root), "The build finished without creating " + AppInfo.KitFile + ".bat.");
        }

        // ----------------------------------------------------------------------
        // Helpers
        // ----------------------------------------------------------------------
        private void OnBuildOutput(object sender, DataReceivedEventArgs e)
        {
            if (e.Data != null)
            {
                _log.Write(AnsiCodes.Replace(e.Data, ""));
            }
        }

        private void ReportDownload(long done, long total)
        {
            // Report every 256 KB so the UI queue stays small.
            if (_lastReported >= 0 && done - _lastReported < 256 * 1024 && done != total)
            {
                return;
            }
            _lastReported = done;
            if (total > 0)
            {
                Progress((int)(done * 100 / total));
                Detail(FileOps.FormatBytes(done) + " of " + FileOps.FormatBytes(total));
            }
            else
            {
                Progress(-1);
                Detail(FileOps.FormatBytes(done) + " downloaded");
            }
        }

        private static void KillTree(Process process)
        {
            try
            {
                if (process.HasExited)
                {
                    return;
                }
                ProcessStartInfo info = new ProcessStartInfo("taskkill.exe", "/PID " + process.Id + " /T /F");
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                using (Process.Start(info))
                {
                }
            }
            catch (InvalidOperationException)
            {
            }
            catch (Win32Exception)
            {
            }
        }

        private static void RequireFile(string path, string message)
        {
            if (!File.Exists(path))
            {
                throw new InstallException(message);
            }
        }

        private void ThrowIfCancelled()
        {
            if (_cancelled)
            {
                throw new OperationCanceledException();
            }
        }

        private void Begin(int step, string title)
        {
            ThrowIfCancelled();
            _log.Write("== Step " + step + " of " + StepCount + ": " + title);
            Action<int, string> started = StepStarted;
            if (started != null)
            {
                started(step, title);
            }
            Progress(-1);
        }

        private void Progress(int percent)
        {
            Action<int> changed = ProgressChanged;
            if (changed != null)
            {
                changed(percent);
            }
        }

        private void Detail(string text)
        {
            Action<string> changed = DetailChanged;
            if (changed != null)
            {
                changed(text);
            }
        }
    }
}
