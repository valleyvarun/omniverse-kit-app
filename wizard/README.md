# 3D Drawing setup wizard

`dist/3D-Drawing-Setup.exe` is the Windows installer that the download page (`../index.html`) links to.
The website calls the app "Drawing Intelligence"; the installer, shortcuts, and Settings entry use "3D Drawing (WIP)".

## What it does

1. Downloads NVIDIA's [kit-app-template](https://github.com/NVIDIA-Omniverse/kit-app-template), pinned to the
   `109.0.3` branch commit in `src/AppInfo.cs`.
2. Replaces the template's `source/` folder with the `main` branch of this repo. Website and installer files
   (`index.html`, `css/`, `js/`, `imgs/`, `wizard/`, `.nojekyll`) are left out.
3. Adds `define_app("3d-drawing.kit")` to `premake5.lua` and sets the `[repo_precache_exts]` apps list in
   `repo.toml`, the same changes `repo template new` makes.
4. Runs `repo.bat build -r`.
5. Adds a desktop shortcut and a Start menu entry with the plexus logo as the icon. Registers an uninstaller in
   Settings > Apps.

The running app's window and taskbar icon is the same logo: `app.window.iconPath` in `apps/3d-drawing.kit` points to
`extensions/varun.drawing.layout.v.01/data/icon.png`.

The default install folder is `%LOCALAPPDATA%\Programs\3D-Drawing`. If that path has a space (for example, a user
name like `Varun SA`), the default is `C:\3D-Drawing`, because premake and the repo tools fail on spaces.

## Publishing updates

- **App changes:** push to `main`. The installer always downloads the latest `main`, so you don't need to rebuild
  it. Users update by running the installer again.
- **Native viewport plugin:** `viewport_light.plugin.dll` is committed, because installed copies can't compile it.
  After changing the native code, rebuild it with `viewport_light/scripts/build_native.py` and commit the new DLL.
- **Installer changes:** run `build.bat`, then commit `dist/3D-Drawing-Setup.exe` and `icon/`.
- **Newer template:** update `TemplateCommit` and `TemplateVersion` in `src/AppInfo.cs` (get the commit with
  `git ls-remote https://github.com/NVIDIA-Omniverse/kit-app-template refs/heads/<version>`), then rebuild.

## Building

```bat
wizard\build.bat
```

It uses the C# compiler built into Windows (.NET Framework 4.8), so you don't need an SDK. It converts
`../imgs/plexus-logo.png` into `icon/app.ico` and `icon/app.png` with `icon/MakeIcon.cs`, then compiles `src/*.cs`
with warnings treated as errors. To change the icon, replace that logo (any square PNG with transparency), rebuild,
and copy `icon/app.png` to `extensions/varun.drawing.layout.v.01/data/icon.png` for the window icon.

## Command line

| Command | Effect |
| --- | --- |
| `3D-Drawing-Setup.exe` | Wizard |
| `3D-Drawing-Setup.exe /silent /accepteula [/dir=C:\path] [/nodesktop]` | Unattended install |
| `/sourcezip=PATH-or-URL`, `/templatezip=PATH-or-URL` | Install from other archives, for example a local zip of unpushed changes |
| `uninstall.exe` or `/uninstall [/silent] [/dir=C:\path]` | Uninstall |

Silent exit codes: `0` installed, `1` cancelled, `2` bad folder or options, `3` failed, `4` license not accepted.
The log is `install.log` in the install folder, or `%TEMP%\3D-Drawing-Setup.log` if setup fails before creating
that folder.

To test unpushed changes, zip the repo the same way GitHub does, then install from that zip:

```bash
cp .git/index /tmp/idx && GIT_INDEX_FILE=/tmp/idx git add -A
git archive --format=zip --prefix=omniverse-kit-app-main/ -o local.zip $(GIT_INDEX_FILE=/tmp/idx git write-tree)
```

Uninstalling removes the install folder, the shortcuts, and the Settings entry. The Kit SDK and extensions stay
in `C:\packman-repo` and `%LOCALAPPDATA%\ov` because other Kit apps share them. Deleting removes junctions
without entering them, so those shared caches are never affected.
