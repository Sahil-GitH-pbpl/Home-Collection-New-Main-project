const currentUser = document.querySelector(".appShell")?.dataset.currentUserName || "Staff";
const appShell = document.querySelector(".appShell");
const currentUserId = Number(appShell?.dataset.currentUserId || 0);
const closureTypes = ["Report", "Invoice", "Lead", "Complaint", "Home Collection", "Doctor", "Pickup", "Other"];
const MESSAGE_PAGE_SIZE = 100;

const state = {
  activeMobile: "",
  activeQueue: "All Chats",
  allRows: [],
  rows: [],
  messageRows: [],
  oldestMessageId: null,
  hasOlderMessages: false,
  loadingOlderMessages: false,
  operatorOptions: [],
  searchTimer: null,
  spellTimer: null,
  autocompleteSuggestion: "",
  autocompleteSuffix: "",
  misspelledWord: "",
  showChatList: true,
  drawerMode: "",
  selectedFile: null,
  lookupCache: new Map(),
  auditCache: new Map(),
};

const els = {
  toast: document.querySelector("#toast"),
  breachCount: document.querySelector("#breachCount"),
  topStateChip: document.querySelector("#topStateChip"),
  queueNav: document.querySelector("#queueNav"),
  conversationList: document.querySelector("#conversationList"),
  messages: document.querySelector("#messages"),
  activeMobile: document.querySelector("#activeMobile"),
  activeSummary: document.querySelector("#activeSummary"),
  ownerLine: document.querySelector("#ownerLine"),
  slaStatus: document.querySelector("#slaStatus"),
  takenAt: document.querySelector("#takenAt"),
  typeSelect: document.querySelector("#typeSelect"),
  saveTypeBtn: document.querySelector("#saveTypeBtn"),
  takeBtn: document.querySelector("#takeBtn"),
  closeBtn: document.querySelector("#closeBtn"),
  replyForm: document.querySelector("#replyForm"),
  replyText: document.querySelector("#replyText"),
  autocompleteGhost: document.querySelector("#autocompleteGhost"),
  sendBtn: document.querySelector("#sendBtn"),
  imageFileInput: document.querySelector("#imageFileInput"),
  documentFileInput: document.querySelector("#documentFileInput"),
  attachImageBtn: document.querySelector("#attachImageBtn"),
  attachDocumentBtn: document.querySelector("#attachDocumentBtn"),
  dictBtn: document.querySelector("#dictBtn"),
  hubGrid: document.querySelector("#hubGrid"),
  toggleChatList: document.querySelector("#toggleChatList"),
  searchInput: document.querySelector("#searchInput"),
  dateInput: document.querySelector("#dateInput"),
  tagStrip: document.querySelector("#tagStrip"),
  tagNote: document.querySelector("#tagNote"),
  contactName: document.querySelector("#contactName"),
  contactContext: document.querySelector("#contactContext"),
  leadList: document.querySelector("#leadList"),
  ticketList: document.querySelector("#ticketList"),
  homeCollectionList: document.querySelector("#homeCollectionList"),
  auditSummary: document.querySelector("#auditSummary"),
  assistPanel: document.querySelector("#assistPanel"),
  drawerBackdrop: document.querySelector("#drawerBackdrop"),
  drawerTitle: document.querySelector("#drawerTitle"),
  drawerQuestion: document.querySelector("#drawerQuestion"),
  drawerFields: document.querySelector("#drawerFields"),
  drawerClose: document.querySelector("#drawerClose"),
  drawerCancel: document.querySelector("#drawerCancel"),
  drawerSave: document.querySelector("#drawerSave"),
  newConversationBtn: document.querySelector("#newConversationBtn"),
  actionButtons: document.querySelectorAll("[data-drawer]"),
  contextTabs: document.querySelectorAll("[data-context-tab]"),
  contextPanels: document.querySelectorAll("[data-context-panel]"),
};


const professionalEmojis = [
  ["ðŸ™", "Polite request"],
  ["âœ…", "Confirmed"],
  ["ðŸ“„", "Report/document"],
  ["ðŸ•’", "Time/please wait"],
  ["ðŸ“", "Location"],
  ["ðŸ“ž", "Call"],
];

const attachmentTypes = [
  ["Report PDF", "Lab_Report.pdf", "PDF Â· secure report"],
  ["Invoice PDF", "Invoice.pdf", "PDF Â· billing"],
  ["Prescription Image", "Prescription.jpg", "Image Â· prescription"],
  ["Receipt", "Payment_Receipt.pdf", "PDF Â· receipt"],
];

const drawerForms = {
  Link: [],
  Create: [],
  "New Conversation": ["Mobile Number", "Message"],
  "Add Tag": ["Tag Name"],
  Reassign: ["New Owner", "Reason"],
};

const linkForms = {
  Patient: ["Labmate Patient ID", "Mobile Number", "Patient Name"],
  Ticket: ["Ticket ID", "Reason"],
  Lead: ["Lead ID", "Reason"],
};

const createForms = {
  Ticket: ["Ticket Type", "Commitment Time", "Additional Information"],
  Lead: ["Name", "Mobile Number", "Follow-up Due"],
  "Home Collection": ["Preferred Date", "Address Area", "Slot"],
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
  }[char]));
}

function mobileKey(value) {
  const digits = String(value || "").replace(/\D/g, "");
  return digits.length > 10 ? digits.slice(-10) : digits;
}

function patientDisplayName(patient) {
  const title = String(patient?.title || "").trim();
  const name = String(patient?.full_name || "").trim();
  return [title, name].filter(Boolean).join(" ") || "-";
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.text();
  let json;
  try {
    json = JSON.parse(body);
  } catch {
    throw new Error(`Server request failed (${response.status})`);
  }
  if (!response.ok || json.ok === false) throw new Error(json.error || "Request failed");
  return json;
}

function showToast(text, type = "info") {
  if (!els.toast) return;
  els.toast.textContent = text;
  els.toast.classList.remove("success", "error", "info");
  els.toast.classList.add(type);
  els.toast.classList.remove("hidden");
  window.setTimeout(() => els.toast.classList.add("hidden"), 2200);
}

function renderAutocompleteGhost() {
  if (!els.autocompleteGhost) return;
  const text = els.replyText.value;
  const word = state.misspelledWord;
  const escapedText = escapeHtml(text);
  const highlighted = word
    ? escapedText.replace(new RegExp(`(\\b${word}\\b)(?![\\s\\S]*\\b${word}\\b)`, "i"), '<mark>$1</mark>')
    : escapedText;
  els.autocompleteGhost.innerHTML = `${highlighted}<span class="ghostCompletion">${escapeHtml(state.autocompleteSuffix)}</span>`;
}

function clearComposerInput() {
  if (els.replyText) els.replyText.value = "";
  state.autocompleteSuggestion = "";
  state.autocompleteSuffix = "";
  state.misspelledWord = "";
  renderAutocompleteGhost();
}

function connectLiveUpdates() {
  if (typeof window.io !== "function") return;
  const socket = window.io();
  socket.on("incoming_message", async (message) => {
    await loadConversations();
    if (mobileKey(state.activeMobile) !== mobileKey(message.mobile)) return;
    const alreadyLoaded = state.messageRows.some((row) => (
      Number(row.id) === Number(message.id) && row.color === "red"
    ));
    if (!alreadyLoaded) {
      state.messageRows = mergeMessageRows(state.messageRows, [message]);
      renderMessages(state.messageRows);
    }
  });
  socket.on("outgoing_message", async (message) => {
    await loadConversations();
    if (mobileKey(state.activeMobile) !== mobileKey(message.mobile)) return;
    const alreadyLoaded = state.messageRows.some((row) => Number(row.id) === Number(message.id) && row.color === "green");
    if (!alreadyLoaded) {
      state.messageRows = mergeMessageRows(state.messageRows, [message]);
      renderMessages(state.messageRows);
    }
  });
  socket.on("ownership_released", async (event) => {
    await loadConversations();
    showToast(`${event.count || 1} unanswered chat ownership released`);
  });
  socket.on("sla_tick", () => loadConversations().catch((error) => showToast(error.message)));
}

