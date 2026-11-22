<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  export let name = "package";
  export let category = "";
  export let compact = false;
  const glyphs = new Set([
    "terminal",
    "quick-terminal",
    "globe",
    "pdf",
    "spreadsheet",
    "file",
    "download",
    "search",
  ]);
  const assets = new Set(
    Object.keys(import.meta.glob("../../../../static/shelf-icons/*.svg")).map(
      (path) => path.slice(path.lastIndexOf("/") + 1, -4),
    ),
  );
  $: asset = assets.has(name) ? name : "package";
  $: tone =
    category === "Design & writing"
      ? "rose"
      : category === "Research & learning"
        ? "blue"
        : category === "Planning"
          ? "gold"
          : "green";
</script>

<span class="package-icon {tone}" class:compact aria-hidden="true">
  {#if glyphs.has(name)}<Icon {name} size={compact ? 16 : 22} />
  {:else}<span style={`--icon: url('/shelf-icons/${asset}.svg')`}></span>{/if}
</span>

<style>
  .package-icon {
    --ink: #587565;
    display: inline-grid;
    place-items: center;
    width: 2.75rem;
    height: 2.75rem;
    flex: 0 0 auto;
    border-radius: 0.7rem;
    color: var(--ink);
    background: color-mix(in srgb, var(--ink) 11%, transparent);
    border: 1px solid color-mix(in srgb, var(--ink) 15%, transparent);
  }
  .compact {
    width: 28px;
    height: 28px;
    border-radius: 7px;
  }
  .package-icon.compact > span {
    width: 16px;
    height: 16px;
  }
  .rose {
    --ink: #a2697a;
  }
  .blue {
    --ink: #648198;
  }
  .gold {
    --ink: #a08049;
  }
  .package-icon > span {
    width: 1.4rem;
    height: 1.4rem;
    background: currentColor;
    mask: var(--icon) center / contain no-repeat;
  }
  :global([data-theme="dark"]) .package-icon {
    color: color-mix(in srgb, var(--ink) 65%, white);
    background: color-mix(in srgb, var(--ink) 17%, transparent);
  }
  @media (prefers-color-scheme: dark) {
    :global(:root:not([data-theme="light"])) .package-icon {
      color: color-mix(in srgb, var(--ink) 65%, white);
      background: color-mix(in srgb, var(--ink) 17%, transparent);
    }
  }
</style>
