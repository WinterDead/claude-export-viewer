/* Collapsible memory blocks (plan §12 polish).
 *
 * Two responsibilities:
 *
 * 1. Anchor-link expansion. When the URL hash points inside a closed
 *    <details> (or several nested ones), the browser scrolls to nothing
 *    visible. This handler walks up from the targeted element on initial
 *    load AND on hashchange, opening every <details> ancestor so the user
 *    lands on visible content. CSS-only :target won't do this — it matches
 *    only the directly-targeted element, not its <details> ancestors.
 *
 * 2. Per-memory expand/collapse-all-sections toggle. Each memory block can
 *    have many <details class="memory-section"> children. The two buttons
 *    inside .memory-block-controls flip every section's .open within their
 *    OWN .memory-block — toggling Memory A doesn't change Memory B.
 *
 * No framework, no state. The print stylesheet already force-opens
 * <details>, so collapsibility is invisible on paper.
 */

(() => {
  function expandAncestorDetails(target) {
    let el = target;
    while (el && el !== document.body) {
      if (el.tagName === "DETAILS") el.open = true;
      el = el.parentElement;
    }
  }

  function applyHash() {
    if (!location.hash) return;
    let id;
    try {
      id = decodeURIComponent(location.hash.slice(1));
    } catch (_e) {
      return;
    }
    if (!id) return;
    const target = document.getElementById(id);
    if (!target) return;
    expandAncestorDetails(target);
    // Re-scroll: the expansion changes layout, and the browser's initial
    // scroll-to-fragment will have landed mid-collapse-animation territory.
    target.scrollIntoView({ block: "start" });
  }

  document.addEventListener("DOMContentLoaded", applyHash);
  window.addEventListener("hashchange", applyHash);

  // Per-memory expand/collapse-all-sections.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".memory-section-toggle");
    if (!btn) return;
    e.preventDefault();
    const memory = btn.closest(".memory-block");
    if (!memory) return;
    const open = btn.dataset.action === "expand";
    memory.querySelectorAll(".memory-section").forEach((sec) => {
      sec.open = open;
    });
  });

  // Page-level expand/collapse-all for the help page (v1.1 Session C). The
  // per-memory handler above is scoped to its own .memory-block; these two
  // buttons act on every top-level section in .help-sections at once.
  // Collapse returns the page to its default state — top-level sections
  // closed, sub-sections open — so reopening a section always shows its
  // sub-sections expanded (their default).
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".help-section-toggle");
    if (!btn) return;
    e.preventDefault();
    const container = document.querySelector(".help-sections");
    if (!container) return;
    const open = btn.dataset.action === "expand";
    container.querySelectorAll("details.help-block").forEach((sec) => {
      sec.open = open;
    });
    container.querySelectorAll("details.memory-section").forEach((sec) => {
      sec.open = true;
    });
  });
})();