function applyWorkflowState(stateRow) {
  if (!stateRow?.mobile) return null;
  state.allRows = state.allRows.map((row) => (
    row.mobile === stateRow.mobile
      ? {
        ...row,
        owner_name: stateRow.owner_name,
        conversation_type: stateRow.conversation_type,
        workflow_status: stateRow.status,
        closed_at: stateRow.closed_at,
        closure_note: stateRow.closure_note,
        sla_minutes: stateRow.sla_started_at ? 0 : row.sla_minutes,
      }
      : row
  ));
  return state.allRows.find((row) => row.mobile === stateRow.mobile);
}

function setComposerEnabled(enabled, placeholder) {
  els.replyText.disabled = !enabled;
  els.sendBtn.disabled = !enabled;
  [els.attachImageBtn, els.attachDocumentBtn, els.dictBtn].forEach((button) => {
    if (button) button.disabled = !enabled;
  });
  els.replyText.placeholder = placeholder;
  if (!enabled) {
    els.replyText.value = "";
    state.autocompleteSuggestion = "";
    state.autocompleteSuffix = "";
    state.misspelledWord = "";
    renderAutocompleteGhost();
    state.selectedFile = null;
    if (els.imageFileInput) els.imageFileInput.value = "";
    if (els.documentFileInput) els.documentFileInput.value = "";
    renderAttachmentDraft();
    closeAssist();
  }
}

function displayTime(row) {
  const date = rowDate(row);
  if (Number.isNaN(date.getTime())) return String(row.datetimess || "").replace("T", " ").slice(0, 16);
  const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const time = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  return `${weekdays[date.getDay()]}, ${String(date.getDate()).padStart(2, "0")} ${months[date.getMonth()]} ${date.getFullYear()}, ${time}`;
}

function rowDate(row) {
  const raw = String(row.datetimess || row.time || "").trim();
  if (!raw) return new Date();
  const mysqlMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (mysqlMatch) {
    return new Date(
      Number(mysqlMatch[1]),
      Number(mysqlMatch[2]) - 1,
      Number(mysqlMatch[3]),
      Number(mysqlMatch[4]),
      Number(mysqlMatch[5]),
      Number(mysqlMatch[6] || 0),
    );
  }
  const isoDate = new Date(raw);
  if (!Number.isNaN(isoDate.getTime())) return isoDate;
  const match = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:,\s*(\d{1,2}):(\d{2})\s*([AP]M))?/i);
  if (!match) return new Date();
  let hour = Number(match[4] || 0);
  const minute = Number(match[5] || 0);
  const meridiem = String(match[6] || "").toUpperCase();
  if (meridiem === "PM" && hour < 12) hour += 12;
  if (meridiem === "AM" && hour === 12) hour = 0;
  return new Date(Number(match[3]), Number(match[2]) - 1, Number(match[1]), hour, minute);
}

function dateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function dateDividerLabel(row) {
  const date = rowDate(row);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (dateKey(date) === dateKey(today)) return "Today";
  if (dateKey(date) === dateKey(yesterday)) return "Yesterday";
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function mediaUrl(value) {
  const text = String(value || "").trim();
  if (text.startsWith("/uploads/")) return text;
  if (!text.startsWith("http://") && !text.startsWith("https://")) return "";
  try {
    const url = new URL(text);
    // Keep panel previews on the same host the user opened, even when Netcore
    // stores an externally reachable public URL for attachment delivery.
    if (url.pathname.startsWith("/uploads/")) return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "";
  }
  return text;
}

function mediaExtension(value) {
  return String(value || "").split("?", 1)[0].split("#", 1)[0].toLowerCase();
}

function mediaInfo(row) {
  const imageUrl = mediaUrl(row.imgid) || mediaUrl(row.img);
  if (imageUrl || row.img) {
    return {
      kind: "image",
      label: row.msg || row.img || "Image attachment",
      url: imageUrl,
      id: row.imgid || row.img || "",
    };
  }
  const documentUrl = mediaUrl(row.docid);
  if (documentUrl || row.pdff || row.docid) {
    return {
      kind: "document",
      label: row.pdff || row.msg || "Document attachment",
      url: documentUrl,
      id: row.docid || "",
    };
  }
  return null;
}

function renderMedia(row) {
  const media = mediaInfo(row);
  if (!media) return "";
  const label = escapeHtml(media.label);
  const url = escapeHtml(media.url);
  const idText = media.id && !media.url ? `<span>${escapeHtml(media.id)}</span>` : "";
  if (media.kind === "image" && media.url) {
    return `
      <a class="imageBubble" href="${url}" target="_blank" rel="noreferrer" aria-label="${label}">
        <img src="${url}" alt="${label}" loading="lazy" decoding="async" />
      </a>
    `;
  }
  if (media.kind === "audio" && media.url) {
    return `
      <div class="audioBubble">
        <strong>${label}</strong>
        <audio controls preload="metadata" src="${url}"></audio>
      </div>
    `;
  }
  if (media.kind === "video" && media.url) {
    return `
      <div class="videoBubble">
        <video controls preload="metadata" src="${url}"></video>
      </div>
    `;
  }
  const href = media.url ? ` href="${url}" target="_blank" rel="noreferrer"` : "";
  return `
    <a class="fileBubble"${href}>
      <div><strong>${label}</strong><span>${media.kind === "document" ? "PDF / document" : media.kind === "audio" ? "Audio" : media.kind === "video" ? "Video" : "Image"}${media.url ? " - open" : " - media id"}</span>${idText}</div>
    </a>
  `;
}

function deliveryTick(row) {
  if (row.color !== "green") return "";
  const status = String(row.delivery_status || (row.provider_message_id ? "accepted" : "local")).toLowerCase();
  const title = status || "local";
  if (status.includes("fail") || status.includes("error") || status.includes("reject")) {
    return `<span class="deliveryTick failed" title="${escapeHtml(title)}">!</span>`;
  }
  if (status.includes("read")) {
    return `<span class="deliveryTick read" title="${escapeHtml(title)}">✓✓</span>`;
  }
  if (status.includes("deliver")) {
    return `<span class="deliveryTick delivered" title="${escapeHtml(title)}">✓✓</span>`;
  }
  if (status === "local") {
    return `<span class="deliveryTick local" title="${escapeHtml(title)}">✓</span>`;
  }
  return `<span class="deliveryTick accepted" title="${escapeHtml(title)}">✓✓</span>`;
}

function conversationType(row) {
  if (row.conversation_type) return row.conversation_type;
  const text = `${row.msg || ""} ${row.pdff || ""}`.toLowerCase();
  if (text.includes("invoice") || text.includes("bill")) return "Invoice";
  if (text.includes("home") || text.includes("collection") || text.includes("pickup")) return "Home Collection";
  if (text.includes("doctor") || text.includes("clinic")) return "Doctor";
  if (text.includes("lead") || text.includes("price") || text.includes("package")) return "Lead";
  return "Report";
}

function validDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function hasPendingLoginUserReply(row) {
  if (!row || isClosed(row)) return false;
  if (row.sla_pending !== undefined && row.sla_pending !== null) return Boolean(row.sla_pending);
  const isIncoming = String(row.color || "").trim().toLowerCase() === "red";
  const incomingAt = validDate(row.last_incoming_at) || (isIncoming ? validDate(row.datetimess) : null);
  if (!incomingAt) return false;
  const userReplyAt = validDate(row.last_user_reply_at);
  return !userReplyAt || incomingAt.getTime() > userReplyAt.getTime();
}

function isWaitingForPatient(row) {
  if (!row || isClosed(row)) return false;
  return !hasPendingLoginUserReply(row) && String(row.color || "").trim().toLowerCase() === "green";
}

function sla(row) {
  if (!hasPendingLoginUserReply(row)) return { value: 0, label: "0m", tone: "green" };
  const serverMinutes = Number(row.sla_minutes);
  const value = Number.isFinite(serverMinutes) ? serverMinutes : 0;
  const tone = value < 5 ? "green" : value <= 15 ? "amber" : value <= 30 ? "red" : "breach";
  return { value, label: `${value}m`, tone };
}

function slaBadge(row) {
  const currentSla = sla(row);
  if (!hasPendingLoginUserReply(row)) {
    return `<span class="sla replied">Replied</span>`;
  }
  return `<span class="sla ${currentSla.tone}">${escapeHtml(currentSla.label)}</span>`;
}

function lockIcon(locked) {
  return locked
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2"></rect><path d="M8 10V7a4 4 0 0 1 8 0v3"></path></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2"></rect><path d="M8 10V7a4 4 0 0 1 7.4-2.1"></path></svg>';
}

function ownerFor(row) {
  return row.owner_name || null;
}

function isClosed(row) {
  return row.workflow_status === "closed";
}

function isOpenConversation(row) {
  return !isClosed(row);
}

function isArchivedForSelectedDate(row) {
  if (!isClosed(row) || !row.closed_at) return false;
  const selectedDate = els.dateInput?.value;
  if (!selectedDate) return false;
  const closedAt = validDate(row.closed_at);
  const selectedAt = validDate(`${selectedDate}T00:00:00`);
  return Boolean(closedAt && selectedAt && dateKey(closedAt) === dateKey(selectedAt));
}

function tagClass(label) {
  return `tag tag-${label.toLowerCase().replaceAll(" ", "-")}`;
}

function mergeTags(row) {
  const type = conversationType(row);
  return Array.from(new Set([type].filter(Boolean)));
}

function conversationPreview(row) {
  const text = row.msg || row.pdff || row.img || "Attachment";
  return row.color === "green" ? `You: ${text}` : text;
}

function displayContactName(row) {
  return row.patient_name || row.mobile;
}

function formatShortDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  }
  return String(value).slice(0, 11);
}

