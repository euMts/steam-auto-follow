(function () {
  const BUTTON_ID = "saf-copy-actions";
  const LABEL_DEFAULT = "Copy URLs";
  const LABEL_COPIED = "copiado";

  function collectActionUrls() {
    const links = document.querySelectorAll("#actions tr:not(.hidden) a[href]");
    const urls = [];

    for (const link of links) {
      try {
        const absolute = new URL(link.getAttribute("href"), location.origin).href;
        if (!urls.includes(absolute)) {
          urls.push(absolute);
        }
      } catch {
        // ignore invalid hrefs
      }
    }

    return urls;
  }

  async function copyText(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  function insertButton() {
    if (document.getElementById(BUTTON_ID)) return;

    const getKey = document.getElementById("getKey");
    if (!getKey) return;

    const grabKey = getKey.querySelector("a.btn, button.btn");
    if (!grabKey) return;

    const button = document.createElement("button");
    button.type = "button";
    button.id = BUTTON_ID;
    button.className = "btn btn-sm";
    button.textContent = LABEL_DEFAULT;

    let resetTimer = null;

    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();

      const urls = collectActionUrls();
      if (!urls.length) return;

      try {
        await copyText(urls.join("\n"));
        button.textContent = LABEL_COPIED;
        if (resetTimer) clearTimeout(resetTimer);
        resetTimer = setTimeout(() => {
          button.textContent = LABEL_DEFAULT;
          resetTimer = null;
        }, 3000);
      } catch (error) {
        console.error("[Giveaway.su Copy Actions] Failed to copy:", error);
      }
    });

    grabKey.insertAdjacentElement("afterend", button);
  }

  function init() {
    insertButton();

    const observer = new MutationObserver(() => {
      insertButton();
    });

    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
