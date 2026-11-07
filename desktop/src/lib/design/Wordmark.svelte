<script lang="ts">
  export let bottom = 6;
</script>

<p class="wordmark" style:margin-bottom={`${bottom}px`}>kejtu</p>

<style>
  /* Branding stays independent of the conversation's reading typography. */
  .wordmark {
    margin: 0;
    font-family: var(--ke-font-heading);
    font-size: 27px;
    font-weight: 400;
    letter-spacing: -1px;
    line-height: 1.15;
    color: var(--ke-text-primary);
  }
</style>
