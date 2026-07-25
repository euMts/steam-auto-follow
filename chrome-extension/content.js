(function () {
  const COPY_BUTTON_ID = "saf-copy-actions";
  const VERIFY_BUTTON_ID = "saf-verify-actions";
  const COPY_LABEL = "Copy URLs";
  const COPY_LABEL_DONE = "copiado";
  const VERIFY_LABEL = "Verify all";
  const VERIFY_LABEL_DONE = "ok";
  const VERIFY_DELAY_MS = 150;

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

  function collectVerifyButtons() {
    return Array.from(
      document.querySelectorAll("#actions tr:not(.hidden) button[data-action]:not(:disabled)")
    );
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function clickAllVerifyButtons() {
    const buttons = collectVerifyButtons();
    for (const button of buttons) {
      button.click();
      await sleep(VERIFY_DELAY_MS);
    }
    return buttons.length;
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

  function flashLabel(button, doneLabel, defaultLabel) {
    button.textContent = doneLabel;
    const previous = button.dataset.resetTimer;
    if (previous) clearTimeout(Number(previous));
    const timer = setTimeout(() => {
      button.textContent = defaultLabel;
      delete button.dataset.resetTimer;
    }, 3000);
    button.dataset.resetTimer = String(timer);
  }

  function insertButtons() {
    const getKey = document.getElementById("getKey");
    if (!getKey) return;

    const grabKey = getKey.querySelector("a.btn, button.btn");
    if (!grabKey) return;

    let copyButton = document.getElementById(COPY_BUTTON_ID);
    if (!copyButton) {
      copyButton = document.createElement("button");
      copyButton.type = "button";
      copyButton.id = COPY_BUTTON_ID;
      copyButton.className = "btn btn-sm";
      copyButton.textContent = COPY_LABEL;

      copyButton.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();

        const urls = collectActionUrls();
        if (!urls.length) return;

        try {
          await copyText(urls.join("\n"));
          flashLabel(copyButton, COPY_LABEL_DONE, COPY_LABEL);
        } catch (error) {
          console.error("[Giveaway.su Copy Actions] Failed to copy:", error);
        }
      });

      grabKey.insertAdjacentElement("afterend", copyButton);
    }

    if (!document.getElementById(VERIFY_BUTTON_ID)) {
      const verifyButton = document.createElement("button");
      verifyButton.type = "button";
      verifyButton.id = VERIFY_BUTTON_ID;
      verifyButton.className = "btn btn-sm";
      verifyButton.textContent = VERIFY_LABEL;

      verifyButton.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();

        if (verifyButton.dataset.running === "1") return;
        verifyButton.dataset.running = "1";
        verifyButton.disabled = true;

        try {
          const count = await clickAllVerifyButtons();
          if (count > 0) {
            flashLabel(verifyButton, VERIFY_LABEL_DONE, VERIFY_LABEL);
          }
        } catch (error) {
          console.error("[Giveaway.su Copy Actions] Failed to verify:", error);
        } finally {
          verifyButton.disabled = false;
          delete verifyButton.dataset.running;
        }
      });

      copyButton.insertAdjacentElement("afterend", verifyButton);
    }
  }

  function init() {
    insertButtons();

    const observer = new MutationObserver(() => {
      insertButtons();
    });

    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