function hcStatusLabel(value) {
  const status = Number(value);
  if (status === 0) return "Pending";
  if (status === 1) return "Assigned";
  if (status === 2) return "Started";
  if (status === 3) return "Completed";
  if (status === 4) return "Cancelled";
  if (status === 5) return "Partial Complete";
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

function miniRecordHtml(record) {
  const tagName = record.href ? "a" : "article";
  const hrefAttr = record.href ? ` href="${escapeHtml(record.href)}"` : "";
  const targetAttr = record.href ? ' target="_blank" rel="noopener"' : "";
  return `
    <${tagName} class="miniRecord"${hrefAttr}${targetAttr}>
      <div class="miniRecordLine">
        <span class="miniLabel">${escapeHtml(record.idLabel || "No")}</span>
        <strong>${escapeHtml(record.id || "-")}</strong>
        <time>${escapeHtml(formatShortDate(record.date))}</time>
      </div>
      <div class="miniRecordLine">
        <span class="miniLabel">${escapeHtml(record.nameLabel || "Name")}</span>
        <span class="miniValue">${escapeHtml(record.name || "-")}</span>
        ${record.status ? `<span class="statusPill">${escapeHtml(record.status)}</span>` : ""}
      </div>
      ${(record.lines || []).map((line) => `
        <div class="miniRecordLine">
          <span class="miniLabel">${escapeHtml(line.label || "-")}</span>
          <span class="miniValue">${escapeHtml(line.value || "-")}</span>
        </div>
      `).join("")}
    </${tagName}>
  `;
}

function renderMiniList(element, rows, mapRow, emptyText) {
  if (!element) return;
  const visible = (rows || []).slice(0, 4);
  if (!visible.length) {
    element.innerHTML = `<p class="emptyLink">${escapeHtml(emptyText)}</p>`;
    return;
  }
  element.innerHTML = visible.map((row) => {
    const mapped = mapRow(row);
    return miniRecordHtml(mapped);
  }).join("");
}

function splitTagText(value) {
  return String(value || "")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function uniqueTags(tags) {
  const seen = new Set();
  return tags.filter((tag) => {
    const key = tag.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderPatientTags(linkedPatients, row) {
  const activeMobile = mobileKey(row?.mobile || state.activeMobile);
  const matched = (linkedPatients || []).filter((patient) => (
    mobileKey(patient.contact_mobile) === activeMobile || mobileKey(patient.alternate_mobile) === activeMobile
  ));
  const source = matched.length ? matched : (linkedPatients || []);
  const tags = uniqueTags(source.flatMap((patient) => splitTagText(patient.tag)));
  if (els.tagNote) els.tagNote.textContent = "";
  if (!els.tagStrip) return;
  els.tagStrip.innerHTML = tags.length
    ? tags.map((tag) => `<span class="patientTagChip">${escapeHtml(tag)}</span>`).join("")
    : '<span class="emptyTag">-</span>';
}

function actionLabel(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function auditDescription(action) {
  const actor = action.performed_by_name || "System";
  const oldOwner = action.old_owner_name || action.old_value || "Unassigned";
  const newOwner = action.new_owner_name || action.new_value || "";
  if (action.action_type === "reassign") {
    return newOwner
      ? `${actor} assigned this chat to ${newOwner}`
      : `${actor} removed this chat's assignment`;
  }
  if (action.action_type === "take_ownership") {
    return `${actor} took ownership`;
  }
  if (action.action_type === "auto_release_ownership") {
    return `${oldOwner} ownership automatically unlocked after 15 minutes without a reply`;
  }
  if (action.action_type === "reopen_conversation") {
    return `${actor} reopened conversation`;
  }
  if (action.action_type === "update_type") {
    return `${actor} changed type from ${action.old_value || "-"} to ${action.new_value || "-"}`;
  }
  if (action.action_type === "close_conversation") {
    return `${actor} closed conversation`;
  }
  if (action.action_type === "send_message") {
    return `${actor} sent message`;
  }
  return `${actor} performed ${actionLabel(action.action_type)}`;
}

function auditDetail(action) {
  const details = [];
  if (action.reason) details.push(`Reason: ${action.reason}`);
  if (action.new_value && !["reassign", "update_type"].includes(action.action_type)) details.push(`Value: ${action.new_value}`);
  return details.join(" | ");
}

function renderAuditActions(actions) {
  if (!els.auditSummary) return;
  const visible = actions || [];
  if (!visible.length) {
    els.auditSummary.innerHTML = '<span class="emptyLink">No audit actions recorded yet.</span>';
    return;
  }
  els.auditSummary.innerHTML = `
    <div class="auditList">
      ${visible.map((action) => `
        <article class="auditItem">
          <strong>${escapeHtml(auditDescription(action))}</strong>
          <span>${escapeHtml(auditDetail(action) || actionLabel(action.action_type))}</span>
          <time>${escapeHtml(formatShortDate(action.created_at))}</time>
        </article>
      `).join("")}
    </div>
  `;
}

function setContextLoading() {
  if (els.leadList) els.leadList.innerHTML = '<p class="emptyLink">Open Leads to load records.</p>';
  if (els.ticketList) els.ticketList.innerHTML = '<p class="emptyLink">Open Tickets to load records.</p>';
  if (els.homeCollectionList) els.homeCollectionList.innerHTML = '<p class="emptyLink">Open HC to load bookings.</p>';
  if (els.auditSummary) els.auditSummary.textContent = "Open Audit to load conversation history.";
}

function activateContextTab(tab) {
  els.contextTabs.forEach((button) => button.classList.toggle("active", button.dataset.contextTab === tab));
  els.contextPanels.forEach((panel) => panel.classList.toggle("active", panel.dataset.contextPanel === tab));
}

function renderUnifiedLookup(data, row) {
  const sections = new Set(data?.sections || ["contact", "hc", "leads", "tickets"]);
  const caller = data?.caller;
  const linkedPatients = data?.linked_patients || [];
  if (sections.has("contact")) {
    els.contactName.textContent = linkedPatients.length
      ? `${linkedPatients.length} linked patient${linkedPatients.length > 1 ? "s" : ""}`
      : "No linked patients found";
    els.contactContext.innerHTML = linkedPatients.length
      ? `
        <div class="linkedPatientList">
          ${linkedPatients.slice(0, 6).map((patient) => {
            const name = patientDisplayName(patient);
            return `
              <div class="linkedPatientItem">
                <strong>${escapeHtml(name)}</strong>
                <span>${escapeHtml(patient.contact_mobile || patient.alternate_mobile || "-")}</span>
              </div>
            `;
          }).join("")}
        </div>
      `
      : `
        <p class="emptyLink">No linked patients found.</p>
      `;
    renderPatientTags(linkedPatients, row);
  }
  if (sections.has("hc")) {
    renderMiniList(
      els.homeCollectionList,
      data?.home_collection_bookings,
      (booking) => ({
        idLabel: "Bkg No",
        id: booking.booking_code || booking.id,
        nameLabel: "Patient",
        name: (booking.patients || []).map((patient) => patient.full_name).filter(Boolean).join(", ") || caller?.full_name || "-",
        date: booking.preferred_visit_date || booking.created_at,
        status: hcStatusLabel(booking.booking_status),
        lines: [{ label: "Phlebo", value: booking.assigned_phlebotomist || "-" }],
      }),
      "No home collection found."
    );
  }
  if (sections.has("leads")) {
    renderMiniList(
      els.leadList,
      data?.leads,
      (lead) => ({
        idLabel: "Lead No",
        id: lead.lead_id || lead.id,
        nameLabel: "Name",
        name: lead.name,
        date: lead.created_at,
        status: lead.status || "",
        lines: [
          { label: "Book By", value: lead.created_by_name || lead.created_by || "-" },
          { label: "Tag", value: lead.tags || "-" },
        ],
        href: lead.lead_id ? `/lead/${encodeURIComponent(lead.lead_id)}` : "",
      }),
      "No lead found."
    );
  }
  if (sections.has("tickets")) {
    renderMiniList(
      els.ticketList,
      data?.tickets,
      (ticket) => ({
        idLabel: "Ticket No",
        id: ticket.id,
        nameLabel: "Name",
        name: ticket.patient_name || ticket.client_name,
        date: ticket.created_at || ticket.commitment_at,
        status: ticket.status || "",
        lines: [{ label: "Category", value: ticket.ticket_category || "-" }],
        href: ticket.id ? `/tickets/${encodeURIComponent(ticket.id)}?mode=view` : "",
      }),
      "No ticket found."
    );
  }
}

async function lookupContext(mobile, section) {
  return fetchJson(`/api/mobile-lookup?mobile=${encodeURIComponent(mobile)}&sections=${encodeURIComponent(section)}`);
}

async function lookupAuditActions(mobile) {
  return fetchJson(`/api/conversations/${encodeURIComponent(mobile)}/actions`);
}

function messageKey(row) {
  const source = row.color === "green" ? "outgoing" : "incoming";
  if (row.id) return `${source}-message-${row.id}`;
  return `${source}-message-${row.datetimess || row.time || ""}-${row.msg || ""}`;
}

function mergeMessageRows(olderRows, newerRows) {
  const seen = new Set();
  return [...olderRows, ...newerRows].filter((row) => {
    const key = messageKey(row);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function fetchMessagesPage(mobile, beforeId = null) {
  const params = new URLSearchParams({ limit: String(MESSAGE_PAGE_SIZE) });
  if (beforeId) params.set("before_id", String(beforeId));
  return fetchJson(`/api/conversations/${encodeURIComponent(mobile)}/messages?${params.toString()}`);
}

function invalidateConversationCache(mobile, options = {}) {
  if (!mobile) return;
  if (options.lookup) state.lookupCache?.delete(mobile);
  if (options.audit !== false) state.auditCache?.delete(mobile);
}

function visibleRows() {
  const q = els.searchInput.value.trim().toLowerCase();
  return state.allRows.filter((row) => {
    const owner = ownerFor(row);
    const currentSla = sla(row);
    const matchesQueue =
      (state.activeQueue === "All Chats" && isOpenConversation(row)) ||
      (state.activeQueue === "Unassigned" && isOpenConversation(row) && !owner) ||
      (state.activeQueue === "My Chats" && isOpenConversation(row) && owner === currentUser) ||
      (state.activeQueue === "SLA Breached" && isOpenConversation(row) && currentSla.value > 30) ||
      (state.activeQueue === "Archived" && isArchivedForSelectedDate(row));
    const text = `${row.mobile || ""} ${row.patient_name || ""}`.toLowerCase();
    return matchesQueue && (!q || text.includes(q));
  });
}

function updateQueueCounts() {
  const openRows = state.allRows.filter(isOpenConversation);
  const counts = {
    "All Chats": openRows.length,
    Unassigned: openRows.filter((row) => !ownerFor(row)).length,
    "My Chats": openRows.filter((row) => ownerFor(row) === currentUser).length,
    "SLA Breached": openRows.filter((row) => sla(row).value > 30).length,
    Archived: state.allRows.filter(isArchivedForSelectedDate).length,
  };
  if (els.breachCount) els.breachCount.textContent = counts["SLA Breached"];
  els.queueNav.querySelectorAll("[data-queue]").forEach((button) => {
    button.querySelector("strong").textContent = counts[button.dataset.queue] ?? 0;
  });
}

function renderConversations() {
  state.rows = visibleRows();
  updateQueueCounts();
  if (!state.rows.length) {
    els.conversationList.innerHTML = '<p class="emptyLink">No patient conversations found.</p>';
    return;
  }
  els.conversationList.innerHTML = state.rows.map((row) => {
    const currentSla = sla(row);
    const owner = ownerFor(row);
    const selected = row.mobile === state.activeMobile ? " selected" : "";
    const tags = mergeTags(row);
    return `
      <button class="chatRow${selected}" type="button" data-mobile="${escapeHtml(row.mobile)}">
        <div class="rowTop">
          <strong>${escapeHtml(displayContactName(row))}</strong>
          <span>${escapeHtml(displayTime(row))}</span>
        </div>
        <p>${escapeHtml(conversationPreview(row))}</p>
        <div class="rowMeta">
          ${tags.map((tag) => `<span class="${tagClass(tag)}">${escapeHtml(tag)}</span>`).join("")}
          ${slaBadge(row)}
          <span>${escapeHtml(owner || "Unassigned")}</span>
          ${isClosed(row) ? '<span class="status closed">Closed</span>' : ""}
          ${row.color === "red" ? "<b>1</b>" : ""}
        </div>
      </button>
    `;
  }).join("");
}

function renderMessages(rows, options = {}) {
  if (!state.activeMobile) {
    els.messages.innerHTML = '<p class="emptyLink">Select a patient conversation to view messages.</p>';
    return;
  }
  if (!rows.length) {
    els.messages.innerHTML = '<p class="emptyLink">No messages in this conversation.</p>';
    return;
  }
  let activeDate = "";
  const olderControl = state.hasOlderMessages
    ? `<button class="loadOlderBtn" type="button" id="loadOlderMessages">${state.loadingOlderMessages ? "Loading..." : "Load older messages"}</button>`
    : "";
  els.messages.innerHTML = `${olderControl}${rows.map((row) => {
    const nextDate = dateKey(rowDate(row));
    const divider = nextDate === activeDate ? "" : `<span class="dateDivider">${escapeHtml(dateDividerLabel(row))}</span>`;
    activeDate = nextDate;
    const media = mediaInfo(row);
    const sender = row.color === "green"
      ? `<strong class="messageSender">${escapeHtml(row.empname || "Staff")}</strong>`
      : "";
    if (media) {
      return `${divider}
        <div class="bubble ${row.color === "green" ? "agent" : "customer"}">
          ${sender}
          ${renderMedia(row)}
          ${row.msg ? `<p>${escapeHtml(row.msg)}</p>` : ""}
          <span class="messageMeta">${escapeHtml(displayTime(row))} ${deliveryTick(row)}</span>
        </div>
      `;
    }
    return `${divider}
      <div class="bubble ${row.color === "green" ? "agent" : "customer"}">
        ${sender}
        <p>${escapeHtml(row.msg || "")}</p>
        <span class="messageMeta">${escapeHtml(displayTime(row))} ${deliveryTick(row)}</span>
      </div>
    `;
  }).join("")}`;
  if (options.scrollToBottom !== false) {
    els.messages.scrollTop = els.messages.scrollHeight;
  }
}

function updateSelected(row) {
  if (!row) return;
  const owner = ownerFor(row);
  const owned = Boolean(owner);
  const closed = isClosed(row);
  const ownedByCurrentUser = canCurrentUserAct(row);
  const type = conversationType(row);
  const currentSla = sla(row);
  els.activeMobile.textContent = displayContactName(row);
  els.activeSummary.textContent = row.mobile;
  els.ownerLine.innerHTML = `
    <span>Owner: ${escapeHtml(owner || "None")}</span>
    <span id="slaStatus" class="headerChip"></span>
    <span id="takenAt" class="headerChip"></span>
    ${closed ? '<span class="status closed">Closed</span>' : ""}
  `;
  els.slaStatus = document.querySelector("#slaStatus");
  els.takenAt = document.querySelector("#takenAt");
  els.slaStatus.innerHTML = `SLA <span class="sla ${currentSla.tone}">${escapeHtml(currentSla.label)}</span>`;
  els.takenAt.textContent = owned ? "Taken" : "Not taken";
  els.typeSelect.value = type;
  els.saveTypeBtn.className = "saveTypeBtn saved";
  els.saveTypeBtn.textContent = "✓";
  els.saveTypeBtn.title = "Conversation type saved";
  els.typeSelect.disabled = !ownedByCurrentUser;
  els.saveTypeBtn.disabled = !ownedByCurrentUser;
  els.actionButtons.forEach((button) => {
    button.disabled = !ownedByCurrentUser;
    button.title = ownedByCurrentUser ? "" : owned ? `Assigned to ${owner}` : "Take ownership first";
  });
  els.takeBtn.disabled = owned || closed;
  els.takeBtn.innerHTML = `${lockIcon(owned)}<span>${owned ? "Locked" : "Ownership"}</span>`;
  els.takeBtn.setAttribute("aria-label", owned ? "Ownership locked" : "Get ownership");
  els.closeBtn.disabled = !ownedByCurrentUser || closed;
  els.closeBtn.textContent = closed ? "Closed" : "Close";
  setComposerEnabled(
    ownedByCurrentUser && !closed,
    closed
      ? "Conversation is closed"
      : ownedByCurrentUser
        ? "Type a WhatsApp reply..."
        : owned
          ? `Assigned to ${owner}`
          : "Take ownership to reply..."
  );
  els.tagStrip.innerHTML = '<span class="emptyTag">-</span>';
  els.tagNote.textContent = "";
  els.contactName.textContent = "Loading linked patients...";
  els.contactContext.innerHTML = '<p class="emptyLink">Loading linked patients...</p>';
  setContextLoading();
  els.auditSummary.textContent = `Owner: ${owner || "not assigned yet"}. Type: ${type}. Status: ${row.workflow_status || "open"}. Delivery: ${row.delivery_status || "not available"}. Row #${row.id}.`;
  if (els.topStateChip) els.topStateChip.textContent = `owner=${owned ? 1 : 0} links=1`;
}

async function loadConversations() {
  const params = new URLSearchParams();
  const query = els.searchInput.value.trim();
  if (query) params.set("q", query);
  if (els.dateInput?.value) params.set("date", els.dateInput.value);
  const data = await fetchJson(`/api/conversations?${params.toString()}`);
  state.allRows = data.rows || [];
  renderConversations();
  const selected = state.allRows.find((row) => row.mobile === state.activeMobile);
  if (selected) updateSelected(selected);
}

async function loadMessages(mobile) {
  state.activeMobile = mobile;
  state.messageRows = [];
  state.oldestMessageId = null;
  state.hasOlderMessages = false;
  state.loadingOlderMessages = false;
  activateContextTab("contact");
  const selected = state.allRows.find((row) => row.mobile === mobile);
  updateSelected(selected);
  renderConversations();
  const [messagesData, lookupData] = await Promise.all([
    fetchMessagesPage(mobile),
    lookupContext(mobile, "contact"),
  ]);
  state.messageRows = messagesData.rows || [];
  state.oldestMessageId = messagesData.oldest_id || null;
  state.hasOlderMessages = Boolean(messagesData.has_more);
  renderMessages(state.messageRows);
  renderUnifiedLookup(lookupData, selected || { mobile });
}

async function loadContextTab(tab) {
  if (!state.activeMobile) return;
  if (tab === "audit") {
    els.auditSummary.textContent = "Loading audit...";
    const data = await lookupAuditActions(state.activeMobile);
    renderAuditActions(data.actions || []);
    return;
  }
  const labelByTab = { hc: "Loading bookings...", leads: "Loading leads...", tickets: "Loading tickets..." };
  if (tab === "hc") els.homeCollectionList.innerHTML = `<p class="emptyLink">${labelByTab.hc}</p>`;
  if (tab === "leads") els.leadList.innerHTML = `<p class="emptyLink">${labelByTab.leads}</p>`;
  if (tab === "tickets") els.ticketList.innerHTML = `<p class="emptyLink">${labelByTab.tickets}</p>`;
  const data = await lookupContext(state.activeMobile, tab);
  const selected = state.allRows.find((row) => row.mobile === state.activeMobile);
  renderUnifiedLookup(data, selected || { mobile: state.activeMobile });
}

async function loadOlderMessages() {
  if (!state.activeMobile || !state.oldestMessageId || !state.hasOlderMessages || state.loadingOlderMessages) return;
  state.loadingOlderMessages = true;
  const previousHeight = els.messages.scrollHeight;
  renderMessages(state.messageRows, { scrollToBottom: false });
  try {
    const data = await fetchMessagesPage(state.activeMobile, state.oldestMessageId);
    const olderRows = data.rows || [];
    state.messageRows = mergeMessageRows(olderRows, state.messageRows);
    state.oldestMessageId = data.oldest_id || state.oldestMessageId;
    state.hasOlderMessages = Boolean(data.has_more);
    state.loadingOlderMessages = false;
    renderMessages(state.messageRows, { scrollToBottom: false });
    els.messages.scrollTop = els.messages.scrollHeight - previousHeight;
  } catch (error) {
    showToast(error.message);
    state.loadingOlderMessages = false;
    renderMessages(state.messageRows, { scrollToBottom: false });
  }
}

function setLayoutClasses() {
  els.hubGrid.classList.toggle("listHidden", !state.showChatList);
  document.querySelector(".conversationList").style.display = state.showChatList ? "" : "none";
  els.toggleChatList.classList.toggle("active", state.showChatList);
  els.toggleChatList.textContent = state.showChatList ? "Hide Chat List" : "Show Chat List";
}

function renderDictionaryPanel(suggestions = [], auto = false) {
  els.assistPanel.className = `assistPanel dictionaryPanel${auto ? " autoSuggest" : ""}`;
  els.assistPanel.innerHTML = `
    <div class="dictionaryHeader">
      <strong>${auto ? "Spelling suggestion" : "Spelling dictionary"}</strong>
      <span>${auto ? "Enter to correct" : "Click a correction to apply"}</span>
    </div>
    <div class="dictionarySuggestions">
      ${suggestions.length ? suggestions.map(([wrong, correct], index) => `
        <button type="button" class="dictionarySuggestion${index === 0 ? " active" : ""}" data-dictionary-wrong="${escapeHtml(wrong)}" data-dictionary-correct="${escapeHtml(correct)}">
          <span>Correct</span><strong>${escapeHtml(wrong)}</strong><small>${escapeHtml(correct)}</small>
        </button>
      `).join("") : '<p class="emptyLink">Type a message to see spelling suggestions.</p>'}
    </div>
  `;
}

function applyDictionaryCorrection(wrong, correct) {
  const expression = new RegExp(`\\b${wrong}\\b`, "gi");
  els.replyText.value = els.replyText.value.replace(expression, correct);
  closeAssist();
  els.replyText.focus();
}

function openAssist(type) {
  document.querySelectorAll(".composerTool").forEach((button) => button.classList.remove("active"));
  if (type === "dict") {
    els.replyText.focus();
    return;
  }
  const source = type === "emoji" ? professionalEmojis : attachmentTypes;
  const panelClass = type === "emoji" ? "emojiPanel" : "attachmentPanel";
  const title = type === "emoji" ? "Professional emojis" : "Attach work file";
  els.assistPanel.className = `assistPanel ${panelClass}`;
  els.assistPanel.innerHTML = `
    <strong>${title}</strong>
    <div>
      ${source.map(([value, hint, meta]) => `
        <button type="button" data-assist="${type}" data-value="${escapeHtml(type === "attach" ? hint : value)}">
          <strong>${escapeHtml(value)}</strong>
          ${hint ? `<span>${escapeHtml(meta || hint)}</span>` : ""}
        </button>
      `).join("")}
    </div>
  `;
}

function closeAssist() {
  els.assistPanel.className = "assistPanel hidden";
}

async function requestSpellSuggestions(text, auto = true) {
  const query = text.trim();
  if (!query) {
    renderDictionaryPanel([], auto);
    return;
  }
  try {
    const data = await fetchJson("/api/spellcheck", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: query }),
    });
    renderDictionaryPanel((data.suggestions || []).map((item) => [item.word, item.suggestion]), auto);
  } catch (error) {
    if (!auto) showToast(error.message);
  }
}

async function requestAutocomplete(text) {
  const match = text.match(/([A-Za-z]{3,})$/);
  if (!match) return;
  const prefix = match[1];
  try {
    const data = await fetchJson("/api/autocomplete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prefix }),
    });
    const suggestion = (data.suggestions || []).find((word) => word.startsWith(prefix.toLowerCase()));
    if (!suggestion || els.replyText.value !== text) return;
    const completion = suggestion.slice(prefix.length);
    if (!completion) return;
    state.autocompleteSuggestion = suggestion;
    state.autocompleteSuffix = completion;
    renderAutocompleteGhost();
  } catch {
    // Autocomplete is optional; normal typing and sending must stay uninterrupted.
  }
}

async function requestSpellHighlight(text) {
  const match = text.match(/([A-Za-z]{3,})$/);
  if (!match) return;
  const word = match[1].toLowerCase();
  try {
    const data = await fetchJson("/api/spellcheck", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: word }),
    });
    if (els.replyText.value !== text) return;
    state.misspelledWord = (data.suggestions || []).some((item) => item.word === word) ? word : "";
    renderAutocompleteGhost();
  } catch {
    // Spell highlight is non-blocking.
  }
}

async function correctCurrentWord() {
  const text = els.replyText.value;
  const match = text.match(/([A-Za-z]{3,})$/);
  if (!match) return;
  const word = match[1].toLowerCase();
  try {
    const data = await fetchJson("/api/spellcheck", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: word }),
    });
    const correction = (data.suggestions || []).find((item) => item.word === word)?.suggestion;
    if (!correction || els.replyText.value !== text) return;
    els.replyText.value = `${text.slice(0, -match[1].length)}${correction}`;
    els.replyText.setSelectionRange(els.replyText.value.length, els.replyText.value.length);
    state.misspelledWord = "";
    renderAutocompleteGhost();
  } catch {
    // Keep the composer usable if spelling lookup is unavailable.
  }
}

