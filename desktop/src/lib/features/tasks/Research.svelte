<script lang="ts">
  import "./planning.css";
  import { onMount } from "svelte";
  import { identity, NativeFailure, request } from "$lib/api/client";
  import {
    isNativeRequest,
    PROTOCOL,
    type Arguments,
    type ResearchTask,
    type ResearchResult,
    type ResearchSearch,
    type ResearchCounts,
  } from "$lib/api/wire.generated";
  import { when, runLabel } from "../background/editor";

  import FindingCard from "./FindingCard.svelte";
  import {
    changes,
    emptyCounts,
    unread,
    searchStatus,
    researchTime,
    type FindingView,
    type ResearchTarget,
  } from "./research-view";
  export let target: ResearchTarget | null = null;
  export let stateId: string;
  export let ownerBusy = false;
  export let navigationLocked = false;
  export let onDiscuss: (text: string) => void = () => {};
  export let onChanged: () => void = () => {};
  type Change =
    | {
        operation: "research.pause" | "research.resume" | "research.delete";
        args: Arguments["research.pause"];
      }
    | { operation: "research.review"; args: Arguments["research.review"] }
    | { operation: "research.seen"; args: Arguments["research.seen"] }
    | {
        operation: "research.disposition";
        args: Arguments["research.disposition"];
      };
  let items: ResearchSearch[] = [],
    findings: ResearchResult[] = [];
  let selected: ResearchTask | null = null;
  let next: number | null = null,
    findingsNext: number | null = null;
  let summary: string | null = null,
    coverage: string | null = null;
  let busy = false,
    error = "",
    ready = false;
  let counts = emptyCounts(),
    allCounts = emptyCounts();
  let totalSearches = 0,
    totalFindings = 0,
    offset = 0;
  let view: FindingView = "inbox",
    query = "",
    searchText = "",
    seenTarget = -1;
  const pageSize = 8;
  const tabs: { view: FindingView; label: string }[] = [
    { view: "inbox", label: "To review" },
    { view: "all", label: "All findings" },
    { view: "saved", label: "Saved" },
    { view: "reviewed", label: "Reviewed" },
    { view: "dismissed", label: "Dismissed" },
  ];
  let pending: Change | null = null;
  let deleting: { id: string; revision: number; title: string } | null = null;
  let notice = "";
  let findingsElement: HTMLDivElement | undefined;
  let findingsHeight = 0;
  const journalKey = `kejtu-next.research.v1.${stateId}`;
  $: navigationLocked = busy || pending !== null;
  $: if (ready && !navigationLocked && target && target.token !== seenTarget) {
    seenTarget = target.token;
    const destination = target;
    void run(async () => {
      await list();
      const id = destination.jobId ?? selected?.id ?? items[0]?.id;
      if (id) await read(id, true, destination.view);
    });
  }
  function tabCount(value: FindingView, current: ResearchCounts) {
    return value === "inbox"
      ? unread(current)
      : value === "all"
        ? current.total
        : current[value];
  }

  async function run(work: () => Promise<void>) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      await work();
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      busy = false;
    }
  }
  async function list(append = false) {
    const page = await request("research.overview", {
      offset: append ? (next ?? 0) : 0,
      limit: 12,
    });
    items = append ? [...items, ...page.items] : page.items;
    next = page.next_offset;
    allCounts = page.counts;
    totalSearches = page.total;
  }
  async function read(id: string, reset = false, preferred?: FindingView) {
    deleting = null;
    notice = "";
    if (reset) {
      query = searchText = "";
      offset = 0;
      findings = [];
      findingsHeight = 0;
      selected = null;
    }
    const value = await request("research.read", { job_id: id });
    selected = value.task;
    summary = value.summary;
    coverage = value.coverage;
    if (reset) view = preferred ?? (selected.new_findings ? "inbox" : "all");
    await findingPage();
  }
  async function findingPage() {
    if (!selected) return;
    if (findingsElement?.getClientRects().length) {
      let scroller = findingsElement.parentElement;
      while (
        scroller &&
        !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)
      )
        scroller = scroller.parentElement;
      const bottom = scroller
        ? scroller.getBoundingClientRect().top +
          scroller.clientTop +
          scroller.clientHeight
        : window.innerHeight;
      // Clearing or shortening a page must not clamp the surrounding scroll.
      // Reserve only the remaining viewport, not the previous result-list height.
      // Keep it for empty/error results too, without forcing a scroll position.
      findingsHeight = Math.max(
        0,
        Math.ceil(bottom - findingsElement.getBoundingClientRect().top),
      );
    }
    findings = [];
    let page = await request("research.browse", {
      job_id: selected.id,
      offset,
      limit: pageSize,
      view,
      query,
    });
    // Removing the final item on a page returns to the last remaining page.
    if (offset && offset >= page.total) {
      offset = Math.max(0, Math.floor((page.total - 1) / pageSize) * pageSize);
      page = await request("research.browse", {
        job_id: selected.id,
        offset,
        limit: pageSize,
        view,
        query,
      });
    }
    findings = page.items;
    counts = page.counts;
    totalFindings = page.total;
    findingsNext = page.next_offset;
  }
  function chooseView(value: FindingView) {
    if (busy || pending) return;
    view = value;
    offset = 0;
    void run(findingPage);
  }
  function review(finding: ResearchResult) {
    void run(() =>
      send({
        operation: "research.review",
        args: {
          finding_id: finding.id,
          expected_revision: finding.revision,
          mutation_key: identity(),
        },
      }),
    );
  }
  function disposition(
    finding: ResearchResult,
    value: ResearchResult["disposition"],
  ) {
    void run(() =>
      send({
        operation: "research.disposition",
        args: {
          finding_id: finding.id,
          expected_revision: finding.revision,
          disposition: value,
          mutation_key: identity(),
        },
      }),
    );
  }
  async function send(change?: Change) {
    if (change) {
      if (pending) return;
      // Persist the exact key and CAS revision before dispatch. Retrying after
      // response loss resolves this intent without repeating its effect.
      localStorage.setItem(journalKey, JSON.stringify(change));
      pending = change;
    }
    if (!pending) return;
    const sent = pending;
    try {
      if (pending.operation === "research.review")
        await request(pending.operation, pending.args);
      else if (pending.operation === "research.seen")
        await request(pending.operation, pending.args);
      else if (pending.operation === "research.disposition")
        await request(pending.operation, pending.args);
      else await request(pending.operation, pending.args);
    } catch (caught) {
      if (
        caught instanceof NativeFailure &&
        [
          "research_conflict",
          "job_conflict",
          "job_cancelled",
          "job_missing",
          "research_missing",
          "research_forbidden",
          "research_source",
          "job_forbidden",
          "native_busy",
        ].includes(caught.code)
      ) {
        localStorage.removeItem(journalKey);
        pending = null;
        deleting = null;
      }
      throw caught;
    }
    localStorage.removeItem(journalKey);
    pending = null;
    // The change is durable. Refresh shared indicators even if the detail
    // reads below are slow or fail; they do not own the notification state.
    onChanged();
    if (sent.operation === "research.delete") {
      selected = null;
      findings = [];
      findingsNext = null;
      summary = coverage = null;
      deleting = null;
      notice = "Task deleted.";
    }
    await list();
    if (selected) await read(selected.id);
  }
  function discuss(finding?: ResearchResult) {
    if (!selected || ownerBusy || pending) return;
    onDiscuss(
      finding
        ? `Let's discuss this finding from “${selected.title}”: ${finding.title}\n${finding.url}\nPlease read the saved assignment and finding first.`
        : `Let's review the latest results for my research assignment “${selected.title}”. Please read its saved criteria and findings first.`,
    );
  }
  onMount(() => {
    void run(async () => {
      const raw = localStorage.getItem(journalKey);
      if (raw) {
        const value = JSON.parse(raw);
        if (
          ![
            "research.pause",
            "research.resume",
            "research.delete",
            "research.seen",
            "research.review",
            "research.disposition",
          ].includes(value.operation) ||
          !isNativeRequest({
            protocol: PROTOCOL,
            request_id: identity(),
            operation: value.operation,
            arguments: value.args,
          })
        )
          throw new Error(
            "The saved assignment change could not be read. Its outcome needs inspection.",
          );
        pending = value;
        await send();
      }
      await list();
      if (!target && items[0]) await read(items[0].id, true);
      ready = true;
    });
  });
