<script lang="ts">
  import { marked, type Token } from "marked";
  import SvelteMarkdown, { allowHtmlOnly } from "@humanspeak/svelte-markdown";
  import SourceLink from "$lib/features/chat/SourceLink.svelte";
  import { webUrl } from "$lib/api/web-links";

  export let source: string;
  const renderers = { link: SourceLink, image: null, html: allowHtmlOnly([]) };
  const sentences =
    typeof Intl.Segmenter === "function"
      ? new Intl.Segmenter(undefined, { granularity: "sentence" })
      : null;

  function readingParagraphs(token: Token): Token[] {
    // Older findings can be a single long paragraph. Only divide ordinary
    // prose at a sentence boundary. Links, lists and other markup stay intact.
    if (
      token.type !== "paragraph" ||
      !sentences ||
      token.text.length < 450 ||
      token.tokens.length !== 1 ||
      token.tokens[0].type !== "text" ||
      token.tokens[0].text !== token.text ||
      token.text.includes("\n")
    )
      return [token];
    const blocks: string[] = [];
    let paragraph = "";
    for (const { segment } of sentences.segment(token.text)) {
      if (paragraph.length >= 280) {
        blocks.push(paragraph);
        paragraph = "";
      }
      paragraph += segment;
    }
    if (paragraph) blocks.push(paragraph);
    return blocks.map((text) => ({
      type: "paragraph",
      raw: text,
      text,
      tokens: [{ type: "text", raw: text, text }],
    }));
  }

  $: blocks = marked.lexer(source).flatMap(readingParagraphs);
</script>

<div class="research-prose">
  <SvelteMarkdown
    source={blocks}
    {renderers}
    sanitizeUrl={(value) => webUrl(value) ?? ""}
    sanitizeAttributes={() => ({})}
  />
</div>

<style>
  .research-prose {
    max-width: 65ch;
    min-width: 0;
    color: var(--ke-text-primary);
    font-size: var(--ke-text-base);
    line-height: 1.72;
    overflow-wrap: anywhere;
  }
  .research-prose :global(:is(p, ul, ol, blockquote, pre)) {
    margin: 0 0 0.85em;
  }
  .research-prose :global(:is(ul, ol)) {
    padding-inline-start: 1.45em;
  }
  .research-prose :global(li + li) {
    margin-top: 0.25em;
  }
  .research-prose :global(:is(h1, h2, h3, h4, h5, h6)) {
    margin: 1em 0 0.3em;
    font: 600 1em/1.5 var(--ke-font-ui);
  }
  .research-prose :global(pre) {
    max-width: 100%;
    overflow-x: auto;
  }
  .research-prose :global(table) {
    display: block;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
  }
  .research-prose :global(:is(th, td)) {
    text-align: start;
    padding: 0.3em 0.65em;
    border-bottom: 1px solid var(--ke-border);
  }
  .research-prose > :global(:last-child) {
    margin-bottom: 0;
  }
</style>
