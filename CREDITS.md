# Credits

Kejtu is [MIT licensed](LICENSE). Its dependencies keep their own licenses,
copyright notices and attribution.

The document workspace includes modified integrations of
[PdfCraft](https://github.com/storytold/pdfcraft) 0.9.0 (`645016a`) and
[WordCraft](https://github.com/storytold/wordcraft) 0.6.1 (`7dfa6b0`), by the
ArtCraft Team and contributors. Their document engines are upstream work;
Kejtu adds host communication, permissions, appearance and save-copy behavior.
These are modified integrations, not official ArtCraft distributions.

Original licenses, notices, dependency notices, font credits and build identities
are kept with [PdfCraft](desktop/static/private/pdfcraft/) and
[WordCraft](desktop/static/private/wordcraft/). Upstream attribution files describe
the upstream repositories; their relative source references refer to those projects.
The embedded bundles contain no ArtCraft logo artwork. Separate OCR weights are
not included.

Markdown editing combines [MDXEditor](https://github.com/mdx-editor/editor),
[CodeMirror](https://github.com/codemirror/dev), React and Lexical;
[their notices](desktop/static/private/markdown/LICENSES.txt) accompany the bundle.
The interface uses Svelte/SvelteKit, Tauri, KaTeX, Mermaid, LayerChart, D3,
Svelte Markdown, Marked and DOMPurify. Python owns state and execution.

Connected tools use the [MCP Python SDK license](ops/mcp/LICENSE-python-sdk),
[Playwright MCP license](ops/mcp/LICENSE-playwright) and
[filesystem server license](ops/mcp/filesystem/LICENSE).
[Exact versions and source records](ops/mcp/NOTICE-SOURCES.json) and the
[Python](ops/mcp/requirements.lock), [browser](ops/mcp/package-lock.json) and
[Files](ops/mcp/filesystem/package-lock.json) locks are included.
Downloaded Craft command-line tools retain their complete upstream notice files.
Their versions differ from the embedded editors and are pinned in the
[installer runtime manifest](packaging/source/runtime.lock.json).

Linux tool isolation uses [Bubblewrap](https://github.com/containers/bubblewrap),
by Alexander Larsson and contributors, under the LGPL. Where a compatible system
runtime cannot be used, setup builds the pinned upstream 0.13.1 source locally.
It retains the complete source archive, upstream `COPYING` and build manifest in
`.state/apps/bubblewrap/bubblewrap-0.13.1/`. The two executable names select tool
and browser policies; they are identical copies of the same build.

ChatGPT sign-in uses PyJWT, cryptography, cffi and pycparser;
[their license texts](docs/licenses/chatgpt/README.md) are included. Kejtu is
independent of OpenAI and includes no Codex installation.

Optional media uses separately installed mpv and yt-dlp. The broader dependency
sets are recorded in [Python requirements](ops/requirements.lock), the
[frontend lock](desktop/package-lock.json) and [Rust lock](desktop/src-tauri/Cargo.lock).
