# Credits

Kejtu is [MIT licensed](LICENSE). Its dependencies keep their own licenses,
copyright notices and attribution.

The Word reader uses [docx-preview](https://github.com/VolodymyrBaydalka/docxjs) by
Volodymyr Baydalka and [JSZip](https://github.com/Stuk/jszip). Their
[licenses and dependency notices](desktop/static/private/word-preview/LICENSES.txt) accompany the reader.

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
[Terminal Use](https://github.com/computer-agent-labs/terminal-use) 0.2.1 by Computer
Agent Labs, Inc. is MIT licensed. Kejtu adds three command tools. The installed
package retains its MIT license, the JetBrains Mono font license in
`fonts/OFL.txt`, and the notices shipped with its dependencies. Quick Terminal
is part of Kejtu and uses the shared MCP Python SDK.

GridCraft, PdfCraft and WordCraft by the ArtCraft Team and contributors provide
command-line tools and retain their upstream notices. Versions are pinned in the
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

The broader dependency sets are recorded in [Python requirements](ops/requirements.lock), the
[frontend lock](desktop/package-lock.json) and [Rust lock](desktop/src-tauri/Cargo.lock).
