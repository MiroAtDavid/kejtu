<script lang="ts">
  import DocumentEditCard from "./DocumentEditCard.svelte";
  import { editSummary } from "$lib/features/workspace/document-assist";
  import StudyRequestCard from "./StudyRequestCard.svelte";
  import { studyParts, type StudyReference } from "./study-requests";
  export let text = "";
  export let complete = true;
  export let disabled = false;
  export let onOpen:
    ((reference: StudyReference) => Promise<void>) | undefined = undefined;
  $: parts = studyParts(text, complete);
</script>

{#if editSummary(text)}<DocumentEditCard
    {text}
  />{:else}{#each parts as part}{#if part.kind === "text"}{part.text}{:else}<StudyRequestCard
        request={part.request}
        {onOpen}
        {disabled}
      />{/if}{/each}
{/if}
