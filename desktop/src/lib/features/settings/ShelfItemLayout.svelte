<script lang="ts">
  export let inline = false;
  export let label: string;
  export let facts: { label: string; value: string }[];
</script>

{#if inline}
  <slot />
{:else}
  <div class="cp-item">
    <div class="cp-main"><slot /></div>
    <dl class="cp-side facts" aria-label={label}>
      {#each facts as fact}
        <div class="cp-row">
          <dt class="cp-key">{fact.label}</dt>
          <dd>{fact.value}</dd>
        </div>
      {/each}
    </dl>
  </div>
{/if}

<style>
  .facts {
    margin: 0;
  }
  .facts .cp-row {
    align-items: baseline;
    min-height: 0;
    padding: 8px 14px;
  }
  .facts dd {
    flex: 1;
    min-width: 0;
    margin: 0;
    overflow-wrap: anywhere;
  }
</style>
