// Export / copy / print controls on the conversation detail page (v1.2
// Session N).
//
// The Export button is a plain GET form submit — no JS involved. This file
// wires the two buttons that need scripting:
//   - Copy conversation: fetches the single-export output for the current
//     branch combination and checkbox states WITHOUT frontmatter
//     (frontmatter=0 — the H1 title stays) and writes it to the clipboard,
//     with a brief "Copied" confirmation.
//   - Print / Save as PDF: window.print().

(function () {
  "use strict";

  const form = document.getElementById("export-form");
  if (!form) return;

  const copyBtn = form.querySelector(".copy-btn");
  const confirmEl = form.querySelector(".copy-confirm");
  // The print button lives OUTSIDE the export form (v1.2.5 Session O): the
  // toggles don't apply to printing, so it sits after the fieldset group.
  const printBtn = document.querySelector(".conv-actions .print-btn");

  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      // FormData carries exactly what an Export submit would: the hidden
      // branch inputs and each checkbox/hidden pair (the endpoint reads the
      // first value per name, so a ticked box wins over its 0 fallback).
      const params = new URLSearchParams(new FormData(form));
      params.append("frontmatter", "0");
      const url = `${copyBtn.dataset.exportUrl}?${params.toString()}`;
      try {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`export returned ${resp.status}`);
        await navigator.clipboard.writeText(await resp.text());
        if (confirmEl) {
          confirmEl.hidden = false;
          setTimeout(() => {
            confirmEl.hidden = true;
          }, 2000);
        }
      } catch (err) {
        console.error("Copy conversation failed:", err);
        window.alert("Copy failed — see the browser console for details.");
      }
    });
  }

  if (printBtn) {
    printBtn.addEventListener("click", () => window.print());
  }
})();
