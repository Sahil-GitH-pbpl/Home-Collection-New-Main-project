function statusBadge(status) {
  const map = {
    0: { text: 'Pending', cls: 'secondary' },
    1: { text: 'Assigned', cls: 'warning' },
    2: { text: 'Started', cls: 'started' },
    3: { text: 'Completed', cls: 'success' },
    4: { text: 'Cancelled', cls: 'danger' },
    5: { text: 'P Completed', cls: 'info' }
  };
  const legacyMap = { Pending: 0, Assigned: 1, Started: 2, Completed: 3, Cancelled: 4, PCompleted: 5 };
  const code = Number.isFinite(Number(status)) ? Number(status) : legacyMap[status];
  const meta = map[code] || map[0];
  if (code === 2) return `<span class="badge text-bg-${meta.cls}" style="background:#7f2eb4 !important;">${meta.text}</span>`;
  return `<span class="badge text-bg-${meta.cls}">${meta.text}</span>`;
}

function assignedStatusButton(row, bookingId) {
  return `
    <button type="button" class="badge text-bg-warning border-0 dash-status-unassign"
      data-booking-id="${Number(bookingId || 0)}"
      data-appointment-id="${Number(row.appointment_id || 0)}">
      Assigned
    </button>
  `;
}

function statusText(status) {
  const labels = { 0: 'Pending', 1: 'Assigned', 2: 'Started', 3: 'Completed', 4: 'Cancelled', 5: 'P Completed' };
  const legacyMap = { Pending: 0, Assigned: 1, Started: 2, Completed: 3, Cancelled: 4, PCompleted: 5 };
  const code = Number.isFinite(Number(status)) ? Number(status) : legacyMap[status];
  return labels[code] || 'Pending';
}

function formatVisitDate(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const day = d.toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'UTC' });
  const date = d.toLocaleDateString('en-GB', { day: '2-digit', month: 'long', timeZone: 'UTC' });
  return `${day} ${date}`;
}

function formatSlotCompact(slotText) {
  const s = String(slotText || '').trim();
  const m = s.match(/(\d{1,2}):(\d{2})\s*([AP]M)\s*to\s*(\d{1,2}):(\d{2})\s*([AP]M)/i);
  if (!m) return s || '-';
  const startHour = String(Number(m[1]));
  const startMin = m[2];
  const endHour = String(Number(m[4]));
  const endMin = m[5];
  const endMer = m[6].toUpperCase();
  const startLabel = startMin === '00' ? startHour : `${startHour}:${startMin}`;
  const endLabel = endMin === '00' ? `${endHour} ${endMer}` : `${endHour}:${endMin} ${endMer}`;
  return `${startLabel} to ${endLabel}`;
}

function slotSortKey(slotText) {
  const s = String(slotText || '').trim();
  const m = s.match(/(\d{1,2}):(\d{2})\s*([AP]M)/i);
  if (!m) return 9999;
  let h = Number(m[1] || 0);
  const mm = Number(m[2] || 0);
  const mer = String(m[3] || '').toUpperCase();
  if (mer === 'PM' && h !== 12) h += 12;
  if (mer === 'AM' && h === 12) h = 0;
  return h * 60 + mm;
}

function routeSortKey(routeText) {
  const txt = String(routeText || '').trim();
  const m = txt.match(/(\d+)/);
  if (m) return Number(m[1]);
  return Number.MAX_SAFE_INTEGER;
}

function formatDateForReschedule(isoDate) {
  const s = String(isoDate || '').trim();
  if (!s) return '-';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const day = d.toLocaleDateString('en-GB', { weekday: 'long', timeZone: 'UTC' });
  const dd = d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
  return `${day}, ${dd}`;
}

function generateRescheduleSlots() {
  const slots = [];
  for (let h = 0; h < 24; h += 1) {
    [0, 30].forEach((m) => {
      const start = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
      const endMinRaw = (h * 60) + m + 30;
      const eh = Math.floor((endMinRaw % 1440) / 60);
      const em = endMinRaw % 60;
      const end = `${String(eh).padStart(2, '0')}:${String(em).padStart(2, '0')}`;
      function to12(t) {
        const [hhRaw, mmRaw] = t.split(':');
        let hh = Number(hhRaw || 0);
        const ap = hh >= 12 ? 'PM' : 'AM';
        hh = hh % 12;
        if (hh === 0) hh = 12;
        return `${String(hh).padStart(2, '0')}:${mmRaw} ${ap}`;
      }
      slots.push(`${to12(start)} to ${to12(end)}`);
    });
  }
  return slots;
}

function tbsLabel(code) {
  const normalized = String(code ?? '').trim();
  if (!normalized) return '-';
  if (
    normalized === 'Test confirmed and booked' ||
    normalized === 'Prescription attached but test not booked' ||
    normalized === 'No test information: ask to patient for tests' ||
    normalized === 'Incompleted test, phlebo verification pending to confirm and book'
  ) return normalized;
  const c = Number(code || 0);
  if (c === 1) return 'Test confirmed and booked';
  if (c === 2) return 'Prescription attached but test not booked';
  if (c === 3) return 'No test information: ask to patient for tests';
  if (c === 4) return 'Incompleted test, phlebo verification pending to confirm and book';
  return '-';
}

function cceTbsShortLabel(value) {
  const normalized = String(value ?? '').trim();
  if (!normalized) return '';
  const lower = normalized.toLowerCase();
  const c = Number(normalized || 0);
  if (lower === 'test confirmed and booked' || c === 1) return 'Confirmed';
  if (lower === 'prescription attached but test not booked' || c === 2) return 'pres.. Only';
  if (lower === 'no test information: ask to patient for tests' || c === 3) return 'No Test';
  if (
    lower === 'incompleted test, phlebo verification pending to confirm and book' ||
    (lower.includes('phlebo verification') && lower.includes('pending')) ||
    c === 4
  ) return 'Verify Pending';
  return normalized;
}

function renderCceTbsCell(rawValue) {
  const values = String(rawValue || '')
    .split(',')
    .map((x) => String(x || '').trim())
    .filter(Boolean);
  const seen = new Set();
  const labels = values
    .map(cceTbsShortLabel)
    .filter((label) => {
      if (!label || seen.has(label)) return false;
      seen.add(label);
      return true;
    });
  return labels.length ? labels.map(esc).join(', ') : '-';
}

