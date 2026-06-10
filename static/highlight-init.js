/* Client-side syntax highlighting (v1.1 Session F).
 *
 * Runs the vendored highlight.js (static/vendor/highlightjs/) over fenced
 * code blocks AFTER sanitisation, on the rendered DOM. Highlighting
 * client-side is a security decision, not a convenience: server-side
 * Pygments would need its <span class="…"> token markup to survive bleach,
 * loosening the sanitiser's allowlist for all rendered content. Don't move
 * this server-side without a written brief.
 *
 * Scope: `.markdown-body pre code` only — every markdown-rendered container
 * (conversation messages, knowledge documents, memory blocks, help page)
 * wraps its output in .markdown-body, and search-result snippets
 * (.search-snippet) deliberately don't, so they are excluded by
 * construction. Inline <code> is never highlighted (the `pre` in the
 * selector).
 *
 * The <mark> guard is load-bearing: highlight.js rebuilds an element from
 * its text content, which would destroy the <mark> tags search highlighting
 * injects. Any code element carrying a <mark> descendant is skipped —
 * search hits stay marked, unhighlighted. That's intended, not a bug.
 *
 * Pages re-render server-side on navigation, so one-shot DOMContentLoaded
 * init is sufficient — no mutation observers. When a language-x class is
 * present highlight.js uses it; otherwise it auto-detects.
 */

(() => {
  document.addEventListener("DOMContentLoaded", () => {
    if (typeof hljs === "undefined") return; // vendored bundle failed to load
    document.querySelectorAll(".markdown-body pre code").forEach((code) => {
      if (code.querySelector("mark")) return;
      hljs.highlightElement(code);
    });
  });
})();
