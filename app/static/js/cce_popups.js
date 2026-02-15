(() => {
  const ctx = window.CCE_CONTEXT || {};
  const WS_URL = ctx.WS_URL || "";
  const EXOTEL_HTTP = ctx.EXOTEL_HTTP || "";
  const ME_NAME = ctx.ME_NAME || "";
  const ME_ID = ctx.ME_ID || "";

  const statusEl = document.getElementById("status") || document.getElementById("cce-status");
  const dotEl = document.getElementById("dot") || document.getElementById("cce-dot");
  const popwrap = document.getElementById("popwrap");
  const missedBody = document.getElementById("missed-body");
  const hasMissedTable = !!missedBody;
  const LANDLINE_OPTIONS = [
    { num: "01149989851", label: "cc ext1", type: "cc" },
    { num: "01149989859", label: "cc ext2", type: "cc" },
    { num: "01149989865", label: "cc ext3", type: "cc" },
    { num: "01149989868", label: "cc ext4", type: "cc" },
    { num: "01149989861", label: "cc ext5", type: "cc" },
    { num: "01149989867", label: "cc ext6", type: "cc" },
    { num: "01149989869", label: "cc ext7", type: "cc" },
    { num: "01149989881", label: "resp ext1", type: "resp" },
    { num: "01149989880", label: "resp ext2", type: "resp" },
    { num: "01149989882", label: "resp ext3", type: "resp" },
    { num: "01149989877", label: "shahana", type: "shahana" },
  ];

  if (!popwrap || (!WS_URL && !EXOTEL_HTTP)) {
    return; // No container or no listener endpoint
  }

  const TERMINALS = new Set([
    "completed",
    "canceled",
    "failed",
    "busy",
    "no-answer",
    "not-answered",
    "hangup",
    "client-hangup",
    "machine-hangup",
  ]);

  const safe = (v) => (v === undefined || v === null || v === "null" ? "" : String(v));

  async function fetchLastClaimant(phone) {
    if (!phone) return null;
    try {
      const r = await fetch(`/cce/last-claimant?phone=${encodeURIComponent(phone)}`);
      const j = await r.json();
      if (j.ok && j.last_claimed_by) {
        return j.last_claimed_by;
      }
    } catch (e) {
      console.log("Last claimant fetch failed:", e);
    }
    return null;
  }

  const fmtElapsed = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  };
  const initials = (txt) => {
    const s = safe(txt || "IN").trim();
    const parts = s.split(/\s+/);
    return (parts[0]?.[0] || "I") + (parts[1]?.[0] || "N");
  };

  function isTerminal(statusText, callType) {
    const s = (statusText || "").toLowerCase();
    const t = (callType || "").toLowerCase();
    return TERMINALS.has(s) || TERMINALS.has(t);
  }

  const POP_KEY = "cce_popups_enabled";
  const callIndex = new Map();

  function popupsEnabled() {
    const v = localStorage.getItem(POP_KEY);
    return v === null ? true : v === "true";
  }
  function applyPopupContainerVisibility() {
    if (popupsEnabled()) {
      popwrap.classList.remove("hide");
    } else {
      popwrap.classList.add("hide");
      [...popwrap.children].forEach((ch) => ch.remove());
      callIndex.clear();
    }
  }
  applyPopupContainerVisibility();

  window.addEventListener("storage", (e) => {
    if (e.key === POP_KEY) {
      applyPopupContainerVisibility();
      handleSocketByPref();
    }
  });
  window.addEventListener("cce:popup-pref-changed", () => {
    applyPopupContainerVisibility();
    handleSocketByPref();
  });

  async function loadMissed() {
    try {
      const r = await fetch("/cce/missed", { cache: "no-store" });
      const j = await r.json();
      if (!j.ok) {
        throw new Error(j.error || "Failed");
      }
      const rows = j.data || [];
      if (hasMissedTable) {
        renderMissed(rows);
      }
      try {
        rows.forEach((rw) => {
          const ct = (rw.call_type || "").toLowerCase();
          const sid = String(rw.call_sid || "");
          if (!sid) return;
          if (ct === "client-hangup" || ct === "call-attempt" || ct === "incomplete") {
            removePopup(sid);
          }
        });
      } catch (e) {
        /* no-op */
      }
    } catch (e) {
      if (hasMissedTable) {
        renderMissed([]);
      }
    }
  }

  function renderMissed(rows) {
    if (!hasMissedTable) return;
    missedBody.innerHTML = "";
    if (!rows.length) {
      const tr = document.createElement("tr");
      tr.className = "empty";
      tr.innerHTML = `<td colspan="5" class="muted" style="padding:14px;">No missed calls</td>`;
      missedBody.appendChild(tr);
      return;
    }
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${safe(r.from_number || "")}</td>
        <td>${safe(r.to_number || "")}</td>
        <td>${safe(r.call_type || "")}</td>
        <td>${safe(r.created_at || "")}</td>
        <td>
          <button class="btn primary cb"
            data-id="${safe(r.id)}"
            data-sid="${safe(r.call_sid || "")}"
            data-phone="${safe(r.from_number || "")}"
            data-to="${safe(r.to_number || "")}"
            data-ctype="${safe((r.call_type || "").toLowerCase())}">Call Back</button>
        </td>
      `;
      missedBody.appendChild(tr);
    }
    [...missedBody.querySelectorAll(".cb")].forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        const id = ev.currentTarget.dataset.id;
        const sid = ev.currentTarget.dataset.sid;
        const phone = ev.currentTarget.dataset.phone || "";
        openLandlinePicker(phone, sid, id, ev.currentTarget.closest("tr"));
      });
    });
  }

  function removePopup(sid) {
    const ref = callIndex.get(sid);
    if (ref?.timer) clearInterval(ref.timer);
    callIndex.delete(sid);
    if (ref?.el) ref.el.remove();
  }

  function normalizeForUI(raw) {
    const sid = safe(raw.call_sid || raw.CallSid || "");
    if (!sid) return null;
    const from = safe(raw.from_number) || safe(raw.From) || safe(raw.CallFrom) || "";
    const to = safe(raw.to_number) || safe(raw.To) || safe(raw.OutgoingPhoneNumber) || "";
    const direction = safe(raw.direction) || safe(raw.Direction) || "incoming";
    const createdAt =
      safe(raw.created_at) || safe(raw.Created) || safe(raw.StartTime) || new Date().toISOString();
    const statusText = (safe(raw.dial_call_status) || safe(raw.DialCallStatus) || "ringing").toLowerCase();
    const callType = (safe(raw.call_type) || safe(raw.CallType) || "").toLowerCase();
    const callerName = safe(raw.CallerName || raw.match_name);
    const acceptedByName = safe(raw.accepted_by_name || raw.accepted_by_username || "");
    const acceptedById = safe(raw.accepted_by || raw.accepted_by_id || "");
    const locked = !!(acceptedByName || acceptedById);
    return { sid, from, to, direction, createdAt, statusText, callType, callerName, acceptedByName, acceptedById, locked, raw };
  }

  /* ======== Callback picker + initiator ======== */
  function openLandlinePicker(patientNumber, incomingSid, incomingId, rowEl) {
    if (!patientNumber) {
      alert("Patient number missing for callback.");
      return;
    }
    const existing = document.querySelector(".cb-overlay");
    if (existing) existing.remove();

    const overlay = document.createElement("div");
    overlay.className = "cb-overlay";
    overlay.innerHTML = `
      <div class="cb-modal">
        <div class="cb-title">Call back ${safe(patientNumber)}</div>
        <div class="cb-grid"></div>
        <div class="cb-actions">
          <button class="btn" id="cb-cancel">Cancel</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const grid = overlay.querySelector(".cb-grid");
    LANDLINE_OPTIONS.forEach((opt) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `cb-btn ${opt.type}`;
      b.innerHTML = `<span class="num">${opt.num}</span><span class="lbl">${opt.label}</span>`;
      b.addEventListener("click", () => {
        startCallback(patientNumber, opt.num, incomingSid, incomingId, rowEl);
        overlay.remove();
      });
      grid.appendChild(b);
    });
    overlay.querySelector("#cb-cancel").addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });
  }

  function startCallback(patientNumber, landline, incomingSid, incomingId, rowEl) {
    if (!confirm(`Call back ${patientNumber} from ${landline}?`)) return;
    fetch("/cce/make-call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to: patientNumber,
        cce_number: landline,
        call_sid: incomingSid || ""
      }),
    })
      .then(async (res) => {
        let j = {};
        try {
          j = await res.json();
        } catch (e) {}
        if (res.ok && (j.status === "ok" || j.ok)) {
          alert("Call initiated");
          if (rowEl) {
            rowEl.remove();
            if (!missedBody.children.length) {
              const trEmpty = document.createElement("tr");
              trEmpty.className = "empty";
              trEmpty.innerHTML = `<td colspan="5" class="muted" style="padding:14px;">No missed calls</td>`;
              missedBody.appendChild(trEmpty);
            }
          }
        } else {
          alert(j.message || "Call initiate failed");
        }
      })
      .catch(() => {
        alert("Network error while initiating call");
      });
  }

  function renderClaimControls(container, info) {
    const block = document.createElement("div");
    block.className = "cta-wrap";
    block.innerHTML = `
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
        <button class="btn btn-success btn-complete" disabled>Completed</button>
        <button class="btn btn-warn btn-mistake">Accepted by Mistake</button>
      </div>
    `;
    container.appendChild(block);

    const sel = block.querySelector(".sel-type");
    const bComplete = block.querySelector(".btn-complete");
    const bMistake = block.querySelector(".btn-mistake");

    sel.addEventListener("change", () => {
      bComplete.disabled = !sel.value;
    });

    bComplete.addEventListener("click", async () => {
      if (!sel.value) {
        alert("Please select Call Type");
        return;
      }
      try {
        const r = await fetch("/cce/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ call_sid: info.sid, call_related_to: sel.value }),
        });
        const j = await r.json().catch(() => ({}));
        if (r.ok && j?.status === "ok") {
          removePopup(info.sid);
        } else {
          alert(j.message || "Complete failed");
        }
      } catch (e) {
        alert("Complete failed");
      }
    });

    bMistake.addEventListener("click", async () => {
      if (!confirm("Release this claim?")) return;
      try {
        const r = await fetch("/cce/release", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ call_sid: info.sid }),
        });
        const j = await r.json().catch(() => ({}));
        if (r.ok && j?.status === "ok") {
          removePopup(info.sid);
        } else {
          alert(j.message || "Release failed");
        }
      } catch (e) {
        alert("Release failed");
      }
    });
  }

  function createPopup(info, preservePosition = false) {
    if (!popupsEnabled()) return;
    if (info.locked && info.acceptedByName && info.acceptedByName !== ME_NAME) return;
    if (info.callType === "client-hangup") {
      removePopup(info.sid);
      return;
    }

    const el = document.createElement("div");
    el.className = "pop";
    el.dataset.sid = info.sid;

    const t0 = new Date(info.createdAt).getTime();

    el.innerHTML = `
      <div class="row">
        <div class="badges">
          <span class="badge inbound">${info.direction.toUpperCase()}</span>
          <span class="badge ringing">${info.statusText.charAt(0).toUpperCase() + info.statusText.slice(1)}</span>
        </div>
        <div class="since"><span class="since-val">00:00</span></div>
      </div>

      <div class="head">
        <div class="caller">
          <div style="width:30px;height:30px;border-radius:50%;background:#f3f4f6;display:grid;place-items:center;font-weight:800">
            ${initials(info.callerName || "IN")}
          </div>
          <div>
            <div class="num">${info.from || "-"}</div>
            <div class="agentline">Agent: ${info.to || "-"}</div>
          </div>
        </div>
        <button class="claim ${info.locked ? "" : "primary"}" ${info.locked ? "disabled" : ""}>
          ${info.locked ? (info.acceptedByName === ME_NAME ? 'Claimed ƒo"' : "Already Claimed") : "Claim"}
        </button>
      </div>

      <div class="cols">
        <div class="box">
          <div class="box-title">MATCHES</div>
          <div class="match-chip">
            <div class="match-summary">Loadingƒ?Ý</div>
          </div>
        </div>
      </div>

      <div class="actions">
        <div class="actions-ctas hide"></div>
        <button class="pill warn dismiss">Dismiss</button>
      </div>
    `;

    const sinceEl = el.querySelector(".since-val");
    let timer = setInterval(() => {
      sinceEl.textContent = fmtElapsed(Date.now() - t0);
    }, 500);
    callIndex.set(info.sid, { el, timer });

    fetch(`/cce/matches?phone=${encodeURIComponent(info.from || "")}`)
      .then((r) => r.json())
      .then((j) => {
        const box = el.querySelector(".match-chip");
        if (!j.ok) {
          box.innerHTML = '<div class="muted">No matches</div>';
          return;
        }

        const tlist = j.matches?.tickets || [];
        const llist = j.matches?.leads || [];
        const tcount =
          j.summary && typeof j.summary.ticket_count === "number" ? j.summary.ticket_count : tlist.length;
        const lcount = j.summary && typeof j.summary.lead_count === "number" ? j.summary.lead_count : llist.length;

        const chunks = [];
        if (tcount > 0) {
          const t = tlist[0] || {};
          chunks.push(`
            <div class="match-summary">dYZ® <b>Ticket</b><span class="chip-pill">${tcount}</span></div>
            <div class="muted" style="font-size:12px">${t.ticket_type || "-"}</div>
          `);
        }
        if (lcount > 0) {
          const l = llist[0] || {};
          chunks.push(`
            <div class="match-summary">dY"< <b>Lead</b><span class="chip-pill">${lcount}</span></div>
            <div class="muted" style="font-size:12px">${l.name || "-"}</div>
          `);
        }
        box.innerHTML = chunks.length ? chunks.join("") : '<div class="muted">No open records</div>';
      })
      .catch(() => {
        el.querySelector(".match-chip").innerHTML = '<div class="muted">Error fetching</div>';
      });

    fetchLastClaimant(info.from).then((claimantName) => {
      if (claimantName) {
        const claimantHtml = `
          <div class="box">
            <div class="box-title">LAST CLAIMED BY</div>
            <div style="font-size:13px;font-weight:600;color:#111827">${claimantName}</div>
          </div>
        `;
        const cols = el.querySelector(".cols");
        cols.insertAdjacentHTML("beforeend", claimantHtml);
      }
    });

    el.querySelector(".dismiss").addEventListener("click", () => removePopup(info.sid));

    const claimBtn = el.querySelector(".claim");
    const actionsCtas = el.querySelector(".actions-ctas");

    function showClaimCTAs() {
      if (!actionsCtas || !actionsCtas.classList.contains("hide")) return;
      actionsCtas.classList.remove("hide");
      renderClaimControls(actionsCtas, info);
    }

    if (claimBtn) {
      claimBtn.addEventListener("click", async () => {
        if (claimBtn.disabled) return;
        claimBtn.disabled = true;
        claimBtn.textContent = "Claimingƒ?Ý";
        try {
          const r = await fetch(`/cce/accept`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ call_sid: info.sid, phone: info.from }),
          });
          const j = await r.json().catch(() => ({}));
          if (r.ok && j?.status === "ok") {
            const who = j.accepted_by_name || ME_NAME || "You";
            claimBtn.textContent = 'Claimed ƒo"';
            claimBtn.classList.remove("primary");

            const currentEl = callIndex.get(info.sid)?.el;
            if (currentEl) {
              currentEl.remove();
            }
            createPopup({ ...info, locked: true, acceptedByName: who }, true);
            return;
          } else {
            claimBtn.disabled = false;
            claimBtn.textContent = "Claim";
            alert(j.message || "Could not claim. Maybe already claimed.");
          }
        } catch (e) {
          claimBtn.disabled = false;
          claimBtn.textContent = "Claim";
        }
      });
    }

    const statusTxtInit = (info.statusText || "").toLowerCase();
    const typeTxtInit = (info.callType || "").toLowerCase();
    if ((TERMINALS.has(statusTxtInit) || TERMINALS.has(typeTxtInit)) && !info.locked) {
      try {
        const ringBadge = el.querySelector(".badge.ringing");
        if (ringBadge) ringBadge.textContent = "Ended";
      } catch (e) {
        /* no-op */
      }
    }

    if (info.callType === "call-attempt") {
      const created = new Date(info.createdAt).getTime();
      const until15 = Math.max(0, 15 * 60 * 1000 - (Date.now() - created));
      setTimeout(() => {
        removePopup(info.sid);
      }, until15);
    }

    if (preservePosition) {
      popwrap.insertBefore(el, popwrap.firstChild);
    } else {
      popwrap.appendChild(el);
    }
    popwrap.scrollTop = popwrap.scrollHeight;

    if (info.locked && info.acceptedByName === ME_NAME && claimBtn) {
      claimBtn.textContent = 'Claimed ƒo"';
      claimBtn.classList.remove("primary");
      claimBtn.disabled = true;
      showClaimCTAs();
    }
  }

  function upsertPopup(raw) {
    // Ignore self-loop/incomplete events from Exotel when From == To (outbound leg webhooks)
    const fn = safe(raw.from_number || raw.From || "");
    const tn = safe(raw.to_number || raw.To || "");
    const ct = safe(raw.call_type || raw.CallType || "").toLowerCase();
    if (fn && tn && fn === tn) return;
    if (ct === "incomplete" && fn === tn) return;

    if (!popupsEnabled()) return;
    const info = normalizeForUI(raw);
    if (!info) return;

    // Skip rendering popups for incomplete calls (they go straight to missed)
    if (info.callType === "incomplete") {
      return;
    }

    if (info.callType === "client-hangup") {
      removePopup(info.sid);
      return;
    }

    const terminal = isTerminal(info.statusText, info.callType);

    if (callIndex.has(info.sid)) {
      const ref = callIndex.get(info.sid);
      if (terminal && !info.locked) {
        try {
          const ringBadge = ref.el.querySelector(".badge.ringing");
          if (ringBadge) ringBadge.textContent = "Ended";
        } catch (e) {
          /* no-op */
        }
      }

      if (info.locked && info.acceptedByName === ME_NAME) {
        const claimBtn = ref.el.querySelector(".claim");
        if (claimBtn) {
          claimBtn.textContent = 'Claimed ƒo"';
          claimBtn.classList.remove("primary");
          claimBtn.disabled = true;
          const actionsCtas = ref.el.querySelector(".actions-ctas");
          if (actionsCtas && actionsCtas.classList.contains("hide")) {
            actionsCtas.classList.remove("hide");
            renderClaimControls(actionsCtas, info);
          }
        }
      }
    } else {
      createPopup(info);
      if (terminal && !info.locked) {
        try {
          const ref = callIndex.get(info.sid);
          if (ref?.el) {
            const ringBadge = ref.el.querySelector(".badge.ringing");
            if (ringBadge) ringBadge.textContent = "Ended";
          }
        } catch (e) {
          /* no-op */
        }
      }
    }
  }

  function debounce(fn, wait) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  }
  const debouncedLoadMissed = debounce(loadMissed, 300);

  let socket = null;
  let fallbackTimer = null;

  function startFallback() {
    if (fallbackTimer || !hasMissedTable) return;
    if (document.visibilityState === "visible") {
      fallbackTimer = setInterval(loadMissed, 60000);
    }
  }
  function stopFallback() {
    if (fallbackTimer) {
      clearInterval(fallbackTimer);
      fallbackTimer = null;
    }
  }

  function connectWS() {
    if (!WS_URL && !EXOTEL_HTTP) return;
    try {
      socket = io(WS_URL || EXOTEL_HTTP, { transports: ["websocket"] });
    } catch (e) {
      socket = io(EXOTEL_HTTP, { transports: ["websocket"] });
    }

    socket.on("connect", () => {
      if (statusEl) statusEl.textContent = 'Live connected ƒo"';
      if (dotEl) dotEl.style.background = "#22c55e";
      stopFallback();
      debouncedLoadMissed();
    });

    socket.on("disconnect", () => {
      if (statusEl) statusEl.textContent = "Disconnectedƒ?Ý retrying";
      if (dotEl) dotEl.style.background = "#ef4444";
      startFallback();
    });

    socket.on("incoming_call", (payload) => {
      upsertPopup(payload);
    });
    socket.on("popup_close", (payload) => {
      try {
        const sid = (payload?.call_sid || payload?.CallSid || "").toString();
        if (!sid) return;
        if (payload?.accepted_by_name && payload.accepted_by_name === ME_NAME) return;
        removePopup(sid);
      } catch (e) {
        /* no-op */
      }
    });

    socket.on("missed_update", () => {
      debouncedLoadMissed();
    });
  }

  function handleSocketByPref() {
    if (!socket || !socket.connected) {
      tryReconnectWS();
    }
    applyPopupContainerVisibility();
  }

  function tryReconnectWS() {
    connectWS();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") stopFallback();
    else if (!socket || !socket.connected) startFallback();
  });

  async function preload() {
    if (hasMissedTable) {
      await loadMissed();
    } else {
      // even if no table, ensure cache cleanups for hangups
      try {
        await loadMissed();
      } catch (e) {
        /* no-op */
      }
    }

    try {
      const p = await fetch(`/cce/persist?minutes=180`, { cache: "no-store" });
      const pj = await p.json();
      const plist = Array.isArray(pj) ? pj : pj.data || [];
      plist.forEach((item) => upsertPopup(item));
    } catch (e) {
      /* no-op */
    }

    try {
      const res = await fetch("/cce/raw?limit=20", { cache: "no-store" });
      const j = await res.json();
      const list = Array.isArray(j) ? j : j.data || [];
      list.forEach((item) => upsertPopup(item));
    } catch (e) {
      /* no-op */
    }
  }

  preload();
  connectWS();
  handleSocketByPref();
})();
