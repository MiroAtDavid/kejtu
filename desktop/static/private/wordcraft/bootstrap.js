import init, * as bindings from './wordcraft-web-2d9b96c6f7bfd5df.js';
// Avoid WebKitGTK's failing cached/preloaded WASM path on reader reopen.
const response = await fetch('./wordcraft-web-2d9b96c6f7bfd5df_bg.wasm', {cache: 'no-store'});
if (!response.ok) throw new Error('Document editor runtime could not load.');
const wasm = await init({module_or_path: await response.arrayBuffer()});
window.wasmBindings = bindings;
dispatchEvent(new CustomEvent("TrunkApplicationStarted", {detail: {wasm}}));