function esc(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtMoney(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '0';
  return n.toFixed(2).replace(/\.00$/, '');
}

function renderBookingReviewModalContent(booking) {
  const $body = $('#booking-modal-body');
  if (!$body.length) return;
  const patients = Array.isArray(booking?.patients) ? booking.patients : [];
  const patientMobiles = patients
    .map((p) => String(p?.contact_mobile || '').trim())
    .filter(Boolean)
    .join(', ');
  const total = Number(booking?.total_amount || 0);
  const computedSub = patients.reduce((acc, p) => acc + ((Array.isArray(p.tests) ? p.tests : []).reduce((a, t) => a + Number(t.mrp || 0), 0)), 0);
  const computedDis = patients.reduce((acc, p) => acc + ((Array.isArray(p.tests) ? p.tests : []).reduce((a, t) => a + Number(t.discount || 0), 0)), 0);
  const sub = Number(booking?.F_Apt_Am ?? computedSub);
  const dis = Number(booking?.F_dis ?? computedDis);
  const rows = patients.map((p) => {
    const tests = Array.isArray(p.tests) ? p.tests : [];
    const fallbackModes = String(p.selected_charge_modes || '').split(',').map((x) => String(x || '').trim().toUpperCase()).filter(Boolean);
    const panelGroups = {};
    tests.forEach((t) => {
      const panelName = String(t.panel_company || '').trim() || (Array.isArray(p.panel_companies) && p.panel_companies.length === 1 ? String(p.panel_companies[0] || '').trim() : '') || String(p.panel_company || '').trim() || '-';
      let mode = String(t.selected_charge_mode || '').trim().toUpperCase();
      if (!mode && fallbackModes.length === 1) mode = fallbackModes[0];
      const key = `${panelName}|${mode}`;
      if (!panelGroups[key]) panelGroups[key] = { panelName, mode, tests: [] };
      panelGroups[key].tests.push(t);
    });
    const sections = Object.values(panelGroups);
    if (!sections.length && tests.length) {
      sections.push({
        panelName: (Array.isArray(p.panel_companies) && p.panel_companies.length ? p.panel_companies.join(', ') : (p.panel_company || '-')),
        mode: fallbackModes.length === 1 ? fallbackModes[0] : '',
        tests,
      });
    }
    const sectionHtml = sections.map((sec) => {
      const chargeModeLabel = sec.mode === 'P' ? 'P (Paying)' : sec.mode === 'C' ? 'C (Credit)' : sec.mode === 'F' ? 'F (FOC)' : '';
      const testsRows = (sec.tests || []).map((t) => `
        <tr>
          <td><strong>${esc([t.booked_code, t.test_name].filter(Boolean).join(' - ') || '-')}</strong></td>
          <td class="text-end">${esc(fmtMoney(t.mrp))}</td>
          <td class="text-end">${esc(fmtMoney(t.discount))}</td>
          <td class="text-end"><strong>${esc(fmtMoney(t.final_charge))}</strong></td>
          <td class="text-center">-</td>
        </tr>
      `).join('');
      const sectionTotal = (sec.tests || []).reduce((acc, t) => acc + Number(t.final_charge || 0), 0);
      return `
      <div class="border rounded p-2 mb-2 bg-white">
        <div class="d-flex flex-wrap gap-3 align-items-center mb-2 hc-review-top-strip">
          <span class="hc-review-panel-chip">${esc(sec.panelName || '-')}</span>
          ${chargeModeLabel ? `<span class="hc-review-panel-chip">${esc(chargeModeLabel)}</span>` : ''}
          <span><strong>Test_Bkg_Status:</strong> <span style="color:#0b6b2d;font-weight:700;">${esc(tbsLabel(p.test_booking_status))}</span></span>
        </div>
        <div class="mb-1"><strong>Tests (${(sec.tests || []).length}):</strong></div>
        <div class="table-responsive hc-review-tests-table-wrap">
          <table class="table table-sm mb-0 hc-review-tests-table">
            <thead>
              <tr>
                <th>Test Name</th>
                <th class="text-end" style="width:120px;">Standard Charge</th>
                <th class="text-end" style="width:100px;">Discount</th>
                <th class="text-end" style="width:120px;">Final Charge</th>
                <th class="text-center" style="width:80px;">TAT</th>
              </tr>
            </thead>
            <tbody>${testsRows || '<tr><td colspan="5" class="text-muted">No tests.</td></tr>'}</tbody>
          </table>
        </div>
        <div class="d-flex flex-wrap align-items-center justify-content-between mt-2 hc-review-bottom-strip">
          <div class="ms-auto text-end"><strong>Charges: ${esc(fmtMoney(sectionTotal))}</strong></div>
        </div>
      </div>`;
    }).join('');
    const patientTotal = tests.reduce((acc, t) => acc + Number(t.final_charge || 0), 0);
    return `
    <div class="card mb-2">
      <div class="card-body">
        <div class="d-flex flex-wrap justify-content-between align-items-start gap-2 hc-patient-name-red mb-2">
          <div>
            <h6 class="mb-0"><span>Patient Name:</span> ${esc(p.full_name || '-')}</h6>
            <div><strong>Patient Mobile:</strong> ${esc(p.contact_mobile || '-')}</div>
          </div>
          <h6 class="mb-0 text-end"><span>Referred By:</span> ${esc(p.ref_by || '-')}</h6>
        </div>
        ${sectionHtml || '<div class="text-muted">No tests.</div>'}
        <div class="d-flex flex-wrap align-items-center justify-content-between mt-2 hc-review-bottom-strip hc-review-bottom-strip-patient">
          <div class="ms-auto text-end"><strong>Total Amount: ${esc(fmtMoney(patientTotal))}</strong></div>
        </div>
      </div>
    </div>`;
  }).join('');

  $body.html(`
    <div class="hc-review-grid mb-2">
      <div class="hc-review-meta">
        <div><strong>Mobile No:</strong> ${esc(patientMobiles || '-')} | <strong>Patients:</strong> ${patients.length}</div>
        <div><strong>Google Location:</strong> <span class="hc-review-linkish">${esc(booking.google_location || '-')}</span></div>
        <div><strong>Internal Referred By:</strong> ${esc(booking.intrnl_rfrncd_by || '-')}</div>
        <div><strong>Lead ID:</strong> ${esc(booking.lead_id || '-')}</div>
      </div>
      <div class="hc-review-address-card">
        <div class="hc-review-address-title">Address:-</div>
        <div class="hc-review-address-grid">
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">House/Flat No:</span> <span class="hc-review-addr-v">${esc(booking.house_flat_no || '-')}</span></div>
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">Floor:</span> <span class="hc-review-addr-v">${esc(booking.floor || '-')}</span></div>
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">Block/Tower No:</span> <span class="hc-review-addr-v">${esc(booking.block_tower_no || '-')}</span></div>
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">Street/Sector:</span> <span class="hc-review-addr-v">${esc(booking.street_line || '-')}</span></div>
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">Landmark:</span> <span class="hc-review-addr-v">${esc(booking.landmark || '-')}</span></div>
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">City:</span> <span class="hc-review-addr-v">${esc(booking.city || '-')}</span></div>
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">Colony:</span> <span class="hc-review-addr-v">${esc(booking.colony_name || '-')}</span></div>
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">Pincode:</span> <span class="hc-review-addr-v">${esc(booking.pincode || '-')}</span></div>
          <div class="hc-review-addr-item"><span class="hc-review-addr-k">Route:</span> <span class="hc-review-addr-v">${esc(booking.route_no || '-')}</span></div>
        </div>
      </div>
    </div>
    ${rows || '<div class="text-muted">No patient details.</div>'}
    <div id="review-net-amount">
      <span class="hc-review-total-line"><strong>Final Sub Total:</strong> ${esc(fmtMoney(sub))}</span>
      <span class="hc-review-total-line"><strong>Final Discount:</strong> ${esc(fmtMoney(dis))}</span>
      <span class="hc-review-net-chip"><strong>Final Amount:</strong> ${esc(fmtMoney(total))}</span>
    </div>
  `);
}

function renderDocPreviewItem(url, labelPrefix, uploadedBy) {
  const src = String(url || '').trim();
  if (!src) return '';
  const uploader = esc(String(uploadedBy || '').trim());
  const lower = src.toLowerCase();
  if (lower.endsWith('.pdf')) {
    return `<button type="button" class="dash-doc-thumb js-doc-preview" data-kind="pdf" data-src="${src}" data-uploaded-by="${uploader}" aria-label="Open PDF" title="Open PDF">PDF</button>`;
  }
  return `<img src="${src}" class="dash-doc-thumb js-doc-preview" data-kind="image" data-src="${src}" data-uploaded-by="${uploader}" alt="doc">`;
}

function setDashboardPageLoading(isLoading) {
  if (isLoading) $('#hd-page-loader').removeClass('d-none');
  else $('#hd-page-loader').addClass('d-none');
}

function markDashboardReady() { $('#h-dashboard-page').addClass('is-ready'); }

function todayIsoLocal() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function openAssignBookingSmoothly(e) {
  if (e) e.preventDefault();
  const targetHref = $('#btn-go-assign-booking').attr('href') || '/hhome-collection/assign-booking';
  $('.hd-loader-text').text('Opening Asgn Booking...');
  setDashboardPageLoading(true);
  $('#h-dashboard-page').addClass('is-leaving');
  setTimeout(function () { window.location.href = targetHref; }, 170);
}

function resetDashboardPageState() {
  $('#h-dashboard-page').removeClass('is-leaving').addClass('is-ready');
  $('.hd-loader-text').text('Loading All Booking...');
  setDashboardPageLoading(false);
}

function loadDashboard() {
  const dr = getDashboardDateRange();
  const params = {
    date_from: dr.from,
    date_to: dr.to,
    status: $('#f-status').val(),
    cce_tbs: $('#f-cce-tbs').val(),
    search: $('#f-search').val(),
    page: dashboardPagingState.page,
    page_size: dashboardPagingState.pageSize,
    sort_key: dashboardSortState.key,
    sort_dir: dashboardSortState.dir
  };

  return $.get('/hhome-collection/dashboard-data', params, function (res) {
    const rows = res.rows || [];
    dashboardRowsState = rows;
    dashboardPagingState.page = Number(res.page || 1);
    dashboardPagingState.totalPages = Number(res.total_pages || 0);
    dashboardPagingState.totalCount = Number(res.total_count || 0);
    if (String(res.page_size || '').toLowerCase() === 'all') {
      dashboardPagingState.pageSize = 'all';
    } else {
      dashboardPagingState.pageSize = String(res.page_size || dashboardPagingState.pageSize || '50');
    }
    $('#dashboard-page-size').val(String(dashboardPagingState.pageSize));
    renderDashboardRows(rows);
    renderDashboardMiniStats(res.status_counts || {});
    renderDashboardPagination();
  });
}

let dashboardRowsState = [];
let dashboardSortState = { key: '', dir: 'asc' };
let dashboardDateSelection = [];
let dashboardPagingState = { page: 1, pageSize: '50', totalPages: 0, totalCount: 0 };

function toIsoLocalDate(d) {
  const dt = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(dt.getTime())) return '';
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, '0');
  const day = String(dt.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function getDashboardDateRange() {
  if (!Array.isArray(dashboardDateSelection) || dashboardDateSelection.length === 0) {
    return { from: '', to: '' };
  }
  const from = toIsoLocalDate(dashboardDateSelection[0]);
  const to = toIsoLocalDate(dashboardDateSelection[1] || dashboardDateSelection[0]);
  return { from, to };
}

function renderDashboardMiniStats(stats) {
  const safe = stats && typeof stats === 'object' ? stats : {};
  $('#dash-chip-total').text(Number(safe.total || 0));
  $('#dash-chip-pending').text(Number(safe.pending || 0));
  $('#dash-chip-started').text(Number(safe.started || 0));
  $('#dash-chip-cancelled').text(Number(safe.cancelled || 0));
}

function renderDashboardRows(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const html = list.map((r, idx) => {
    const statusCode = Number(r.booking_status || 0);
    const bookingId = Number(r.booking_id || r.id || 0);
    const cceTbsHtml = renderCceTbsCell(r.cce_tbs_values);
    const patientText = String(r.patient_names || r.caller_name || '-');
    const mobileText = String(r.patient_primary_mobiles || '-');
    const batchBadge = Number(r.is_batched || 0) === 1 ? ' <span class="dash-batched-badge">(Batched)</span>' : '';
    const hasPatientTag = Number(r.has_patient_tag || 0) === 1;
    const hasBookingTag = String(r.booking_tags || '').trim() && String(r.booking_tags || '').trim() !== '-';
    const hasAnyTag = hasPatientTag || hasBookingTag;
    const colonyText = String(r.colony_name_snapshot || r.colony_name || '-');
    const routeText = String(r.route_no_snapshot || r.route_no || '-');
    const rowExpandId = `dash-expand-${bookingId}-${idx}`;
    return `
      <tr class="dash-data-row">
        <td class="${r.row_type === 'APPOINTMENT' ? 'dash-sr-apmt' : ''}">
          <span class="dash-expand-trigger" data-expand-row="${rowExpandId}" data-booking-id="${bookingId}" data-appointment-id="${Number(r.appointment_id || 0)}" data-row-type="${String(r.row_type || 'BOOKING')}" data-row-status="${Number(r.booking_status || 0)}">
            <span class="dash-expand-arrow">&#9656;</span> ${bookingId || '-'}
          </span>
        </td>
        <td>
          <div class="dash-patient-mobile">
            <div>
              ${r.row_type === 'APPOINTMENT' ? `[A${r.appointment_no || '-'}] ` : ''}${patientText}
              ${hasAnyTag ? '<span title="Patient or booking tag exists" style="color:#dc2626;font-size:15px;margin-left:6px;vertical-align:middle;">🏷️</span>' : ''}
            </div>
            <div class="dash-mobile-sub">${mobileText}${batchBadge}</div>
          </div>
        </td>
        <td>${cceTbsHtml}</td>
        <td>${formatVisitDate(r.preferred_visit_date)}</td>
        <td>${formatSlotCompact(r.preferred_time_slot || '-')}</td>
        <td>
          <div class="dash-route-merge">
            <div>${colonyText}</div>
            <div class="dash-route-sub">${routeText}</div>
          </div>
        </td>
        <td><strong>${Number(r.total_amount || 0).toFixed(2).replace(/\.00$/, '')}</strong></td>
        <td>${String(r.booked_by_name || '-').trim() || '-'}</td>
        <td>${statusCode === 1 ? assignedStatusButton(r, bookingId) : statusBadge(r.booking_status)}</td>
        <td>${String(r.assigned_phlebo_name || '-').trim() || '-'}</td>
        <td class="dash-actions-cell">
          <button class="btn btn-sm btn-outline-primary dash-action-btn btn-view" data-booking-id="${r.booking_id || r.id}" data-appointment-id="${r.appointment_id || 0}">View</button>
          ${[3, 4, 5].includes(statusCode)
            ? '<button class="btn btn-sm btn-outline-secondary dash-action-btn" type="button" disabled>Modify</button>'
            : `<button class="btn btn-sm btn-outline-secondary dash-action-btn btn-modify" data-booking-id="${r.booking_id || r.id}" data-appointment-id="${r.appointment_id || 0}">Modify</button>`}
          ${statusCode === 1
            ? `<button class="btn btn-sm dash-action-btn btn-reassign" style="background:#0b1f4d;color:#fff;border-color:#0b1f4d;" data-booking-id="${r.booking_id || r.id}" data-appointment-id="${r.appointment_id || 0}">ReAsgn</button>`
            : ([2, 3, 4].includes(statusCode)
              ? '<button class="btn btn-sm btn-outline-secondary dash-action-btn" type="button" disabled>Assign</button>'
              : `<button class="btn btn-sm btn-outline-warning dash-action-btn btn-assign" data-booking-id="${r.booking_id || r.id}" data-appointment-id="${r.appointment_id || 0}">Assign</button>`)}
        </td>
      </tr>
      <tr id="${rowExpandId}" class="dash-expand-row d-none">
        <td colspan="11"><div class="dash-expand-content"></div></td>
      </tr>
    `;
  }).join('');

  $('#dashboard-table tbody').html(html || '<tr class="dash-empty-row"><td colspan="11" class="text-center">No records</td></tr>');
  bindRowActions();
  bindExpandActions();
}

function renderDashboardPagination() {
  const totalPages = Number(dashboardPagingState.totalPages || 0);
  const currentPage = Number(dashboardPagingState.page || 1);
  const isAll = String(dashboardPagingState.pageSize).toLowerCase() === 'all';
  const hasPages = totalPages > 0;

  $('#dashboard-page-info').text(isAll ? 'Page 1 / 1' : `Page ${hasPages ? currentPage : 0} / ${totalPages}`);
  $('#dashboard-total-info').text(`Total: ${Number(dashboardPagingState.totalCount || 0)}`);

  $('#dashboard-page-first').prop('disabled', isAll || !hasPages || currentPage <= 1);
  $('#dashboard-page-prev').prop('disabled', isAll || !hasPages || currentPage <= 1);
  $('#dashboard-page-next').prop('disabled', isAll || !hasPages || currentPage >= totalPages);
  $('#dashboard-page-last').prop('disabled', isAll || !hasPages || currentPage >= totalPages);
}

function resetDashboardPaging() {
  dashboardPagingState.page = 1;
}

function loadDashboardWithLoader() {
  setDashboardPageLoading(true);
  const req = loadDashboard();
  if (req && typeof req.always === 'function') {
    req.always(function () {
      setDashboardPageLoading(false);
      markDashboardReady();
    });
  } else {
    setDashboardPageLoading(false);
    markDashboardReady();
  }
  return req;
}

function renderExpandedDetails(b, rowStatusCode, bookingId, appointmentId, rowType) {
  const patients = Array.isArray(b.patients) ? b.patients : [];
  const isAppointment = String(rowType || '').toUpperCase() === 'APPOINTMENT' || Number(appointmentId || 0) > 0;
  const bookingTagChips = String(b.booking_tags || '')
    .split(',')
    .map((x) => String(x || '').trim())
    .filter(Boolean)
    .map((tag) => `<span class="dash-tag-chip">${esc(tag)}</span>`)
    .join(' ');
  const cancelReason = String(b.cancel_reason || '').trim();
  const rescheduleTo = String(b.reschedule_to || '').trim();
  const cancelReasonHtml = cancelReason
    ? `   <strong>Cancel Reason:</strong> ${esc(cancelReason)}${rescheduleTo ? ` ( Reshedueled:- ${esc(rescheduleTo)})` : ''}`
    : '';
  const patientBlocks = patients.map((p, idx) => {
    const docUrls = Array.isArray(p.patient_document_urls) ? p.patient_document_urls : [];
    const rxUrls = Array.isArray(p.prescription_urls) ? p.prescription_urls : [];
    const rxUploadedBy = String(p.pres_uploaded_by_display || '').trim();
    const panelCompanies = Array.isArray(p.panel_companies) && p.panel_companies.length ? p.panel_companies.join(', ') : (p.panel_company || '-');
    const docThumbs = docUrls.map((u, i) => renderDocPreviewItem(u, `DOC ${i + 1}`)).join('');
    const rxThumbs = rxUrls.map((u, i) => renderDocPreviewItem(u, `RX ${i + 1}`, rxUploadedBy)).join('');
    const patientTagChips = String(p.tag || '')
      .split(',')
      .map((x) => String(x || '').trim())
      .filter(Boolean)
      .map((tag) => `<span class="dash-tag-chip">${tag}</span>`)
      .join(' ');
    const patientId = Number(p.patient_id || 0);
    const trfButton = patientId > 0
      ? `<button type="button" class="btn btn-sm btn-outline-primary dash-patient-trf-btn" data-booking-id="${Number(bookingId || b.id || 0)}" data-patient-id="${patientId}" data-appointment-id="${Number(appointmentId || 0)}">Print TRF</button>`
      : '';
    return `
      <div class="dash-expand-patient-card">
        <div class="dash-expand-patient-actions">${trfButton}</div>
        <div class="dash-expand-patient-title">Patient ${idx + 1}: ${p.full_name || '-'}</div>
        <div><strong>Patient Tags:</strong> ${patientTagChips || '-'}</div>
        <div><strong>Test Booking Status:</strong> ${tbsLabel(p.test_booking_status)}</div>
        <div><strong>Panel Company:</strong> ${panelCompanies}</div>
        <div><strong>Tests:</strong> ${p.tests_display || '-'}</div>
        <div><strong>Patient Documents:</strong> ${docThumbs || '-'}</div>
        <div><strong>Prescriptions:</strong> ${rxThumbs || '-'}</div>
      </div>
    `;
  }).join('');
  return `
    <div class="dash-expand-booking-meta">
      <div class="dash-expand-booking-meta-left">
        <div><strong>Referred By:</strong> ${esc(b.referred_by || '-')}${cancelReasonHtml}</div>
        <div><strong>Internal Referred By:</strong> ${esc(b.intrnl_rfrncd_by || '-')}</div>
        <div><strong>Booking Tags:</strong> ${bookingTagChips || '-'}</div>
      </div>
      <div class="dash-expand-booking-meta-right">
        ${(!isAppointment && Number(rowStatusCode || 0) !== 4)
          ? `<button class="btn btn-sm btn-outline-info dash-action-btn dash-expand-action-bookappt" data-booking-id="${Number(bookingId || b.id || 0)}">Book Appt</button>`
          : ''}
        ${[0, 1, 2].includes(Number(rowStatusCode || 0))
          ? `<button class="btn btn-sm btn-outline-secondary dash-action-btn dash-expand-action-reschedule" data-booking-id="${Number(bookingId || b.id || 0)}" data-appointment-id="${Number(appointmentId || 0)}">Reschedule</button>`
          : ''}
        ${[3, 4, 5].includes(Number(rowStatusCode || 0))
          ? `<button class="btn btn-sm btn-outline-secondary dash-action-btn" type="button" disabled>Cancel</button>`
          : `<button class="btn btn-sm btn-outline-danger dash-action-btn dash-expand-action-cancel" data-booking-id="${Number(bookingId || b.id || 0)}" data-appointment-id="${Number(appointmentId || 0)}">Cancel</button>`}
      </div>
    </div>
    <div class="dash-expand-patient-grid">${patientBlocks || '<div class="text-muted">No patient details.</div>'}</div>
  `;
}

function openPatientTrfPreview(bookingId, patientId, appointmentId) {
  const bid = Number(bookingId || 0);
  const pid = Number(patientId || 0);
  const aid = Number(appointmentId || 0);
  if (!bid || !pid) {
    alert('Invalid booking/patient for TRF print.');
    return;
  }
  const url = `/hhome-collection/trf-patient-preview?booking_id=${encodeURIComponent(String(bid))}&patient_id=${encodeURIComponent(String(pid))}${aid ? `&appointment_id=${encodeURIComponent(String(aid))}` : ''}`;
  const w = window.open(url, '_blank');
  if (!w) alert('Please allow popups to print TRF.');
}

function bindExpandActions() {
  $('[data-expand-row]').off('click').on('click', function () {
    const rowId = String($(this).data('expand-row') || '');
    const bookingId = Number($(this).data('booking-id') || 0);
    const appointmentId = Number($(this).data('appointment-id') || 0);
    const rowType = String($(this).data('row-type') || 'BOOKING');
    const rowStatusCode = Number($(this).data('row-status') || 0);
    const $parentRow = $(this).closest('tr');
    const $detailRow = $(`#${rowId}`);
    const $content = $detailRow.find('.dash-expand-content');
    const $arrow = $(this).find('.dash-expand-arrow');
    if (!$detailRow.length || bookingId <= 0) return;
    if (!$detailRow.hasClass('d-none')) {
      $detailRow.addClass('d-none');
      $arrow.html('&#9656;');
      $parentRow.removeClass('row-expanded');
      return;
    }
    $('.dash-expand-row').addClass('d-none');
    $('.dash-data-row').removeClass('row-expanded');
    $('.dash-expand-arrow').html('&#9656;');
    $detailRow.removeClass('d-none');
    $content.html('Loading...');
    $arrow.html('&#9662;');
    $parentRow.addClass('row-expanded');
    const detailUrl = appointmentId > 0
      ? `/hhome-collection/booking/${bookingId}?appointment_id=${appointmentId}`
      : `/hhome-collection/booking/${bookingId}`;
    $.get(detailUrl, function (res) {
      const b = res.booking || {};
      $content.html(renderExpandedDetails(b, rowStatusCode, bookingId, appointmentId, rowType));
      $content.find('.dash-expand-action-bookappt').off('click').on('click', function () {
        openBookAppointmentReasonModal(Number($(this).data('booking-id') || 0));
      });
      $content.find('.dash-expand-action-reschedule').off('click').on('click', function () {
        openRescheduleModal({
          booking_id: Number($(this).data('booking-id') || 0),
          appointment_id: Number($(this).data('appointment-id') || 0),
        });
      });
      $content.find('.dash-expand-action-cancel').off('click').on('click', function () {
        openCancelReasonModal({
          booking_id: Number($(this).data('booking-id') || 0),
          appointment_id: Number($(this).data('appointment-id') || 0),
        });
      });
      $content.find('.dash-patient-trf-btn').off('click').on('click', function () {
        openPatientTrfPreview(
          Number($(this).data('booking-id') || 0),
          Number($(this).data('patient-id') || 0),
          Number($(this).data('appointment-id') || 0)
        );
      });
      $('.js-doc-preview').off('click').on('click', function () {
        const src = String($(this).data('src') || $(this).attr('src') || '');
        const kind = String($(this).data('kind') || '').toLowerCase();
        const uploadedBy = String($(this).data('uploaded-by') || '').trim();
        if (!src) return;
        $('#doc-preview-title')
          .text(uploadedBy ? `Uploaded by:- ${uploadedBy}` : 'Document Preview')
          .toggleClass('fw-bold', !!uploadedBy)
          .css('color', uploadedBy ? '#0b2f6b' : '');
        if (kind === 'pdf' || src.toLowerCase().endsWith('.pdf')) {
          $('#doc-preview-image').addClass('d-none').attr('src', '');
          $('#doc-preview-pdf').removeClass('d-none').attr('src', src);
        } else {
          $('#doc-preview-pdf').addClass('d-none').attr('src', '');
          $('#doc-preview-image').removeClass('d-none').attr('src', src);
        }
        new bootstrap.Modal(document.getElementById('docPreviewModal')).show();
      });
    }).fail(function () {
      $content.html('<span class="text-danger">Unable to load details.</span>');
    });
  });
}

function bindAssignForSingleBooking(bookingId) {
  let appointmentId = 0;
  if (typeof bookingId === 'object' && bookingId !== null) {
    appointmentId = Number(bookingId.appointment_id || 0);
    bookingId = Number(bookingId.booking_id || 0);
  }
  $.get('/hhome-collection/phlebotomists', function (res) {
    if (!res.phlebotomists.length) return alert('No active users found with designation "Home Collection Phlebo".');
    const $modalHeader = $('#bookingModal .modal-header');
    const $modalFooter = $('#booking-modal-footer');
    const originalHeaderHtml = $modalHeader.html();

    const rows = (typeof window.renderHcAssignAlphaChipGroups === 'function')
      ? window.renderHcAssignAlphaChipGroups(res.phlebotomists || [], { selectedUserId: 0, assignedSet: new Set() })
      : '';

    $modalHeader.html(`
      <h5 class="modal-title">Assign Phlebotomist</h5>
      <div class="asg-modal-search-wrap">
        <input id="dash-assign-search" type="text" class="form-control form-control-sm" placeholder="Type 2 letters to search">
        <div id="dash-assign-suggest" class="asg-phlebo-search-suggest d-none"></div>
      </div>
      <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
    `);

    $('#booking-modal-body').html(`<div id="assign-chip-list" class="assign-alpha-groups">${rows}</div>`);
    $modalFooter.removeClass('d-none').html('<button id="save-assign" type="button" class="btn btn-warning btn-sm" disabled>Save</button>');
    const m = new bootstrap.Modal(document.getElementById('bookingModal'));
    m.show();

    let selectedUserId = null;
    let pickerCtrl = null;
    function setSelectedUser(uid, name) {
      selectedUserId = Number(uid || 0);
      $('#save-assign').prop('disabled', !selectedUserId);
      if (name) $('#dash-assign-search').val(name);
    }

    if (typeof window.initHcAssignUserPicker === 'function') {
      pickerCtrl = window.initHcAssignUserPicker({
        inputSelector: '#dash-assign-search',
        suggestSelector: '#dash-assign-suggest',
        chipContainerSelector: '#assign-chip-list',
        saveBtnSelector: '#save-assign',
        searchUrl: '/hhome-collection/internal-ref-users',
        limit: 20,
        debounceMs: 200,
        onSelect: function (uid, name) { setSelectedUser(uid, name); }
      });
    }

    $(document.getElementById('bookingModal')).off('hidden.bs.modal.assignPickerCleanup').on('hidden.bs.modal.assignPickerCleanup', function () {
      if (pickerCtrl && typeof pickerCtrl.destroy === 'function') pickerCtrl.destroy();
      $modalHeader.html(originalHeaderHtml);
      $modalFooter.addClass('d-none').html('<button type="button" class="btn btn-primary btn-sm" id="booking-modal-print-btn">Print Slip</button>');
    });

    $('#save-assign').off('click').on('click', function () {
      if (!selectedUserId) return;
      $.ajax({
        url: '/hhome-collection/assign-phlebotomist', method: 'POST', contentType: 'application/json',
        data: JSON.stringify({ booking_id: bookingId, appointment_id: appointmentId, user_id: selectedUserId }),
        success: function () { m.hide(); loadDashboard(); },
        error: function (xhr) { alert(xhr.responseJSON?.message || 'Assign failed'); }
      });
    });
  });
}

function openCancelReasonModal(bookingId) {
  let appointmentId = 0;
  if (typeof bookingId === 'object' && bookingId !== null) {
    appointmentId = Number(bookingId.appointment_id || 0);
    bookingId = Number(bookingId.booking_id || 0);
  }
  const modalEl = document.getElementById('cancelReasonModal');
  if (!modalEl) return;
  const m = new bootstrap.Modal(modalEl);
  $('#cancel-booking-id').val(String(bookingId || 0));
  $('#cancel-appointment-id').val(String(appointmentId || 0));
  $('#cancel-reason-select').val('');
  $('#cancel-additional-info').val('');
  $('#cancel-additional-info-wrap').addClass('d-none');
  $('input[name="cancel-reschedule-requested"][value="no"]').prop('checked', true);

  const allSlots = generateRescheduleSlots();
  $('#cancel-new-slot').html(allSlots.map((slot) => `<option value="${slot}">${slot}</option>`).join(''));

  $.get(`/hhome-collection/booking/${bookingId}`, function (res) {
    const b = res?.booking || {};
    $('#cancel-booking-code').val(String(b.booking_code || ''));
    const oldDateIso = String(b.preferred_visit_date || '').trim();
    const oldSlot = String(b.preferred_time_slot || '').trim();
    const m = oldDateIso.match(/^(\d{4}-\d{2}-\d{2})/);
    let inputDate = m ? m[1] : '';
    if (!inputDate) {
      const d = new Date(oldDateIso);
      if (!Number.isNaN(d.getTime())) {
        const y = d.getUTCFullYear();
        const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
        const dd = String(d.getUTCDate()).padStart(2, '0');
        inputDate = `${y}-${mm}-${dd}`;
      }
    }
    $('#cancel-new-date').val(inputDate);
    if (oldSlot && allSlots.includes(oldSlot)) $('#cancel-new-slot').val(oldSlot);
    $('#cancel-new-date').attr('min', todayIsoLocal());
    $('#cancel-new-date, #cancel-new-slot').prop('disabled', true);
  });

  $('input[name="cancel-reschedule-requested"]').off('change.cancelRes').on('change.cancelRes', function () {
    const yes = $('input[name="cancel-reschedule-requested"]:checked').val() === 'yes';
    $('#cancel-new-date, #cancel-new-slot').prop('disabled', !yes);
  });
  $('#cancel-reason-select').off('change.cancelInfo').on('change.cancelInfo', function () {
    const needsInfo = String($(this).val() || '').trim() === 'Patient requested cancellation';
    $('#cancel-additional-info-wrap').toggleClass('d-none', !needsInfo);
    if (!needsInfo) $('#cancel-additional-info').val('');
  });

  m.show();
  $('#btn-confirm-cancel-reason').off('click').on('click', function () {
    const reason = ($('#cancel-reason-select').val() || '').trim();
    if (!reason) return alert('Please select cancel reason.');
    const additionalInfo = ($('#cancel-additional-info').val() || '').trim();
    if (reason === 'Patient requested cancellation' && !additionalInfo) {
      return alert('Please enter additional information.');
    }
    const bookingCode = String($('#cancel-booking-code').val() || '');
    if (!window.confirm(`Are you sure you want to cancel ${bookingCode || `booking #${bookingId}`}? Cancellation is final and will be recorded in timeline.`)) {
      return;
    }
    const rescheduleRequested = $('input[name="cancel-reschedule-requested"]:checked').val() === 'yes';
    const newDate = String($('#cancel-new-date').val() || '').trim();
    const newSlot = String($('#cancel-new-slot').val() || '').trim();
    $.ajax({
      url: '/hhome-collection/cancel-booking', method: 'POST', contentType: 'application/json',
      data: JSON.stringify({
        booking_id: bookingId,
        appointment_id: appointmentId,
        reason_text: reason,
        additional_info: additionalInfo,
        reschedule_requested: rescheduleRequested ? 1 : 0,
        new_slot_known: rescheduleRequested ? 1 : 0,
        proposed_visit_date: newDate,
        proposed_time_slot: newSlot,
      }),
      success: function () { m.hide(); loadDashboard(); },
      error: function (xhr) { alert(xhr.responseJSON?.message || 'Cancel failed'); }
    });
  });
}

function openBookAppointmentReasonModal(bookingId) {
  const modalEl = document.getElementById('modifyReasonModal');
  if (!modalEl) return;
  const m = new bootstrap.Modal(modalEl);
  const $title = $('#modifyReasonModal .modal-title');
  const prevTitle = $title.text();
  $title.text('Book Appointment');
  $('#modifyReasonModal .form-label').text('Reason for Appointment *');
  $('#modify-reason-options').addClass('d-none');
  if (!$('#modify-reason-free').length) {
    $('#modifyReasonModal .modal-body').append(
      '<textarea id="modify-reason-free" class="form-control" rows="4" placeholder="Enter reason"></textarea>'
    );
  }
  $('#modify-reason-free').val('');
  m.show();
  $('#btn-open-modify-flow').off('click').on('click', function () {
    const reason = String($('#modify-reason-free').val() || '').trim();
    if (!reason) return alert('Reason is required.');
    const $btn = $(this);
    $btn.prop('disabled', true);
    $('.hd-loader-text').text('Opening Book Appointment...');
    setDashboardPageLoading(true);
    $('#h-dashboard-page').addClass('is-leaving');
    $.ajax({
      url: '/hhome-collection/book-appointment-init',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ booking_id: bookingId, reason_text: reason }),
      success: function (res) {
        m.hide();
        $title.text(prevTitle);
        const target = res?.redirect_url || '/hhome-collection?mode=book-appointment';
        window.location.href = target;
      },
      error: function (xhr) {
        $btn.prop('disabled', false);
        $('#h-dashboard-page').removeClass('is-leaving');
        $('.hd-loader-text').text('Loading All Booking...');
        setDashboardPageLoading(false);
        alert(xhr.responseJSON?.message || 'Unable to start appointment flow');
      }
    });
  });
  $(modalEl).off('hidden.bs.modal.bookAppt').on('hidden.bs.modal.bookAppt', function () {
    $title.text(prevTitle);
    $('#modify-reason-options').removeClass('d-none');
    $('#modify-reason-free').remove();
    $('.modify-reason-opt').prop('checked', false);
  });
}

