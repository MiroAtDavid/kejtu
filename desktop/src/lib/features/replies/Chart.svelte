<script lang="ts">
  import { untrack } from "svelte";
  import { LineChart, BarChart, ScatterChart } from "layerchart/svg";
  import { scaleLinear, scaleLog } from "d3-scale";
  import type { ChartSpec } from "./chart";
  import { freshChart, type ChartState } from "./chart";
  import ChartSources from "./ChartSources.svelte";
  import type { WebSourceSummary } from "$lib/api/wire.generated";
  let {
    spec,
    sources = [],
  }: { spec: ChartSpec; sources?: WebSourceSummary[] } = $props();
  const fieldId = $props.id();
  const hasSourceLinks = $derived(
    !!spec.sourceRefs?.length &&
      spec.sourceRefs.every((ref) => sources.some((s) => s.source_id === ref)),
  );
  // ChartBlock keys this component by its exact original JSON body.
  let view = $state<ChartState>(untrack(() => freshChart(spec)));
  const numeric = $derived(typeof spec.values[0].x === "number");
  const kind = $derived(numeric ? view.kind : "bar");
  const positive = $derived(spec.values.every((d) => d.y > 0));
  const logarithmic = $derived(
    view.scale === "log" && kind !== "bar" && positive,
  );
  const point = $derived(
    spec.values[Math.min(view.point, spec.values.length - 1)],
  );
  const limits = $derived([
    Math.min(...spec.values.map((d) => d.y)),
    Math.max(...spec.values.map((d) => d.y)),
  ]);
  const yDomain = $derived(
    logarithmic
      ? limits[0] === limits[1]
        ? [limits[0] / 2, limits[1] * 2]
        : limits
      : [
          Math.min(0, limits[0]),
          Math.max(0, limits[1]) || (limits[0] === 0 ? 1 : 0),
        ],
  );
  const yTicks = $derived(
    logarithmic
      ? [...new Set(spec.values.map((d) => d.y))].sort((a, b) => a - b)
      : 5,
  );
  const chartProps = $derived({
    xAxis: {
      ticks: spec.values
        .filter(
          (_, i) => i % Math.max(1, Math.ceil(spec.values.length / 8)) === 0,
        )
        .map((d) => d.x),
      label: spec.xLabel,
      format: (v: number) => String(v),
    },
    yAxis: {
      ticks: yTicks,
      format: (v: number) => String(v),
    },
    grid: {
      x: false,
      y: true,
      yTicks: yTicks,
    },
    points: { r: 4 },
    tooltip: { hideTotal: true },
  });
  const annotations = $derived([
    {
      type: "point" as const,
      x: point.x,
      y: point.y,
      r: 7,
      props: {
        circle: {
          fill: "var(--ke-surface-raised)",
          stroke: "var(--ke-accent)",
          strokeWidth: 2,
        },
      },
    },
  ]);
  function update(patch: Partial<ChartState>) {
    view = { ...view, ...patch };
  }
</script>

<figure
  class="rich-chart"
  data-chart={kind}
  data-scale={logarithmic ? "log" : "linear"}
