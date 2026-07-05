// Bulk-action controller for /conversations (plan §12 step 4.5; export
// action added in v1.2 Session N).
//
// Responsibilities:
//   - show/hide the sticky action bar based on whether anything is selected
//   - update count and the "K already tagged" indicator
//   - enable/disable Tag, Retag, Untag, and Export selected appropriately
//   - shift-click range select on the row checkboxes
//
// In inspect mode the bar carries only the Export selected action — the
// tagging controls are structurally absent from the template, so every
// tagging-related reference below is null-guarded.
//
// The form-attribute trick (`form="bulk-form"` on each checkbox) means the
// browser includes selected uuids in the bulk-form submission automatically;
// this script does no manual serialisation.

(function () {
  "use strict";

  const bar = document.getElementById("bulk-form");
  if (!bar) return;

  const checkboxes = Array.from(document.querySelectorAll(".conv-checkbox"));
  if (!checkboxes.length) return;

  const countEl = bar.querySelector(".bulk-count");
  const alreadyEl = bar.querySelector(".bulk-already-tagged");
  const picker = bar.querySelector(".bulk-project-picker");
  const btnTag = bar.querySelector(".bulk-btn-tag");
  const btnRetag = bar.querySelector(".bulk-btn-retag");
  const btnUntag = bar.querySelector(".bulk-btn-untag");
  const btnExport = bar.querySelector(".bulk-btn-export");

  let lastClicked = null;

  function update() {
    const selected = checkboxes.filter((c) => c.checked);
    const n = selected.length;

    bar.dataset.count = String(n);
    countEl.textContent = `${n} selected`;
    countEl.dataset.count = String(n);

    if (btnExport) {
      btnExport.disabled = n === 0;
    }

    if (!picker) return; // inspect mode — no tagging controls in the bar

    const alreadyTagged = selected.filter((c) => {
      const row = c.closest(".conv-row");
      return row && row.dataset.tagged;
    }).length;

    if (alreadyTagged > 0) {
      alreadyEl.textContent = `${alreadyTagged} already tagged`;
      alreadyEl.dataset.count = String(alreadyTagged);
      alreadyEl.hidden = false;
    } else {
      alreadyEl.hidden = true;
      alreadyEl.dataset.count = "0";
    }

    const hasPicker = !!picker.value;
    btnTag.disabled = n === 0 || !hasPicker;
    btnUntag.disabled = n === 0;
    btnRetag.hidden = alreadyTagged === 0;
    btnRetag.disabled = alreadyTagged === 0 || !hasPicker;
  }

  checkboxes.forEach((cb) => {
    cb.addEventListener("click", (e) => {
      // Shift-click range select: toggle every checkbox between this one and
      // the last clicked to match this checkbox's new state.
      if (e.shiftKey && lastClicked && lastClicked !== cb) {
        const a = checkboxes.indexOf(lastClicked);
        const b = checkboxes.indexOf(cb);
        const lo = Math.min(a, b);
        const hi = Math.max(a, b);
        const target = cb.checked;
        for (let i = lo; i <= hi; i++) {
          checkboxes[i].checked = target;
        }
      }
      lastClicked = cb;
      update();
    });
  });

  if (picker) {
    picker.addEventListener("change", update);
  }

  // Initial paint (handles BFCache restores where checkboxes may already be set).
  update();
})();