</script>

<section aria-label="Kejtu's research" class="research planning-page">
  <header class="heading">
    <div>
      <h2 class="settings-title planning-title">Research</h2>
    </div>
    <button
      disabled={busy || !!pending}
      on:click={() =>
        run(async () => {
          await list();
          if (selected) await read(selected.id);
          onChanged();
        })}>Refresh</button
    >
  </header>

  {#if error && !selected}<p role="alert" class="error">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if pending}<p role="status">
      A change is awaiting confirmation. <button
        disabled={busy}
        on:click={() => run(() => send())}>Check saved change</button
      >
    </p>{/if}
  <div class="overview" aria-label="Across all research searches">
    <div><strong>{unread(allCounts)}</strong><span>To review</span></div>
    <div><strong>{allCounts.saved}</strong><span>Saved</span></div>
    <div>
      <strong>{totalSearches}</strong><span
        >{totalSearches === 1 ? "Search" : "Searches"}</span
      >
    </div>
  </div>
  {#if ready && !items.length}<div class="empty">
      <h3>A place for what Kejtu finds.</h3>
      <p>
        Try “Check daily for a flat matching these requirements…” and describe
        what matters to you.
      </p>
    </div>{/if}
  <div class="workspace">
    <nav class="searches" aria-label="Research searches">
      {#each items as item (item.id)}
        <button
          class="assignment"
          class:chosen={selected?.id === item.id}
          aria-pressed={selected?.id === item.id}
          disabled={busy || !!pending}
          on:click={() => run(() => read(item.id, true))}
        >
          <span class="name">{item.title}</span>
          <span class="change-count" class:has-updates={unread(item.counts) > 0}
            >{changes(item.counts) || `${item.counts.total} findings`}</span
          >
          <span class="muted"
            >{searchStatus(item)}{#if item.next_at && item.enabled}<span
                class="next-check"
                title={when(item.next_at, item.timezone)}
                >Next {researchTime(item.next_at, item.timezone)}</span
              >{/if}</span
          >
        </button>
      {/each}
      {#if next !== null}<button
          disabled={busy || !!pending}
          on:click={() => run(() => list(true))}>More searches</button
        >{/if}
    </nav>
    {#if selected}<article class="detail" aria-label="Selected search">
        <!-- A linked search can be outside the current overview page. Only
               show its name here when no visible selection card identifies it. -->
        {#if !items.some((item) => item.id === selected?.id)}
          <h3 class="search-context">{selected.title}</h3>
        {/if}
        <details class="criteria">
          <summary>Search criteria & controls</summary>
          <p class="body">{selected.instructions}</p>
          <p class="muted">
            {selected.schedule.repeat === "none"
              ? "Once"
              : `Repeats ${selected.schedule.repeat}, every ${selected.schedule.interval}`}
            · {when(selected.schedule.starts_at, selected.schedule.timezone)}
          </p>
          <div class="actions">
            <button
              disabled={ownerBusy || busy || !!pending}
              on:click={() =>
                selected &&
                run(() =>
                  send({
                    operation: selected!.enabled
                      ? "research.pause"
                      : "research.resume",
                    args: {
                      job_id: selected!.id,
                      expected_revision: selected!.revision,
                      mutation_key: identity(),
                    },
                  }),
                )}>{selected.enabled ? "Pause" : "Resume"}</button
            >
            <button
              disabled={ownerBusy || busy || !!pending}
              on:click={() => discuss()}>Discuss in chat</button
            >
            <button
              class="delete"
              disabled={ownerBusy || busy || !!pending || !!deleting}
              on:click={() =>
                (deleting = {
                  id: selected!.id,
                  revision: selected!.revision,
                  title: selected!.title,
                })}>Delete task…</button
            >
          </div>
        </details>
        {#if deleting}
          <section
            class="delete-confirmation"
            aria-label="Confirm task deletion"
          >
            <h4>Delete “{deleting.title}”?</h4>
            <p>
              This permanently removes the task, its schedule, run history and
              all findings, including saved findings. A running check will be
              stopped. This cannot be undone.
            </p>
            <p class="muted">
              Files it created and messages already in chat remain.
            </p>
            <div class="actions">
              <button
                disabled={busy || !!pending}
                on:click={() => (deleting = null)}>Keep task</button
              >
              <button
                class="delete"
                disabled={ownerBusy || busy || !!pending}
                on:click={() => {
                  if (!deleting || ownerBusy || busy || pending) return;
                  const args = {
                    job_id: deleting.id,
                    expected_revision: deleting.revision,
                    mutation_key: identity(),
                  };
                  void run(() => send({ operation: "research.delete", args }));
                }}>Delete permanently</button
              >
            </div>
          </section>
        {/if}
        {#if selected.last_run && selected.last_run.status !== "completed"}
          <p class="check-status">
            Latest check: {runLabel(
              selected.last_run.status,
              selected.last_run.error_code,
            )}. {#if selected.last_run.status === "running" && selected.last_run.error_code === "research_paused"}Progress
              is kept. Work resumes after your reply finishes and the app has
              been idle for one minute.{/if}
            {#if selected.last_run.status === "pending" && selected.last_run.error_code === "research_retry"}The
              interrupted check will retry when the app is idle. Interruptions
              do not count as completed checks.{/if}
            {#if ["failed", "cancelled", "interrupted", "deferred"].includes(selected.last_run.status)}This
              check did not finish. Existing findings below remain from earlier
              checks.{/if}
          </p>
        {/if}

        {#if summary}<details class="report">
            <summary
              ><span
                >{coverage === "partial"
                  ? "Search report · Incomplete search"
                  : "Search report"}</span
              ></summary
            >
            {#if coverage === "partial"}<p class="muted">
                This report covers only part of the requested search. Read the
                summary below for details.
              </p>{/if}
            {#if selected.last_run?.finished_at}<p class="muted">
                Report saved <time
                  datetime={selected.last_run.finished_at}
                  title={when(selected.last_run.finished_at)}
                  >{researchTime(selected.last_run.finished_at)}</time
                >
              </p>{/if}
            <p class="body">{summary}</p>
            {#if selected.new_result && selected.last_run}<div
                class="report-actions"
              >
                <button
                  disabled={ownerBusy || busy || !!pending}
                  on:click={() =>
                    run(() =>
                      send({
                        operation: "research.seen",
                        args: {
                          job_id: selected!.id,
                          run_id: selected!.last_run!.id,
                          mutation_key: identity(),
                        },
                      }),
                    )}>Mark report reviewed</button
                ><span class="muted"
                  >Also reviews findings added or changed in this report.</span
                >
              </div>{/if}
            <p class="muted">
              Last full check: {selected.last_checked
                ? researchTime(selected.last_checked)
                : "Not yet"}. Listings may have changed since they were checked.
            </p>
          </details>{/if}
        <nav class="filters" aria-label="Finding views">
          {#each tabs as tab}<button
              class:active={view === tab.view}
              aria-pressed={view === tab.view}
              disabled={busy || !!pending}
              on:click={() => chooseView(tab.view)}
              >{tab.label} <span>{tabCount(tab.view, counts)}</span></button
            >{/each}
        </nav>
        <form
          class="find"
          on:submit|preventDefault={() => {
            if (busy || pending) return;
            query = searchText.trim();
            offset = 0;
            void run(findingPage);
          }}
        >
          <input
            aria-label="Search findings"
            placeholder="Search findings"
            bind:value={searchText}
            maxlength="200"
            disabled={busy || !!pending}
          />
          <button disabled={busy || !!pending}>Find</button>
          {#if query}<button
              type="button"
              disabled={busy || !!pending}
              on:click={() => {
                query = searchText = "";
                offset = 0;
                void run(findingPage);
              }}>Clear</button
            >{/if}
        </form>
        <div
          class="finding-results"
          bind:this={findingsElement}
          style:min-height={`${findingsHeight}px`}
        >
          {#if error}<p role="alert" class="error">{error}</p>{/if}
          {#if busy}<p class="muted" role="status">Updating research…</p>{/if}
          <div class="findings" aria-busy={busy}>
            {#each findings as finding (finding.id)}<FindingCard
                {finding}
                disabled={ownerBusy || busy || !!pending}
                onReview={() => review(finding)}
                onDisposition={(value) => disposition(finding, value)}
                onDiscuss={() => discuss(finding)}
              />{/each}
          </div>
          {#if !busy && !error && !findings.length}<div class="empty">
              <h4>
                {query
                  ? "No matching findings."
                  : view === "inbox"
                    ? "You’re caught up on findings."
                    : view === "saved"
                      ? "No saved findings yet."
                      : view === "dismissed"
                        ? "No dismissed findings."
                        : view === "reviewed"
                          ? "No reviewed findings yet."
                          : "No findings yet."}
              </h4>
              <p>
                {query
                  ? "Try another title, source or detail."
                  : view === "inbox"
                    ? "Reviewed items remain in All findings. Saved items stay in Saved."
                    : view === "saved"
                      ? "Save a finding to keep it close."
                      : "Results from this search will stay together here."}
              </p>
            </div>{/if}
          {#if !busy && !error && totalFindings > 0}<footer class="pagination">
              <span
                >{offset + 1}–{Math.min(
                  offset + findings.length,
                  totalFindings,
                )} of
                {totalFindings} findings</span
              >
              <div class="actions">
                <button
                  disabled={busy || !!pending || offset === 0}
                  on:click={() => {
                    offset = Math.max(0, offset - pageSize);
                    void run(findingPage);
                  }}>Previous</button
                ><button
                  disabled={busy || !!pending || findingsNext === null}
                  on:click={() => {
                    offset = findingsNext ?? offset;
                    void run(findingPage);
                  }}>Next</button
                >
              </div>
            </footer>{/if}
        </div>
      </article>{/if}
  </div>
</section>

<style>
  .research {
    margin: 0 0 2rem;
    color: var(--ke-text-primary);
  }
  .heading,
  .actions,
  .pagination,
  .report-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .heading,
  .pagination {
    justify-content: space-between;
  }

  .heading h2 {
    margin: 0;
  }

  button {
    font-size: 12px;
    border: 1px solid var(--ke-border);
    border-radius: 5px;
    padding: 7px 10px;
    color: var(--ke-text-primary);
    background: transparent;
  }
  button:hover:not(:disabled) {
    background: var(--ke-surface-inset);
  }
  button:focus-visible,
  input:focus-visible,
  summary:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 3px;
  }
  .overview {
    display: flex;
    flex-wrap: wrap;
    gap: 10px 24px;
    margin: 12px 0 24px;
  }
  .overview > div {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }
  .overview strong {
    font: 500 16px/1.4 var(--ke-font-ui);
    font-variant-numeric: tabular-nums;
  }
  .overview span {
    font-size: 11px;
    color: var(--ke-text-secondary);
  }
  .workspace {
    display: grid;
    gap: 24px;
    min-width: 0;
  }
  .searches {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr));
    gap: 10px;
    align-items: stretch;
  }
  .assignment {
    display: grid;
    grid-template-rows: 1fr auto auto;
    gap: 6px;
    text-align: left;
    width: 100%;
    padding: 12px;
    border-radius: 8px;
  }
  .assignment.chosen {
    background: var(--ke-surface-inset);
    border-color: var(--ke-border-strong);
  }
  .name {
    font-size: 13px;
    font-weight: 550;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .change-count {
    font-size: 11px;
    color: var(--ke-text-secondary);
  }
  .has-updates {
    color: var(--ke-text-primary);
  }
  .next-check {
    display: block;
    margin-top: 4px;
    font-size: 10px;
  }
  .muted {
    color: var(--ke-text-secondary);
    font-size: 11px;
    line-height: 1.6;
  }
  .detail {
    min-width: 0;
  }
  .search-context {
    font: 500 13px/1.5 var(--ke-font-ui);
    margin: 0 0 12px;
    overflow-wrap: anywhere;
  }
  .criteria {
    margin-bottom: 18px;
    padding-bottom: 15px;
    border-bottom: 1px solid var(--ke-border);
  }
  summary {
    cursor: pointer;
    font-size: 12px;
    color: var(--ke-text-secondary);
  }
  .body {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: 13px;
    line-height: 1.8;
  }
  .report {
    background: color-mix(in srgb, var(--ke-marker-sage) 7%, var(--ke-surface));
    padding: 14px 16px;
    border-radius: 8px;
    margin: 16px 0 22px;
  }
  .report summary {
    color: var(--ke-text-primary);
  }
  .report-actions {
    align-items: baseline;
  }
  .report-actions .muted {
    font-size: 10px;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
    border-bottom: 1px solid var(--ke-border);
    padding-bottom: 10px;
    margin-top: 26px;
  }
  .filters button {
    border-color: transparent;
    font-size: 11px;
  }
  .filters button.active {
    background: var(--ke-surface-inset);
  }
  .filters span {
    margin-left: 4px;
    color: var(--ke-text-secondary);
    font-variant-numeric: tabular-nums;
  }
  .find {
    display: flex;
    gap: 8px;
    margin: 16px 0;
  }
  .find input {
    min-width: 0;
    flex: 1;
    font-size: 12px;
    padding: 8px 10px;
    background: transparent;
    border: 1px solid var(--ke-border);
    border-radius: 5px;
    color: var(--ke-text-primary);
  }
  .findings {
    display: grid;
    gap: 12px;
  }
  .finding-results {
    display: flow-root;
    overflow-anchor: none;
  }
  .pagination {
    font-size: 11px;
    color: var(--ke-text-secondary);
    margin-top: 18px;
  }
  .empty {
    padding: 26px 0;
    color: var(--ke-text-secondary);
    font-size: 13px;
    line-height: 1.7;
  }
  .empty h4 {
    color: var(--ke-text-primary);
    font-weight: 500;
    margin: 0;
  }
  .delete,
  .error {
    color: var(--ke-danger);
  }
  .error {
    overflow-wrap: anywhere;
  }
  .delete-confirmation {
    border: 1px solid var(--ke-border);
    border-radius: 8px;
    padding: 16px;
    margin: 16px 0;
    font-size: 13px;
  }
  .check-status {
    font-size: 12px;
    line-height: 1.8;
    border-left: 2px solid var(--ke-marker-sand);
    padding-left: 12px;
  }
</style>
