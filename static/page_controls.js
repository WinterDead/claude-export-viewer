// Small page behaviours that used to be inline attributes (v1.4.0 review, F1):
// the app's pages carry a Content Security Policy that forbids inline script,
// so these are wired here instead. Loaded deferred, so the DOM is parsed.
//
// - data-autosubmit on a <select> or checkbox submits its form on change (the
//   Theme control, the tag dropdowns and filter, Hide legacy memory).
// - data-stop-propagation on a link inside a <summary> stops its click from
//   reaching listeners on the surrounding <details> — exactly what the old
//   inline onclick="event.stopPropagation()" did.

(function () {
  "use strict";

  document.addEventListener("change", (event) => {
    const el = event.target;
    if (el instanceof HTMLElement && el.hasAttribute("data-autosubmit") && el.form) {
      el.form.submit();
    }
  });

  document.querySelectorAll("[data-stop-propagation]").forEach((el) => {
    el.addEventListener("click", (event) => event.stopPropagation());
  });
})();
