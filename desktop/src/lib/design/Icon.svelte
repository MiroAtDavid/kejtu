<script lang="ts">
  export let name = "file";
  export let size = 16;
  const paths: Record<string, string> = {
    home: "M3 10 12 3l9 7v11h-6v-7H9v7H3Z",
    folder: "M3 5h7l2 3h9v12H3Z",
    code: "m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18",
    compare: "M4 3h16v18H4ZM12 3v18M6 8h4M14 14h4M16 12v4",
    text: "M4 5h16M12 5v14M8 19h8",
    comment: "M4 4h16v12H9l-5 4ZM7 8h10M7 12h7",
    "zoom-in": "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm5 12 6 6M7 10h6M10 7v6",
    "zoom-out": "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm5 12 6 6M7 10h6",
    save: "M5 3h12l4 4v14H3V3Zm2 0v6h10V3M7 21v-8h10v8",
    undo: "M9 5 4 10l5 5M4 10h10a6 6 0 0 1 0 12",
    redo: "m15 5 5 5-5 5M20 10H10a6 6 0 0 0 0 12",
    tasks: "M9 5h12M9 12h12M9 19h12M2 5l1 1 2-2M2 12l1 1 2-2M2 19l1 1 2-2",
    search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm5 12 6 6",
    play: "M8 5l11 7-11 7Z",
    pause: "M9 5v14M15 5v14",
    stop: "M6 6h12v12H6Z",
    sidebar: "M9 4v16M4 4h16v16H4Z",
    calendar: "M4 5h16v15H4ZM8 3v4M16 3v4M4 10h16M8 14h2M14 14h2",
    book: "M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15",
    file: "M14 3H5v18h14V8Zm0 0v5h5M8 12h8M8 16h6",
    widgets: "M3 3h7v7H3ZM14 3h7v7h-7ZM3 14h7v7H3ZM14 14h7v7h-7Z",
    plus: "M12 5v14M5 12h14",
    close: "m6 6 12 12M18 6 6 18",
    "arrow-up": "M12 19V5m-6 6 6-6 6 6",
    arrow: "M4 12h15m-6-6 6 6-6 6",
    left: "m15 6-6 6 6 6",
    down: "m7 10 5 5 5-5",
    check: "m5 12 4 4L19 6",
    refresh: "M20 11a8 8 0 1 0-2 7M20 4v7h-7",
    clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 6v6l4 2",
    expand: "M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5",
  };
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  aria-hidden="true"
  fill="none"
  stroke="currentColor"
  stroke-width="1.4"
  stroke-linecap="round"
  stroke-linejoin="round"><path d={paths[name] || paths.file} /></svg
>

<style>
  svg {
    flex: none;
    vertical-align: middle;
  }
</style>
