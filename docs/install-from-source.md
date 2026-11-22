# Building Kejtu from source on Manjaro

For a prebuilt Debian or Arch package, follow the [Linux installation guide](install.md).
These source-build instructions cover 64-bit Manjaro. The installer downloads its
dependencies and builds the desktop app in the folder you choose. Keep that
folder at the same location after installation.

The Manjaro GNOME test machine passed fresh installation, updates, first launch,
offline Help and close/reopen, plus file permissions, browser and document checks.
Other desktop environments have not been verified here. Earlier Ubuntu checks
are retained below; Ubuntu, Windows and macOS are not maintenance commitments
for this release. No platform implementation has been removed.

For optional search by meaning, follow the [memory search setup](install.md#memory-search-requires-a-separate-gpu-setup).

## Requirements

Run setup as yourself, without putting `sudo` before the installer command. You
need an internet connection and enough disk space to build the app and download
its tools. Python 3.14+, Node 22.12+/npm, Rust/Cargo 1.93+, Bubblewrap,
GTK/WebKit development libraries and the other distribution packages are checked
by the installer. Missing system packages need `sudo`; application files and
runtimes stay in this directory.
If a suitable system Bubblewrap cannot be used, setup builds a pinned version
locally using Meson, Ninja and libcap development files, also checked by the installer.
The Manjaro build was checked with 8 GB RAM and two build jobs; its installation
used about 5.3 GB, excluding system packages and shared toolchain caches.

On Ubuntu, setup also uses `sudo` for a per-installation AppArmor profile covering
the shared Chromium headless shell and two sandbox launcher paths. Ordinary
tools retain their restrictions; the browser launcher permits Chromium's nested
sandbox. Ubuntu's global restrictions stay enabled. These are user-writable paths,
and another program under the same account could invoke the browser launcher.
Custom browser selections are preserved; setup grants no policy to arbitrary browsers.

Files, page reading, the temporary browser and document tools are included in
setup. Page reading and the temporary browser share one Chromium download and run in separate sessions.
Linux setup also includes Quick Terminal and Terminal Use. Turn on each one separately in
**Shelf → Installed → Connections**. Quick Terminal uses Kejtu’s Python. Terminal Use
uses system Node.js 22.12 or newer. The document tools download separately.
No model weights or API account are included. Your provider may charge for requests.

## Install and start

If Python or Git is missing, install them with
`sudo pacman -S --needed python git`. Use Manjaro's normal update procedure if
its package lists are stale.

Download or clone the release repository into its final location. Keep this
checkout there; environments and saved tool paths refer to it. Run the plan
first, then install from a terminal in the desktop session:

```sh
python3 packaging/source/install.py
python3 packaging/source/install.py --install
```

Use `--jobs 2` to limit parallel build jobs. Rerunning the installer repairs missing dependencies without replacing
your data or saved access choices. It refuses to build while this copy is running.

Setup also adds **Kejtu** to your application menu, using the existing icon.
The shortcut initializes a new workspace once and reopens it on later launches.
It calls the same managed commands below. If Kejtu is already running, it does
not start another copy.

For the first launch from a terminal:

```sh
./kejtu initialize
```

Connect a model in Settings. Close the first window normally, then use:

```sh
./kejtu start
./kejtu status
```

The current desktop retains its development identity and local port 1431; run one
copy at a time. Built-in [Help](../src/kejtu_next/help/01-start.md) is also available
under Settings → Help.

The shortcut is saved as
`~/.local/share/applications/dev.kejtu.next.development.desktop`; an absolute
`XDG_DATA_HOME` replaces `~/.local/share`. Its icon points to this checkout's
existing PNG, so keep the checkout at its installed path. A shortcut from another
checkout or one you edited is preserved; use `--no-desktop-entry` when installing
a second test copy. To register an already installed copy without rebuilding:

```sh
python3 packaging/source/desktop.py install
```

## Update, backup and remove

Save all drafts and finish any running reply before updating:

```sh
./kejtu stop --composer-empty
./kejtu backup
git pull --ff-only
python3 packaging/source/install.py --install
./kejtu start
```

Use the same directory; do not copy another installation's private state into it.
The installer can be rerun after an interrupted download or build. It does not
perform an automatic source downgrade or retry uncertain document operations.

Conversations, backend settings, grants, cached runtimes and backups are under
`.state/` in this checkout. `./kejtu backup` saves the database and selected
configuration there; it is not a complete application backup.
Appearance preferences, avatars, some drafts and pending recovery records live in
`~/.local/share/dev.kejtu.next.development/`. Window geometry lives in
`~/.config/dev.kejtu.next.development/.window-state.json`. An absolute
`XDG_DATA_HOME` or `XDG_CONFIG_HOME` replaces the corresponding base directory.
Back up these desktop directories with Kejtu stopped as well; they are shared by
source checkouts using this desktop identity.
API keys use the desktop keyring. ChatGPT sign-in tokens are stored in private,
permission-protected files under `.state/runtime/chatgpt/`; those files are not
keyring-encrypted. Treat a full `.state/` backup as sensitive.
`./kejtu backup` leaves every connection key out of its copy of the connection
settings, including one an earlier version saved in that file. The copy keeps the
field names, and `receipt.json` lists what has to be entered again after a
restore. A copy of the whole `.state/` folder still holds the live settings file.

A backup also takes each installed package's own data, never its program files
or caches. A package's data is left out only if you cleared **Include its data in backups**
on that package in Shelf → Installed; `receipt.json` and the command's output name
each package with its size either way. A backup is refused while a tool of a
package is running.

To put a backup into a checkout that has no archive yet:

```sh
./kejtu restore /full/path/to/.state/backups/<backup folder>
./kejtu start
```

Restore never writes over an existing archive or settings, and it accepts only a
backup made by the same archive format. It brings back the archive, the
settings, the launch profile and each package's data. It prints what is still to
do, and Packages shows the same list: packages whose program files have to be
installed again, accounts to sign into again, keys to enter again, and package
data that was left out. Connections stay off until you enable them. Account
passwords, API keys and login tokens are not restored; sign in or enter keys
yourself on the new installation. Restore never reads or copies the keyring.
Keep the managed backup and your shared documents before removing the application;
shared documents live in the folders you chose. Removing this checkout removes
its local archive and backups, but leaves the desktop directories, shared
documents, keyring entries and system dependencies in place. Keep the copied
backups somewhere safe; desktop storage can contain unsaved private text too.

Before removing the checkout, remove its application-menu entry:

```sh
python3 packaging/source/desktop.py remove
```

This removes only the unchanged entry registered by this copy. It preserves an
edited entry, the icon source, app data and credentials. The small registration
receipt and coordination lock may remain; neither launches anything.

On Ubuntu, setup prints its exact `/etc/apparmor.d/kejtu-source-…` profile path.
To remove that policy after stopping Kejtu, run the following with that exact
path, then remove the same file with `sudo rm -- /etc/apparmor.d/kejtu-source-EXACT_HASH`:

```sh
sudo /usr/sbin/apparmor_parser --remove --skip-cache /etc/apparmor.d/kejtu-source-EXACT_HASH
```

Keep the policy when reinstalling at the same path. Do not use a wildcard or
disable AppArmor. The installer recreates a missing profile when needed.

## Limits

- Both VMs passed Files read/write and permission denials, private desktop-data
  protection, both browser tools, spreadsheet/word-processing operations and PDF
  creation/read-back. Editing existing PDF content remains unverified here.
- The Manjaro test machine's password vault stayed locked after reboot, so saving
  and retrieving API keys was not verified in that session. An earlier test did
  verify key access. See the password-vault note below if an API key cannot be saved.

- Model calls and enabled web tools send selected information to their services.
  Local storage does not make remote inference private. File access still needs
  your saved folder permissions; installation does not grant folders.
- Small-context model configurations can refuse a turn when required instructions
  and tool results will not fit. Kejtu does not silently truncate required material.
  Focused release checks pass; the broader development test suite still has
  unresolved failures. This is not a claim that the whole application is tested.
- Account sign-in, paid APIs and GPU embeddings require separate acceptance on
  suitable hardware/accounts. No guaranteed search results or automatic updater
  is provided.
- This is a source installation, not a portable binary or a distribution package.
  Test and development-history files are intentionally absent from this release
  repository. `RELEASE.json` identifies the canonical source and exported files.

[Credits and licenses](../CREDITS.md) accompany the application.

## If an API key cannot be saved

Kejtu saves API keys in your desktop's password vault. On Linux this uses an
interface called Secret Service, commonly provided by GNOME Keyring. Your desktop
will usually unlock its vault when you log in. If Kejtu reports that it cannot
save or retrieve a key, open your desktop's password manager and unlock the vault,
then try the setting again. Setup does not reset its password.

You do not need an unlocked password vault just to open Kejtu or use a local
model. ChatGPT subscription sign-in uses the separate private files described
under backups above.

## Earlier Ubuntu checks

The unchanged installer also accepts Ubuntu 26.04 on x86-64. Earlier VM checks
passed fresh installation, failed-setup recovery, update, offline Help and
close/reopen. A populated upgrade and reinstall preserved saved data, and the
existing test API-key credential remained readable. The file, browser and
document checks above passed there too. The Ubuntu-specific setup and removal
notes in this guide are retained for those installations; this release's
maintenance focus is Manjaro.