function renderAttachmentDraft() {
  document.querySelector(".attachmentDraft")?.remove();
  if (!state.selectedFile) return;
  const draft = document.createElement("div");
  draft.className = "attachmentDraft";
  draft.innerHTML = `
    <span>${escapeHtml(state.selectedFile.name)}</span>
    <button type="button" id="clearAttachment">Remove</button>
  `;
  els.replyForm.insertAdjacentElement("afterend", draft);
  document.querySelector("#clearAttachment").addEventListener("click", () => {
    state.selectedFile = null;
    if (els.imageFileInput) els.imageFileInput.value = "";
    if (els.documentFileInput) els.documentFileInput.value = "";
    renderAttachmentDraft();
  });
}

async function uploadSelectedFile() {
  if (!state.selectedFile) return null;
  const formData = new FormData();
  formData.append("file", state.selectedFile);
  const data = await fetchJson("/api/uploads", {
    method: "POST",
    body: formData,
  });
  return data.media;
}

function drawerFieldsHtml(fields) {
  return fields.map((field) => `
    <label class="modalField">
      ${escapeHtml(field)}
      ${field === "New Owner"
        ? '<input class="ownerSearch" list="ownerOptions" data-field="New Owner" autocomplete="off" placeholder="Search owner name" /><datalist id="ownerOptions"></datalist><input type="hidden" data-field="New Owner ID" />'
        : field.includes("Information") || field.includes("Note") || field === "Message"
          ? `<textarea data-field="${escapeHtml(field)}"></textarea>`
          : `<input data-field="${escapeHtml(field)}" />`}
    </label>
  `).join("");
}

