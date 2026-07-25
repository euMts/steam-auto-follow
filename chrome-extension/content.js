(function () {
  const BUTTON_ID = "saf-copy-actions";
  const LABEL_DEFAULT = "Copy URLs";
  const LABEL_COPIED = "copiado";

  function decodeActionPayload(encoded) {
    try {
      const json = atob(encoded);
      return JSON.parse(json);
    } catch {
      return null;
    }
  }

  function resolveFinalUrl(payload) {
    if (!payload?.task) return null;

    const task = String(payload.task);
    const data = payload.data;

    // Wishlist / userdata checks use an internal endpoint + app id
    if (task.includes("/steam/userdata") && data != null && data !== "") {
      const appId = Array.isArray(data) ? data[0] : data;
      if (appId) {
        return `https://store.steampowered.com/app/${appId}`;
      }
    }

    try {
      return new URL(task).href;
    } catch {
      return null;
    }
  }

  function collectActionUrls() {
    const rows = document.querySelectorAll("#actions tr:not(.hidden)");
    const urls = [];

    for (const row of rows) {
      const button = row.querySelector("button[data-action]");
      if (!button) continue;

      const payload = decodeActionPayload(button.getAttribute("data-action"));
      const finalUrl = resolveFinalUrl(payload);
      if (finalUrl && !urls.includes(finalUrl)) {
        urls.push(finalUrl);
      }
    }

    // Fallback: redirect hrefs if payload decode fails
    if (!urls.length) {
      const links = document.querySelectorAll("#actions tr:not(.hidden) a[href]");
      for (const link of links) {
        try {
          const absolute = new URL(link.getAttribute("href"), location.origin).href;
          if (!urls.includes(absolute)) urls.push(absolute);
        } catch {
          // ignore
        }
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
