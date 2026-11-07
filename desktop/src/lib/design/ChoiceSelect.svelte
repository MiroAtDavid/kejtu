<script lang="ts">
  import type { Readable } from "svelte/store";
  import type { SelectPresentation } from "./choice-select";
  import ChoicePicker from "./ChoicePicker.svelte";
  export let state: Readable<SelectPresentation>;
  export let choose: (value: string) => void;
</script>

<ChoicePicker
  id={$state.id}
  label={$state.label}
  value={$state.value}
  options={$state.options}
  disabled={$state.disabled}
  required={$state.required}
  hideLabel
  labelOnly
  describedBy={[
    $state.description,
    $state.validation ? `${$state.id}-validation` : "",
  ]
    .filter(Boolean)
    .join(" ")}
  invalid={!!$state.validation}
  onChange={choose}
/>
{#if $state.validation}<span
    class="validation"
    id={`${$state.id}-validation`}
    role="alert">{$state.validation}</span
  >{/if}

<style>
  .validation {
    display: block;
    margin-top: 6px;
    color: var(--ke-danger);
    font-size: var(--ke-text-xs);
  }
</style>