function fieldValue(label) {
  return els.drawerFields.querySelector(`[data-field="${CSS.escape(label)}"]`)?.value?.trim() || "";
}

function cleanConversationMobile(value) {
  const digits = String(value || "").replace(/\D/g, "");
  if (digits.length === 10) return `91${digits}`;
  if (digits.length === 11 && digits.startsWith("0")) return `91${digits.slice(-10)}`;
  if (digits.length > 10 && digits.startsWith("91")) return `91${digits.slice(-10)}`;
  if (digits.length > 10) return `91${digits.slice(-10)}`;
  return digits;
}

function drawerPayload() {
  const payload = {};
  els.drawerFields.querySelectorAll("[data-field]").forEach((field) => {
    if (field.type === "hidden") return;
    payload[field.dataset.field] = field.value?.trim?.() || "";
  });
  const activeCreate = els.drawerQuestion.querySelector("[data-create-type].active");
  const activeLink = els.drawerQuestion.querySelector("[data-link-type].active");
  if (activeCreate) payload.create_type = activeCreate.dataset.createType;
  if (activeLink) payload.link_type = activeLink.dataset.linkType;
  return payload;
}

async function loadUsers(query = "") {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  const data = await fetchJson(`/api/operators?${params.toString()}`);
  state.operatorOptions = data.users || [];
  const options = document.querySelector("#ownerOptions");
  if (options) {
    options.innerHTML = state.operatorOptions.map((user) => (
      `<option value="${escapeHtml(user.display_name)}" data-id="${user.id}">${escapeHtml(user.username)}</option>`
    )).join("");
  }
}

