<script lang="ts">
  import { derived } from "svelte/store";
  import type { Snippet } from "svelte";
  import type { SkillSelection } from "$lib/api/wire.generated";
  import type { DocumentDiscussion as Discussion } from "$lib/features/workspace/document-discussion";
  import ReaderNotice from "$lib/features/workspace/ReaderNotice.svelte";
  import Activity from "./Activity.svelte";
  import Composer from "./Composer.svelte";
  import { oneLine, type StudyRequest } from "./study-requests";

  export let discussion: Discussion;
  export let disabled = false;
  export let readOnly = false;
  export let sending = false;
  export let working = false;
  export let conversationId = "";
  export let maximumMessageBytes: number | null = null;
  export let tools: Snippet | undefined = undefined;
  export let selectedSkill: SkillSelection | null = null;
  export let onImageViewer: (open: boolean) => void = () => {};
  // Keyed by the discussion in Transcript. This lens forwards every edit to the
  // live document owner, including unsent instructions on context/view switches.
  const { model, actions } = discussion;
  const instruction = {
    subscribe: derived(model, (value) => value?.instruction ?? "").subscribe,
    set: actions.input,
  };
  let composer: Composer;
  // The passage marked in the document, as a card in the chat box; removing
  // it unmarks the document (the whole document is the context again).
  $: card = cardFor($model?.marked ?? "", !!$model?.insertion);
  function cardFor(marked: string, insertion: boolean): StudyRequest | null {
    const passage = oneLine(marked);
    if (!passage) return null;
    return {
      reference: {
        courseId: discussion.reference.course_id,
        artifactId: "",
        path:
          discussion.reference.file ||
          discussion.reference.name ||
          "Markdown draft",
        page: 0,
      },
      label: insertion ? "Insertion point" : "Marked passage",
      raw: marked,
      start: 0,
      end: marked.length,
      passage,
    };
  }
  export function focus() {
    composer?.focus();
  }
</script>

<section class="document-chat" aria-label="Document in chat">
  {#if $model?.assistError || $model?.proposal}
    <ReaderNotice
      error
      label="Dismiss editing message"
      onDismiss={() =>
        $model?.proposal ? actions.dismissProposal() : actions.dismissError()}
    >
      {$model?.assistError ||
        "The proposed edit was not applied. Review it in the document options."}
    </ReaderNotice>
  {/if}
  {#if $model?.asking && !working}
    <Activity
      label={$model.phase}
      stopping={$model.stopped}
      onStop={() => {
        void actions.stop();
      }}
    />
  {/if}
  <Composer
    documentMode
    bind:this={composer}
    bind:draft={$instruction}
    {conversationId}
    {maximumMessageBytes}
    {tools}
    bind:selectedSkill
    {onImageViewer}
    disabled={disabled || !$model?.ready}
    readOnly={readOnly || !$model?.available || !!$model?.asking}
    {sending}
    documentCard={card}
    onRemoveDocumentCard={() => void actions.wholeDocument()}
    onSend={() => {
      void actions.ask();
    }}
  />
</section>

<style>
  .document-chat {
    min-width: 0;
  }
</style>
