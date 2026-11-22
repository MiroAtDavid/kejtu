<script lang="ts">
  import {
    editSummary,
    markedPassage,
    replySummary,
  } from "$lib/features/workspace/document-assist";
  import RichText from "./RichText.svelte";
  import StudyRequestCard from "./StudyRequestCard.svelte";
  import { oneLine } from "./study-requests";
  export let text: string;
  export let answer = false;
  $: summary = answer ? null : editSummary(text);
  $: reply = answer ? replySummary(text) : null;
  // A passage marked in the Markdown document shows under the question the
  // same way as a marked PDF passage; the whole document shows none.
  $: passage = summary ? oneLine(markedPassage(text)) : "";
</script>

{#if answer}<RichText
    source={reply?.explanation ?? ""}
    complete
  />{:else}{summary?.instruction ??
    text}{#if summary && passage}<StudyRequestCard
      request={{
        reference: {
          courseId: summary.document?.course_id ?? "",
          artifactId: "",
          path: summary.document?.file || summary.name || "Markdown draft",
          page: 0,
        },
        label: summary.mode === "insert" ? "Insertion point" : "Marked passage",
        raw: text,
        start: 0,
        end: text.length,
        passage,
      }}
      place=""
    />{/if}{/if}