function openModifyReasonModal(bookingTarget) {
  let appointmentId = 0;
  let bookingId = bookingTarget;
  if (typeof bookingTarget === 'object' && bookingTarget !== null) {
    appointmentId = Number(bookingTarget.appointment_id || 0);
    bookingId = Number(bookingTarget.booking_id || 0);
  }
  const modalEl = document.getElementById('modifyReasonModal');
  if (!modalEl) return;
  const m = new bootstrap.Modal(modalEl);
  $('#modifyReasonModal .form-label').text('Reason for Modification *');
  $('#modify-reason-options').removeClass('d-none');
  $('#modify-reason-free').remove();
  $('.modify-reason-opt').prop('checked', false);
  m.show();
  $('#btn-open-modify-flow').off('click').on('click', function () {
    const reason = $('.modify-reason-opt:checked').map(function () {
      return String($(this).val() || '').trim();
    }).get().filter(Boolean).join(',');
    if (!reason) return alert('Modify reason is required.');
    const $btn = $(this);
    $btn.prop('disabled', true);
    $('.hd-loader-text').text('Opening Modify Flow...');
    setDashboardPageLoading(true);
    $('#h-dashboard-page').addClass('is-leaving');

    $.ajax({
      url: '/hhome-collection/modify-init',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ booking_id: bookingId, appointment_id: appointmentId, reason_text: reason }),
      success: function (res) {
        m.hide();
        const target = res?.redirect_url || '/hhome-collection?mode=book-appointment';
        window.location.href = target;
      },
      error: function (xhr) {
        $btn.prop('disabled', false);
        $('#h-dashboard-page').removeClass('is-leaving');
        $('.hd-loader-text').text('Loading All Booking...');
        setDashboardPageLoading(false);
        alert(xhr.responseJSON?.message || 'Unable to start modify flow');
      }
    });
  });
  $(modalEl).off('hidden.bs.modal.modifyReason').on('hidden.bs.modal.modifyReason', function () {
    $('#modify-reason-options').removeClass('d-none');
    $('#modify-reason-free').remove();
    $('.modify-reason-opt').prop('checked', false);
  });
}

