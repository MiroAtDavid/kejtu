<script lang="ts">
  import { userAvatar } from "./user-avatar";
  import { rotatingKejtu } from "./theme";

  export let role: "user" | "assistant";
  export let working = false;
</script>

<span
  class="chat-avatar"
  role={role === "user" && !$userAvatar ? undefined : "img"}
  aria-hidden={role === "user" && !$userAvatar ? "true" : undefined}
  aria-label={role === "user" ? ($userAvatar ? "You" : undefined) : "Kejtu"}
>
  {#if role === "user"}
    {#if $userAvatar}<img
        src={$userAvatar}
        width="24"
        height="24"
        alt=""
        draggable="false"
      />{/if}
  {:else}
    <svg
      class:working={working && $rotatingKejtu}
      viewBox="0 0 96 96"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M48 8C70 6 88 23 87 47C86 70 70 88 46 87C23 86 7 69 9 45C11 22 27 10 48 8Z"
        stroke="currentColor"
        stroke-width="2.5"
      />
      <circle cx="34" cy="34" r="6" fill="currentColor" />
      <circle cx="63" cy="63" r="3.5" fill="currentColor" opacity=".58" />
    </svg>
  {/if}
</span>

<style>
  /* Fixed dimensions keep history/stream measurements independent of image load. */
  .chat-avatar {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    flex: none;
    color: var(--ke-accent);
    user-select: none;
  }
  img,
  svg {
    display: block;
    width: 24px;
    height: 24px;
    object-fit: contain;
  }
  img {
    border-radius: 3px;
  }
  @media (prefers-reduced-motion: no-preference) {
    /* Rotate the complete mark, keeping both eyes and the outline together. */
    svg.working {
      transform-origin: 50% 50%;
      animation: kejtu-working 4s linear infinite;
    }
  }
  @keyframes kejtu-working {
    to {
      transform: rotate(360deg);
    }
  }
</style>
