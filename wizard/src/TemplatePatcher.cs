using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Register the app with the template build
    // ----------------------------------------------------------------------
    // Mirrors what `repo template new` does for an app: a define_app line in premake5.lua
    // and the .kit file in repo.toml's precache list.
    internal static class TemplatePatcher
    {
        private static readonly Encoding Utf8 = new UTF8Encoding(false);

        public static void AddApp(string root, string kitFile)
        {
            AddDefineApp(Path.Combine(root, "premake5.lua"), kitFile);
            SetPrecacheApps(Path.Combine(root, "repo.toml"), kitFile);
        }

        // --- 1) premake5.lua ---
        private static void AddDefineApp(string path, string kitFile)
        {
            string text = File.ReadAllText(path);
            string newline = text.Contains("\r\n") ? "\r\n" : "\n";
            string pattern = "define_app\\(\\s*\"" + Regex.Escape(kitFile) + "\"\\s*\\)";
            if (Regex.IsMatch(text, pattern))
            {
                return;
            }
            if (!text.EndsWith("\n"))
            {
                text += newline;
            }
            text += "define_app(\"" + kitFile + "\")" + newline;
            File.WriteAllText(path, text, Utf8);
        }

        // --- 2) repo.toml [repo_precache_exts] apps ---
        private static void SetPrecacheApps(string path, string kitFile)
        {
            string text = File.ReadAllText(path);
            string newline = text.Contains("\r\n") ? "\r\n" : "\n";
            List<string> lines = new List<string>(text.Replace("\r\n", "\n").Split('\n'));

            // Find the section, then its apps array (which may span several lines).
            int section = lines.FindIndex(delegate(string line) { return line.Trim() == "[repo_precache_exts]"; });
            if (section < 0)
            {
                throw new InstallException("repo.toml has no [repo_precache_exts] section.");
            }
            int start = -1;
            for (int i = section + 1; i < lines.Count; i++)
            {
                string trimmed = lines[i].TrimStart();
                if (trimmed.StartsWith("["))
                {
                    break;
                }
                if (Regex.IsMatch(trimmed, "^apps\\s*="))
                {
                    start = i;
                    break;
                }
            }
            if (start < 0)
            {
                throw new InstallException("repo.toml has no apps list in [repo_precache_exts].");
            }
            int end = start;
            while (end < lines.Count && !lines[end].Contains("]"))
            {
                end++;
            }
            if (end >= lines.Count)
            {
                throw new InstallException("repo.toml has an unterminated apps list.");
            }

            lines.RemoveRange(start, end - start + 1);
            lines.Insert(start, "apps = [\"${root}/source/apps/" + kitFile + "\"]");
            File.WriteAllText(path, string.Join(newline, lines), Utf8);
        }
    }
}