function openRescheduleModal(bookingTarget) {
  let bid = 0;
  let appointmentId = 0;
  if (typeof bookingTarget === 'object' && bookingTarget !== null) {
    bid = Number(bookingTarget.booking_id || bookingTarget.id || 0);
    appointmentId = Number(bookingTarget.appointment_id || 0);
  } else {
    bid = Number(bookingTarget || 0);
  }
  if (bid <= 0) return;
  const modalEl = document.getElementById('rescheduleModal');
  if (!modalEl) return;
  const m = new bootstrap.Modal(modalEl);
  const detailUrl = appointmentId > 0
    ? `/hhome-collection/booking/${bid}?appointment_id=${appointmentId}`
    : `/hhome-collection/booking/${bid}`;
  $.get(detailUrl, function (res) {
    const b = res?.booking || {};
    const bookingCode = String(b.booking_code || `Booking ${bid}`).trim();
    const rowLabel = appointmentId > 0 ? `${bookingCode} Appointment` : `Booking ${bookingCode}`;
    const oldDateIso = String(b.preferred_visit_date || '').trim();
    const oldSlot = String(b.preferred_time_slot || '').trim();
    const oldDateDisplay = formatDateForReschedule(oldDateIso);

    $('#reschedule-booking-id').val(String(bid));
    $('#reschedule-appointment-id').remove();
    $('#reschedule-booking-id').after(`<input type="hidden" id="reschedule-appointment-id" value="${appointmentId}">`);
    $('#reschedule-old-date').val(oldDateDisplay || '-');
    $('#reschedule-old-slot').val(oldSlot || '-');
    $('#reschedule-new-date').val(oldDateIso || '');
    $('#reschedule-new-date').attr('min', todayIsoLocal());
    $('#reschedule-reason').val('');
    $('#reschedule-booking-title').text(`Reschedule ${appointmentId > 0 ? 'Appointment' : 'Booking'} - ${bookingCode}`);

    const allSlots = generateRescheduleSlots();
    const options = allSlots.map((slot) => `<option value="${slot}">${slot}</option>`).join('');
    $('#reschedule-new-slot').html(options);
    if (oldSlot && allSlots.includes(oldSlot)) $('#reschedule-new-slot').val(oldSlot);

    function updateSummary() {
      const nd = String($('#reschedule-new-date').val() || '').trim();
      const ns = String($('#reschedule-new-slot').val() || '').trim();
      const newDateDisplay = formatDateForReschedule(nd);
      $('#reschedule-summary-line').html(
        `${rowLabel} will move from <strong>${oldDateDisplay}, ${oldSlot || '-'}</strong> -> <strong>${newDateDisplay}, ${ns || '-'}</strong>.`,
      );
    }

    $('#reschedule-new-date, #reschedule-new-slot').off('change.reschedule input.reschedule').on('change.reschedule input.reschedule', updateSummary);
    updateSummary();
    m.show();
  }).fail(function () {
    alert('Unable to load booking details for reschedule');
  });

  $('#btn-confirm-reschedule').off('click').on('click', function () {
    const payload = {
      booking_id: Number($('#reschedule-booking-id').val() || 0),
      appointment_id: Number($('#reschedule-appointment-id').val() || 0),
      preferred_visit_date: String($('#reschedule-new-date').val() || '').trim(),
      preferred_time_slot: String($('#reschedule-new-slot').val() || '').trim(),
      reason_text: String($('#reschedule-reason').val() || '').trim(),
    };
    if (!payload.booking_id || !payload.preferred_visit_date || !payload.preferred_time_slot || !payload.reason_text) {
      return alert('Date, slot and reason are required.');
    }
    $.ajax({
      url: '/hhome-collection/reschedule-booking',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
      success: function () {
        m.hide();
        loadDashboard();
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || 'Reschedule failed');
      },
    });
  });
}

