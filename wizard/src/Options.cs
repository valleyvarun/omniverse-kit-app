using System;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Command-line options
    // ----------------------------------------------------------------------
    // 3D-Drawing-Setup.exe                          wizard
    // 3D-Drawing-Setup.exe /silent /accepteula      unattended install
    //     [/dir=PATH] [/nodesktop] [/templatezip=PATH|URL] [/sourcezip=PATH|URL]
    // uninstall.exe /uninstall [/silent]            remove an installation
    internal sealed class Options
    {
        public bool Silent;
        public bool Uninstall;
        public bool AcceptEula;
        // Skips the uninstall confirmation after the uninstaller relaunches itself.
        public bool Confirmed;
        public bool DesktopShortcut = true;
        public string InstallDir;
        public string TemplateZip = AppInfo.TemplateZipUrl;
        public string SourceZip = AppInfo.SourceZipUrl;

        public const string Usage =
            "Usage:\n" +
            "  3D-Drawing-Setup.exe\n" +
            "  3D-Drawing-Setup.exe /silent /accepteula [/dir=PATH] [/nodesktop]\n" +
            "  uninstall.exe /uninstall [/silent]";

        public static Options Parse(string[] args)
        {
            Options options = new Options();
            foreach (string arg in args)
            {
                // --- 1) SPLIT /name=value ---
                string name = arg;
                string value = null;
                int equals = arg.IndexOf('=');
                if (equals > 0)
                {
                    name = arg.Substring(0, equals);
                    value = arg.Substring(equals + 1);
                }

                // --- 2) APPLY ---
                switch (name.TrimStart('/', '-').ToLowerInvariant())
                {
                    case "silent":
                        options.Silent = true;
                        break;
                    case "uninstall":
                        options.Uninstall = true;
                        break;
                    case "accepteula":
                        options.AcceptEula = true;
                        break;
                    case "confirmed":
                        options.Confirmed = true;
                        break;
                    case "nodesktop":
                        options.DesktopShortcut = false;
                        break;
                    case "dir":
                        options.InstallDir = RequireValue(arg, value);
                        break;
                    case "templatezip":
                        options.TemplateZip = RequireValue(arg, value);
                        break;
                    case "sourcezip":
                        options.SourceZip = RequireValue(arg, value);
                        break;
                    default:
                        throw new ArgumentException("Unknown option: " + arg);
                }
            }
            return options;
        }

        private static string RequireValue(string arg, string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                throw new ArgumentException("Missing value: " + arg);
            }
            return value.Trim();
        }
    }
}
