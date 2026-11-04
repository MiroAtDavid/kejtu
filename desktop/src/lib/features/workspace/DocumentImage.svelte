<script lang="ts">
  export let href = "";
  export let text = "";
  export let title: string | undefined = undefined;
</script>

{#if href}<img src={href} alt={text} {title} loading="lazy" />{:else}<span
    >{text}</span
  >{/if}

<style>
  img {
    max-width: 100%;
    height: auto;
  }
</style>
