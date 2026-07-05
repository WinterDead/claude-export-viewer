// Print-only links appendix for the conversation detail page (v1.2 Session
// N, task B4).
//
// Builds, on page load, a "Links" section at the end of the conversation
// listing every content link (.markdown-body scope — viewer chrome is
// excluded by the selector), as "N. <link text> — <full URL>", and puts a
// small [N] marker after each link's text inline. Repeated URLs share one
// number. Both the markers and the appendix carry print-only classes, so
// screen rendering is unchanged.
//
// WYSIWYG rule: links inside collapsed sections (thinking blocks,
// attachments) appear only if the section is open — the same rule the rest
// of printing follows. The appendix is therefore rebuilt on beforeprint,
// so sections the reader opened or closed since page load are honoured at
// print time.

(function () {
  "use strict";

  const scope = document.querySelector(".conv-detail");
  if (!scope) return;

  function isVisibleForPrint(el) {
    // A link prints only if no enclosing <details> is closed.
    for (let d = el.closest("details"); d; d = d.parentElement.closest("details")) {
      if (!d.open) return false;
    }
    return true;
  }

  function rebuild() {
    const old = scope.querySelector(".print-links");
    if (old) old.remove();

    const anchors = Array.from(scope.querySelectorAll(".markdown-body a[href]"));
    const numberByUrl = new Map(); // href → number (first-appearance order)
    const entries = []; // [{text, url}] in number order

    anchors.forEach((a) => {
      let marker = a.nextElementSibling;
      if (!marker || !marker.classList || !marker.classList.contains("print-link-marker")) {
        marker = document.createElement("sup");
        marker.className = "print-link-marker";
        a.after(marker);
      }
      if (!isVisibleForPrint(a)) {
        marker.textContent = "";
        return;
      }
      const url = a.href;
      let n = numberByUrl.get(url);
      if (n === undefined) {
        n = numberByUrl.size + 1;
        numberByUrl.set(url, n);
        entries.push({ text: a.textContent.trim() || url, url: url });
      }
      marker.textContent = `[${n}]`;
    });

    if (!entries.length) return;

    const section = document.createElement("section");
    section.className = "print-links";
    const heading = document.createElement("h2");
    heading.textContent = "Links";
    section.appendChild(heading);
    const list = document.createElement("ol");
    entries.forEach((entry) => {
      const item = document.createElement("li");
      const text = document.createElement("span");
      text.className = "print-link-text";
      text.textContent = entry.text;
      const url = document.createElement("span");
      url.className = "print-link-url";
      url.textContent = entry.url;
      item.appendChild(text);
      item.appendChild(document.createTextNode(" — "));
      item.appendChild(url);
      list.appendChild(item);
    });
    section.appendChild(list);
    scope.appendChild(section);
  }

  rebuild();
  window.addEventListener("beforeprint", rebuild);
})();
