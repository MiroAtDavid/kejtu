<script lang="ts">
  import "$lib/design/collection.css";
  import { onMount, tick } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { toPageTop } from "$lib/design/collection";
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
  import ResearchProse from "./ResearchProse.svelte";
  import KejtuMark from "$lib/design/KejtuMark.svelte";
  import { rotatingKejtu } from "$lib/design/theme";
  import {
    changes,
    emptyCounts,
    findingHost,
    findingState,
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
  /** Searches are set up by asking Kejtu: this starts a conversation for one. */
  export let onNew: () => void = () => {};
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
    { view: "all", label: "All" },
    { view: "saved", label: "Saved" },
    { view: "reviewed", label: "Reviewed" },
    { view: "dismissed", label: "Dismissed" },
  ];
  let pending: Change | null = null;
  let deleting: { id: string; revision: number; title: string } | null = null;
  let notice = "";
  let findingsElement: HTMLDivElement | undefined;
  let findingsHeight = 0;
  // A finding has its own page: while it is open the list is not shown.
  let opened: ResearchResult | null = null;
  let criteriaOpen = false,
    reportOpen = false;
  let root: HTMLElement;
  let findingTitle: HTMLElement;
  async function openFinding(finding: ResearchResult) {
    if (busy || pending) return;
    opened = finding;
    await tick();
    findingTitle?.focus({ preventScroll: true });
    toPageTop(root);
  }
  // Back returns to the card the finding was opened from.
  async function back() {
    const id = opened?.id;
    opened = null;
    await tick();
    root?.querySelector<HTMLElement>(`[data-finding="${id}"] .open`)?.focus();
  }
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
  // A row's next check, short like a task's date: "Nov 20, 08:00".
  function nextLabel(at: string, zone: string) {
    try {
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: zone,
      }).format(new Date(at));
    } catch {
      return when(at, zone);
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
      opened = null;
      criteriaOpen = reportOpen = false;
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
    if (opened) {
      // The open page follows the current record. Only this page of the list
      // was read: a finding missing from it may have left the view (reviewed,
      // dismissed) or moved to another page. Its page closes, since its
      // current record is not known, and the list says just that much.
      const title = opened.title;
      opened = findings.find((item) => item.id === opened?.id) ?? null;
      if (!opened) notice = `“${title}” is no longer on this page.`;
    }
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
      opened = null;
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

<section
  aria-label="Kejtu's research"
  class="research collection-page"
  bind:this={root}
>
  {#if opened && selected}
    {@const saved = opened.disposition === "saved"}
    <div class="finding-page" role="region" aria-label="Finding">
      <button
        class="cp-back"
        aria-label="Back to findings"
        disabled={busy || !!pending}
        on:click={back}><Icon name="left" size={13} />{selected.title}</button
      >
      <div class="finding-head">
        <h3 class="finding-title" tabindex="-1" bind:this={findingTitle}>
          {opened.title}
        </h3>
        <div class="actions">
          {#if opened.new && opened.disposition !== "dismissed"}<button
              class="cp-action primary"
              disabled={ownerBusy || busy || !!pending}
              on:click={() => opened && review(opened)}>Mark reviewed</button
            >{/if}
          <button
            class="cp-action"
            disabled={ownerBusy || busy || !!pending}
            on:click={() =>
              opened && disposition(opened, saved ? "open" : "saved")}
            >{saved ? "Unsave" : "Save"}</button
          >
          <button
            class="cp-action"
            disabled={ownerBusy || busy || !!pending}
            on:click={() =>
              opened &&
              disposition(
                opened,
                opened.disposition === "dismissed" ? "open" : "dismissed",
              )}
            >{opened.disposition === "dismissed"
              ? "Restore"
              : "Dismiss"}</button
          >
          <button
            class="cp-action"
            disabled={ownerBusy || busy || !!pending}
            on:click={() => opened && discuss(opened)}>Discuss</button
          >
        </div>
      </div>
      {#if error}<p role="alert" class="cp-note">{error}</p>{/if}
      {#if pending}<p role="status" class="cp-note">
          A change is awaiting confirmation. <button
            disabled={busy}
            on:click={() => run(() => send())}>Check saved change</button
          >
        </p>{/if}
      {#if busy}<p class="cp-hint updating" role="status">
          Updating research…
        </p>{/if}
      <div class="cp-item">
        <div class="cp-main">
          <section class="cp-section">
            <h4 class="cp-cap">Summary</h4>
            <ResearchProse source={opened.details} />
          </section>
          {#if opened.unknowns}<section class="cp-section">
              <h4 class="cp-cap">Still to verify</h4>
              <ResearchProse source={opened.unknowns} />
            </section>{/if}
        </div>
        <dl class="cp-side facts">
          <div class="cp-row">
            <dt class="cp-key">Status</dt>
            <dd>{findingState(opened)}{saved ? " · Saved" : ""}</dd>
          </div>
          <div class="cp-row">
            <dt class="cp-key">Source</dt>
            <dd>
              <a href={opened.url} target="_blank" rel="noreferrer"
                >{findingHost(opened.url)}<span aria-hidden="true"> ↗</span></a
              >
            </dd>
          </div>
          <div class="cp-row">
            <dt class="cp-key">First found</dt>
            <dd>{when(opened.first_seen)}</dd>
          </div>
          <div class="cp-row">
            <dt class="cp-key">Last seen</dt>
            <dd>{when(opened.last_seen)}</dd>
          </div>
        </dl>
      </div>
    </div>
  {:else}
    <div class="cp-head">
      <div>
        <h2 class="cp-title">Research</h2>
        <p class="cp-status">
          {#if ready}{totalSearches}
            {totalSearches === 1 ? "search" : "searches"} · {unread(allCounts)} to
            review · {allCounts.saved} saved{:else}&nbsp;{/if}<!-- Progress shares
            this line and shows only when the wait is noticeable, so returning
            to the page neither moves nor flashes the cards. -->{#if busy}<span
              class="cp-late"
              ><span aria-hidden="true"> · </span><span role="status"
                >Updating research…</span
              ></span
            >{/if}
        </p>
      </div>
      <button
        class="cp-action"
        disabled={ownerBusy || busy || !!pending}
        on:click={onNew}>New research</button
      >
    </div>
    <!-- The same bar as the Shelf's: the selected search's Criteria and Report
         where the Shelf has its views, then Find and Refresh. -->
    {#if items.length || selected}<div class="cp-bar research-bar">
        <div class="cp-pills" role="group" aria-label="Selected search">
          {#if selected}<button
              class="cp-pill"
              aria-pressed={criteriaOpen}
              aria-expanded={criteriaOpen}
              on:click={() => (criteriaOpen = !criteriaOpen)}>Criteria</button
            >{#if summary}<button
                class="cp-pill"
                aria-pressed={reportOpen}
                aria-expanded={reportOpen}
                on:click={() => (reportOpen = !reportOpen)}
                >{#if selected.new_result}<span class="dot" aria-hidden="true"
                  ></span>{/if}Report{coverage === "partial"
                  ? " · incomplete"
                  : ""}</button
              >{/if}{/if}
        </div>
        {#if selected}<form
            class="cp-find"
            role="search"
            on:submit|preventDefault={() => {
              if (busy || pending) return;
              query = searchText.trim();
              offset = 0;
              void run(findingPage);
            }}
          >
            <button
              class="cp-icon"
              aria-label="Find"
              title="Find"
              disabled={busy || !!pending}
              ><Icon name="search" size={14} /></button
            >
            <input
              type="search"
              aria-label="Search findings"
              placeholder="Find"
              bind:value={searchText}
              maxlength="200"
              disabled={busy || !!pending}
            />
            {#if query}<button
                type="button"
                class="cp-text"
                disabled={busy || !!pending}
                on:click={() => {
                  query = searchText = "";
                  offset = 0;
                  void run(findingPage);
                }}>Clear</button
              >{/if}
          </form>{/if}
        <button
          class="cp-icon"
          aria-label="Refresh"
          title="Refresh"
          disabled={busy || !!pending}
          on:click={() =>
            run(async () => {
              await list();
              if (selected) await read(selected.id);
              onChanged();
            })}><Icon name="refresh" size={14} /></button
        >
      </div>{/if}

    {#if error && !selected}<p role="alert" class="cp-note">{error}</p>{/if}
    {#if notice}<p role="status" class="cp-note">{notice}</p>{/if}
    {#if pending}<p role="status" class="cp-note">
        A change is awaiting confirmation. <button
          disabled={busy}
          on:click={() => run(() => send())}>Check saved change</button
        >
      </p>{/if}
    {#if ready && !items.length}<div class="empty">
        <h3>A place for what Kejtu finds.</h3>
        <p>
          Try “Check daily for a flat matching these requirements…” and describe
          what matters to you.
        </p>
      </div>{/if}
    {#if items.length}<ul
        class="cp-lines search-list"
        aria-label="Research searches"
      >
        {#each items as item (item.id)}
          {@const status = searchStatus(item)}
          {@const fresh = unread(item.counts) > 0}
          <li class="cp-line" class:selected={selected?.id === item.id}>
            <button
              class="search-row"
              aria-pressed={selected?.id === item.id}
              disabled={busy || !!pending}
              on:click={() => run(() => read(item.id, true))}
            >
              <!-- Like a task's tick: empty, or filled when findings are new. -->
              <span class="news" class:fresh aria-hidden="true"></span>
              <span class="name" title={item.title}>{item.title}</span>
              <!-- A search that is checking right now shows Kejtu at the desk. -->
              {#if item.last_run?.status === "running" && item.last_run.error_code !== "research_paused"}<span
                  class="run-mark"
                  title="Checking now"
                  ><KejtuMark
                    state={$rotatingKejtu ? "desk" : "idle"}
                    size={16}
                  /></span
                >{/if}
              <span class="count">{fresh ? changes(item.counts) : ""}</span>
              <span class="state">{status !== "Up to date" ? status : ""}</span>
              <span class="next"
                >{item.enabled && item.next_at
                  ? `Next ${nextLabel(item.next_at, item.timezone)}`
                  : ""}</span
              >
            </button>
          </li>
        {/each}
      </ul>
      {#if next !== null}<button
          class="cp-chip more-searches"
          disabled={busy || !!pending}
          on:click={() => run(() => list(true))}>More searches</button
        >{/if}{/if}
    {#if selected}<article class="detail" aria-label="Selected search">
        <!-- A linked search can be outside the current overview page. Only
               show its name here when no visible pill identifies it. -->
        {#if !items.some((item) => item.id === selected?.id)}
          <h3 class="search-context">{selected.title}</h3>
        {/if}
        <div class="cp-chips">
          <nav class="cp-chip-group" aria-label="Finding views">
            {#each tabs as tab}<button
                class="cp-chip"
                aria-pressed={view === tab.view}
                disabled={busy || !!pending}
                on:click={() => chooseView(tab.view)}
                >{tab.label}
                <span class="cp-count">{tabCount(tab.view, counts)}</span
                ></button
              >{/each}
          </nav>
        </div>
        {#if criteriaOpen}<section
            class="cp-section panel"
            aria-label="Search criteria & controls"
          >
            <h4 class="cp-cap">Search criteria & controls</h4>
            <p class="body">{selected.instructions}</p>
            <p class="cp-hint">
              {selected.schedule.repeat === "none"
                ? "Once"
                : `Repeats ${selected.schedule.repeat}, every ${selected.schedule.interval}`}
              · {when(selected.schedule.starts_at, selected.schedule.timezone)}
              {#if selected.enabled && selected.next_at}· Next check {researchTime(
                  selected.next_at,
                  selected.schedule.timezone,
                )}{/if}
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
          </section>{/if}
        {#if deleting}
          <section class="cp-section panel" aria-label="Confirm task deletion">
            <h4 class="confirm-title">Delete “{deleting.title}”?</h4>
            <p>
              This permanently removes the task, its schedule, run history and
              all findings, including saved findings. A running check will be
              stopped. This cannot be undone.
            </p>
            <p class="cp-hint">
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
          <p class="cp-note check-status">
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

        {#if reportOpen && summary}<section
            class="cp-section panel"
            aria-label="Search report"
          >
            <h4 class="cp-cap">
              {coverage === "partial"
                ? "Search report · Incomplete search"
                : "Search report"}
            </h4>
            {#if coverage === "partial"}<p class="cp-hint">
                This report covers only part of the requested search. Read the
                summary below for details.
              </p>{/if}
            {#if selected.last_run?.finished_at}<p class="cp-hint">
                Report saved <time
                  datetime={selected.last_run.finished_at}
                  title={when(selected.last_run.finished_at)}
                  >{researchTime(selected.last_run.finished_at)}</time
                >
              </p>{/if}
            <ResearchProse source={summary} />
            {#if selected.new_result && selected.last_run}<div class="actions">
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
                ><span class="cp-hint"
                  >Also reviews findings added or changed in this report.</span
                >
              </div>{/if}
            <p class="cp-hint">
              Last full check: {selected.last_checked
                ? researchTime(selected.last_checked)
                : "Not yet"}. Listings may have changed since they were checked.
            </p>
          </section>{/if}
        <div
          class="finding-results"
          bind:this={findingsElement}
          style:min-height={`${findingsHeight}px`}
        >
          {#if error}<p role="alert" class="cp-note">{error}</p>{/if}
          <div class="findings" aria-busy={busy}>
            {#each findings as finding (finding.id)}<FindingCard
                {finding}
                disabled={ownerBusy || busy || !!pending}
                onOpen={() => openFinding(finding)}
                onReview={() => review(finding)}
                onDisposition={(value) => disposition(finding, value)}
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
  {/if}
</section>

<style>
  .research {
    margin: 0 0 2rem;
  }
  .actions,
  .pagination {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .pagination {
    justify-content: space-between;
    margin-top: 16px;
    color: var(--ke-text-secondary);
  }
  /* Searches as rows, like the Tasks list: same length, details at the end. */
  /* Below the bar at the Shelf's spacing (its chips follow its bar). */
  .search-list {
    margin-top: 12px;
  }
  .search-list .cp-line {
    padding: 0;
  }
  .search-list .cp-line.selected {
    border-color: var(--ke-text-tertiary);
  }
  .search-row {
    display: flex;
    flex: 1;
    align-items: center;
    gap: 10px;
    min-width: 0;
    min-height: 42px;
    margin: 0;
    padding: 0 14px;
    border: 0;
    border-radius: inherit;
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .search-row:hover:not(:disabled) {
    background: transparent;
  }
  .news {
    flex: none;
    box-sizing: border-box;
    width: 16px;
    height: 16px;
    border: 1.5px solid var(--ke-border-strong);
    border-radius: 50%;
  }
  .news.fresh {
    border-color: var(--ke-accent);
    background: var(--ke-accent);
  }
  .name {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--ke-text-sm);
  }
  .selected .name {
    font-weight: 500;
  }
  .count,
  .state,
  .next {
    flex: none;
    color: var(--ke-text-tertiary);
    font-size: 11.5px;
    text-align: right;
    white-space: nowrap;
  }
  .count {
    min-width: 48px;
    color: var(--ke-text-primary);
    font-weight: 500;
  }
  .state {
    min-width: 92px;
  }
  .next {
    min-width: 104px;
  }
  .more-searches {
    margin-top: 8px;
  }
  @media (max-width: 560px) {
    .next {
      display: none;
    }
    .state {
      min-width: 0;
    }
  }
  .run-mark {
    display: inline-flex;
    flex: none;
    color: var(--ke-accent);
  }
  .dot {
    flex: none;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--ke-marker-sage);
  }
  .detail {
    min-width: 0;
  }
  .search-context {
    margin: 16px 0 0;
    font: 500 13px/1.5 var(--ke-font-ui);
    overflow-wrap: anywhere;
  }
  .panel {
    margin-top: 12px;
    border-top: 1px solid var(--ke-border);
    padding: 12px 0;
  }
  .panel p {
    margin: 0 0 8px;
  }
  .panel .actions {
    margin-top: 10px;
  }
  .confirm-title {
    margin: 0 0 8px;
    font: 500 13px/1.5 var(--ke-font-ui);
  }
  .body {
    margin: 0 0 8px;
    font-size: var(--ke-text-sm);
    line-height: 1.7;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .check-status {
    line-height: 1.7;
  }
  .updating {
    margin: 0 0 8px;
  }
  .finding-results {
    display: flow-root;
    margin-top: 16px;
    overflow-anchor: none;
  }
  .findings {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(230px, 100%), 1fr));
    gap: 12px;
  }
  .empty {
    padding: 26px 0;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
    line-height: 1.7;
  }
  .empty :is(h3, h4) {
    margin: 0;
    color: var(--ke-text-primary);
    font: 500 var(--ke-text-sm) / 1.7 var(--ke-font-ui);
  }
  .empty p {
    margin: 0;
  }
  .delete {
    color: var(--ke-danger);
  }
  [role="alert"] {
    overflow-wrap: anywhere;
  }
  /* A finding's own page. */
  .finding-page .cp-section > .cp-cap {
    margin-bottom: 8px;
    color: var(--ke-text-primary);
    font: 500 var(--ke-text-sm)/1.5 var(--ke-font-ui);
  }
  .finding-head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px 16px;
    margin-top: 10px;
  }
  .finding-title {
    flex: 1 1 280px;
    min-width: 0;
    margin: 0;
    outline: 0;
    font: 400 28px/1.3 var(--ke-font-heading);
    letter-spacing: -0.5px;
    overflow-wrap: anywhere;
  }
  .finding-page .updating {
    margin: 12px 0 0;
  }
  .facts {
    margin: 0;
  }
  .facts .cp-row {
    display: block;
    min-height: 0;
    padding: 5px 12px;
  }
  .facts dd {
    flex: 1;
    min-width: 0;
    margin: 0;
    overflow-wrap: anywhere;
  }
  .facts a {
    color: inherit;
    text-decoration: underline;
    text-decoration-color: var(--ke-border-strong);
    text-underline-offset: 3px;
  }
  @media (max-width: 650px) {
    .finding-title {
      font-size: 24px;
    }
  }
</style>
