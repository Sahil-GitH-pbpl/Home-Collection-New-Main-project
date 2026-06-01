(function () {
  const tableEl = document.getElementById('asg-grid-table');
  if (!tableEl) return;

  const dateEl = document.getElementById('asg-plan-date');
  const metaEl = document.getElementById('asg-meta');
  const gridWrapEl = document.querySelector('.h-assign-grid-wrap');
  const xscrollEl = document.getElementById('asg-xscroll');
  const xscrollInnerEl = document.getElementById('asg-xscroll-inner');
  const modalEl = document.getElementById('asgModal');
  const modal = new bootstrap.Modal(modalEl);
  const modalSearchInputEl = document.getElementById('asg-phlebo-search');
  const commitBtn = document.getElementById('asg-commit-btn');
  const reloadBtn = document.getElementById('asg-reload');
  const toastEl = document.getElementById('asg-feedback-toast');
  const toastBodyEl = document.getElementById('asg-feedback-toast-body');
  const feedbackToast = toastEl ? new bootstrap.Toast(toastEl) : null;
  const commitBtnDefaultHtml = commitBtn ? commitBtn.innerHTML : 'Assign All Bookings';
  const reviewModalEl = document.getElementById('asgReviewModal');
  const reviewBodyEl = document.getElementById('asg-review-body');
  const reviewModal = reviewModalEl ? new bootstrap.Modal(reviewModalEl) : null;
  const hoverPanelEl = document.getElementById('asg-hover-panel');

  const state = {
    routes: [],
    bookings: [],
    phlebos: [],
    routeOrder: [],
    routeAssignee: {},
    currentRouteForAssign: null,
    selectedPhlebo: null,
    draggedBookingId: null,
    isCommitting: false,
  };
  let assignPickerCtrl = null;

  function showFeedbackToast(kind, text) {
    if (!feedbackToast || !toastEl || !toastBodyEl) return;
    toastEl.classList.remove('text-bg-success', 'text-bg-danger', 'text-bg-warning');
    if (kind === 'success') toastEl.classList.add('text-bg-success');
    else if (kind === 'danger') toastEl.classList.add('text-bg-danger');
    else toastEl.classList.add('text-bg-warning');
    toastBodyEl.textContent = text || '';
    feedbackToast.show();
  }

  function setCommitLoading(isLoading) {
    state.isCommitting = !!isLoading;
    if (!commitBtn) return;
    commitBtn.disabled = !!isLoading;
    if (reloadBtn) reloadBtn.disabled = !!isLoading;
    if (isLoading) {
      commitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Assigning...';
    } else {
      commitBtn.innerHTML = commitBtnDefaultHtml;
    }
  }

  function formatMinutesTo12h(totalMinutes) {
    const mins = ((totalMinutes % 1440) + 1440) % 1440;
    let h24 = Math.floor(mins / 60);
    const m = mins % 60;
    const ap = h24 >= 12 ? 'PM' : 'AM';
    let h12 = h24 % 12;
    if (h12 === 0) h12 = 12;
    return `${String(h12).padStart(2, '0')}:${String(m).padStart(2, '0')} ${ap}`;
  }

  function generateHalfHourSlots() {
    const slots = [];
    for (let mins = 6 * 60; mins < 24 * 60; mins += 30) {
      slots.push(`${formatMinutesTo12h(mins)} to ${formatMinutesTo12h(mins + 30)}`);
    }
    return slots;
  }

  const TIME_SLOTS = generateHalfHourSlots();

  function esc(v) {
    return String(v ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function shortCityName(raw) {
    const city = String(raw || '').trim().toLowerCase();
    if (!city) return '';
    if (city === 'delhi' || city === 'new delhi') return 'Del';
    if (city === 'gurgram' || city === 'gurugram' || city === 'gurgaon') return 'Grgm';
    if (city === 'noida') return 'Nda';
    if (city === 'ghaziabad') return 'Gzbd';
    if (city === 'faridabad' || city === 'fardaabd') return 'Frdbd';
    return String(raw || '').trim();
  }

  function fmtMoney(v) {
    const n = Number(v || 0);
    if (!Number.isFinite(n)) return '0';
    return n.toFixed(2).replace(/\.00$/, '');
  }


  function tbsLabel(code) {
    const c = Number(code || 0);
    if (c === 1) return 'Test confirmed and booked';
    if (c === 2) return 'Prescription attached but test not booked';
    if (c === 3) return 'No test information: ask to patient for tests';
    if (c === 4) return 'Incompleted test, phlebo verification pending to confirm and book';
    return '-';
  }

  function renderReviewModalContent(booking) {
    if (!reviewBodyEl) return;
    const patients = Array.isArray(booking?.patients) ? booking.patients : [];
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
          <h6 class="hc-patient-name-red"><span>Patient Name:</span> ${esc(p.full_name || '-')}</h6>
          ${sectionHtml || '<div class="text-muted">No tests.</div>'}
          <div class="d-flex flex-wrap align-items-center justify-content-between mt-2 hc-review-bottom-strip hc-review-bottom-strip-patient">
            <div class="ms-auto text-end"><strong>Total Amount: ${esc(fmtMoney(patientTotal))}</strong></div>
          </div>
        </div>
      </div>`;
    }).join('');

    reviewBodyEl.innerHTML = `
      <div class="hc-review-grid mb-2">
        <div class="hc-review-meta">
          <div><strong>Caller:</strong> ${esc(booking.primary_mobile || '-')} | <strong>Patients:</strong> ${patients.length}</div>
          <div><strong>Google Location:</strong> <span class="hc-review-linkish">${esc(booking.google_location || '-')}</span></div>
          <div><strong>Referred By:</strong> ${esc(booking.referred_by || '-')}</div>
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
    `;
  }

  function tbsLabelFromCodes(csv) {
    const map = {
      '1': 'Test confirmed and booked',
      '2': 'Prescription attached but test not booked',
      '3': 'No test information: ask to patient for tests',
      '4': 'Incompleted test, phlebo verification pending to confirm and book'
    };
    const parts = String(csv || '').split(',').map(x => String(x || '').trim()).filter(Boolean);
    const labels = Array.from(new Set(parts.map(x => map[x] || x).filter(Boolean)));
    return labels.join(', ') || '-';
  }

  function buildHoverText(b) {
    return [
      `Test Booking Status: ${tbsLabelFromCodes(b.test_booking_status_codes)}`,
      `Patient Tags: ${String(b.patient_tags || '-').trim() || '-'}`,
      `Booking Tags: ${String(b.booking_tags || '-').trim() || '-'}`,
      `Panel Company: ${String(b.panel_companies || '-').trim() || '-'}`,
      `Referred By: ${String(b.referred_by || '-').trim() || '-'}`,
      `Internal Ref By: ${String(b.internal_referred_by || '-').trim() || '-'}`,
      `Total Value: ${Number(b.total_amount || 0).toFixed(2).replace(/\.00$/, '')}`
    ].join('\n');
  }

  function defaultTomorrow() {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    return d.toISOString().slice(0, 10);
  }

  function phleboName(id) {
    const p = state.phlebos.find(x => Number(x.id) === Number(id));
    return p ? p.full_name : '';
  }

  function countRoute(route) {
    return state.bookings.filter(b => b.route_name === route).length;
  }

  function renderMeta() {
    const total = state.bookings.length;
    const activeRoutes = state.routeOrder.filter(r => countRoute(r) > 0);
    const assigned = activeRoutes.filter(r => !!state.routeAssignee[r]).length;
    metaEl.innerHTML = `
      <span><strong>Total bookings:</strong> ${total}</span>
      <span><strong>Visible routes:</strong> ${state.routeOrder.length}</span>
      <span><strong>Routes assigned:</strong> ${assigned}</span>
    `;
  }

  function renderTable() {
    const routes = [...state.routeOrder];
    const slots = [...TIME_SLOTS];

    let html = '<thead><tr><th class="asg-time-col">Time Slot</th>';
    routes.forEach(route => {
      const uid = state.routeAssignee[route] || '';
      const aname = uid ? phleboName(uid) : 'No phlebo';
      html += `
        <th class="asg-route-th" data-route-id="${esc(route)}">
          <div class="asg-route-head">
            <div class="asg-route-top">
              <div class="asg-route-title" draggable="true" data-route-id="${esc(route)}">${esc(route)}</div>
              <button class="btn btn-sm btn-assign-route" data-route-id="${esc(route)}">Asgn</button>
            </div>
            <div class="asg-route-bottom">
              <div class="asg-route-assignee ${uid ? '' : 'none'}">${esc(aname)}</div>
              <div class="asg-route-count">${countRoute(route)}</div>
            </div>
          </div>
        </th>
      `;
    });
    html += '</tr></thead><tbody>';

    slots.forEach(slot => {
      html += `<tr><td class="asg-time-col">${esc(slot)}</td>`;
      routes.forEach(route => {
        const items = state.bookings.filter(b => b.route_name === route && b.slot === slot);
        html += `<td class="asg-slot-cell" data-route-id="${esc(route)}" data-slot="${esc(slot)}">`;
        if (!items.length) {
          html += '<div class="asg-empty">-</div>';
        } else {
          items.forEach(b => {
            const colony = esc(b.colony_name || b.colony_name_snapshot || '');
            const pincode = esc(b.pincode || '');
            const city = esc(shortCityName(b.city || ''));
            const placeLead = colony || pincode;
            const place = `${placeLead || '-'}${city ? `, <span class="asg-city-short">${city}</span>` : ''}`;
            const callerMobile = esc(b.caller_mobile || '');
            const callerMobileDisplay = callerMobile ? `(${callerMobile})` : '(-)';
            const patientCount = Number(b.patient_count || 0);
            const extraCount = patientCount > 1 ? (patientCount - 1) : 0;
            const detailBookingId = String(b.row_type || '').toUpperCase() === 'APPOINTMENT'
              ? Number(b.parent_booking_id || 0)
              : Number(b.booking_id || 0);
            const hoverText = esc(buildHoverText(b));
            const hasPatientTags = String(b.patient_tags || '').trim().length > 0;
            html += `
              <div class="asg-booking ${hasPatientTags ? 'has-patient-tag' : ''}" draggable="true" data-booking-id="${b.booking_id}" data-detail-booking-id="${detailBookingId}" data-hover-text="${hoverText}">
                ${extraCount > 0 ? `<div class="asg-extra-pill">+${extraCount}</div>` : ''}
                <div class="asg-baddr">${place}</div>
                <div class="asg-bmob">${callerMobileDisplay}</div>
              </div>
            `;
          });
        }
        html += '</td>';
      });
      html += '</tr>';
    });

    html += '</tbody>';
    tableEl.innerHTML = html;
    bindBookingDnD();
    bindColumnDnD();
    bindAssignRouteBtns();
    syncBottomScroller();
  }

  function syncBottomScroller() {
    if (!gridWrapEl || !xscrollEl || !xscrollInnerEl) return;
    xscrollInnerEl.style.width = `${tableEl.scrollWidth}px`;
    xscrollEl.scrollLeft = gridWrapEl.scrollLeft;
  }

  function bindBottomScrollerSync() {
    if (!gridWrapEl || !xscrollEl) return;
    xscrollEl.addEventListener('scroll', () => {
      gridWrapEl.scrollLeft = xscrollEl.scrollLeft;
    });
  }

  function bindBookingDnD() {
    tableEl.querySelectorAll('.asg-booking').forEach(card => {
      card.addEventListener('dragstart', e => {
        state.draggedBookingId = card.dataset.bookingId || null;
        e.dataTransfer.setData('text/booking', state.draggedBookingId || '');
        e.dataTransfer.effectAllowed = 'move';
      });
      card.addEventListener('dragend', () => {
        state.draggedBookingId = null;
        tableEl.querySelectorAll('.asg-slot-cell').forEach(c => c.classList.remove('drop-on'));
      });
      card.addEventListener('mouseenter', () => {
        if (!hoverPanelEl) return;
        hoverPanelEl.textContent = String(card.dataset.hoverText || '').replaceAll('\\n', '\n');
        const rect = card.getBoundingClientRect();
        hoverPanelEl.style.left = `${Math.max(8, Math.min(window.innerWidth - 320, rect.left + 8))}px`;
        hoverPanelEl.style.top = `${Math.min(window.innerHeight - 120, rect.bottom + 8)}px`;
        hoverPanelEl.classList.remove('d-none');
      });
      card.addEventListener('mouseleave', () => {
        if (!hoverPanelEl) return;
        hoverPanelEl.classList.add('d-none');
      });
      card.addEventListener('dblclick', () => {
        const detailBookingId = Number(card.dataset.detailBookingId || 0);
        if (!detailBookingId || !reviewBodyEl || !reviewModal) return;
        reviewBodyEl.innerHTML = '<div class="text-muted">Loading...</div>';
        reviewModal.show();
        $.get(`/hhome-collection/booking/${detailBookingId}`, function (res) {
          const bk = res?.booking || {};
          renderReviewModalContent(bk);
        }).fail(function (xhr) {
          reviewBodyEl.innerHTML = `<div class="text-danger">${esc(xhr?.responseJSON?.message || 'Unable to load details')}</div>`;
        });
      });
    });

    tableEl.querySelectorAll('.asg-slot-cell').forEach(cell => {
      cell.addEventListener('dragover', e => {
        const bookingId = state.draggedBookingId || e.dataTransfer.getData('text/booking');
        if (!bookingId) return;
        const item = state.bookings.find(b => String(b.booking_id) === String(bookingId));
        if (!item) return;
        if (String(item.slot) !== String(cell.dataset.slot)) return; // same-row only
        e.preventDefault();
        cell.classList.add('drop-on');
      });

      cell.addEventListener('dragleave', () => cell.classList.remove('drop-on'));

      cell.addEventListener('drop', e => {
        const bookingId = state.draggedBookingId || e.dataTransfer.getData('text/booking');
        if (!bookingId) return;
        const item = state.bookings.find(b => String(b.booking_id) === String(bookingId));
        if (!item) return;
        if (String(item.slot) !== String(cell.dataset.slot)) return; // block up/down
        e.preventDefault();
        cell.classList.remove('drop-on');
        item.route_name = cell.dataset.routeId; // UI grouping only
        renderAll();
      });
    });
  }

  function bindColumnDnD() {
    tableEl.querySelectorAll('.asg-route-title').forEach(title => {
      title.addEventListener('dragstart', e => {
        e.dataTransfer.setData('text/route', title.dataset.routeId);
        e.dataTransfer.effectAllowed = 'move';
      });
    });

    tableEl.querySelectorAll('.asg-route-th').forEach(th => {
      th.addEventListener('dragover', e => {
        if (e.dataTransfer.types.includes('text/route')) e.preventDefault();
      });
      th.addEventListener('drop', e => {
        const src = e.dataTransfer.getData('text/route');
        const tgt = th.dataset.routeId;
        if (!src || !tgt || src === tgt) return;
        const from = state.routeOrder.indexOf(src);
        const to = state.routeOrder.indexOf(tgt);
        if (from < 0 || to < 0) return;
        state.routeOrder.splice(from, 1);
        state.routeOrder.splice(to, 0, src);
        renderAll();
      });
    });
  }

  function bindAssignRouteBtns() {
    tableEl.querySelectorAll('.btn-assign-route').forEach(btn => {
      btn.addEventListener('click', () => openAssignModal(btn.dataset.routeId));
    });
  }

  function setSelectedPhlebo(userId, displayName = '') {
    const uid = Number(userId || 0);
    if (!uid) return;
    const cleanName = String(displayName || '').trim();
    const idx = (state.phlebos || []).findIndex(x => Number(x.id) === uid);
    if (idx < 0 && cleanName) {
      state.phlebos.push({ id: uid, full_name: cleanName });
    } else if (idx >= 0 && cleanName && !String(state.phlebos[idx].full_name || '').trim()) {
      state.phlebos[idx].full_name = cleanName;
    }
    state.selectedPhlebo = uid;
    const list = document.getElementById('asg-phlebo-list');
    if (list) {
      list.querySelectorAll('.assign-chip').forEach(x => x.classList.remove('active'));
      const selectedChip = list.querySelector(`.assign-chip[data-user-id="${uid}"]`);
      if (selectedChip) selectedChip.classList.add('active');
    }
    const saveBtn = document.getElementById('asg-save-route-btn');
    if (saveBtn) saveBtn.disabled = false;
  }

  function openAssignModal(routeId) {
    state.currentRouteForAssign = routeId;
    state.selectedPhlebo = state.routeAssignee[routeId] || null;
    document.getElementById('asg-modal-title').textContent = `Assign ${routeId}`;
    const globallyAssigned = new Set(
      Object.values(state.routeAssignee)
        .map(v => Number(v))
        .filter(v => Number.isFinite(v) && v > 0)
    );

    const html = (typeof window.renderHcAssignAlphaChipGroups === 'function')
      ? window.renderHcAssignAlphaChipGroups(state.phlebos || [], {
        selectedUserId: Number(state.selectedPhlebo || 0),
        assignedSet: globallyAssigned,
      })
      : '';
    document.getElementById('asg-phlebo-list').innerHTML = html;
    const saveBtn = document.getElementById('asg-save-route-btn');
    saveBtn.disabled = !state.selectedPhlebo;
    if (modalSearchInputEl) modalSearchInputEl.value = '';
    if (assignPickerCtrl && typeof assignPickerCtrl.hideSuggest === 'function') {
      assignPickerCtrl.hideSuggest();
    }
    modal.show();
  }

  function bindModalEvents() {
    const saveBtn = document.getElementById('asg-save-route-btn');
    if (typeof window.initHcAssignUserPicker === 'function') {
      assignPickerCtrl = window.initHcAssignUserPicker({
        inputSelector: '#asg-phlebo-search',
        suggestSelector: '#asg-phlebo-search-suggest',
        chipContainerSelector: '#asg-phlebo-list',
        saveBtnSelector: '#asg-save-route-btn',
        searchUrl: '/hhome-collection/internal-ref-users',
        limit: 20,
        debounceMs: 200,
        onSelect: function (uid, name) {
          setSelectedPhlebo(uid, name);
        },
      });
    }

    saveBtn.addEventListener('click', () => {
      if (!state.currentRouteForAssign || !state.selectedPhlebo) return;
      state.routeAssignee[state.currentRouteForAssign] = state.selectedPhlebo;
      modal.hide();
      renderAll();
    });
  }

  function buildRouteAssigneeFromRows() {
    const grouped = {};
    state.bookings.forEach(b => {
      const r = b.route_name;
      grouped[r] = grouped[r] || [];
      if (b.assigned_user_id) grouped[r].push(Number(b.assigned_user_id));
    });
    const routeAssignee = {};
    Object.keys(grouped).forEach(route => {
      const uniq = Array.from(new Set(grouped[route]));
      routeAssignee[route] = uniq.length === 1 ? uniq[0] : null;
    });
    state.routeAssignee = routeAssignee;
  }

  function loadPlanner() {
    const d = dateEl.value || defaultTomorrow();
    $.get('/hhome-collection/assign-booking-data', { date: d }, function (res) {
      if (!res?.ok) {
        showFeedbackToast('danger', res?.message || 'Unable to load assignment planner');
        return;
      }
      state.routes = res.routes || [];
      state.bookings = res.rows || [];
      state.phlebos = res.phlebos || [];
      state.routeOrder = [...state.routes];
      buildRouteAssigneeFromRows();
      renderAll();
    }).fail(function (xhr) {
      showFeedbackToast('danger', xhr.responseJSON?.message || 'Unable to load assignment planner');
    });
  }

  function commitAssignments() {
    if (state.isCommitting) return;

    const assignments = state.bookings
      .filter(b => !!state.routeAssignee[b.route_name])
      .map(b => ({
        row_type: b.row_type || (Number(b.appointment_id || 0) > 0 ? 'APPOINTMENT' : 'BOOKING'),
        booking_id: b.booking_id,
        appointment_id: Number(b.appointment_id || 0),
        parent_booking_id: Number(b.parent_booking_id || 0),
        grouped_route: b.route_name,
        assigned_user_id: state.routeAssignee[b.route_name]
      }));

    if (!assignments.length) {
      showFeedbackToast('warning', 'Please assign at least one route first.');
      return;
    }

    setCommitLoading(true);
    $.ajax({
      url: '/hhome-collection/assign-bookings-commit',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({
        plan_date: dateEl.value || defaultTomorrow(),
        assignments
      }),
      success: function (res) {
        showFeedbackToast('success', `Assigned successfully. Updated bookings: ${res.updated_count || 0}`);
        // message preview returned by backend if needed in future
        console.log('assignment messages_preview', res.messages_preview || []);
        loadPlanner();
      },
      error: function (xhr) {
        showFeedbackToast('danger', xhr.responseJSON?.message || 'Assignment failed');
      },
      complete: function () {
        setCommitLoading(false);
      }
    });
  }

  function renderAll() {
    renderMeta();
    renderTable();
  }

  function init() {
    dateEl.value = dateEl.value || window.H_ASSIGN_DEFAULT_DATE || defaultTomorrow();
    bindModalEvents();
    bindBottomScrollerSync();
    if (reloadBtn) reloadBtn.addEventListener('click', loadPlanner);
    if (commitBtn) commitBtn.addEventListener('click', commitAssignments);
    loadPlanner();
  }

  init();
})();