function selectedOwnerId() {
  const name = fieldValue("New Owner").toLowerCase();
  const match = state.operatorOptions.find((user) => (
    user.display_name.toLowerCase() === name || user.username.toLowerCase() === name
  ));
  return match?.id || "";
}

function canCurrentUserAct(row) {
  return Boolean(row && ownerFor(row) === currentUser && !isClosed(row));
}

function openDrawer(title) {
  const selected = state.allRows.find((row) => row.mobile === state.activeMobile);
  if (title !== "New Conversation" && !canCurrentUserAct(selected)) {
    showToast(ownerFor(selected) ? `Assigned to ${ownerFor(selected)}` : "Take ownership first");
    return;
  }
  state.drawerMode = title;
  els.drawerTitle.textContent = title;
  let question = "";
  let fields = drawerForms[title] || [];
  if (title === "Create") {
    question = `
      <section class="createQuestion">
        <h3>What do you want to create?</h3>
        <div>${Object.keys(createForms).map((type, index) => `<button class="${index === 0 ? "active" : ""}" type="button" data-create-type="${type}">${type}</button>`).join("")}</div>
      </section>
    `;
    fields = createForms.Ticket;
  }
  if (title === "Link") {
    question = `
      <section class="createQuestion">
        <h3>What do you want to link?</h3>
        <div>${Object.keys(linkForms).map((type, index) => `<button class="${index === 0 ? "active" : ""}" type="button" data-link-type="${type}">${type}</button>`).join("")}</div>
      </section>
    `;
    fields = linkForms.Patient;
  }
  if (title === "New Conversation") {
    question = `
      <section class="closureQuestion">
        <h3>Start WhatsApp conversation</h3>
        <p>Ownership will be locked to you before sending the first message.</p>
      </section>
    `;
  }
  if (title === "Close Conversation") {
    const savedType = conversationType(selected);
    const selectedType = closureTypes.includes(savedType) ? savedType : "";
    question = `
      <section class="closureQuestion">
        <h3>Mark type before closure</h3>
        <p>CCE must classify the conversation so tags like Invoice, Report, and Lead have a clear source.</p>
        <label class="modalField">Conversation Type
          <select id="closureType"><option value="">Select type</option>${closureTypes.map((type) => `<option${type === selectedType ? " selected" : ""}>${type}</option>`).join("")}</select>
        </label>
      </section>
    `;
    fields = ["Closure Note"];
  }
  els.drawerQuestion.innerHTML = question;
  els.drawerFields.innerHTML = drawerFieldsHtml(fields);
  els.drawerSave.textContent = title === "Close Conversation" ? "Close Conversation" : title === "New Conversation" ? "Start Conversation" : "Save";
  els.drawerBackdrop.classList.remove("hidden");
  if (title === "Reassign") loadUsers().catch((error) => showToast(error.message));
}

