<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import { onMount } from "svelte";
  import { identity, reconcilePending, request } from "$lib/api/client";
  import type {
    Arguments,
    Lookup,
    NotebookItem,
    NoteRevision,
    Results,
  } from "$lib/api/wire.generated";

  export let conversationId: string;
  export let busy = false;
  export let locked = false;
  export let onAccess: () => void = () => {};
  export let onRemoved: () => void = () => {};

  let items: NotebookItem[] = [];
  let collection: "current" | "historical" = "current";
  let hasHistorical = false;
  let collectionError = "";
  let cursor: string | null = null;
  let viewToken = "";
  let selected: (NoteRevision & { note_id: string }) | null = null;
  let body: Results["note.read"] | null = null;
  let preview: Results["note.removal.preview"] | null = null;
  let confirmed = false;
  let loading = false;
  let uncertain = false;
  let intent: Arguments["note.remove"] | null = null;
  let notice = "";
  let error = "";
  let generation = 0;
  let destroyed = false;
  $: locked = uncertain || intent !== null;

  function failure(caught: unknown) {
    error = caught instanceof Error ? caught.message : String(caught);
  }
  function changeCollection(event: Event) {
    const value = (event.currentTarget as HTMLSelectElement).value;
    if (value !== "current" && value !== "historical") return;
    collection = value;
    void list();
  }
  function clearView() {
    items = [];
    cursor = null;
    viewToken = "";
    selected = null;
    body = null;
    preview = null;
    confirmed = false;
  }

  async function refresh() {
    if (loading || locked || busy) return;
    loading = true;
    collectionError = "";
    try {
      // A bounded read discovers actual imported material; no empty legacy
      // collection is advertised on a fresh installation.
      const page = await request("notes.page", {
        conversation_id: conversationId,
        cursor: null,
        collection: "historical",
        page_size: 1,
      });
      if (destroyed) return;
      hasHistorical = page.items.length > 0;
    } catch {
      if (!destroyed)
        collectionError =
          "Could not check for older notes. Refresh notes to retry.";
    } finally {
      if (!destroyed) loading = false;
    }
    if (!destroyed) await list();
  }

  async function list(more = false) {
    if (loading || intent || uncertain || busy) return;
    const token = ++generation;
    const next = more ? cursor : null;
    if (!more) clearView();
    loading = true;
    error = "";
    try {
      const page = await request("notes.page", {
        conversation_id: conversationId,
        cursor: next,
        collection,
      });
      if (destroyed || token !== generation) return;
      if (more && viewToken !== page.view_token)
        throw new Error("Notebook changed. Refresh before continuing.");
      items = more ? [...items, ...page.items] : page.items;
      cursor = page.next_cursor;
      viewToken = page.view_token;
    } catch (caught) {
      if (token === generation && !destroyed) {
        clearView();
        failure(caught);
      }
    } finally {
      if (token === generation && !destroyed) loading = false;
    }
  }

  async function read(
    revision: NoteRevision & { note_id: string },
    more = false,
  ) {
    if (loading || locked || busy) return;
    const token = ++generation;
    const previous = more ? body : null;
    const offset = previous?.end ?? 0;
    selected = revision;
    body = previous;
    loading = true;
    error = "";
    try {
      const part = await request("note.read", {
        conversation_id: conversationId,
        note_id: revision.note_id,
        revision_id: revision.revision_id,
        view_token: viewToken,
        offset,
        maximum_bytes: 16384,
      });
      if (destroyed || token !== generation) return;
      if (
        part.note_id !== revision.note_id ||
        part.revision_id !== revision.revision_id ||
        part.sha256 !== revision.sha256 ||
        part.total_bytes !== revision.total_bytes ||
        part.offset !== offset ||
        part.end < offset ||
        part.end > part.total_bytes ||
        part.complete !== (part.end === part.total_bytes) ||
        (!part.complete && part.end === offset) ||
        new TextEncoder().encode(part.content).length !== part.end - offset
      ) {
        throw new Error(
          "Note page identity or extent changed. Refresh before reading.",
        );
      }
      body = { ...part, content: (previous?.content ?? "") + part.content };
    } catch (caught) {
      // No cached partial body remains visible after authority or identity fails.
      if (token === generation && !destroyed) {
        clearView();
        failure(caught);
      }
    } finally {
      if (token === generation && !destroyed) loading = false;
    }
  }

  async function previewRemoval(more = false) {
    if (!selected || loading || locked || busy) return;
    const token = ++generation;
    const target = selected.note_id;
    const previous = more ? preview : null;
    preview = previous;
    confirmed = false;
    loading = true;
    error = "";
    notice = "";
    try {
      const page = await request("note.removal.preview", {
        conversation_id: conversationId,
        note_id: target,
        cursor: previous?.next_cursor ?? null,
      });
      if (destroyed || token !== generation) return;
      if (
        page.note_id !== target ||
        (previous &&
          (page.preview_token !== previous.preview_token ||
            page.head_revision !== previous.head_revision ||
            page.total_revisions !== previous.total_revisions ||
            page.total_bytes !== previous.total_bytes ||
            page.view_token !== previous.view_token))
      ) {
        throw new Error("Removal preview changed. Refresh and preview again.");
      }
      const revisions = [...(previous?.revisions ?? []), ...page.revisions];
      if (
        new Set(revisions.map((r) => r.revision_id)).size !==
          revisions.length ||
        revisions.length > page.total_revisions ||
        (!page.next_cursor && revisions.length !== page.total_revisions)
      ) {
        throw new Error(
          "Retained revision inventory is incomplete. Refresh and preview again.",
        );
      }
      viewToken = page.view_token;
      preview = { ...page, revisions };
    } catch (caught) {
      if (token === generation && !destroyed) {
        clearView();
        failure(caught);
      }
    } finally {
      if (token === generation && !destroyed) loading = false;
    }
  }

  function receipt(result: Extract<Lookup, { kind: "note.remove" }>) {
    if (
      !intent ||
      result.mutation_key !== intent.mutation_key ||
      result.note_id !== intent.note_id ||
      result.conversation_id !== intent.conversation_id
    )
      throw new Error("Removal receipt does not match this request.");
  }

  async function removed() {
    clearView();
    intent = null;
    uncertain = false;
    notice =
      "Note and all retained revisions removed from active storage. Independent notes are unchanged.";
    onRemoved();
    loading = false;
    // Drop content before issuing any follow-up read; a refresh failure cannot
    // turn an acknowledged removal into an ambiguous mutation or restore a cache.
    await list();
  }

  async function remove() {
    if (
      !preview ||
      !confirmed ||
      preview.next_cursor ||
      loading ||
      locked ||
      busy
    )
      return;
    loading = true;
    error = "";
    notice = "";
    intent = {
      conversation_id: conversationId,
      note_id: preview.note_id,
      preview_token: preview.preview_token,
      mutation_key: identity(),
    };
    try {
      const result = await request("note.remove", intent);
      if (destroyed) return;
      receipt(result);
      await removed();
    } catch (caught) {
      if (!destroyed) {
        clearView();
        uncertain = true;
        notice =
          "Removal is unconfirmed. Check its saved outcome before doing anything else. No retry was sent.";
        failure(caught);
      }
    } finally {
      if (!destroyed) loading = false;
    }
  }

  async function resolve() {
    if (!intent || loading) return;
    loading = true;
    error = "";
    try {
      let result = await reconcilePending();
      // The bridge may already have validated and cleared its marker before
      // the WebView lost the response. Lookup remains read-only in that case.
      if (result.status === "none")
        result = await request("mutation.lookup", {
          kind: "note.remove",
          conversation_id: intent.conversation_id,
          mutation_key: intent.mutation_key,
        });
      if (destroyed) return;
      if (result.status === "accepted") {
        if (result.kind !== "note.remove")
          throw new Error("A different pending operation needs recovery.");
        receipt(result);
        await removed();
        notice =
          "Saved removal confirmed. No operation was repeated. Independent notes are unchanged.";
      } else {
        intent = null;
        uncertain = false;
        clearView();
        loading = false;
        notice =
          "No recorded removal. Nothing was retried. Refresh and preview again if you still want to remove the note.";
        await list();
      }
    } catch (caught) {
      if (!destroyed) {
        uncertain = true;
        failure(caught);
      }
    } finally {
      if (!destroyed) loading = false;
    }
  }

  onMount(() => {
    void refresh();
    return () => {
      destroyed = true;
      generation++;
    };
  });
