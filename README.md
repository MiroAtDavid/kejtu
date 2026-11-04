# Kejtu — early Linux source release

A personal workspace for conversations, notes, projects, tasks, research and
documents. You connect the AI provider or local model you want to use.
This is experimental software: useful already, but rough edges remain.

This recipe targets **x86-64 Ubuntu 26.04 and Manjaro**. Verification of this
release candidate is still in progress; no completed fresh-install or update
acceptance is claimed yet. Other distributions, Windows and macOS are outside
this source release.

## Requirements

Use a normal desktop account with an unlocked Secret Service keyring, an internet
connection and enough disk space for a Rust build and downloaded tools. Python
3.14+, Node/npm, Rust/Cargo, Bubblewrap, GTK/WebKit development libraries and the
other distribution packages are checked by the installer. Missing system packages
need `sudo`; application files and runtimes stay in this directory.

Files, page reading, the temporary browser and document tools are included in
setup. Chromium runtimes and the pinned document tools are separate downloads;
no model weights or API account are included. `--media` also prepares optional
radio/video dependencies. Your provider may charge for requests.

## Install and start

Clone the private release repository with Git into its final location. Keep this
checkout there; environments and saved tool paths refer to it. Run the plan
first, then install from a terminal in the desktop session:

```sh
python3 packaging/source/install.py
python3 packaging/source/install.py --install
```

Add `--media` for optional playback dependencies. Use `--jobs 2` to limit parallel
build jobs. Rerunning the installer repairs missing dependencies without replacing
your data or saved access choices. It refuses to build while this copy is running.

For the first launch:

```sh
./kejtu initialize
```

Connect a model in Settings. Close the first window normally, then use:

```sh
./kejtu start
./kejtu status
```

The current desktop retains its development identity and local port 1431; run one
copy at a time. Built-in [Help](src/kejtu_next/help/01-start.md) is also available
under Settings → Help.

## Update, backup and remove

Save all drafts and finish any running reply before updating:

```sh
./kejtu stop --composer-empty
./kejtu backup
git pull --ff-only
python3 packaging/source/install.py --install
./kejtu start
```

If you installed media dependencies, keep `--media` on the install command.
Use the same directory; do not copy another installation's private state into it.
The installer can be rerun after an interrupted download or build. It does not
perform an automatic source downgrade or retry uncertain document operations.

Conversations, settings, grants, cached runtimes and backups are under `.state/`
in this checkout. `./kejtu backup` saves the database and selected configuration there; it is not
a complete copy of all runtime files.
API keys use the desktop keyring. ChatGPT sign-in tokens are stored in private,
permission-protected files under `.state/runtime/chatgpt/`; those files are not
keyring-encrypted. Treat a full `.state/` backup as sensitive.
Back up the stopped checkout's entire `.state/` and your shared documents before
removing the application; shared documents live in the folders you chose.
Keyring credentials need their own system backup. Removing this checkout removes
its local data and backups, but does not uninstall system dependencies or delete
shared documents or keyring entries. Keep the copied backup somewhere safe.

## Limits

- Local conversations, notebook, projects, tasks, research, document editors and
  controlled tools are implemented. A feature being present does not establish
  that every provider, file format or device has been tested.
- Model calls and enabled web tools send selected information to their services.
  Local storage does not make remote inference private. File access still needs
  your saved folder permissions; installation does not grant folders.
- Semantic recall needs the separately configured compatible GPU embedding
  runtime and exact model. It is not installed automatically. CPU fallback is
  deferred; the installer does not substitute a model or rebuild the index under
  another model. The CPU-only test VMs cannot verify GPU embedding behavior.
- Small-context model configurations can refuse a turn when required instructions
  and tool results will not fit. Kejtu does not silently truncate required material.
- Account sign-in, paid APIs, GPU embeddings and audible media output require
  separate acceptance on suitable hardware/accounts. No bundled media content,
  guaranteed search results or automatic updater is provided.
- This is a source installation, not a portable binary or a distribution package.
  Test and development-history files are intentionally absent from this release
  repository. `RELEASE.json` identifies the canonical source and exported files.

[Credits and licenses](CREDITS.md) accompany the application.
