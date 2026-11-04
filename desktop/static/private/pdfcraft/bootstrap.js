import init, * as bindings from './pdfcraft-web-f030fd1e70a40ba2.js';
// Avoid WebKitGTK's failing cached/preloaded WASM path on reader reopen.
const response = await fetch('./pdfcraft-web-f030fd1e70a40ba2_bg.wasm', {cache: 'no-store'});
if (!response.ok) throw new Error('PDF reader runtime could not load.');
const wasm = await init({module_or_path: await response.arrayBuffer()});
window.wasmBindings = bindings;
dispatchEvent(new CustomEvent("TrunkApplicationStarted", {detail: {wasm}}));