</script>

<section class="notebook" aria-label="Notebook">
  <h2 class="settings-title">Notes</h2>
  <p class="panel-intro muted">Notes available to this conversation.</p>
  <div class="actions">
    <button
      disabled={loading || locked || busy}
      on:click={() => {
        if (!loading && !locked && !busy) onAccess();
      }}>Privacy &amp; access</button
    >
    <button disabled={loading || locked || busy} on:click={refresh}
      >Refresh notes</button
    >
  </div>
  {#if hasHistorical || collection === "historical"}<label
      >Collection
      <select
        use:choiceSelect={collection}
        value={collection}
        disabled={loading || locked || busy}
        on:change={changeCollection}
      >
        <option value="current">Current notebook</option>
        <option value="historical">Old notebook · historical</option>
      </select>
    </label>{/if}
  {#if collectionError}<p class="error" role="alert">{collectionError}</p>{/if}
  {#if collection === "historical"}
    <p class="muted">
      Original notes from the old app. Read-only; dates and plans describe that
      time. Kejtu can recall these and keep useful material in a new current
      note.
    </p>
  {/if}
  {#if busy}<p>
      Notebook actions are paused while a conversation operation is active.
    </p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if uncertain}<button disabled={loading} on:click={resolve}
      >Check removal outcome</button
    >{/if}
  {#if loading}<p role="status">Loading…</p>{/if}
  {#if !selected && !loading && !error && !uncertain && items.length === 0}<p>
      No notes available in this collection.
    </p>{/if}
  {#if !selected}
    <ul class="notes">
      {#each items as item (item.note_id)}
        <li>
          <button
            disabled={loading || locked || busy}
            on:click={() => read(item)}
          >
            {#if item.note?.title}<strong>{item.note.title}</strong>{/if}
            {#if item.historical_note}<strong
                >{item.historical_note.title}</strong
              >{/if}
            <span class="preview">{item.preview}</span>
            {#if item.note?.preference_scope}<small
                >Applies when: {item.note.preference_scope}</small
              >{/if}
            {#if item.note}<small
                >{item.note.purpose.replaceAll("_", " ")}{item.note
                  .thread_status
                  ? ` · ${item.note.thread_status}`
                  : ""}{item.note.tags.length
                  ? ` · ${item.note.tags.join(", ")}`
                  : ""}</small
              >{/if}
            <small
              >{item.author === "user"
                ? "Your memory · "
                : ""}{item.note_id.slice(0, 8)} · {item.total_bytes} UTF-8 bytes ·
              {item.created_at ?? "Date unavailable"}</small
            >
          </button>
        </li>
      {/each}
    </ul>
    {#if cursor}<button
        disabled={loading || locked || busy}
        on:click={() => list(true)}>More notes</button
      >{/if}
  {:else}
    <div class="heading">
      <button
        disabled={loading || locked || busy}
        on:click={() => {
          selected = null;
          body = null;
          preview = null;
          confirmed = false;
        }}>All notes</button
      >
      {#if collection === "current"}<button
          disabled={loading || locked || busy}
          on:click={() => previewRemoval()}>Preview removal</button
        >{/if}
    </div>
    <p class="identity">
      {body?.author === "user" ? "Your memory" : "Note"}
      {selected.note_id}<br />Revision {selected.revision_id}
    </p>
    {#if body}
      {#if body.historical_note}
        <h3>{body.historical_note.title}</h3>
        <p class="muted">
          Historical note · original revision {body.historical_note
            .source_revision} · {body.historical_note.updated_at ??
            "Date unavailable"}
        </p>
      {/if}
      {#if body.note}
        {#if body.note.title}<h3>{body.note.title}</h3>{/if}
        {#if body.note.preference_scope}<p class="muted">
            Applies when: {body.note.preference_scope}
          </p>{/if}
        <p>
          {body.note.purpose.replaceAll("_", " ")}{body.note.thread_status
            ? ` · ${body.note.thread_status}`
            : ""} · {body.note.basis.replaceAll("_", " ")}
        </p>
        {#if body.note.tags.length}<p>{body.note.tags.join(", ")}</p>{/if}
      {/if}
      <pre aria-label="Exact note text">{body.content}</pre>
      {#if body.note?.flags?.length}
        <p aria-label="Note state flags">State: {body.note.flags.join(", ")}</p>
        {#if body.note.flags.includes("user-rejected")}
          <p class="muted">
            Excluded from ordinary note recall; retained for explicit
            inspection.
          </p>
        {/if}
        {#if body.note.flags.includes("coverage-partial")}
          <p class="muted">Coverage is partial, not a complete survey.</p>
        {/if}
      {/if}
      {#if body.correction}
        <details>
          <summary
            >Last user correction{body.note?.change_kind === "edited"
              ? " (followed by later edits)"
              : ""}</summary
          >
          <p>{body.correction.reason}</p>
          <blockquote>{body.correction.source_quote}</blockquote>
        </details>
      {/if}
      {#if body.attribution}<details>
          <summary>Attributed user wording</summary>
          <blockquote>{body.attribution.source_quote}</blockquote>
        </details>{/if}
      <p class="muted">
        {body.end} of {body.total_bytes} UTF-8 bytes shown{body.complete
          ? " — complete revision"
          : " — partial revision"}.
      </p>
      {#if !body.complete}<button
          disabled={loading || locked || busy}
          on:click={() => selected && read(selected, true)}
          >More note text</button
        >{/if}
    {/if}
    {#if preview}
      <section class="removal" aria-label="Removal preview">
        <h3>Remove this whole note?</h3>
        <p>
          This removes all {preview.total_revisions} retained revisions ({preview.total_bytes}
          UTF-8 bytes), their active bodies and search projections. There is no undo
          in this app. Surviving independent notes are not removed or rewritten.
        </p>
        <p>
          Known copies can block removal. This does not promise erasure from
          backups or detection of every paraphrase.
        </p>
        <p>
          {preview.revisions.length} of {preview.total_revisions} revision entries
          loaded. Select an entry to read its exact text.
        </p>
        <ul class="revisions">
          {#each preview.revisions as revision (revision.revision_id)}
            <li>
              <button
                disabled={loading || locked || busy}
                on:click={() =>
                  preview && read({ ...revision, note_id: preview.note_id })}
              >
                {revision.revision_id.slice(0, 8)}{revision.revision_id ===
                preview.head_revision
                  ? " · current"
                  : ""} · {revision.total_bytes} bytes
              </button>
            </li>
          {/each}
        </ul>
        {#if preview.next_cursor}<button
            disabled={loading || locked || busy}
            on:click={() => previewRemoval(true)}
            >More retained revisions</button
          >{/if}
        <label
          ><input
            type="checkbox"
            bind:checked={confirmed}
            disabled={!!preview.next_cursor || loading || locked || busy}
          /> I want to remove this note and all its retained revisions.</label
        >
        <div class="heading">
          <button
            class="danger"
            disabled={!confirmed ||
              !!preview.next_cursor ||
              loading ||
              locked ||
              busy}
            on:click={remove}>Remove whole note</button
          >
          <button
            disabled={loading || locked || busy}
            on:click={() => {
              preview = null;
              confirmed = false;
            }}>Cancel removal</button
          >
        </div>
      </section>
    {/if}
  {/if}
</section>

<style>
  .notebook {
    padding: 0;
    max-width: 920px;
    width: 100%;
    margin: 0;
  }
  .heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .notes,
  .revisions {
    list-style: none;
    padding: 0;
  }
  .notes li {
    margin-bottom: 9px;
  }
  .notes button {
    width: 100%;
    text-align: left;
  }
  .preview {
    display: block;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  small,
  .muted {
    color: var(--ke-text-secondary);
  }
  small {
    display: block;
    margin-top: 6px;
  }
  .identity {
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font: inherit;
    padding: 12px;
    color: var(--ke-text-primary);
    background: var(--ke-surface-raised);
    border: 1px solid var(--ke-border);
    border-radius: 6px;
  }
  .removal {
    border: 1px solid var(--ke-danger);
    border-radius: 6px;
    padding: 16px;
    margin-top: 24px;
    background: var(--ke-surface-raised);
  }
  .revisions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  label {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 16px 0;
  }
  .danger {
    color: var(--ke-text-primary);
    background: var(--ke-surface-raised);
    border-color: var(--ke-danger);
  }
  .error {
    color: var(--ke-danger);
  }
</style>