els.queueNav.addEventListener("click", (event) => {
  const button = event.target.closest("[data-queue]");
  if (!button) return;
  state.activeQueue = button.dataset.queue;
  els.queueNav.querySelectorAll("[data-queue]").forEach((item) => item.classList.toggle("active", item === button));
  renderConversations();
});

els.conversationList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-mobile]");
  if (!button) return;
  loadMessages(button.dataset.mobile).catch((error) => showToast(error.message));
});

els.messages.addEventListener("click", (event) => {
  if (!event.target.closest("#loadOlderMessages")) return;
  loadOlderMessages();
});

els.searchInput.addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => loadConversations().catch((error) => showToast(error.message)), 250);
});

els.dateInput?.addEventListener("change", () => loadConversations().catch((error) => showToast(error.message)));

els.toggleChatList.addEventListener("click", () => {
  state.showChatList = !state.showChatList;
  setLayoutClasses();
});

els.takeBtn.addEventListener("click", async () => {
  if (!state.activeMobile) return;
  try {
    const data = await fetchJson(`/api/conversations/${encodeURIComponent(state.activeMobile)}/ownership`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const selected = applyWorkflowState(data.state);
    invalidateConversationCache(state.activeMobile);
    updateSelected(selected);
    renderConversations();
    showToast(`Ownership locked to ${currentUser}`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
});

els.closeBtn.addEventListener("click", () => {
  if (state.activeMobile) openDrawer("Close Conversation");
});

els.typeSelect.addEventListener("change", () => {
  const selected = state.allRows.find((row) => row.mobile === state.activeMobile);
  if (!canCurrentUserAct(selected)) return;
  els.saveTypeBtn.className = "saveTypeBtn update";
  els.saveTypeBtn.textContent = "Update";
  els.saveTypeBtn.title = "Update conversation type";
});

els.saveTypeBtn.addEventListener("click", async () => {
  if (!state.activeMobile) return;
  const current = state.allRows.find((row) => row.mobile === state.activeMobile);
  if (!canCurrentUserAct(current)) {
    showToast(ownerFor(current) ? `Assigned to ${ownerFor(current)}` : "Take ownership first", "error");
    return;
  }
  try {
    const data = await fetchJson(`/api/conversations/${encodeURIComponent(state.activeMobile)}/type`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_type: els.typeSelect.value }),
    });
    const selected = applyWorkflowState(data.state);
    invalidateConversationCache(state.activeMobile);
    updateSelected(selected);
    renderConversations();
    showToast(`Conversation type saved as ${els.typeSelect.value}`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
});

els.replyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const msg = els.replyText.value.trim();
  const selected = state.allRows.find((row) => row.mobile === state.activeMobile);
  const canReply = canCurrentUserAct(selected);
  if (!canReply) {
    showToast("Take ownership before replying", "error");
    setComposerEnabled(false, "Take ownership to reply...");
    return;
  }
  if (!state.activeMobile || (!msg && !state.selectedFile)) return;
  els.sendBtn.disabled = true;
  try {
    const media = await uploadSelectedFile();
    await fetchJson(`/api/conversations/${encodeURIComponent(state.activeMobile)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ msg, empname: currentUser, media }),
    });
    invalidateConversationCache(state.activeMobile);
    clearComposerInput();
    state.selectedFile = null;
    if (els.imageFileInput) els.imageFileInput.value = "";
    if (els.documentFileInput) els.documentFileInput.value = "";
    renderAttachmentDraft();
    closeAssist();
    await loadConversations();
    await loadMessages(state.activeMobile);
    clearComposerInput();
    showToast(media ? "Attachment sent through WhatsApp Official API" : "Message sent through WhatsApp Official API", "success");
  } catch (error) {
    if (/unassigned|current owner/i.test(error.message)) {
      await loadConversations();
      const refreshed = state.allRows.find((row) => row.mobile === state.activeMobile);
      if (refreshed) updateSelected(refreshed);
    }
    showToast(error.message, "error");
  } finally {
    const current = state.allRows.find((row) => row.mobile === state.activeMobile);
    els.sendBtn.disabled = !canCurrentUserAct(current);
  }
});

els.replyText.addEventListener("input", () => {
  const query = els.replyText.value.trim();
  clearTimeout(state.spellTimer);
  state.autocompleteSuggestion = "";
  state.autocompleteSuffix = "";
  state.misspelledWord = "";
  renderAutocompleteGhost();
  if (query.length >= 3) {
    state.spellTimer = setTimeout(() => {
      requestAutocomplete(els.replyText.value);
      requestSpellHighlight(els.replyText.value);
    }, 220);
  }
});

els.replyText.addEventListener("keydown", (event) => {
  if (event.key !== "Tab") return;
  event.preventDefault();
  if (state.autocompleteSuggestion) {
    els.replyText.value += state.autocompleteSuffix;
    state.autocompleteSuggestion = "";
    state.autocompleteSuffix = "";
    renderAutocompleteGhost();
  } else {
    correctCurrentWord();
  }
});

document.querySelector("#dictBtn").addEventListener("click", () => openAssist("dict"));
els.attachImageBtn?.addEventListener("click", () => {
  closeAssist();
  els.imageFileInput?.click();
});

els.attachDocumentBtn?.addEventListener("click", () => {
  closeAssist();
  els.documentFileInput?.click();
});

function selectAttachment(file, input) {
  if (!file) return;
  const allowed = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
  if (!allowed.includes(file.type)) {
    showToast("Only JPG, PNG, WEBP, and PDF files are allowed", "error");
    input.value = "";
    return;
  }
  state.selectedFile = file;
  renderAttachmentDraft();
}

els.imageFileInput?.addEventListener("change", () => {
  selectAttachment(els.imageFileInput.files?.[0], els.imageFileInput);
});

els.documentFileInput?.addEventListener("change", () => {
  selectAttachment(els.documentFileInput.files?.[0], els.documentFileInput);
});

els.assistPanel.addEventListener("click", (event) => {
  const dictionaryButton = event.target.closest("[data-dictionary-correct]");
  if (dictionaryButton) {
    applyDictionaryCorrection(dictionaryButton.dataset.dictionaryWrong, dictionaryButton.dataset.dictionaryCorrect);
    return;
  }
  const button = event.target.closest("[data-value]");
  if (!button) return;
  const value = button.dataset.value;
  els.replyText.value = `${els.replyText.value}${els.replyText.value ? " " : ""}${value} `;
  closeAssist();
  els.replyText.focus();
});

document.querySelectorAll("[data-drawer]").forEach((button) => {
  button.addEventListener("click", () => openDrawer(button.dataset.drawer));
});

els.contextTabs.forEach((button) => {
  button.addEventListener("click", () => {
    const tab = button.dataset.contextTab;
    els.contextTabs.forEach((item) => item.classList.toggle("active", item === button));
    els.contextPanels.forEach((panel) => panel.classList.toggle("active", panel.dataset.contextPanel === tab));
    loadContextTab(tab).catch((error) => showToast(error.message));
  });
});

els.drawerQuestion.addEventListener("click", (event) => {
  const createButton = event.target.closest("[data-create-type]");
  const linkButton = event.target.closest("[data-link-type]");
  if (createButton) {
    els.drawerQuestion.querySelectorAll("[data-create-type]").forEach((button) => button.classList.toggle("active", button === createButton));
    els.drawerFields.innerHTML = drawerFieldsHtml(createForms[createButton.dataset.createType]);
  }
  if (linkButton) {
    els.drawerQuestion.querySelectorAll("[data-link-type]").forEach((button) => button.classList.toggle("active", button === linkButton));
    els.drawerFields.innerHTML = drawerFieldsHtml(linkForms[linkButton.dataset.linkType]);
  }
});

els.drawerFields.addEventListener("input", (event) => {
  const input = event.target.closest(".ownerSearch");
  if (!input) return;
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => loadUsers(input.value).catch((error) => showToast(error.message)), 200);
});

[els.drawerClose, els.drawerCancel].forEach((button) => {
  button.addEventListener("click", () => els.drawerBackdrop.classList.add("hidden"));
});

els.drawerSave.addEventListener("click", async () => {
  try {
  if (state.drawerMode === "New Conversation") {
    const mobile = cleanConversationMobile(fieldValue("Mobile Number"));
    const message = fieldValue("Message");
    if (mobile.length !== 12 || !mobile.startsWith("91")) {
      showToast("Enter a valid mobile number", "error");
      return;
    }
    if (!message) {
      showToast("Enter message text", "error");
      return;
    }
    els.drawerSave.disabled = true;
    await fetchJson(`/api/conversations/${encodeURIComponent(mobile)}/ownership`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reopen: true }),
    });
    await fetchJson(`/api/conversations/${encodeURIComponent(mobile)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ msg: message, empname: currentUser }),
    });
    invalidateConversationCache(mobile);
    state.activeMobile = mobile;
    await loadConversations();
    await loadMessages(mobile);
    showToast("New conversation started", "success");
  } else {
  if (!state.activeMobile) return;
  if (state.drawerMode === "Close Conversation") {
    const closureType = document.querySelector("#closureType")?.value || els.typeSelect.value;
    if (!closureType) {
      showToast("Select conversation type before closure", "error");
      return;
    }
    const data = await fetchJson(`/api/conversations/${encodeURIComponent(state.activeMobile)}/close`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_type: closureType, note: fieldValue("Closure Note") }),
    });
    const selected = applyWorkflowState(data.state);
    invalidateConversationCache(state.activeMobile);
    updateSelected(selected);
    renderConversations();
    showToast(`Conversation closed as ${closureType}`, "success");
  } else if (state.drawerMode === "Reassign") {
    const ownerUserId = selectedOwnerId();
    if (!ownerUserId) {
      showToast("Select a valid owner", "error");
      return;
    }
    const data = await fetchJson(`/api/conversations/${encodeURIComponent(state.activeMobile)}/reassign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner_id: ownerUserId, reason: fieldValue("Reason") }),
    });
    const selected = applyWorkflowState(data.state);
    invalidateConversationCache(state.activeMobile);
    updateSelected(selected);
    renderConversations();
    await loadMessages(state.activeMobile);
    showToast(`Conversation reassigned to ${ownerFor(selected)}`, "success");
  } else {
    await fetchJson(`/api/conversations/${encodeURIComponent(state.activeMobile)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action_type: state.drawerMode.toLowerCase().replaceAll(" ", "_"),
        payload: drawerPayload(),
      }),
    });
    invalidateConversationCache(state.activeMobile, {
      lookup: ["Link", "Create"].includes(state.drawerMode),
    });
    await loadMessages(state.activeMobile);
    showToast(`${state.drawerMode} saved to audit trail`, "success");
  }
  }
  els.drawerBackdrop.classList.add("hidden");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    els.drawerSave.disabled = false;
  }
});

if (els.dateInput) els.dateInput.valueAsDate = new Date();
setComposerEnabled(false, "Take ownership to reply...");
setLayoutClasses();
loadConversations().catch((error) => showToast(error.message));
connectLiveUpdates();
