<script lang="ts">
  import { onMount } from "svelte";
  import {
    workspaceRequest,
    type Source,
    type Workspace,
    type RecordRow,
  } from "./workspace";
  export let workspace: Workspace;
  export let courseId: string;
  export let source: Source | null = null;
  export let busy = false;
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  export let onReadSource: (source: Source) => void = () => {};
  type Attempt = {
    course_id: string;
    topic: string;
    question: string;
    answer: string;
    feedback: string;
    confidence: number | null;
    basis: string;
    sources: Source[];
  };
  let topic = "",
    question = "",
    answer = "",
    feedback = "",
    confidence = "",
    error = "",
    next: number | null = null;
  let items: RecordRow<Attempt>[] = [];
  $: locked = busy || $workspace.busy || !!$workspace.pending;
  async function refresh(append = false) {
    try {
      const result = await workspaceRequest<{
        items: RecordRow<Attempt>[];
        next_offset: number | null;
      }>("record.page", {
        kind: "practice",
        offset: append ? next || 0 : 0,
        limit: 50,
      });
      items = append ? [...items, ...result.items] : result.items;
      next = result.next_offset;
    } catch (caught) {
      error = String(caught);
    }
  }
  async function save() {
    if (!source || locked) return;
    const result = await workspace.mutate("record.save", {
      kind: "practice",
      data: {
        course_id: courseId,
        topic,
        question,
        answer,
        feedback,
        confidence: confidence === "" ? null : Number(confidence),
        basis: answer.trim() ? "exercise_attempt" : "user_report",
        sources: [source],
      },
    });
    if (result) {
      answer = "";
      feedback = "";
      await refresh();
    }
  }
  function discuss(kind: string) {
    if (!source || locked) return;
    onDiscuss(
      `${kind}\nProject ${courseId}. Topic: ${topic || "the selected material"}. Question: ${question || "Choose a suitable question from this source"}. My attempt: ${answer || "No answer yet"}. Read exact retained source ${source.artifact_id} (original ${source.path}, page ${source.page}) with university materials.snapshot first. Preserve source references. Keep reported confidence separate from observed answers.`,
      source,
    );
  }
  onMount(() => {
    void refresh();
  });
</script>

<div class="practice-layout">
  <form class="card" on:submit|preventDefault={save}>
    <h2>Practise from your material</h2>
    {#if source}<p class="source">
        {source.path} · page {source.page || "text"}
      </p>{:else}<p class="notice">
        Open material and choose “Practise this page” from the reader.
      </p>{/if}
    <label
      >Topic to work on / revisit<input required bind:value={topic} /></label
    >
    <label>Question<textarea required bind:value={question}></textarea></label>
    <div class="row">
      <button
        type="button"
        disabled={!source || locked}
        on:click={() =>
          discuss(
            "Give me one source-linked practice question. Let me try before giving the answer.",
          )}>Ask for a question</button
      ><button
        type="button"
        disabled={!source || locked}
        on:click={() =>
          discuss("Give me one small hint, without the full answer.")}
        >One hint</button
      >
    </div>
    <label>Your attempt<textarea bind:value={answer}></textarea></label>
    <label
      >Feedback / what to revisit<textarea bind:value={feedback}
      ></textarea></label
    >
    <label
      >Your reported confidence<select bind:value={confidence}
        ><option value="">Not recorded</option
        >{#each [0, 1, 2, 3, 4, 5] as value}<option value={String(value)}
            >{value} / 5</option
          >{/each}</select
      ></label
    >
    <p class="muted">
      Confidence is your own report. Saved answers and feedback remain separate
      evidence; there is no automatic mastery score.
    </p>
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <div class="row">
      <button disabled={locked || !source || !topic.trim() || !question.trim()}
        >Save attempt / revisit topic</button
      ><button
        type="button"
        disabled={!source || locked}
        on:click={() =>
          discuss(
            "Explain the solution from this source, and assess my answer only against the actual question.",
          )}>Discuss my answer</button
      >
    </div>
  </form>
  <section aria-label="Saved practice" class="stack">
    <div class="row spread">
      <h2>Attempts &amp; revisit notes</h2>
      <button on:click={() => refresh()}>Refresh attempts</button>
    </div>
    {#each items.filter((row) => row.data.course_id === courseId) as row (row.id)}
      <article class="card">
        <h3>{row.data.topic}</h3>
        <p>{row.data.question}</p>
        {#if row.data.answer}<p>
            <strong>Recorded answer:</strong>
            {row.data.answer}
          </p>{/if}{#if row.data.feedback}<p>
            <strong>Feedback / revisit:</strong>
            {row.data.feedback}
          </p>{/if}
        <p class="muted">
          {row.data.basis.replaceAll("_", " ")} · {row.created_at}{row.data
            .confidence !== null
            ? ` · Reported confidence ${row.data.confidence}/5`
            : ""}
        </p>
        {#each row.data.sources as reference}<button
            class="plain source"
            on:click={() => onReadSource(reference)}
            >Read exact original source · {reference.path.split("/").pop()} · {reference.page ||
              "text"}</button
          >{/each}
      </article>
    {:else}<p class="muted">Your saved attempts will appear here.</p>{/each}
    {#if next !== null}<button on:click={() => refresh(true)}
        >More attempts</button
      >{/if}
  </section>
</div>

<style>
  .practice-layout {
    display: grid;
    grid-template-columns: minmax(280px, 1fr) minmax(280px, 1fr);
    gap: 24px;
    align-items: start;
  }
  .practice-layout p {
    white-space: pre-wrap;
  }
  @media (max-width: 900px) {
    .practice-layout {
      grid-template-columns: 1fr;
    }
  }
</style>
