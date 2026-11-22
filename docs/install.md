# Install Kejtu on Linux

The experimental Linux prerelease includes packages for 64-bit Intel and AMD
computers. Download the file for your distribution, then open a terminal in the
folder where you saved it. [Download Kejtu for Linux](https://github.com/MiroAtDavid/kejtu/releases/tag/v0.0.1-preview.2).

## Debian-based systems

Download the `.deb` file. This build requires glibc 2.39 or newer.

```sh
sudo apt install ./kejtu_0.0.1+20261122165426.g0cab185-1_amd64.deb
```

## Arch-based systems

Download the `.pkg.tar.zst` file.

```sh
sudo pacman -U ./kejtu-0.0.1+20261122165426.g0cab185-1-x86_64.pkg.tar.zst
```

Open **Kejtu** from your applications menu. The welcome screen helps you connect
the model that writes your replies. You can use keyword search in your permitted
conversation history without setting up an embedding model.

## Memory search requires a separate GPU setup

Searching by meaning can find an earlier conversation even when you use different
words. This optional feature uses Ollama on your computer, including when an
online model writes your replies. The Debian and Arch packages leave Ollama and
the embedding model for you to install separately.

1. Install Ollama on the same computer as Kejtu and start it at its default local
   address, `http://127.0.0.1:11434`. Use a GPU and an Ollama runtime that can
   run this exact model. [Ollama for Linux](https://docs.ollama.com/linux).
2. Download the model Kejtu uses to search memory.

   ```sh
   ollama pull embeddinggemma-2:270m-bf16-text
   ```

3. Run `ollama ls`. The model ID should begin with `afeb999480b2`. Leave Ollama
   running and let Kejtu sit idle so it can load the model. Run `ollama ps` to
   see whether the model is loaded and using the GPU.

Kejtu checks the model's full digest before using it. If the model is missing,
has a different digest, or cannot run, keyword search still works. This
prerelease has no supported CPU fallback for search by meaning. Its package
tests used a simulated model and did not exercise GPU memory search.
[Required model and digest](../src/kejtu_next/core/recall.py).

## Updates and backups

Save your drafts and finish active work before updating. Install the newer
package with the same `apt install` or `pacman -U` command, using the new file
name. Reopen Kejtu from your applications menu.

The packages keep your data under `~/.local/share/kejtu` by default. Run
`kejtu status` to see the location for your installation. With Kejtu closed, run
`kejtu backup` and keep a copy of the backup and any documents in shared
folders. Credentials in the desktop keyring need their own backup. Removing
the package preserves your Kejtu data.

For checkout-specific build commands, backups and removal instructions, see
[Building Kejtu from source on Manjaro](install-from-source.md).
