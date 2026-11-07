<script lang="ts">
  import {
    setTheme,
    themeChoice,
    type ThemeChoice,
    setPalette,
    paletteChoice,
    type PaletteChoice,
    setDensity,
    densityChoice,
    type DensityChoice,
    chatAppearance,
    setChatAppearance,
    type ChatAppearance,
  } from "$lib/design/theme";
  import Icon from "$lib/design/Icon.svelte";
  import UserThumbnail from "./UserThumbnail.svelte";

  let saved = true;
  const themes: ThemeChoice[] = ["system", "light", "dark"];
  const densities: DensityChoice[] = ["comfortable", "compact"];
  const chatChoices: { key: keyof ChatAppearance; label: string }[] = [
    { key: "userThumbnail", label: "Show your thumbnail" },
    { key: "kejtuThumbnail", label: "Show Kejtu’s thumbnail" },
    { key: "rotateKejtu", label: "Animate Kejtu while working" },
  ];
  const palettes: {
    id: PaletteChoice;
    name: string;
    detail: string;
    colors: string[];
  }[] = [
    {
      id: "default",
      name: "Default",
      detail: "Sage in light mode, Soft slate in dark mode.",
      colors: ["#fafaf8", "#202226", "#829b85"],
    },
    {
      id: "kejtu",
      name: "Kejtu",
      detail: "Neutral whites and charcoal.",
      colors: ["#fafafa", "#212121", "#8c94ac"],
    },
    {
      id: "slate",
      name: "Soft slate",
      detail: "A subtle blue-grey tint.",
      colors: ["#f9fafb", "#202226", "#8e9fb6"],
    },
    {
      id: "sage",
      name: "Sage",
      detail: "Soft greens and warm neutrals.",
      colors: ["#fafaf8", "#20231f", "#829b85"],
    },
    {
      id: "lavender",
      name: "Muted lavender",
      detail: "Quiet lilac and soft violet-grey.",
      colors: ["#faf9fc", "#242129", "#a295b3"],
    },
    {
      id: "sand",
      name: "Warm sand",
      detail: "Warm ivory and gentle earth tones.",
      colors: ["#fcfaf6", "#26231e", "#b39b79"],
    },
    {
      id: "rose",
      name: "Dusty rose",
      detail: "Soft blush and muted rosewood.",
      colors: ["#fcf9f9", "#292123", "#bd949d"],
    },
  ];
</script>

<section aria-label="Appearance">
  <h2 class="settings-title">Appearance</h2>
  <p>Choose how Kejtu looks on this computer.</p>
  <h3>Appearance</h3>
  <div class="choices" role="group" aria-label="Theme">
    {#each themes as value}<button
        type="button"
        aria-pressed={$themeChoice === value}
        on:click={() => (saved = setTheme(value))}
        >{value[0].toUpperCase() + value.slice(1)}</button
      >{/each}
  </div>
  <h3>Spacing</h3>
  <div class="choices" role="group" aria-label="Interface spacing">
    {#each densities as value}<button
        type="button"
        aria-pressed={$densityChoice === value}
        on:click={() => (saved = setDensity(value))}
        >{value[0].toUpperCase() + value.slice(1)}</button
      >{/each}
  </div>
  <p class="hint">
    Compact uses smaller interface labels and tighter gaps. Reading text keeps
    its size.
  </p>
  <h3>Palette</h3>
  <div class="palettes" role="group" aria-label="Colour palette">
    {#each palettes as value}<button
        class="palette"
        type="button"
        aria-pressed={$paletteChoice === value.id}
        on:click={() => (saved = setPalette(value.id))}
      >
        <span class="swatches" aria-hidden="true"
          >{#each value.colors as color}<i style:background={color}
            ></i>{/each}</span
        >
        <span class="description"
          ><strong>{value.name}</strong><small>{value.detail}</small></span
        >
        <span class="check" class:chosen={$paletteChoice === value.id}
          ><Icon name="check" /></span
        >
      </button>{/each}
  </div>
  <p class="hint">
    Applies immediately. System follows your computer’s appearance. Your choice
    is remembered when you reopen Kejtu.
  </p>
  <h3>Conversation</h3>
  <div class="chat-choices" role="group" aria-label="Conversation appearance">
    {#each chatChoices as option}
      <label>
        <input
          type="checkbox"
          checked={$chatAppearance[option.key]}
          on:change={(event) =>
            (saved = setChatAppearance(
              option.key,
              event.currentTarget.checked,
            ))}
        />
        <span>{option.label}</span>
      </label>
    {/each}
  </div>
  <p class="hint">
    Three dots show activity when animation or Kejtu’s thumbnail is off.
  </p>
  {#if !saved}
    <p role="status">
      The appearance changed for this session, but your choice could not be
      saved.
    </p>
  {/if}
  <h3>Your thumbnail</h3>
  <UserThumbnail />
</section>

<style>
  section {
    max-width: 560px;
  }
  .choices {
    display: flex;
    gap: 8px;
  }
  .chat-choices {
    display: grid;
    gap: 12px;
  }
  .chat-choices label {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: var(--ke-text-xs);
    cursor: pointer;
  }
  .chat-choices input {
    margin: 0;
    accent-color: var(--ke-accent);
  }
  button {
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: none;
    color: var(--ke-text-secondary);
    padding: 8px 12px;
    font: inherit;
    font-size: var(--ke-text-xs);
    cursor: pointer;
  }
  button[aria-pressed="true"] {
    background: var(--ke-accent-soft);
    color: var(--ke-accent);
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 3px;
  }
  .palettes {
    display: grid;
    gap: 6px;
  }
  .palette {
    display: flex;
    align-items: center;
    gap: 14px;
    text-align: left;
    border-color: transparent;
    padding: 12px;
  }
  .palette:hover {
    background: var(--ke-surface-sidebar);
  }
  .palette[aria-pressed="true"] {
    border-color: var(--ke-border);
  }
  .description {
    flex: 1;
  }
  .description strong {
    display: block;
    font-size: var(--ke-text-xs);
    font-weight: 500;
  }
  .description small {
    display: block;
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-secondary);
    margin-top: 5px;
  }
  .swatches {
    display: flex;
    width: 58px;
    flex: none;
  }
  .swatches i {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    margin-right: -5px;
    border: 1px solid #0002;
  }
  .check {
    visibility: hidden;
  }
  .check.chosen {
    visibility: visible;
  }
  .hint {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
    line-height: 1.7;
  }
</style>