>
  <figcaption>
    <strong>{spec.title}</strong><span
      >{spec.provenance === "illustrative"
        ? "Illustrative values"
        : hasSourceLinks
          ? "Reported values · check source"
          : "Provided values · source link unavailable"} · {spec.note}</span
    >
  </figcaption>
  <fieldset class="rich-chart-controls">
    <legend class="rich-sr-only">Chart display</legend>
    <div class="rich-choice-group" role="group" aria-label="Chart type">
      <button
        aria-pressed={kind === "line"}
        disabled={!numeric}
        onclick={() => update({ kind: "line" })}>Line</button
      >
      <button
        aria-pressed={kind === "bar"}
        onclick={() => update({ kind: "bar", scale: "linear" })}>Bars</button
      >
      <button
        aria-pressed={kind === "scatter"}
        disabled={!numeric}
        onclick={() => update({ kind: "scatter" })}>Points</button
      >
    </div>
    <div class="rich-choice-group" role="group" aria-label="Vertical scale">
      <button
        aria-pressed={!logarithmic}
        onclick={() => update({ scale: "linear" })}>Linear</button
      >
      <button
        aria-pressed={logarithmic}
        disabled={kind === "bar" || !positive}
        onclick={() => update({ scale: "log" })}>Logarithmic</button
      >
    </div>
  </fieldset>
  <p class="rich-axis-caption">
    {spec.yLabel} · {logarithmic ? "logarithmic scale" : "linear scale"}
  </p>
  <div
    class="rich-plot"
    role="img"
    aria-label={`${spec.yLabel} by ${spec.xLabel}, ${logarithmic ? "logarithmic" : "linear"} vertical scale. Exact values are available below.`}
  >
    <div aria-hidden="true">
      {#key `${kind}-${logarithmic}`}
        {#if kind === "bar"}
          <BarChart
            data={spec.values}
            x="x"
            y="y"
            orientation="vertical"
            {yDomain}
            yBaseline={0}
            yNice={false}
            motion="none"
            rule={false}
            highlight={false}
            {annotations}
            height={280}
            padding={{ left: 44, right: 18, top: 12, bottom: 42 }}
            props={chartProps}
            series={[{ key: "cells", label: spec.yLabel, value: "y" }]}
          />
        {:else if kind === "scatter"}
          <ScatterChart
            data={spec.values}
            x="x"
            y="y"
            xScale={scaleLinear()}
            yScale={logarithmic ? scaleLog() : scaleLinear()}
            {yDomain}
            yBaseline={logarithmic ? null : 0}
            yNice={false}
            motion="none"
            rule={false}
            highlight={false}
            {annotations}
            height={280}
            padding={{ left: 44, right: 18, top: 12, bottom: 42 }}
            props={chartProps}
            series={[{ key: "cells", label: spec.yLabel, value: "y" }]}
          />
        {:else}
          <LineChart
            data={spec.values}
            x="x"
            y="y"
            xScale={scaleLinear()}
            yScale={logarithmic ? scaleLog() : scaleLinear()}
            {yDomain}
            yBaseline={logarithmic ? null : 0}
            yNice={false}
            motion="none"
            rule={false}
            highlight={false}
            {annotations}
            points
            height={280}
            padding={{ left: 44, right: 18, top: 12, bottom: 42 }}
            props={chartProps}
            series={[{ key: "cells", label: spec.yLabel, value: "y" }]}
          />
        {/if}
      {/key}
    </div>
  </div>
  <p class="rich-scale-note">
    {kind === "bar"
      ? "Bars start at zero. Use Line or Points to explore the logarithmic scale."
      : logarithmic
        ? "Equal vertical gaps mean equal multiplying factors."
        : "Equal vertical gaps mean equal amounts added."}
  </p>
  <div class="rich-point-inspector">
    <label for={`point-${fieldId}`}>Inspect {spec.xLabel}</label>
    <input
      id={`point-${fieldId}`}
      type="range"
      min="0"
      max={spec.values.length - 1}
      step="1"
      value={Math.min(view.point, spec.values.length - 1)}
      aria-valuetext={`${spec.xLabel} ${point.x}: ${point.y} ${spec.yLabel}`}
      oninput={(e) => update({ point: Number(e.currentTarget.value) })}
    />
    <output aria-live="polite"
      >{point.x} · <strong>{point.y} {spec.yLabel}</strong></output
    >
  </div>
  <div class="rich-chart-footer">
    <button
      class="rich-text-button"
      aria-expanded={view.table}
      onclick={() => update({ table: !view.table })}
      >{view.table ? "Hide values" : "Show values"}</button
    >
  </div>
  {#if view.table}<table class="rich-values">
      <caption>Original values</caption><thead
        ><tr
          ><th scope="col">{spec.xLabel}</th><th scope="col">{spec.yLabel}</th
          ></tr
        ></thead
      ><tbody
        >{#each spec.values as d}<tr class:selected={d.x === point.x}
            ><th scope="row">{d.x}</th><td>{d.y}</td></tr
          >{/each}</tbody
      >
    </table>{/if}
  <ChartSources refs={spec.sourceRefs} {sources} />
</figure>
