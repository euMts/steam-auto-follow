(function () {
  const COPY_BUTTON_ID = "saf-copy-actions";
  const VERIFY_BUTTON_ID = "saf-verify-actions";
  const COPY_LABEL = "Copy URLs";
  const COPY_LABEL_DONE = "copiado";
  const VERIFY_LABEL = "Verify all";
  const VERIFY_LABEL_DONE = "ok";
  const VERIFY_DELAY_MS = 150;
  const SETTLE_POLL_MS = 300;
  const SETTLE_STABLE_ROUNDS = 3;
  const SETTLE_MAX_MS = 10000;

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

  function isActionComplete(button) {
    return (
      button.disabled ||
      button.hasAttribute("data-result") ||
      button.classList.contains("btn-success")
    );
  }

  function getActionRows() {
    return Array.from(document.querySelectorAll("#actions tr:not(.hidden)"));
  }

  function collectActionUrls({ incompleteOnly = false } = {}) {
    const urls = [];

    for (const row of getActionRows()) {
      const button = row.querySelector("button[data-action]");
      if (!button) continue;
      if (incompleteOnly && isActionComplete(button)) continue;

      const payload = decodeActionPayload(button.getAttribute("data-action"));
      const finalUrl = resolveFinalUrl(payload);
      if (finalUrl && !urls.includes(finalUrl)) {
        urls.push(finalUrl);
      }
    }

    // Fallback: redirect hrefs if payload decode fails
    if (!urls.length) {
      for (const row of getActionRows()) {
        const button = row.querySelector("button[data-action]");
        if (incompleteOnly && button && isActionComplete(button)) continue;

        const link = row.querySelector("a[href]");
        if (!link) continue;
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
    ).filter((button) => !isActionComplete(button));
  }

  function actionsSignature() {
    return getActionRows()
      .map((row) => {
        const button = row.querySelector("button[data-action]");
        if (!button) return "";
        return [
          button.disabled ? "1" : "0",
          button.className,
          button.getAttribute("data-result") || "",
        ].join(":");
      })
      .join("|");
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

  async function waitForVerifySettlement() {
    const deadline = Date.now() + SETTLE_MAX_MS;
    let lastSignature = actionsSignature();
    let stableRounds = 0;

    while (Date.now() < deadline) {
      await sleep(SETTLE_POLL_MS);
      const signature = actionsSignature();
      if (signature === lastSignature) {
        stableRounds += 1;
        if (stableRounds >= SETTLE_STABLE_ROUNDS) return;
      } else {
        lastSignature = signature;
        stableRounds = 0;
      }
    }
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

        if (copyButton.dataset.running === "1") return;
        copyButton.dataset.running = "1";
        copyButton.disabled = true;

        try {
          await clickAllVerifyButtons();
          await waitForVerifySettlement();

          const urls = collectActionUrls({ incompleteOnly: true });
          if (!urls.length) {
            flashLabel(copyButton, "0", COPY_LABEL);
            return;
          }

          await copyText(urls.join("\n"));
          flashLabel(copyButton, COPY_LABEL_DONE, COPY_LABEL);
        } catch (error) {
          console.error("[Giveaway.su Copy Actions] Failed to copy:", error);
        } finally {
          copyButton.disabled = false;
          delete copyButton.dataset.running;
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
            await waitForVerifySettlement();
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
