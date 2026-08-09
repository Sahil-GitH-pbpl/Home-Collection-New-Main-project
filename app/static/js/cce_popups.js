(() => {
  const ctx = window.CCE_CONTEXT || {};
  const WS_URL = ctx.WS_URL || "";
  const ISSABEL_HTTP = ctx.ISSABEL_HTTP || "";
  const ME_NAME = ctx.ME_NAME || "";
  const ME_ID = ctx.ME_ID || "";

  const statusEl = document.getElementById("status") || document.getElementById("cce-status");
  const dotEl = document.getElementById("dot") || document.getElementById("cce-dot");
  const popwrap = document.getElementById("popwrap");

  if (!popwrap || (!WS_URL && !ISSABEL_HTTP)) return;

  const POP_KEY = "cce_popups_enabled";
  const MIN_KEY = "cce_minimized_popups";
  const callIndex = new Map();
  let socket = null;
  let presenceOnline = false;
  let popupSeq = 0;
  const recentSidEvents = new Map();

  const safe = (v) => (v === undefined || v === null || v === "null" ? "" : String(v));
  const esc = (v) =>
    safe(v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  function popupsEnabled() {
    const v = localStorage.getItem(POP_KEY);
    return v === null ? true : v === "true";
  }

  function applyPopupContainerVisibility() {
    if (popupsEnabled()) {
      popwrap.classList.remove("hide");
    } else {
      popwrap.classList.add("hide");
      for (const ref of callIndex.values()) {
        if (ref?.timer) clearInterval(ref.timer);
      }
      [...popwrap.children].forEach((ch) => ch.remove());
      callIndex.clear();
    }
  }

  function minimizedKeys() {
    try {
      const parsed = JSON.parse(localStorage.getItem(MIN_KEY) || "[]");
      return new Set(Array.isArray(parsed) ? parsed : []);
    } catch (e) {
      return new Set();
    }
  }

  function setMinimizedKey(key, minimized) {
    if (!key) return;
    const keys = minimizedKeys();
    if (minimized) keys.add(key);
    else keys.delete(key);
    localStorage.setItem(MIN_KEY, JSON.stringify([...keys]));
  }

  function isMinimizedKey(key) {
    return !!key && minimizedKeys().has(key);
  }

  function popupStorageKey(info) {
    return info?.sid ? `sid:${info.sid}` : safe(info?.key || "");
  }

  function setPopupMinimized(info, minimized) {
    setMinimizedKey(info.key, minimized);
    setMinimizedKey(popupStorageKey(info), minimized);
  }

  function isPopupMinimized(info) {
    return isMinimizedKey(info.key) || isMinimizedKey(popupStorageKey(info));
  }

  function fmtElapsed(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  }

  function normalizeAnswered(raw) {
    const sid = safe(raw.call_sid || raw.CallSid || "");
    const phone = safe(raw.phone || raw.from_number || raw.From || raw.CallFrom || "");
    if (!sid || !phone) return null;
    const eventTime = safe(raw.answered_at || raw.AnsweredAt || raw.created_at || raw.Created || "");
    const suppliedKey = safe(raw.popup_key || raw.ui_key || "");
    const durationSec = Number(raw.dial_call_duration || raw.duration || raw.duration_seconds || 0) || 0;
    return {
      key: suppliedKey || `sid:${sid}`,
      sid,
      phone,
      extension: safe(raw.extension || ""),
      extensionIp: safe(raw.extension_ip || raw.ip_address || ""),
      answeredAt: eventTime || new Date().toISOString(),
      durationSec,
      callStatus: safe(raw.dial_call_status || raw.status || ""),
      callType: safe(raw.call_type || ""),
    };
  }

  function isAnsweredPayload(raw) {
    const status = safe(raw.dial_call_status || raw.DialCallStatus || raw.status || "").toLowerCase();
    return status === "answered" || !!safe(raw.answered_at || raw.AnsweredAt || "");
  }

  function compactDate(value) {
    if (!value) return "";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return safe(value).slice(0, 16);
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  }

  function recordLink(kind, item, phone) {
    if (kind === "hc") return "";
    if (kind === "tickets") return item.id ? `/tickets/${encodeURIComponent(item.id)}?mode=view` : "";
    if (kind === "leads") return item.lead_id ? `/lead/${encodeURIComponent(item.lead_id)}` : "";
    return "";
  }

  function renderRecordItem(kind, item, phone) {
    const href = recordLink(kind, item, phone);
    if (kind === "hc") {
      const patients = (item.patients || []).map((p) => p.full_name).filter(Boolean).join(", ") || "-";
      return `
        <div class="rel-item rel-item-hc">
          <span class="rel-main">
            <span class="rel-kv"><b>Bkg No</b><em>${esc(item.booking_code || item.id || "-")}</em></span>
            <span class="rel-kv"><b>Patient</b><em>${esc(patients)}</em></span>
            <span class="rel-kv"><b>Phlebo</b><em>${esc(item.assigned_phlebotomist || "-")}</em></span>
          </span>
        </div>
      `;
    }
    let title = "-";
    let meta = "";
    let status = "";
    if (kind === "hc") {
      title = item.booking_code || item.id || "Booking";
      const patients = (item.patients || []).map((p) => p.full_name).filter(Boolean).join(", ");
      meta = patients || item.remarks || "Home collection";
      status = item.booking_status || "";
    } else if (kind === "tickets") {
      title = item.id ? `#${item.id}` : "Ticket";
      meta = item.patient_name || item.client_name || item.ticket_category || "-";
      status = item.status || "";
    } else {
      title = item.lead_id || item.id || "Lead";
      meta = item.name || item.phone || "-";
      status = item.status || "";
    }
    const when = compactDate(item.preferred_visit_date || item.created_at || item.commitment_at);
    const inner = `
      <span class="rel-main">
        <b>${esc(title)}</b>
        <span>${esc(meta)}</span>
      </span>
      <span class="rel-side">
        ${status ? `<em>${esc(status)}</em>` : ""}
        ${when ? `<small>${esc(when)}</small>` : ""}
      </span>
    `;
    return href
      ? `<a class="rel-item" href="${esc(href)}" ${kind === "hc" ? 'target="_blank" rel="noopener noreferrer"' : ""}>${inner}</a>`
      : `<div class="rel-item">${inner}</div>`;
  }

  function renderRecordSection(title, kind, rows, phone) {
    const list = (rows || []).slice(0, 2);
    return `
      <div class="rel-section rel-section-${esc(kind)}">
        <div class="rel-title"><span>${esc(title)}</span></div>
        <div class="rel-list">
          ${
            list.length
              ? list.map((item) => renderRecordItem(kind, item, phone)).join("")
              : '<div class="rel-empty">No records</div>'
          }
        </div>
      </div>
    `;
  }

  async function loadRelatedRecords(info, el) {
    const box = el.querySelector(".related-records");
    if (!box || !info.phone) return;
    try {
      const r = await fetch(`/api/mobile-lookup?mobile=${encodeURIComponent(info.phone)}&sections=hc,leads,tickets`, { cache: "no-store" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j?.ok) throw new Error(j?.error || "lookup failed");
      box.innerHTML = [
        renderRecordSection("Home Collection", "hc", j.home_collection_bookings, info.phone),
        renderRecordSection("Tickets", "tickets", j.tickets, info.phone),
        renderRecordSection("Leads", "leads", j.leads, info.phone),
      ].join("");
    } catch (e) {
      box.innerHTML = '<div class="rel-empty">Unable to load related records</div>';
    }
  }

  function removePopup(key, opts = {}) {
    const ref = callIndex.get(key);
    if (ref?.timer) clearInterval(ref.timer);
    callIndex.delete(key);
    if (opts.clearState) {
      setMinimizedKey(key, false);
      if (ref?.sid) setMinimizedKey(`sid:${ref.sid}`, false);
    }
    if (ref?.el) ref.el.remove();
  }

  function removePopupsBySid(sid) {
    for (const [key, ref] of callIndex.entries()) {
      if (ref?.sid !== sid) continue;
      if (ref?.timer) clearInterval(ref.timer);
      if (ref?.el) ref.el.remove();
      callIndex.delete(key);
      setMinimizedKey(key, false);
      setMinimizedKey(`sid:${sid}`, false);
    }
  }

  function isStoppedCall(info) {
    const status = safe(info.callStatus).toLowerCase();
    return info.durationSec > 0 || ["hangup", "completed", "complete", "answered"].includes(status) && info.durationSec > 0;
  }

  function stopPopupTimerBySid(sid, durationSec) {
    if (!sid) return;
    for (const ref of callIndex.values()) {
      if (ref?.sid !== sid || ref.stopped) continue;
      if (ref.timer) clearInterval(ref.timer);
      ref.timer = null;
      ref.stopped = true;
      const sinceEl = ref.el?.querySelector(".since-val");
      const elapsedMs = durationSec > 0 ? durationSec * 1000 : Date.now() - ref.startedAt;
      if (sinceEl) sinceEl.textContent = fmtElapsed(elapsedMs);
    }
  }

  async function removePopupIfCallTypeSaved(sid) {
    if (!sid) return;
    try {
      const r = await fetch(`/cce/call-status?call_sid=${encodeURIComponent(sid)}`, { cache: "no-store" });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j?.has_call_related_to) {
        removePopupsBySid(sid);
      }
    } catch (e) {
      // Keep the popup when status cannot be verified.
    }
  }

  async function persistAnsweredPopup(info) {
    try {
      await fetch("/cce/answered-popup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          call_sid: info.sid,
          phone: info.phone,
          extension: info.extension,
          answered_at: info.answeredAt,
        }),
      });
    } catch (e) {
      // The live popup should still stay visible even if persistence fails.
    }
  }

  async function loadPendingPopups() {
    if (!popupsEnabled()) return;
    try {
      const r = await fetch("/cce/pending-popups", { cache: "no-store" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || j?.status !== "ok") return;
      (j.data || []).forEach((raw) => {
        const info = normalizeAnswered(raw || {});
        if (info) createAnsweredPopup(info, { persist: false });
      });
    } catch (e) {
      // no-op
    }
  }

  function createAnsweredPopup(info, opts = {}) {
    if (!popupsEnabled()) return;

    removePopup(info.key);
    if (opts.persist !== false) {
      persistAnsweredPopup(info);
    }

    const el = document.createElement("div");
    el.className = "pop";
    el.dataset.sid = info.sid;
    el.dataset.key = info.key;
    const answeredMs = Date.parse(info.answeredAt || "");
    const t0 = Number.isNaN(answeredMs) ? Date.now() : answeredMs;

    el.innerHTML = `
      <div class="row">
        <div class="badges">
          <span class="badge inbound">ANSWERED</span>
          ${info.extension ? `<span class="badge ringing">Ext ${info.extension}</span>` : ""}
        </div>
        <div class="pop-top-actions">
          <div class="since"><span class="since-val">00:00</span></div>
          <button class="pop-min" type="button" title="Minimize" aria-label="Minimize popup">-</button>
        </div>
      </div>

      <button class="mini-face" type="button" title="${esc(info.phone || "Call")}" aria-label="Restore popup">
        <span>IN</span>
      </button>

      <div class="pop-content">
      <div class="head">
        <div class="caller">
          <div style="width:30px;height:30px;border-radius:50%;background:#f3f4f6;display:grid;place-items:center;font-weight:800">
            IN
          </div>
          <div>
            <div class="num">${info.phone || "-"}</div>
          </div>
        </div>
      </div>

      <div class="related-records">
        <div class="rel-empty">Loading related records...</div>
      </div>

      <div class="actions-ctas">
        <div class="cta-wrap">
          <div>
            <div class="label">Call type</div>
            <select class="select sel-type">
              <option value="">Select...</option>
              <option value="Lead">Lead</option>
              <option value="Ticket">Ticket</option>
              <option value="Home Collection Appointment">Home Collection Appointment</option>
              <option value="Report Query">Report Query</option>
              <option value="Test Inquiry">Test Inquiry</option>
              <option value="Spam Call">Spam Call</option>
            </select>
          </div>
          <div class="cta-buttons">
            <button class="btn btn-success btn-complete" disabled>Save</button>
          </div>
        </div>
      </div>
      </div>
    `;

    const sinceEl = el.querySelector(".since-val");
    const stopped = isStoppedCall(info);
    sinceEl.textContent = fmtElapsed(stopped ? info.durationSec * 1000 : Date.now() - t0);
    const timer = stopped
      ? null
      : setInterval(() => {
          sinceEl.textContent = fmtElapsed(Date.now() - t0);
        }, 500);
    if (isPopupMinimized(info)) {
      el.classList.add("is-minimized");
    }
    callIndex.set(info.key, { el, timer, sid: info.sid, startedAt: t0, stopped });
    loadRelatedRecords(info, el);

    const sel = el.querySelector(".sel-type");
    const saveBtn = el.querySelector(".btn-complete");
    const minBtn = el.querySelector(".pop-min");
    const miniFace = el.querySelector(".mini-face");
    minBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      el.classList.add("is-minimized");
      setPopupMinimized(info, true);
    });
    miniFace.addEventListener("click", (ev) => {
      ev.stopPropagation();
      el.classList.remove("is-minimized");
      setPopupMinimized(info, false);
    });
    sel.addEventListener("change", () => {
      saveBtn.disabled = !sel.value;
    });

    saveBtn.addEventListener("click", async () => {
      if (!sel.value) {
        alert("Please select Call Type");
        return;
      }
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving...";
      try {
        const r = await fetch("/cce/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ call_sid: info.sid, call_related_to: sel.value }),
        });
        const j = await r.json().catch(() => ({}));
    if (r.ok && j?.status === "ok") {
          removePopup(info.key, { clearState: true });
        } else {
          saveBtn.disabled = false;
          saveBtn.textContent = "Save";
          alert(j.message || "Save failed");
        }
      } catch (e) {
        saveBtn.disabled = false;
        saveBtn.textContent = "Save";
        alert("Save failed");
      }
    });

    popwrap.appendChild(el);
    popwrap.scrollTop = popwrap.scrollHeight;
  }

  async function setPresenceOnline() {
    if (!ISSABEL_HTTP || !ME_NAME || presenceOnline) return;
    try {
      const r = await fetch(`${ISSABEL_HTTP}/presence/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_name: ME_NAME, user_id: ME_ID || null }),
      });
      presenceOnline = r.ok;
    } catch (e) {
      presenceOnline = false;
    }
  }

  function setPresenceOffline() {
    if (!ISSABEL_HTTP || !ME_NAME || !presenceOnline) return;
    const payload = JSON.stringify({ user_name: ME_NAME, user_id: ME_ID || null });
    try {
      navigator.sendBeacon(
        `${ISSABEL_HTTP}/presence/logout`,
        new Blob([payload], { type: "application/json" })
      );
    } catch (e) {
      /* no-op */
    }
    presenceOnline = false;
  }

  function connectWS() {
    if (socket && socket.connected) return;
    if (typeof io !== "function") return;

    const socketOpts = {
      transports: ["websocket", "polling"],
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 10000,
      auth: {
        user_name: ME_NAME,
        user_id: ME_ID,
      },
    };

    try {
      socket = io(WS_URL || ISSABEL_HTTP, socketOpts);
    } catch (e) {
      socket = io(ISSABEL_HTTP, socketOpts);
    }

    socket.on("connect", () => {
      if (statusEl) statusEl.textContent = "Issabel connected";
      if (dotEl) dotEl.style.background = "#22c55e";
      setPresenceOnline();
    });

    socket.on("disconnect", () => {
      if (statusEl) statusEl.textContent = "Disconnected retrying";
      if (dotEl) dotEl.style.background = "#ef4444";
    });

    socket.on("connect_error", () => {
      if (statusEl) statusEl.textContent = "Connection retrying";
      if (dotEl) dotEl.style.background = "#ef4444";
    });

    function handleAnsweredPayload(payload) {
      const info = normalizeAnswered(payload || {});
      if (!info) return;
      const now = Date.now();
      const last = recentSidEvents.get(info.sid) || 0;
      if (last && now - last < 3000) return;
      recentSidEvents.set(info.sid, now);
      setTimeout(() => recentSidEvents.delete(info.sid), 5000);
      if (info) createAnsweredPopup(info);
    }

    socket.on("call_answered", (payload) => {
      handleAnsweredPayload(payload);
    });

    socket.on("incoming_call", (payload) => {
      if (!isAnsweredPayload(payload || {})) return;
      handleAnsweredPayload(payload);
    });

    socket.on("popup_close", (payload) => {
      const sid = safe(payload?.call_sid || payload?.CallSid || "");
      removePopupIfCallTypeSaved(sid);
    });

    socket.on("call_hangup", (payload) => {
      const sid = safe(payload?.call_sid || payload?.CallSid || "");
      const durationSec = Number(payload?.dial_call_duration || payload?.duration || 0) || 0;
      stopPopupTimerBySid(sid, durationSec);
    });
  }

  applyPopupContainerVisibility();
  window.addEventListener("storage", (e) => {
    if (e.key === POP_KEY) applyPopupContainerVisibility();
  });
  window.addEventListener("cce:popup-pref-changed", applyPopupContainerVisibility);
  window.addEventListener("pagehide", setPresenceOffline);
  window.addEventListener("beforeunload", setPresenceOffline);

  connectWS();
  loadPendingPopups();
})();