function openUnassignReasonModal(bookingTarget) {
  const bookingId = Number(bookingTarget?.booking_id || 0);
  const appointmentId = Number(bookingTarget?.appointment_id || 0);
  const modalEl = document.getElementById('unassignReasonModal');
  if (!modalEl || !bookingId) return;
  $('#unassign-booking-id').val(String(bookingId));
  $('#unassign-appointment-id').val(String(appointmentId || 0));
  $('#unassign-reason-text').val('');
  new bootstrap.Modal(modalEl).show();
}

function bindRowActions() {
  $('.btn-view').off('click').on('click', function () {
    const bookingId = Number($(this).data('booking-id') || 0);
    const appointmentId = Number($(this).data('appointment-id') || 0);
    if (!bookingId) return;
    $('#bookingModal .modal-title').text('Booking Details');
    $('#booking-modal-body').html('<div class="text-muted">Loading...</div>');
    $('#booking-modal-footer').removeClass('d-none');
    $('#booking-modal-print-btn').off('click').on('click', function () {
      window.open(`/hhome-collection/print/${bookingId}`, '_blank');
    });
    new bootstrap.Modal(document.getElementById('bookingModal')).show();
    const detailUrl = appointmentId > 0
      ? `/hhome-collection/booking/${bookingId}?appointment_id=${appointmentId}`
      : `/hhome-collection/booking/${bookingId}`;
    $.get(detailUrl, function (res) {
      const b = res.booking;
      renderBookingReviewModalContent(b || {});
    }).fail(function (xhr) {
      $('#booking-modal-body').html(`<div class="text-danger">${esc(xhr?.responseJSON?.message || 'Unable to load details')}</div>`);
    });
  });

  $('.btn-assign').off('click').on('click', function () {
    bindAssignForSingleBooking({
      booking_id: Number($(this).data('booking-id') || 0),
      appointment_id: Number($(this).data('appointment-id') || 0),
    });
  });
  $('.btn-reassign').off('click').on('click', function () {
    bindAssignForSingleBooking({
      booking_id: Number($(this).data('booking-id') || 0),
      appointment_id: Number($(this).data('appointment-id') || 0),
    });
  });
  $('.dash-status-unassign').off('click').on('click', function () {
    openUnassignReasonModal({
      booking_id: Number($(this).data('booking-id') || 0),
      appointment_id: Number($(this).data('appointment-id') || 0),
    });
  });

  $('.btn-cancel').off('click').on('click', function () {
    openCancelReasonModal({
      booking_id: Number($(this).data('booking-id') || 0),
      appointment_id: Number($(this).data('appointment-id') || 0),
    });
  });

  $('.btn-modify').off('click').on('click', function () {
    openModifyReasonModal({
      booking_id: Number($(this).data('booking-id') || 0),
      appointment_id: Number($(this).data('appointment-id') || 0),
    });
  });

  $('.btn-book-appt').off('click').on('click', function () {
    openBookAppointmentReasonModal(Number($(this).data('booking-id') || 0));
  });

  $('.btn-reschedule').off('click').on('click', function () {
    openRescheduleModal({
      booking_id: Number($(this).data('booking-id') || 0),
      appointment_id: Number($(this).data('appointment-id') || 0),
    });
  });
}

