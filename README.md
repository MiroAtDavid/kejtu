# Kejtu

Kejtu is a personal AI workspace. Chat with a model, keep notes, organise projects
and tasks, research a topic, and work with documents in one desktop app.

Your conversations and notes are saved on your computer. You choose which AI
service or local model to connect. If you use an online service, it receives the
messages and context needed to answer you. You choose which folders Kejtu can use.

## Install on Manjaro

This is an early release for 64-bit Manjaro. You install it from source, so the
first setup takes time and needs an internet connection.

Download or clone this repository into the folder where you want to keep Kejtu.
Open a terminal in that folder and run:

```sh
python3 packaging/source/install.py --install
```

The installer downloads the tools Kejtu needs and builds the app. It may ask for
your password to install missing system packages. If Python or Git is missing,
install them first with `sudo pacman -S --needed python git`.

When setup finishes, open **Kejtu** from your application menu. In **Settings**,
connect your preferred AI service or local model. No model or paid account is
included. Help is available inside the app under **Settings → Help**.

> **Memory search by meaning requires a separate GPU setup.** The installer
> does not include it. See the [memory search requirements](docs/install.md#memory-search-requires-a-separate-gpu-setup).

Keep the installation folder where it is. See the [installation guide](docs/install.md)
for updates, backups and help with setup.

## Still in development

Expect bugs and unfinished parts. Video playback and editing existing PDFs still
need testing.
Keep backups of anything important.

Manjaro is the focus of this release. Other platforms are not maintained as part
of it. Community ports and contributions are welcome.

[Documentation](docs/README.md) · [Credits](CREDITS.md) · [MIT license](LICENSE)
