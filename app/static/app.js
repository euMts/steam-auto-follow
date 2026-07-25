(() => {
  "use strict";

  const state = {
    status: null,
    tasks: [],
    logs: [],
    ws: null,
    pollTimer: null,
    reconnectDelay: 1000,
  };

  const AUTH_LABELS = {
    not_verified: "Não verificado",
    verifying: "Verificando",
    authenticated: "Autenticado",
    not_authenticated: "Não autenticado",
    cookies_missing: "Cookies não configurados",
    error: "Erro ao verificar",
  };

  const ACTION_LABELS = {
    follow_curator: "Seguir curador",
    follow_publisher: "Seguir publisher",
    follow_group: "Entrar no grupo",
    auto: "Detectar pela URL",
  };

  const STATUS_CLASS = {
    pending: "badge-info",
    running: "badge-warn",
    completed: "badge-ok",
    failed: "badge-danger",
    cancelled: "badge-muted",
  };

  const $ = (sel) => document.querySelector(sel);

  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("pill-danger", isError);
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
  }

  function fmtTime(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString("pt-BR");
  }

  function fmtClock(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleTimeString("pt-BR");
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let data = null;
    const text = await res.text();
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail = data?.detail;
      const msg = typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : (data?.message || `Erro HTTP ${res.status}`);
      throw new Error(msg);
    }
    return data;
  }

  function setWsStatus(ok, text) {
    const el = $("#ws-status");
    el.textContent = text;
    el.className = `pill ${ok ? "pill-ok" : "pill-warn"}`;
  }

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/dashboard`);
    state.ws = ws;

    ws.onopen = () => {
      setWsStatus(true, "WebSocket: conectado");
      state.reconnectDelay = 1000;
      stopPolling();
      refreshAll();
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleEvent(msg);
      } catch {
        /* ignore */
      }
    };

    ws.onclose = () => {
      setWsStatus(false, "WebSocket: reconectando…");
      startPolling();
      setTimeout(connectWs, state.reconnectDelay);
      state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 8000);
    };

    ws.onerror = () => {
      try { ws.close(); } catch { /* ignore */ }
    };
  }

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(refreshAll, 1000);
  }

  function stopPolling() {
    if (!state.pollTimer) return;
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  async function handleEvent(msg) {
    if (!msg || !msg.type) return;
    if (msg.type === "log" && msg.payload) {
      state.logs.push(msg.payload);
      if (state.logs.length > 200) state.logs = state.logs.slice(-200);
      renderLogs();
      return;
    }
    // Demais eventos: atualiza status/tarefas
    await refreshAll();
  }

  async function refreshAll() {
    try {
      const [status, tasks] = await Promise.all([
        api("/api/status"),
        api("/api/tasks?limit=200"),
      ]);
      state.status = status;
      state.tasks = tasks.items || [];
      state.logs = status.recent_logs || state.logs;
      renderAll();
    } catch (err) {
      console.error(err);
    }
  }

  function renderAll() {
    const s = state.status;
    if (!s) return;

    const browserOpen = !!s.browser?.is_open;
    const browserBadge = $("#browser-badge");
    browserBadge.textContent = browserOpen ? "Aberto" : (s.browser?.closed_manually ? "Fechado manualmente" : "Fechado");
    browserBadge.className = `badge ${browserOpen ? "badge-ok" : "badge-muted"}`;
    $("#browser-url").textContent = s.browser?.current_url || s.current_url || "—";
    $("#browser-nav").textContent = fmtTime(s.browser?.last_navigation);
    $("#browser-action").textContent = s.browser?.last_action || s.last_action || "—";

    const auth = s.authentication || {};
    const authKey = auth.status || "not_verified";
    const authBadge = $("#auth-badge");
    authBadge.textContent = AUTH_LABELS[authKey] || authKey;
    authBadge.className = `badge ${
      authKey === "authenticated" ? "badge-ok"
        : authKey === "verifying" ? "badge-info"
          : authKey === "error" || authKey === "not_authenticated" ? "badge-danger"
            : authKey === "cookies_missing" ? "badge-warn"
              : "badge-muted"
    }`;

    const cookies = auth.cookies || {};
    $("#cookie-status").textContent = cookies.configured
      ? `Configurado (${cookies.steam_login_secure_masked || "•••"} / ${cookies.sessionid_masked || "•••"})`
      : "Não configurado";
    $("#account-name").textContent = auth.account_name || "—";
    $("#auth-checked").textContent = fmtTime(auth.checked_at);

    const q = s.queue || {};
    $("#stat-pending").textContent = q.pending ?? 0;
    $("#stat-running").textContent = q.running ?? 0;
    $("#stat-completed").textContent = q.completed ?? 0;
    $("#stat-failed").textContent = q.failed ?? 0;
    const queueBadge = $("#queue-badge");
    if (q.manual_action_required) {
      queueBadge.textContent = "Ação manual necessária";
      queueBadge.className = "badge badge-warn";
    } else if (q.paused) {
      queueBadge.textContent = "Pausada";
      queueBadge.className = "badge badge-warn";
    } else {
      queueBadge.textContent = "Ativa";
      queueBadge.className = "badge badge-ok";
    }
    const banner = $("#manual-banner");
    if (q.manual_action_required && q.manual_action_message) {
      banner.textContent = q.manual_action_message;
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }

    const cur = s.current_task || {};
    $("#current-id").textContent = cur.id ?? "—";
    $("#current-url").textContent = cur.url || "—";
    $("#current-action").textContent = ACTION_LABELS[cur.action_type] || cur.action_type || "—";
    $("#current-step").textContent = cur.current_step || "—";
    $("#current-started").textContent = fmtTime(cur.started_at);
    $("#current-attempts").textContent = cur.attempts != null ? String(cur.attempts) : "—";
    $("#current-message").textContent = cur.status_message || (cur.id ? "Em andamento" : "Nenhuma tarefa em execução");

    const settings = s.settings || {};
    const form = $("#settings-form");
    if (document.activeElement?.form !== form) {
      form.min_task_interval_seconds.value = settings.min_task_interval_seconds ?? 8;
      form.navigation_timeout_ms.value = settings.navigation_timeout_ms ?? 45000;
      form.element_timeout_ms.value = settings.element_timeout_ms ?? 15000;
      form.max_attempts.value = settings.max_attempts ?? 3;
    }

    renderTasks();
    renderLogs();
  }

  function renderTasks() {
    const body = $("#tasks-body");
    $("#tasks-count").textContent = `${state.tasks.length} tarefa(s)`;
    if (!state.tasks.length) {
      body.innerHTML = `<tr><td colspan="10" class="empty">Nenhuma tarefa cadastrada</td></tr>`;
      return;
    }
    body.innerHTML = state.tasks.map((t) => {
      const badge = STATUS_CLASS[t.status] || "badge-muted";
      const result = t.result_message || t.last_error || "—";
      return `
        <tr>
          <td>${t.id}</td>
          <td class="url-cell">${escapeHtml(t.url)}</td>
          <td>${ACTION_LABELS[t.action_type] || t.action_type}</td>
          <td><span class="badge ${badge}">${t.status}</span></td>
          <td>${t.attempts}/${t.max_attempts}</td>
          <td>${escapeHtml(result)}</td>
          <td>${fmtClock(t.created_at)}</td>
          <td>${fmtClock(t.started_at)}</td>
          <td>${fmtClock(t.finished_at)}</td>
          <td>
            <div class="row-actions">
              <button type="button" class="tiny secondary" data-task="cancel" data-id="${t.id}">Cancelar</button>
              <button type="button" class="tiny secondary" data-task="retry" data-id="${t.id}">Tentar novamente</button>
              <button type="button" class="tiny secondary" data-task="open" data-id="${t.id}">Abrir URL</button>
              <button type="button" class="tiny secondary" data-task="error" data-id="${t.id}">Erro</button>
              <button type="button" class="tiny danger" data-task="delete" data-id="${t.id}">Remover</button>
            </div>
          </td>
        </tr>`;
    }).join("");
  }

  function renderLogs() {
    const el = $("#log-terminal");
    const lines = (state.logs || []).slice(-120).map((log) => {
      const time = fmtClock(log.created_at);
      return `<span class="log-${escapeHtml(log.level)}">[${time}] [${escapeHtml(log.level)}] [${escapeHtml(log.source)}] ${escapeHtml(log.message)}</span>`;
    });
    el.innerHTML = lines.join("\n") || '<span class="muted">Sem logs ainda</span>';
    el.scrollTop = el.scrollHeight;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function runAction(fn, okMessage) {
    try {
      await fn();
      if (okMessage) toast(okMessage);
      await refreshAll();
    } catch (err) {
      toast(err.message || String(err), true);
    }
  }

  document.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-action], [data-task]");
    if (!btn) return;

    if (btn.dataset.action) {
      const map = {
        "browser-start": () => api("/api/browser/start", { method: "POST" }),
        "browser-restart": () => api("/api/browser/restart", { method: "POST" }),
        "browser-close": () => api("/api/browser/close", { method: "POST" }),
        "session-verify": () => api("/api/session/verify", { method: "POST" }),
        "session-apply": () => api("/api/session/apply", { method: "POST" }),
        "session-clear": () => api("/api/session/cookies", { method: "DELETE" }),
        "queue-pause": () => api("/api/queue/pause", { method: "POST" }),
        "queue-resume": () => api("/api/queue/resume", { method: "POST" }),
        "queue-clear": () => api("/api/queue/completed", { method: "DELETE" }),
        "queue-retry-failed": () => api("/api/queue/retry-failed", { method: "POST" }),
      };
      const fn = map[btn.dataset.action];
      if (fn) await runAction(fn, "OK");
      return;
    }

    const id = Number(btn.dataset.id);
    const task = state.tasks.find((t) => t.id === id);
    if (btn.dataset.task === "cancel") {
      await runAction(() => api(`/api/tasks/${id}/cancel`, { method: "POST" }), "Tarefa cancelada");
    } else if (btn.dataset.task === "retry") {
      await runAction(() => api(`/api/tasks/${id}/retry`, { method: "POST" }), "Tarefa reenfileirada");
    } else if (btn.dataset.task === "delete") {
      await runAction(() => api(`/api/tasks/${id}`, { method: "DELETE" }), "Tarefa removida");
    } else if (btn.dataset.task === "open") {
      await runAction(() => api(`/api/browser/open-task/${id}`, { method: "POST" }), "URL aberta");
    } else if (btn.dataset.task === "error") {
      const dialog = $("#error-dialog");
      $("#error-dialog-text").textContent = task?.last_error || "Sem erro registrado";
      const shot = $("#error-dialog-shot");
      if (task?.screenshot_path) {
        const name = task.screenshot_path.split(/[/\\]/).pop();
        shot.innerHTML = `<img src="/api/screenshots/${encodeURIComponent(name)}" alt="Screenshot do erro" />`;
        shot.classList.remove("hidden");
      } else {
        shot.innerHTML = "";
        shot.classList.add("hidden");
      }
      dialog.showModal();
    }
  });

  $("#cookie-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const payload = {
      steamLoginSecure: form.steamLoginSecure.value,
      sessionid: form.sessionid.value,
    };
    await runAction(async () => {
      await api("/api/session/cookies", { method: "POST", body: JSON.stringify(payload) });
      form.reset();
    }, "Cookies salvos e sessão verificada");
  });

  $("#task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const payload = {
      urls: form.urls.value,
      action_type: form.action_type.value,
    };
    await runAction(async () => {
      await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
      form.urls.value = "";
    }, "Tarefas salvas — processamento iniciado");
  });

  $("#settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const payload = {
      min_task_interval_seconds: Number(form.min_task_interval_seconds.value),
      navigation_timeout_ms: Number(form.navigation_timeout_ms.value),
      element_timeout_ms: Number(form.element_timeout_ms.value),
      max_attempts: Number(form.max_attempts.value),
    };
    await runAction(
      () => api("/api/settings", { method: "PUT", body: JSON.stringify(payload) }),
      "Configurações salvas",
    );
  });

  setInterval(() => {
    $("#clock").textContent = new Date().toLocaleTimeString("pt-BR");
  }, 1000);

  connectWs();
  startPolling();
  refreshAll();
})();