$(function () {
  if (!$('#dashboard-table').length) return;

  window.addEventListener('pageshow', function () {
    resetDashboardPageState();
  });

  if (typeof flatpickr === 'function' && $('#f-date-range').length) {
    flatpickr('#f-date-range', {
      mode: 'range',
      dateFormat: 'Y-m-d',
      locale: { rangeSeparator: ' - ' },
      allowInput: false,
      clickOpens: true,
      onReady: function (_sel, _str, inst) {
        const today = new Date();
        dashboardDateSelection = [today];
        inst.setDate(today, false);
        inst.input.value = toIsoLocalDate(today);
      },
      onChange: function (selectedDates, _dateStr, inst) {
        dashboardDateSelection = selectedDates || [];
        if (dashboardDateSelection.length === 1) {
          inst.input.value = toIsoLocalDate(dashboardDateSelection[0]);
        } else if (dashboardDateSelection.length === 0) {
          inst.input.value = '';
        }
      }
    });
  } else {
    dashboardDateSelection = [new Date()];
  }

  setDashboardPageLoading(true);
  $('#btn-go-assign-booking').off('click').on('click', openAssignBookingSmoothly);
  $('#btn-filter').off('click').on('click', function () {
    resetDashboardPaging();
    loadDashboardWithLoader();
  });
  $('#dashboard-page-size').off('change').on('change', function () {
    dashboardPagingState.pageSize = String($(this).val() || '50').toLowerCase();
    resetDashboardPaging();
    loadDashboardWithLoader();
  });
  $('#dashboard-page-first').off('click').on('click', function () {
    if (dashboardPagingState.page <= 1) return;
    dashboardPagingState.page = 1;
    loadDashboardWithLoader();
  });
  $('#dashboard-page-prev').off('click').on('click', function () {
    if (dashboardPagingState.page <= 1) return;
    dashboardPagingState.page -= 1;
    loadDashboardWithLoader();
  });
  $('#dashboard-page-next').off('click').on('click', function () {
    if (dashboardPagingState.page >= dashboardPagingState.totalPages) return;
    dashboardPagingState.page += 1;
    loadDashboardWithLoader();
  });
  $('#dashboard-page-last').off('click').on('click', function () {
    if (dashboardPagingState.totalPages <= 0) return;
    dashboardPagingState.page = dashboardPagingState.totalPages;
    loadDashboardWithLoader();
  });
  $('#dashboard-table thead').off('click', '.sortable-col').on('click', '.sortable-col', function () {
    const key = String($(this).data('sort-key') || '');
    if (!key) return;
    if (dashboardSortState.key === key) {
      dashboardSortState.dir = dashboardSortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
      dashboardSortState.key = key;
      dashboardSortState.dir = 'asc';
    }
    resetDashboardPaging();
    loadDashboardWithLoader();
  });
  $('#btn-confirm-unassign').off('click').on('click', function () {
    const payload = {
      booking_id: Number($('#unassign-booking-id').val() || 0),
      appointment_id: Number($('#unassign-appointment-id').val() || 0),
      reason_text: String($('#unassign-reason-text').val() || '').trim(),
    };
    if (!payload.reason_text) return alert('Reason is required.');
    const $btn = $(this).prop('disabled', true);
    $.ajax({
      url: '/hhome-collection/unassign-phlebotomist',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
      success: function () {
        const modal = bootstrap.Modal.getInstance(document.getElementById('unassignReasonModal'));
        if (modal) modal.hide();
        loadDashboardWithLoader();
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || 'Unassign failed');
      },
      complete: function () {
        $btn.prop('disabled', false);
      },
    });
  });
  loadDashboardWithLoader();
});
