(function () {
  const STORAGE_KEY = "saf-giveaway-used-keys";
  const CHECK_CLASS = "saf-used-check";

  function loadUsedMap() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function saveUsedMap(map) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  }

  function getGiveawayId(col) {
    const link = col.querySelector("a.giveaway-item[href*='/giveaway/view/']");
    if (!link) return null;
    const match = link.getAttribute("href")?.match(/\/giveaway\/view\/(\d+)/);
    return match ? match[1] : null;
  }

  function applyUsedState(col, used) {
    col.classList.toggle("saf-giveaway-used", used);
  }

  function attachCheckbox(col, usedMap) {
    if (col.querySelector(`.${CHECK_CLASS}`)) return;

    const id = getGiveawayId(col);
    if (!id) return;

    if (getComputedStyle(col).position === "static") {
      col.style.position = "relative";
    }

    const label = document.createElement("label");
    label.className = CHECK_CLASS;
    label.title = "Marcar como já usado";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(usedMap[id]);

    const text = document.createElement("span");
    text.textContent = "Usado";

    label.append(checkbox, text);

    label.addEventListener("click", (event) => {
      event.stopPropagation();
    });

    checkbox.addEventListener("click", (event) => {
      event.stopPropagation();
    });

    checkbox.addEventListener("change", () => {
      const map = loadUsedMap();
      if (checkbox.checked) {
        map[id] = true;
      } else {
        delete map[id];
      }
      saveUsedMap(map);
      applyUsedState(col, checkbox.checked);
    });

    applyUsedState(col, checkbox.checked);
    col.prepend(label);
  }

  function scan() {
    const root = document.getElementById("giveaways");
    if (!root) return;

    const usedMap = loadUsedMap();
    const cols = root.querySelectorAll(".row > .col-md-4.col-sm-6");
    for (const col of cols) {
      attachCheckbox(col, usedMap);
    }
  }

  function init() {
    scan();

    const root = document.getElementById("giveaways") || document.body;
    const observer = new MutationObserver(() => {
      scan();
    });
    observer.observe(root, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
