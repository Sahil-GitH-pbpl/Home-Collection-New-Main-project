let currentStep = 1;
let linkedPatientsCache = [];
let referenceAddressesCache = [];
let callerHistoryCache = null;
let hasCallerContext = false;
let colonyRequestSeq = 0;
let wizardData = {
  searchedMobile: '',
  appointment: {},
  testsBilling: {},
  prescriptionUploads: {},
  modify: {}
};
let selectedPatientTags = [];
let selectedPermanentTags = [];
let selectedBookingTags = [];
let selectedPatientsCache = [];
let tagOptions = {
  patient: [],
  permanent: [],
  transactional: []
};
let editingPatientId = null;
let editingAddressId = null;
let addressColonyCatalog = [];
let slotPlannerModal = null;
let panelTestsModal = null;
let slotSelectedRoute = '';
let summaryRequestSeq = 0;
let testSpecimenCatalog = null;
let testSpecimenCatalogPromise = null;
let panelTestSearchTimer = null;
let panelTestSearchQuery = '';
let panelTestSearchSeq = 0;
let phleboTagModal = null;
let phleboTagMode = '';
let phleboTagSelected = null;
let bookingDatePicker = null;
let slotGridDatePicker = null;
let activePanelPicker = {
  patientId: null,
  panelIndex: 0,
  compCatId: null,
  billingName: '',
  selectedGcode: '',
  selectedScode: '',
  tempSelected: {}
};
const PATIENT_TBS_OPTIONS = [
  { code: 1, label: 'Test confirmed and booked' },
  { code: 2, label: 'Prescription attached but test not booked' },
  { code: 3, label: 'No test information: ask to patient for tests' },
  { code: 4, label: 'Incompleted test, phlebo verification pending to confirm and book' }
];

const TITLE_MASTER = [
  { id: 1, title: 'Mr.', gender: 'Male' },
  { id: 2, title: 'Mrs.', gender: 'Female' },
  { id: 3, title: 'Dr', gender: 'Male' },
  { id: 4, title: 'Dr (Ms)', gender: 'Female' },
  { id: 5, title: 'Master', gender: 'Male' },
  { id: 6, title: 'Baby', gender: 'Female' },
  { id: 7, title: 'Daughter of', gender: 'Female' },
  { id: 8, title: 'Son Of', gender: 'Male' },
  { id: 9, title: 'Miss', gender: 'Female' },
  { id: 11, title: 'MS.', gender: 'Female' },
  { id: 12, title: 'Mr', gender: 'Male' },
  { id: 13, title: 'MST.', gender: 'Male' },
  { id: 14, title: 'Mrs', gender: 'Female' },
  { id: 15, title: 'Mst', gender: 'Male' },
  { id: 16, title: 'Ms', gender: 'Female' },
  { id: 18, title: 'Care Of', gender: 'Other' },
  { id: 19, title: 'CARE', gender: 'Other' },
  { id: 20, title: 'PROF.', gender: 'Male' },
  { id: 21, title: 'CAPT.', gender: 'Male' },
  { id: 23, title: 'Prof', gender: 'Male' },
  { id: 24, title: 'COL.', gender: 'Male' },
  { id: 25, title: 'BRIG.', gender: 'Male' },
  { id: 26, title: 'MAJ.', gender: 'Male' },
  { id: 27, title: 'MAJ.GEN', gender: 'Male' },
  { id: 28, title: 'JUSTIC', gender: 'Male' },
  { id: 29, title: 'DSD', gender: 'Other' }
];

const titleGenderMap = TITLE_MASTER.reduce((acc, row) => {
  acc[row.title] = row.gender;
  return acc;
}, {});

function renderPatientTitleOptions(selectedTitle = '') {
  const $title = $('#p-title');
  if (!$title.length) return;

  const normalizedSelected = String(selectedTitle || '').trim();
  const options = ['<option value="">Select</option>']
    .concat(TITLE_MASTER.map((row) => {
      const title = String(row.title || '').trim();
      const selected = title === normalizedSelected ? ' selected' : '';
      return `<option value="${escHtml(title)}"${selected}>${escHtml(title)}</option>`;
    }))
    .join('');

  $title.html(options);
}

function setLayoutForWizard() {
  $('#wizard-right-col').removeClass('d-none');
  $('#wizard-left-col').removeClass('col-lg-12').addClass('col-lg-9');
  $('#wizard-top-tags').removeClass('d-none');
}

function setLayoutForSuccess() {
  $('#wizard-right-col').addClass('d-none');
  $('#wizard-left-col').removeClass('col-lg-9').addClass('col-lg-12');
  $('#wizard-top-tags').addClass('d-none');
}

function setStep(step) {
  setLayoutForWizard();
  slotPlannerModal = null;
  currentStep = step;
  $('.step-pill').removeClass('active');
  $(`.step-pill[data-step="${step}"]`).addClass('active');
  $.get(`/hhome-collection/step/${step}`, function (html) {
    $('#wizard-left-panel').html(html);
    bindStepEvents();
    if (step === 2) hydrateStep2();
    if (step === 3) renderTestsBilling();
    if (step === 4) renderReview();
  });
}


function showAlert(target, type, text) {
  $(target).html(`<div class="alert alert-${type} hc-alert">${text}</div>`);
}

function escHtml(v) {
  return String(v ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function normalizeChargeModeCode(raw) {
  const src = String(raw || '').toUpperCase().trim();
  if (!src) return '';
  const keep = [];
  for (const ch of src) {
    if ((ch === 'C' || ch === 'P' || ch === 'F') && !keep.includes(ch)) {
      keep.push(ch);
    }
  }
  return ['C', 'P', 'F'].filter(ch => keep.includes(ch)).join('');
}

function chargeModeLabel(codeRaw) {
  const code = normalizeChargeModeCode(codeRaw);
  if (!code) return '-';
  const parts = [];
  if (code.includes('C')) parts.push('Credit');
  if (code.includes('P')) parts.push('Paying');
  if (code.includes('F')) parts.push('Free');
  return `${code} (${parts.join(' + ')})`;
}

function chargeModeOptions(codeRaw) {
  const code = normalizeChargeModeCode(codeRaw);
  if (!code) return [];
  return ['C', 'P', 'F'].filter((x) => code.includes(x));
}

function panelDomKey(patientId, panelIndex) {
  return `${String(patientId || '').replace(/[^A-Za-z0-9_-]/g, '_')}_${Number(panelIndex || 0)}`;
}

function normalizePanelSection(section) {
  const s = section && typeof section === 'object' ? section : {};
  s.panel = s.panel || null;
  s.billing = s.billing || null;
  s.selected_tests = Array.isArray(s.selected_tests) ? s.selected_tests : [];
  return s;
}

function panelShowMrp(section) {
  const s = section && typeof section === 'object' ? section : {};
  const panel = s.panel && typeof s.panel === 'object' ? s.panel : {};
  const billing = s.billing && typeof s.billing === 'object' ? s.billing : {};
  return Number(panel.showmrp || billing.showmrp || 0) === 1;
}

function pricedForPanel(t, showMrp) {
  const mrp = Number(t?.mrp || 0);
  if (showMrp) {
    return { mrp, charge: mrp, max_discount: 0, max_allowed_discount: Number(t?.max_allowed_discount || 0) };
  }
  return {
    mrp,
    charge: Number(t?.charge || 0),
    max_discount: Number(t?.max_discount || 0),
    max_allowed_discount: Number(t?.max_allowed_discount || 0)
  };
}

function getPatientPanels(patientId) {
  const tb = ensureTbObject(patientId);
  return tb.panels;
}

function getPanelSection(patientId, panelIndex) {
  const panels = getPatientPanels(patientId);
  const idx = Number(panelIndex || 0);
  while (panels.length <= idx) {
    panels.push(normalizePanelSection({ panel: null, billing: null, selected_tests: [] }));
  }
  return normalizePanelSection(panels[idx]);
}

function syncPrimaryPanelFields(tb) {
  const first = normalizePanelSection((tb.panels || [])[0] || {});
  tb.panel = first.panel || null;
  tb.billing = first.billing || null;
  tb.selected_tests = first.selected_tests || [];
}

function renderChargeModeControl(patientId, panelIndex, billing) {
  const pid = String(patientId || '');
  if (typeof panelIndex === 'object' && billing === undefined) {
    billing = panelIndex;
    panelIndex = 0;
  }
  const idx = Number(panelIndex || 0);
  const key = panelDomKey(pid, idx);
  if (!pid || !billing) return;

  const options = chargeModeOptions(billing.allowed_charge_mode_code || billing.charge_mode || billing.charge_mode_code || '');
  const $holder = $(`#tb-charge-mode-${key}`);
  if (!$holder.length) return;

  if (!options.length) {
    billing.selected_charge_mode = '';
    billing.charge_mode_code = '';
    $holder.text('-');
    return;
  }

  if (options.length === 1) {
    const only = options[0];
    billing.charge_mode_code = only;
    billing.selected_charge_mode = only;
    $holder.text(chargeModeLabel(only));
    return;
  }

  let selected = normalizeChargeModeCode(billing.selected_charge_mode || billing.charge_mode_code || billing.charge_mode || '');
  if (!options.includes(selected)) selected = options[0];
  billing.charge_mode_code = selected;
  billing.selected_charge_mode = selected;

  const html = `
    <select id="tb-charge-mode-select-${key}" class="form-select form-select-sm tb-charge-mode-select" data-patient-id="${escHtml(pid)}" data-panel-index="${idx}">
      ${options.map((m) => `<option value="${escHtml(m)}" ${m === selected ? 'selected' : ''}>${escHtml(chargeModeLabel(m))}</option>`).join('')}
    </select>
  `;
  $holder.html(html);
}

function renderWithTransition(target, html) {
  const $el = $(target);
  $el.removeClass('hc-anim-enter');
  $el.html(html);
  // force reflow so animation retriggers on repeated updates
  void $el[0].offsetWidth;
  $el.addClass('hc-anim-enter');
}

function setCallerInlineChip(caller, notFoundText = '') {
  if (!caller && !notFoundText) {
    $('#caller-inline-chip').addClass('d-none').html('');
    return;
  }
  if (notFoundText) {
    $('#caller-inline-chip')
      .removeClass('d-none')
      .html(`<span class="caller-pill caller-pill-warning">${notFoundText}</span>`);
    return;
  }
  $('#caller-inline-chip')
    .removeClass('d-none')
    .html(`<span class="caller-pill"><strong>${caller.full_name}</strong> <small>(${caller.caller_code})</small></span>`);
}

function getLocalPrescriptionUploadCount(patientId) {
  const pid = String(patientId || '');
  const map = wizardData.prescriptionUploads || {};
  const count = Number(map[pid] || 0);
  return Number.isFinite(count) && count > 0 ? count : 0;
}

function getStagedPrescriptionItems(patient) {
  const items = Array.isArray(patient?.staged_prescription_files) ? patient.staged_prescription_files : [];
  return items
    .map((item) => ({
      rel_name: String(item?.rel_name || '').trim(),
      label: String(item?.label || '').trim()
    }))
    .filter((item) => item.rel_name);
}

function renderReferenceAddressChip(r) {
  const area = escHtml(r.area || '-');
  const city = escHtml(r.city || '-');
  const pincode = escHtml(r.pincode || '-');
  const routename = escHtml(r.routename || '-');
  const address = escHtml(r.address || '-');
  return `
    <div class="reference-address-card" data-reference-address-id="${r.id}">
      <div class="reference-address-top">
        <div class="reference-address-title">${escHtml([r.area, r.city].filter(Boolean).join(', ') || 'Reference Address')}</div>
        <span class="reference-address-status">REF</span>
      </div>
      <div class="reference-address-meta">
        <div><strong>Area:</strong> ${area}</div>
        <div><strong>City:</strong> ${city}</div>
        <div><strong>Pin code:</strong> ${pincode}</div>
        <div><strong>Route name:</strong> ${routename}</div>
      </div>
      <div class="reference-address-line"><strong>Address:</strong> ${address}</div>
      <div class="reference-address-actions">
        <button type="button" class="btn btn-sm btn-outline-danger btn-finalize-reference-address" data-reference-address-id="${r.id}">
          Finalize & Remove
        </button>
      </div>
    </div>
  `;
}

function formatHistoryDate(isoDate) {
  const v = String(isoDate || '').trim();
  const m = v.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return v || '-';
  return `${m[3]}-${m[2]}-${m[1]}`;
}

function renderCallerHistoryPanel(history) {
  const $panel = $('#caller-history-panel');
  if (!$panel.length) return;

  const h = history && typeof history === 'object' ? history : null;
  const counts = h?.counts || {};
  const rows = Array.isArray(h?.last_bookings) ? h.last_bookings : [];

  if (!h) {
    $panel.html('<div class="hc-h-inline-empty text-muted small">Search mobile to view history.</div>');
    return;
  }

  const countsHtml = `
    <div class="hc-h-counts">
      <span class="hc-h-title">Caller History :</span>
      <span class="hc-h-count">Linked Patient: <strong>${Number(counts.linked_patients || 0)}</strong></span>
      <span class="hc-h-count">Total Booking: <strong>${Number(counts.total_bookings || 0)}</strong></span>
      <span class="hc-h-count">Delayed: <strong>${Number(counts.delayed_bookings || 0)}</strong></span>
      <span class="hc-h-count">Cancelled: <strong>${Number(counts.cancelled_bookings || 0)}</strong></span>
    </div>
  `;

  if (!rows.length) {
    $panel.html(`<div class="hc-h-inline">${countsHtml}</div>`);
    return;
  }

  const chipsHtml = rows.map((b, idx) => {
    const dt = escHtml(formatHistoryDate(b.preferred_visit_date));
    const status = escHtml(b.status_label || '-');
    const amount = escHtml(formatCharge(Number(b.total_amount || 0)));
    const latestCls = idx === 0 || b.is_latest ? 'is-latest' : '';
    return `
      <button
        type="button"
        class="hc-h-chip-btn hc-history-chip-trigger ${latestCls}"
        data-history-booking-id="${Number(b.booking_id || 0)}"
      >
        <span class="hc-h-chip-main">${dt}</span>
        <span class="hc-h-chip-sep">|</span>
        <span class="hc-h-chip-sub">${status}</span>
        <span class="hc-h-chip-sep">|</span>
        <span class="hc-h-chip-amt">${amount}</span>
      </button>
    `;
  }).join('');

  $panel.html(`<div class="hc-h-inline">${countsHtml}<div class="hc-h-list">${chipsHtml}</div><div id="hc-h-external-popup" class="hc-h-external-popup d-none"></div></div>`);
}

function bindCallerHistoryChipEvents() {
  $(document).off('click.hcHistoryChip', '.hc-history-chip-trigger').on('click.hcHistoryChip', '.hc-history-chip-trigger', function (e) {
    e.preventDefault();
    const $btn = $(this);
    const bid = Number($btn.data('history-booking-id') || 0);
    if (bid <= 0) return;
    const wasActive = $btn.hasClass('active');
    if (wasActive) {
      $btn.removeClass('active');
      $('#hc-h-external-popup').addClass('d-none').empty();
      return;
    }
    $('.hc-history-chip-trigger').removeClass('active');
    $btn.addClass('active');
    const $popup = $('#hc-h-external-popup');
    $popup.html('<div class="text-light small">Loading details...</div>').removeClass('d-none');

    $.get('/hhome-collection/caller-history-booking', { booking_id: bid })
      .done(function (res) {
        const b = res?.booking || {};
        const patients = Array.isArray(b.patients) ? b.patients : [];
        const patientsHtml = patients.length
          ? patients.map((p) => `
            <div class="hc-h-patient-card">
              <div class="hc-h-patient-name">${escHtml(p.full_name || '-')}</div>
              <div class="hc-h-patient-meta"><span>Gender:</span> <strong>${escHtml(p.gender || '-')}</strong></div>
              <div class="hc-h-patient-meta"><span>Age:</span> <strong>${escHtml(p.age || '-')}</strong></div>
              <div class="hc-h-patient-meta"><span>Tag:</span> <strong>${escHtml(p.tag || '-')}</strong></div>
              <div class="hc-h-patient-meta"><span>Panel Company:</span> <strong>${escHtml(p.panel_company || '-')}</strong></div>
              ${String(p.labmate_pid || '').trim() ? `<div class="hc-h-patient-meta"><span>Labmate PID:</span> <strong>${escHtml(p.labmate_pid)}</strong></div>` : ''}
              <div class="hc-h-patient-meta"><span>Contact Mobile:</span> <strong>${escHtml(p.contact_mobile || '-')}</strong></div>
            </div>`).join('')
          : '<div class="text-light small">No patient details.</div>';
        $popup.html(`
          <div class="hc-h-pop-slot">${escHtml(b.preferred_time_slot || '-')}</div>
          <div class="hc-h-patient-grid ${patients.length > 1 ? 'multi' : 'single'}">${patientsHtml}</div>
        `);
      })
      .fail(function (xhr) {
        $popup.html(`<div class="text-light small">${escHtml(xhr?.responseJSON?.message || 'Unable to load details')}</div>`);
      });
  });
}

function renderRightPanelState(patients, referenceAddresses, callerHistory) {
  const patientList = patients || [];
  const refList = referenceAddresses || [];
  if (callerHistory !== undefined) callerHistoryCache = callerHistory;
  linkedPatientsCache = patientList;
  referenceAddressesCache = refList;

  const linkedHtml = patientList.length
    ? patientList.map(p => `
        <div class="chip ${p.selected ? 'selected' : ''}" data-patient-id="${p.id}">
          <div class="linked-patient-main">
            <span><strong>${escHtml(p.full_name || '')}</strong> (${escHtml(p.age || '-')}) ${renderTagBadges(p.tag)}</span>
            ${p.can_inactivate ? `<button type="button" class="linked-patient-inactive-btn" data-patient-id="${p.id}">Inactive</button>` : ''}
          </div>
          <small>${escHtml(p.default_address || '')}</small>
        </div>
      `).join('')
    : '<div class="text-muted small">No linked patients yet.</div>';

  const refHtml = refList.length
    ? refList.map(renderReferenceAddressChip).join('')
    : '<div class="text-muted small">No reference addresses.</div>';

  $('#linked-patients-panel').html(linkedHtml);
  $('#reference-addresses-panel').html(refHtml);
  renderCallerHistoryPanel(callerHistoryCache);
}

function applyStep1Bundle(res) {
  renderRightPanelState(
    res.linked_patients || [],
    res.reference_addresses || [],
    Object.prototype.hasOwnProperty.call((res || {}), 'caller_history') ? (res.caller_history || null) : undefined
  );
  renderSelectedPatientsState(res.selected_patients || []);
  bindRemovePatientButtons();
  bindEditPatientButtons();
  renderAddressesState(res.addresses || [], res.selected_address_id || 0);
  bindUseAddressButtons();
  bindEditAddressButtons();
  bindFinalizeReferenceAddressButtons();
}

function handleCallerMergePrompt(res, afterMerge) {
  if (!res || !res.merge_required || !res.target_caller) return false;
  const ok = window.confirm(res.message || 'This mobile is already mapped with another caller. Do you want to merge the current caller into that caller?');
  if (!ok) return true;

  $.ajax({
    url: '/hhome-collection/merge-caller',
    method: 'POST',
    contentType: 'application/json',
    data: JSON.stringify({ target_caller_id: res.target_caller.id, conflict_mobile: res.conflict_mobile || '' }),
    success: function (mergeRes) {
      hasCallerContext = true;
      toggleStep1Workspace(true);
      if (mergeRes.caller) {
        setCallerInlineChip(mergeRes.caller);
      }
      if (typeof afterMerge === 'function') {
        afterMerge(mergeRes);
        return;
      }
      resetPatientFormState();
      resetAddressFormState();
      applyStep1Bundle(mergeRes || {});
      alert(mergeRes.message || 'Caller merged successfully.');
    },
    error: function (xhr) {
      alert(xhr.responseJSON?.message || 'Unable to merge caller');
    }
  });
  return true;
}

function bindRemovePatientButtons() {
  $('.rm-patient').off('click').on('click', function () {
    const patientId = $(this).data('patient-id');
    $.ajax({
      url: '/hhome-collection/remove-selected-patient',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ patient_id: patientId }),
      success: function (res) {
        applyStep1Bundle(res || {});
      }
    });
  });
}

function bindEditPatientButtons() {
  $('.btn-edit-patient').off('click').on('click', function () {
    const patientId = Number($(this).data('patient-id') || 0);
    if (!patientId) return;
    startEditPatient(patientId);
  });
}

function renderSelectedPatientsState(list) {
  const selectedList = list || [];
  selectedPatientsCache = Array.isArray(selectedList) ? selectedList.slice() : [];
  wizardData.selectedPatients = selectedPatientsCache.slice();
  if (!selectedList.length) {
    $('#selected-patient-tags').html('<div class="text-muted">No patients selected yet.</div>');
    return;
  }
  const formatDob = (raw) => {
    const s = String(raw || '').trim();
    if (!s) return '-';
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return s;
    return `${m[3]}-${m[2]}-${m[1]}`;
  };
  const html = selectedList.map(p => `
    <div class="selected-patient-card">
      <div class="selected-patient-card-top">
        <div class="selected-patient-card-name">${p.full_name} ${renderTagBadges(p.tag)}</div>
        <button class="rm-patient" data-patient-id="${p.patient_id}" title="Remove">x</button>
      </div>
      <div class="selected-patient-card-meta">
        <span><strong>Gender:</strong> ${p.gender || '-'}</span>
        <span><strong>Age/DOB:</strong> ${p.age || '-'} / ${formatDob(p.date_of_birth)}</span>
        <span><strong>Contact:</strong> ${p.contact_mobile || '-'}</span>
        <span><strong>Alt Mobile:</strong> ${p.alternate_mobile || '-'}</span>
        <span><strong>Labmate PID:</strong> ${p.labmate_pid || '-'}</span>
        <span><strong>Email:</strong> ${p.email || '-'}</span>
        <span><strong>Panel:</strong> ${p.panel_company || '-'}</span>
      </div>
      <div class="selected-patient-card-actions">
        <button class="btn btn-sm btn-outline-success btn-use-address btn-edit-patient" data-patient-id="${p.patient_id}" title="Edit">Edit</button>
      </div>
    </div>
  `).join('');
  $('#selected-patient-tags').html(html);
}

function bindUseAddressButtons() {
  $('#address-list .btn-use-address').off('click').on('click', function () {
    const addressId = $(this).data('address-id');
    if (!addressId) return;
    $.ajax({
      url: '/hhome-collection/select-address',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ address_id: addressId }),
      success: function (r) {
        slotSelectedRoute = (r?.snapshot?.route_no || '').trim() || slotSelectedRoute;
        $('#slot-selected-route').val(slotSelectedRoute);
        loadAddresses();
      }
    });
  });
}

function bindEditAddressButtons() {
  $('.btn-edit-address').off('click').on('click', function () {
    const addressId = Number($(this).data('address-id') || 0);
    if (!addressId) return;
    startEditAddress(addressId);
  });
}

function bindFinalizeReferenceAddressButtons() {
  $('#reference-addresses-panel .btn-finalize-reference-address').off('click').on('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    const referenceAddressId = Number($(this).data('reference-address-id') || 0);
    if (!referenceAddressId) return;
    if (!confirm('Are you sure you want to finalize and remove this reference address?')) return;
    $.ajax({
      url: `/hhome-collection/reference-address/${referenceAddressId}/finalize`,
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({}),
      success: function (res) {
        applyStep1Bundle(res || {});
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || 'Unable to finalize reference address');
      }
    });
  });
}

function renderAddressesState(addresses, selectedAddressId) {
  const list = addresses || [];
  if (!list.length) {
    renderWithTransition('#address-list', '<div class="text-muted">No addresses linked yet.</div>');
    return;
  }
  const selectedId = Number(selectedAddressId || 0);
  const html = list.map(a => `
    <div class="address-card ${selectedId === a.id ? 'selected-address' : ''}">
      <div><strong>${a.address_type}</strong></div>
      <div>${escHtml([a.house_flat_no, a.floor_display || a.floor || '', a.block_tower_no || '', a.street_sector || a.street_line || ''].filter(Boolean).join(', '))}</div>
      <div>${a.colony_name}, ${a.pincode} | ${a.route_no} | ${a.city}</div>
      <div class="address-card-actions">
        <button class="btn btn-sm btn-outline-success btn-use-address" data-address-id="${a.id}">Use This Address</button>
        <button class="btn btn-sm btn-outline-success btn-use-address btn-edit-address" data-address-id="${a.id}" title="Edit">Edit</button>
      </div>
    </div>
  `).join('');
  renderWithTransition('#address-list', html);
}

function bindStepEvents() {
  initAppointmentTagPickers();

  if (currentStep === 1) {
    renderPatientTitleOptions('');
    $('#btn-search-caller').off('click').on('click', function (e) {
      e.preventDefault();
      searchCaller();
    });
    $('#search-mobile').off('keydown.searchEnter').on('keydown.searchEnter', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        searchCaller();
      }
    });
    $('#btn-show-patient-form').off('click').on('click', function () {
      if (editingPatientId) {
        resetPatientFormState();
        return;
      }
      $('#new-patient-form').toggleClass('d-none');
      const opened = !$('#new-patient-form').hasClass('d-none');
      $(this).text(opened ? 'Cancel' : '+ Add New Patient');
      if (!opened) {
        resetPatientFormState();
      }
    });
    $('#btn-save-patient').off('click').on('click', savePatient);
    $('#btn-show-address-form').off('click').on('click', function () {
      if (editingAddressId) {
        resetAddressFormState();
        return;
      }
      $('#new-address-form').toggleClass('d-none');
      const opened = !$('#new-address-form').hasClass('d-none');
      $(this).text(opened ? 'Cancel' : '+ Add New Address');
      if (!opened) {
        resetAddressFormState();
      }
    });
    $('#btn-save-address').off('click').on('click', saveAddress);
    $('#btn-go-step2').off('click').on('click', goStep2);
    $('#p-dob').off('change').on('change', autoFillAgeFromDob);
    $('#p-age-years').off('input change').on('input change', autoDobFromAge);
    $('#p-title').off('change').on('change', autoGenderByTitle);
    $('#btn-patient-documents').off('click').on('click', function () {
      $('#p-patient-documents').trigger('click');
    });
    $('#p-patient-documents').off('change').on('change', function () {
      const files = Array.from(this.files || []);
      if (files.length > 5) {
        alert('Maximum 5 patient documents per patient allowed.');
        $(this).val('');
        renderPatientDocumentSelection([]);
        return;
      }
      renderPatientDocumentSelection(files);
    });
    $('#a-floor-special').off('change').on('change', function () {
      syncFloorFieldState();
      if (!$(this).val()) {
        $('#a-floor').trigger('input');
      }
    });
    $('#a-floor').off('input').on('input', function () {
      const cleaned = String($(this).val() || '').replace(/[^\d]/g, '');
      if (cleaned !== $(this).val()) {
        $(this).val(cleaned);
      }
      if (cleaned) {
        $('#a-floor-special').val('');
      }
      syncFloorFieldState();
      validateFloorField();
    });
    $('#a-colony-manual').off('change').on('change', function () {
      if ($(this).is(':checked')) {
        $('#a-colony').val('').trigger('change');
        $('#a-pincode').val('');
        $('#a-route').val('');
      } else {
        $('#a-colony-free').val('');
      }
      syncAddressColonyMode();
      if (isAddressColonyManualMode()) {
        updateRouteFromManualPincode();
      }
    });
    $('#a-pincode').off('input.manualPincode').on('input.manualPincode', function () {
      if (!isAddressColonyManualMode()) return;
      updateRouteFromManualPincode();
    });
    initPatientTagPicker();
  wirePatientPanelSuggest();
    $(document).off('change', '#a-city').on('change', '#a-city', function () {
      $('#a-pincode').val('');
      $('#a-route').val('');
      loadColonies(true);
    });
    if ($('#a-city').val()) {
      loadColonies(false);
    }
    resetAddressFormState();
    resetPatientFormState();
    hydrateStep1FromSession();
  }

  if (currentStep === 2) {
    const today = isoToday();
    $('#b-date').attr('min', today);
    $('#slot-grid-date').attr('min', today);
    wireAppointmentReferredSuggest();
    wireInternalRefSuggest();
    $('#btn-back-step1').off('click').on('click', () => setStep(1));
    $('#btn-go-step3').off('click').on('click', goStep3);
    $('#btn-open-slots').off('click').on('click', function (e) {
      e.preventDefault();
      openSlotPlanner();
    });
    $('#slot-grid-date').off('change').on('change', function () {
      loadRouteSlotGrid();
    });
  }

  if (currentStep === 3) {
    $('#btn-back-step2').off('click').on('click', () => setStep(2));
    $('#btn-go-step4').off('click').on('click', goStep4);
    bindPanelBillingEvents();
    $('#btn-apply-panel-tests').off('click').on('click', applySelectedPanelTests);
  }

  if (currentStep === 4) {
    $('#btn-back-step3').off('click').on('click', () => setStep(3));
    $('#btn-confirm-booking').off('click').on('click', confirmBooking);
  }

  $('#linked-patients-panel').off('click', '.chip').on('click', '.chip', function () {
    const patientId = $(this).data('patient-id');
    const selected = linkedPatientsCache.find(x => x.id === patientId || x.id === Number(patientId));
    if (selected && selected.selected) {
      $.ajax({
        url: '/hhome-collection/remove-selected-patient',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ patient_id: patientId }),
        success: function (res) {
          applyStep1Bundle(res || {});
        }
      });
      return;
    }

    $.ajax({
      url: '/hhome-collection/select-patient',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ patient_id: patientId }),
      success: function (res) {
        applyStep1Bundle(res || {});
        if (currentStep === 1) {
          hasCallerContext = true;
          toggleStep1Workspace(true);
        }
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || 'Unable to select patient');
      }
    });
  });

  $('#linked-patients-panel').off('click', '.linked-patient-inactive-btn').on('click', '.linked-patient-inactive-btn', function (e) {
    e.preventDefault();
    e.stopPropagation();
    const patientId = Number($(this).data('patient-id') || 0);
    if (!patientId) return;
    if (!window.confirm('Are you sure you want to mark this patient as inactive?')) return;
    $.ajax({
      url: `/hhome-collection/patient/${encodeURIComponent(patientId)}/inactive`,
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({}),
      success: function (res) {
        applyStep1Bundle(res || {});
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || 'Unable to mark patient inactive');
      }
    });
  });
}

function renderTagBadges(tagCsv) {
  if (!tagCsv) return '';
  return tagCsv
    .split(',')
    .map(x => x.trim())
    .filter(Boolean)
    .map(t => `<span class="patient-tag-badge">${t}</span>`)
    .join('');
}

function getPatientContactPrefill() {
  const fromSearchState = String(wizardData.searchedMobile || '').trim();
  if (fromSearchState) return fromSearchState;
  return String($('#search-mobile').val() || '').trim();
}

function selectedPatientsContactError(selectedPatients) {
  const missing = (selectedPatients || []).filter((p) => !String(p.contact_mobile || '').trim());
  if (!missing.length) return '';
  const names = missing.slice(0, 5).map((p) => p.full_name || `Patient ${p.patient_id || ''}`.trim()).join(', ');
  const more = missing.length > 5 ? ` +${missing.length - 5} more` : '';
  return `Contact No is required for selected patient: ${names}${more}`;
}

function ensureStep1Ready(done) {
  $.get('/hhome-collection/selected-patients', function (pRes) {
    const selected = pRes.selected_patients || [];
    if (!selected.length) {
      alert('Select at least one patient.');
      return;
    }
    const contactError = selectedPatientsContactError(selected);
    if (contactError) {
      alert(contactError);
      return;
    }
    $.get('/hhome-collection/addresses', { _ts: Date.now() }, function (aRes) {
      if (!aRes.selected_address_id) {
        alert('Select at least one address (Use This Address).');
        return;
      }
      done(aRes);
    });
  });
}

function resetPatientFormState() {
  editingPatientId = null;
  $('#new-patient-form').addClass('d-none');
  $('#btn-save-patient').text('Save Patient');
  $('#btn-show-patient-form').text('+ Add New Patient');
  renderPatientTitleOptions('');
  $('#p-full-name').val('');
  $('#p-labmate-pid').val('');
  $('#p-panel-company').val('');
  $('#p-card-number').val('');
  $('#p-patient-documents').val('');
  $('#p-patient-documents-list').text('0 selected');
  $('#p-panel-company-suggest').addClass('d-none').html('');
  $('#p-gender').val('Male');
  $('#p-dob').val('');
  $('#p-age-years').val('');
  $('#p-contact-mobile').val(getPatientContactPrefill());
  $('#p-alternate-mobile').val('');
  $('#p-email').val('');
  selectedPatientTags = [];
  $('#patient-tag-select').val('');
  renderSelectedTags($('#patient-tag-picker'), selectedPatientTags, 'patient-tag-remove');
}

function renderPatientDocumentSelection(files, existingDocs = []) {
  const selectedCount = Array.from(files || []).length;
  const existingCount = (existingDocs || []).filter(Boolean).length;
  const parts = [];
  if (existingCount) parts.push(`${existingCount} uploaded`);
  parts.push(`${selectedCount} selected`);
  $('#p-patient-documents-list').text(parts.join(', '));
}

function resetAddressFormState() {
  editingAddressId = null;
  $('#new-address-form').addClass('d-none');
  $('#btn-save-address').text('Save Address');
  $('#btn-show-address-form').text('+ Add New Address');
  $('#a-type').val('Home');
  $('#a-house').val('');
  $('#a-floor').val('');
  $('#a-floor-special').val('');
  $('#a-city').val('');
  $('#a-colony').val('').trigger('change');
  $('#a-colony-free').val('').addClass('d-none');
  $('#a-colony-manual').prop('checked', false);
  $('#a-pincode').val('');
  $('#a-route').val('');
  $('#a-block').val('');
  $('#a-street').val('');
  $('#a-landmark').val('');
  $('#a-google-location').val('');
  $('#a-access').val('');
  syncFloorFieldState();
  syncAddressColonyMode();
}

function isAddressColonyManualMode() {
  return $('#a-colony-manual').is(':checked');
}

function sanitizePincodeInput(raw) {
  return String(raw || '').replace(/[^\d]/g, '').slice(0, 6);
}

function syncAddressColonyMode() {
  const manual = isAddressColonyManualMode();
  const $colony = $('#a-colony');
  const $colonyFree = $('#a-colony-free');
  const $pincode = $('#a-pincode');
  const $route = $('#a-route');

  if (manual) {
    $colony.prop('required', false).closest('.select2-container').hide();
    if ($colony.hasClass('select2-hidden-accessible')) {
      $colony.next('.select2').addClass('d-none');
    }
    $colony.addClass('d-none');
    $colonyFree.removeClass('d-none').prop('required', true);
    $pincode.prop('readonly', false).attr('maxlength', '6').attr('inputmode', 'numeric');
    $route.prop('readonly', true);
  } else {
    $colony.removeClass('d-none').prop('required', true);
    if ($colony.hasClass('select2-hidden-accessible')) {
      $colony.next('.select2').removeClass('d-none');
    }
    $colonyFree.addClass('d-none').prop('required', false);
    $pincode.prop('readonly', true).removeAttr('maxlength').removeAttr('inputmode');
  }
}

function updateRouteFromManualPincode() {
  if (!isAddressColonyManualMode()) return;
  const city = String($('#a-city').val() || '').trim().toLowerCase();
  const pincode = sanitizePincodeInput($('#a-pincode').val());
  $('#a-pincode').val(pincode);
  if (pincode.length !== 6) {
    $('#a-route').val('');
    return;
  }
  const mapped = (addressColonyCatalog || []).find((c) => {
    const cCity = String(c.city || '').trim().toLowerCase();
    const cPin = String(c.pincode || '').trim();
    return cCity === city && cPin === pincode && String(c.route_no || '').trim();
  });
  $('#a-route').val(mapped ? String(mapped.route_no || '').trim() : '');
}

function clearFloorValidity() {
  const el = document.getElementById('a-floor');
  if (el && typeof el.setCustomValidity === 'function') {
    el.setCustomValidity('');
  }
}

function floorIsFullHouse() {
  return String($('#a-floor-special').val() || '').trim().length > 0;
}

function syncFloorFieldState() {
  const checked = floorIsFullHouse();
  const $floor = $('#a-floor');
  $floor.prop('disabled', checked);
  if (checked) {
    $floor.val('');
  }
  clearFloorValidity();
}

function validateFloorField() {
  const $floor = $('#a-floor');
  const el = $floor[0];
  if (!el) return true;

  if (floorIsFullHouse()) {
    clearFloorValidity();
    return true;
  }

  const raw = String($floor.val() || '').trim();
  if (!raw) {
    el.setCustomValidity('Enter a floor number from 1 to 99 or select one floor option.');
    return false;
  }
  if (!/^\d{1,2}$/.test(raw)) {
    el.setCustomValidity('Floor must be a number from 1 to 99.');
    return false;
  }
  const num = Number(raw);
  if (!Number.isInteger(num) || num < 1 || num > 99) {
    el.setCustomValidity('Floor must be a number from 1 to 99.');
    return false;
  }
  clearFloorValidity();
  return true;
}

function getAddressPayload() {
  const selectedSpecial = String($('#a-floor-special').val() || '').trim();
  const floorChecked = selectedSpecial.length > 0;
  const floorValue = String($('#a-floor').val() || '').trim();
  const manualColony = isAddressColonyManualMode();
  const manualColonyName = String($('#a-colony-free').val() || '').trim();
  return {
    address_type: $('#a-type').val(),
    house_flat_no: $('#a-house').val().trim(),
    full_house: floorChecked,
    floor: floorChecked ? selectedSpecial : floorValue,
    block_tower_no: $('#a-block').val().trim(),
    street_sector: $('#a-street').val().trim(),
    landmark: $('#a-landmark').val().trim(),
    city: $('#a-city').val(),
    colony_id: manualColony ? '' : $('#a-colony').val(),
    colony_not_found: manualColony,
    colony_name: manualColony ? manualColonyName : '',
    pincode: sanitizePincodeInput($('#a-pincode').val().trim()),
    route: $('#a-route').val().trim(),
    google_location: $('#a-google-location').val().trim(),
    access_notes: $('#a-access').val().trim()
  };
}

function normalizeTagList(values) {
  const seen = new Set();
  const out = [];
  (values || []).forEach((raw) => {
    const txt = String(raw || '').trim();
    if (!txt) return;
    const key = txt.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(txt);
  });
  return out;
}

function displayTagLabel(tag) {
  return String(tag || '').replace(/\s*\(/g, ' (').trim();
}

function renderSelectedTags($container, tags, removeClass) {
  const list = normalizeTagList(tags);
  if (!list.length) {
    $container.html('<div class="text-muted small">No tags selected.</div>');
    return;
  }
  const html = list.map((tag) => `
    <span class="patient-tag-chip active">
      ${escHtml(displayTagLabel(tag))}
      <button type="button" class="tag-chip-remove ${removeClass}" data-tag="${escHtml(tag)}" aria-label="Remove">&times;</button>
    </span>
  `).join('');
  $container.html(html);
}

function buildTagSelectOptions(typeKey, selected) {
  const list = normalizeTagList(tagOptions?.[typeKey] || []);
  const selectedSet = new Set((selected || []).map((x) => String(x || '').trim().toLowerCase()));
  const rows = ['<option value="">Select</option>'];
  const vvipKey = 'vvip(top priority)';
  let insertedSpecialAfterVvip = false;
  list.forEach((tag) => {
    const key = String(tag || '').toLowerCase();
    if (!selectedSet.has(key)) {
      rows.push(`<option value="${escHtml(tag)}">${escHtml(displayTagLabel(tag))}</option>`);
      if (typeKey === 'permanent' && key === vvipKey) {
        rows.push('<option value="__pick_preferred_phlebo__">Preferred Phlebo</option>');
        rows.push('<option value="__pick_avoid_phlebo__">Avoid Phlebo</option>');
        insertedSpecialAfterVvip = true;
      }
    }
  });
  if (typeKey === 'permanent' && !insertedSpecialAfterVvip) {
    rows.push('<option value="__pick_preferred_phlebo__">Preferred Phlebo</option>');
    rows.push('<option value="__pick_avoid_phlebo__">Avoid Phlebo</option>');
  }
  return rows.join('');
}

function refreshTagDropdowns() {
  const $patient = $('#patient-tag-select');
  if ($patient.length) {
    $patient.html(buildTagSelectOptions('patient', selectedPatientTags)).val('');
  }
  const $permanent = $('#ap-permanent-tag-select');
  if ($permanent.length) {
    $permanent.html(buildTagSelectOptions('permanent', selectedPermanentTags)).val('');
  }
  const $booking = $('#ap-booking-tag-select');
  if ($booking.length) {
    $booking.html(buildTagSelectOptions('transactional', selectedBookingTags)).val('');
  }
}

function initPatientTagPicker() {
  renderSelectedTags($('#patient-tag-picker'), selectedPatientTags, 'patient-tag-remove');
  refreshTagDropdowns();

  $('#patient-tag-select').off('change').on('change', function () {
    const tag = String($(this).val() || '').trim();
    if (!tag) return;
    selectedPatientTags = normalizeTagList([...(selectedPatientTags || []), tag]);
    renderSelectedTags($('#patient-tag-picker'), selectedPatientTags, 'patient-tag-remove');
    refreshTagDropdowns();
  });

  $('#patient-tag-picker').off('click', '.patient-tag-remove').on('click', '.patient-tag-remove', function () {
    const tag = String($(this).data('tag') || '').trim().toLowerCase();
    selectedPatientTags = (selectedPatientTags || []).filter((x) => String(x || '').trim().toLowerCase() !== tag);
    renderSelectedTags($('#patient-tag-picker'), selectedPatientTags, 'patient-tag-remove');
    refreshTagDropdowns();
  });
}

function initAppointmentTagPickers() {
  renderSelectedTags($('#ap-permanent-tags'), selectedPermanentTags, 'ap-permanent-tag-remove');
  renderSelectedTags($('#ap-booking-tags'), selectedBookingTags, 'ap-booking-tag-remove');
  refreshTagDropdowns();

  $('#ap-permanent-tag-select').off('change').on('change', function () {
    const tag = String($(this).val() || '').trim();
    if (!tag) return;
    if (tag === '__pick_preferred_phlebo__' || tag === '__pick_avoid_phlebo__') {
      $(this).val('');
      openPhleboTagModal(tag);
      return;
    }
    selectedPermanentTags = normalizeTagList([...(selectedPermanentTags || []), tag]);
    renderSelectedTags($('#ap-permanent-tags'), selectedPermanentTags, 'ap-permanent-tag-remove');
    refreshTagDropdowns();
  });

  $('#ap-booking-tag-select').off('change').on('change', function () {
    const tag = String($(this).val() || '').trim();
    if (!tag) return;
    selectedBookingTags = normalizeTagList([...(selectedBookingTags || []), tag]);
    renderSelectedTags($('#ap-booking-tags'), selectedBookingTags, 'ap-booking-tag-remove');
    refreshTagDropdowns();
  });

  $('#ap-permanent-tags').off('click', '.ap-permanent-tag-remove').on('click', '.ap-permanent-tag-remove', function () {
    const tag = String($(this).data('tag') || '').trim().toLowerCase();
    selectedPermanentTags = (selectedPermanentTags || []).filter((x) => String(x || '').trim().toLowerCase() !== tag);
    renderSelectedTags($('#ap-permanent-tags'), selectedPermanentTags, 'ap-permanent-tag-remove');
    refreshTagDropdowns();
  });

  $('#ap-booking-tags').off('click', '.ap-booking-tag-remove').on('click', '.ap-booking-tag-remove', function () {
    const tag = String($(this).data('tag') || '').trim().toLowerCase();
    selectedBookingTags = (selectedBookingTags || []).filter((x) => String(x || '').trim().toLowerCase() !== tag);
    renderSelectedTags($('#ap-booking-tags'), selectedBookingTags, 'ap-booking-tag-remove');
    refreshTagDropdowns();
  });
}

function openPhleboTagModal(mode) {
  phleboTagMode = mode === '__pick_avoid_phlebo__' ? 'avoid' : 'preferred';
  phleboTagSelected = null;
  $('#phleboTagModalTitle').text(phleboTagMode === 'avoid' ? 'Select Avoid Phlebo' : 'Select Preferred Phlebo');
  $('#phlebo-tag-chip-list').html('<div class="text-muted">Loading phlebos...</div>');
  $('#phlebo-tag-apply-btn').prop('disabled', true);

  if (!phleboTagModal) {
    const el = document.getElementById('phleboTagModal');
    if (el) phleboTagModal = new bootstrap.Modal(el);
  }
  if (phleboTagModal) phleboTagModal.show();

  $.get('/hhome-collection/phlebotomists', function (res) {
    const list = (res?.phlebotomists || []).map((x) => ({
      id: Number(x.id || 0),
      full_name: String(x.full_name || x.name || '').trim()
    })).filter((x) => x.id > 0 && x.full_name);

    const html = (typeof window.renderHcAssignAlphaChipGroups === 'function')
      ? window.renderHcAssignAlphaChipGroups(list, { selectedUserId: 0, assignedSet: new Set() })
      : list.map((x) => `<button type="button" class="assign-chip" data-user-id="${x.id}">${escHtml(x.full_name)}</button>`).join('');
    $('#phlebo-tag-chip-list').html(html || '<div class="text-muted">No phlebo found.</div>');
  }).fail(function () {
    $('#phlebo-tag-chip-list').html('<div class="text-danger">Unable to load phlebo list.</div>');
  });
}

$(document).off('click.phleboTag', '#phlebo-tag-chip-list .assign-chip').on('click.phleboTag', '#phlebo-tag-chip-list .assign-chip', function () {
  const $chip = $(this);
  $('#phlebo-tag-chip-list .assign-chip').removeClass('active');
  $chip.addClass('active');
  phleboTagSelected = {
    id: Number($chip.data('user-id') || 0),
    name: String($chip.text() || '').trim()
  };
  $('#phlebo-tag-apply-btn').prop('disabled', !(phleboTagSelected.id > 0 && phleboTagSelected.name));
});

$(document).off('click.phleboTagApply', '#phlebo-tag-apply-btn').on('click.phleboTagApply', '#phlebo-tag-apply-btn', function () {
  if (!phleboTagSelected || !phleboTagSelected.name) return;
  const label = phleboTagMode === 'avoid'
    ? `avoid ${phleboTagSelected.name} phlebo`
    : `prefered ${phleboTagSelected.name} phlebo`;
  selectedPermanentTags = normalizeTagList([...(selectedPermanentTags || []), label]);
  renderSelectedTags($('#ap-permanent-tags'), selectedPermanentTags, 'ap-permanent-tag-remove');
  refreshTagDropdowns();
  if (phleboTagModal) phleboTagModal.hide();
});

function autoFillAgeFromDob() {
  const dobStr = $('#p-dob').val();
  if (!dobStr) return;
  const dob = new Date(dobStr + 'T00:00:00');
  if (Number.isNaN(dob.getTime())) return;
  const today = new Date();
  let age = today.getFullYear() - dob.getFullYear();
  const m = today.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) {
    age -= 1;
  }
  if (age < 0) age = 0;
  $('#p-age-years').val(age);
}

function autoGenderByTitle() {
  const title = ($('#p-title').val() || '').trim();
  const gender = titleGenderMap[title];
  if (gender) {
    $('#p-gender').val(gender);
  }
}

function autoDobFromAge() {
  const raw = ($('#p-age-years').val() || '').trim();
  if (!raw) return;
  const age = parseInt(raw, 10);
  if (Number.isNaN(age) || age < 0) return;
  const year = new Date().getFullYear() - age;
  const dob = `${year}-01-01`;
  $('#p-dob').val(dob);
}

function isAppointmentLevelFlow() {
  const flow = String(wizardData?.modify?.flow_type || '').trim().toLowerCase();
  return flow === 'modify_appointment' || flow === 'followup_appointment';
}

function isModifyContextActive() {
  return Number(wizardData?.modify?.booking_id || 0) > 0;
}

function applyPatientAddRules() {
  const isAppointmentFlow = isAppointmentLevelFlow();
  const $btn = $('#btn-show-patient-form');
  if (!$btn.length) return;

  $btn.prop('disabled', isAppointmentFlow);
  if (isAppointmentFlow) {
    $('#new-patient-form').addClass('d-none');
    if (!editingPatientId) {
      $btn.text('+ Add New Patient');
    }
  }
}

function toggleStep1Workspace(enabled) {
  const note = enabled
    ? '<span class="text-success">Caller selected. Add/select patient and address.</span>'
    : 'Search number first. If not found, save patient with contact details and caller will auto-create.';
  $('#step1-workspace-note').html(note);
}

function setStep1SearchLock(isLocked) {
  const lock = Boolean(isLocked);
  $('#search-mobile').prop('readonly', lock);
  $('#btn-search-caller').prop('disabled', lock);
}

function applyTagOptionsFromContext(ctxRes) {
  const opts = ctxRes?.tag_options || {};
  tagOptions = {
    patient: normalizeTagList(opts.patient || []),
    permanent: normalizeTagList(opts.permanent || []),
    transactional: normalizeTagList(opts.transactional || [])
  };
  refreshTagDropdowns();
}

function hydrateStep1FromSession() {
  $.get('/hhome-collection/modify-context', function (ctxRes) {
    applyTagOptionsFromContext(ctxRes);
    const hasModifyContext = Boolean(ctxRes?.ok && ctxRes?.active && ctxRes?.context);
    setStep1SearchLock(hasModifyContext);

    if (ctxRes?.ok && ctxRes?.active && ctxRes?.context) {
      const ctx = ctxRes.context || {};
      const existingModify = wizardData.modify || {};
      const sameModifySession =
        Number(existingModify.booking_id || 0) === Number(ctx.booking_id || 0) &&
        Number(existingModify.appointment_id || 0) === Number(ctx.appointment_id || 0);
      const hasLocalTestsState = Object.keys(wizardData.testsBilling || {}).length > 0;
      wizardData.modify = {
        booking_id: Number(ctx.booking_id || 0),
        appointment_id: Number(ctx.appointment_id || 0),
        flow_type: String(ctx.flow_type || '').trim().toLowerCase(),
        reason_text: ctx.reason_text || ''
      };
      wizardData.appointment = ctx.appointment || {};
      selectedPermanentTags = normalizeTagList(String(wizardData.appointment.permanent_tags || '').split(','));
      selectedBookingTags = normalizeTagList(String(wizardData.appointment.booking_tags || '').split(','));
      renderSelectedTags($('#ap-permanent-tags'), selectedPermanentTags, 'ap-permanent-tag-remove');
      renderSelectedTags($('#ap-booking-tags'), selectedBookingTags, 'ap-booking-tag-remove');
      refreshTagDropdowns();
      const shouldHydrateTestsFromContext = !(sameModifySession && hasLocalTestsState);
      wizardData.modify.original_tests_billing_map = deepClone(ctx.tests_billing_map || {});
      if (shouldHydrateTestsFromContext) {
        wizardData.testsBilling = ctx.tests_billing_map || {};
        wizardData.modify.pending_tests_map = ctx.pending_tests_map || {};
        wizardData.modify.parent_context_map = ctx.parent_context_map || {};
        if (wizardData.modify.flow_type === 'auto_followup_pending_child') {
          wizardData.modify.parent_seed_tests_map = buildZeroSeedTestsMap(ctx.tests_billing_map || {});
        }
      }
      wizardData.searchedMobile = ctx.searched_mobile || wizardData.searchedMobile;
    } else {
      wizardData.modify = {};
    }
    applyPatientAddRules();

    $.get('/hhome-collection/current-caller', function (res) {
      const caller = res?.caller || null;
      if (!caller) {
        hasCallerContext = false;
        toggleStep1Workspace(false);
        if (wizardData.searchedMobile) $('#search-mobile').val(wizardData.searchedMobile);
        renderCallerHistoryPanel(res?.caller_history || null);
        return;
      }

      hasCallerContext = true;
      toggleStep1Workspace(true);
      setCallerInlineChip(caller);
      renderCallerHistoryPanel(res?.caller_history || null);
      if (caller.primary_mobile) $('#search-mobile').val(caller.primary_mobile);

      $.get('/hhome-collection/linked-patients', function (lp) {
        renderRightPanelState(lp?.patients || [], lp?.reference_addresses || []);
      });
      $.get('/hhome-collection/selected-patients', function (sp) {
        renderSelectedPatientsState(sp?.selected_patients || []);
        bindRemovePatientButtons();
        bindEditPatientButtons();
      });
      loadAddresses();
    });
  });
}

function searchCaller() {
  if (Number(wizardData?.modify?.booking_id || 0) > 0) return;
  const mobile = $('#search-mobile').val().trim();
  if (!mobile) return alert('Enter mobile number');
  wizardData.searchedMobile = mobile;

  $.ajax({
    url: '/hhome-collection/search-caller',
    method: 'POST',
    contentType: 'application/json',
    data: JSON.stringify({ mobile }),
    success: function (res) {
      if (res.found) {
        hasCallerContext = true;
        toggleStep1Workspace(true);
        setCallerInlineChip(res.caller);
        $('#search-result').html('');
        renderSelectedPatientsState(res.selected_patients || []);
        bindRemovePatientButtons();
        bindEditPatientButtons();
        renderAddressesState(res.addresses || [], res.selected_address_id || 0);
        bindUseAddressButtons();
        bindEditAddressButtons();
        renderRightPanelState(res.linked_patients || [], res.reference_addresses || [], res.caller_history || null);
        bindFinalizeReferenceAddressButtons();
        resetPatientFormState();
        resetAddressFormState();
      } else {
        hasCallerContext = false;
        setCallerInlineChip(null, 'Caller not found...');
        $('#search-result').html('');
        toggleStep1Workspace(false);
        renderSelectedPatientsState([]);
        renderAddressesState([], 0);
        renderRightPanelState([], [], res.caller_history || null);
        resetPatientFormState();
        resetAddressFormState();
        $('#p-contact-mobile').val(res.mobile || mobile);
      }
    },
    error: function (xhr) {
      setCallerInlineChip(null);
      showAlert('#search-result', 'danger', xhr.responseJSON?.message || 'Search failed');
    }
  });
}

function startEditPatient(patientId) {
  $.get(`/hhome-collection/patient/${patientId}`, function (res) {
    const p = res?.patient || {};
    editingPatientId = Number(p.id || patientId);
    $('#new-patient-form').removeClass('d-none');
    $('#btn-save-patient').text('Update Patient');
    $('#btn-show-patient-form').text('Cancel Edit');

    renderPatientTitleOptions(p.title || '');
    $('#p-full-name').val(p.full_name || '');
    $('#p-labmate-pid').val(p.labmate_pid || '');
    $('#p-panel-company').val(p.panel_company || '');
    $('#p-card-number').val(p.card_number || '');
    $('#p-patient-documents').val('');
    renderPatientDocumentSelection([], p.patient_documents || []);
    $('#p-gender').val(p.gender || 'Male');
    $('#p-dob').val(p.date_of_birth || '');
    $('#p-age-years').val(p.age_years || '');
    $('#p-contact-mobile').val(p.contact_mobile || '');
    $('#p-alternate-mobile').val(p.alternate_mobile || '');
    $('#p-email').val(p.email || '');

    selectedPatientTags = normalizeTagList(String(p.tag || '').split(','));
    initPatientTagPicker();
  }).fail(function (xhr) {
    alert(xhr.responseJSON?.message || 'Unable to load patient details');
  });
}

function startEditAddress(addressId) {
  $.get(`/hhome-collection/address/${addressId}`, function (res) {
    const a = res?.address || {};
    editingAddressId = Number(a.id || addressId);
    $('#new-address-form').removeClass('d-none');
    $('#btn-save-address').text('Update Address');
    $('#btn-show-address-form').text('Cancel Edit');

    $('#a-type').val(a.address_type || 'Home');
    $('#a-house').val(a.house_flat_no || '');
    const floorValue = String(a.floor_display || a.floor || '').trim();
    const specialOptions = ['Ground_F', 'Basement', 'Upper_F', 'Full_hous'];
    if (specialOptions.includes(floorValue)) {
      $('#a-floor-special').val(floorValue);
      $('#a-floor').val('');
    } else {
      $('#a-floor-special').val('');
      $('#a-floor').val(floorValue);
    }
    $('#a-city').val(a.city || '');
    $('#a-block').val(a.block_tower_no || '');
    $('#a-street').val(a.street_sector || '');
    $('#a-landmark').val(a.landmark || '');
    $('#a-google-location').val(a.google_location || '');
    $('#a-access').val(a.access_notes || '');
    $('#a-pincode').val(a.pincode || '');
    $('#a-route').val(a.route_no || '');
    $('#a-colony-free').val(a.colony_name || '');

    syncFloorFieldState();
    const manual = !(Number(a.colony_id || 0) > 0);
    $('#a-colony-manual').prop('checked', manual);
    syncAddressColonyMode();
    loadColonies(false, String(a.colony_id || ''));
  }).fail(function (xhr) {
    alert(xhr.responseJSON?.message || 'Unable to load address details');
  });
}


function wirePatientPanelSuggest() {
  const $input = $('#p-panel-company');
  if (!$input.length) return;

  let $suggest = $('#p-panel-company-suggest');
  if (!$suggest.length) {
    $input.after('<div id="p-panel-company-suggest" class="tb-panel-suggest d-none"></div>');
    $suggest = $('#p-panel-company-suggest');
  }

  $input.off('input.patientPanel').on('input.patientPanel', function () {
    const q = String($(this).val() || '').trim();
    if (q.length < 2) {
      $suggest.addClass('d-none').html('');
      return;
    }
    $.get('/hhome-collection/panel-companies', { q, limit: 20, atype: 'C' }, function (res) {
      const items = res?.items || [];
      if (!items.length) {
        $suggest.html('<div class="tb-panel-item">No panel found</div>').removeClass('d-none');
        return;
      }
      const html = items.map(x => `
        <div class="tb-panel-item" data-pname="${escHtml(x.pname || '')}">
          <strong>${escHtml(x.pname || '')}</strong>
          <span class="meta">CenterID: ${escHtml(x.CenterID || '')}</span>
        </div>
      `).join('');
      $suggest.html(html).removeClass('d-none');
    });
  });

  $suggest.off('click.patientPanel', '.tb-panel-item').on('click.patientPanel', '.tb-panel-item', function () {
    const pname = String($(this).data('pname') || '').trim();
    if (pname) $input.val(pname);
    $suggest.addClass('d-none').html('');
  });

  $(document).off('click.patientPanel').on('click.patientPanel', function (e) {
    if (!$(e.target).closest('#p-panel-company, #p-panel-company-suggest').length) {
      $suggest.addClass('d-none').html('');
    }
  });
}

function wireAppointmentReferredSuggest() {
  const $input = $('#ap-referred-by');
  const $suggest = $('#ap-referred-by-suggest');
  if (!$input.length || !$suggest.length) return;

  $input.off('input.appReferred').on('input.appReferred', function () {
    const q = String($(this).val() || '').trim();
    if (q.length < 2) {
      $suggest.addClass('d-none').html('');
      return;
    }
    // Referred-by should include both C and D (no atype filter).
    $.get('/hhome-collection/panel-companies', { q, limit: 20 }, function (res) {
      const items = res?.items || [];
      if (!items.length) {
        $suggest.html('<div class="tb-panel-item">No panel found</div>').removeClass('d-none');
        return;
      }
      const html = items.map(x => `
        <div class="tb-panel-item" data-pname="${escHtml(x.pname || '')}">
          <strong>${escHtml(x.pname || '')}</strong>
          <span class="meta">CenterID: ${escHtml(x.CenterID || '')}</span>
        </div>
      `).join('');
      $suggest.html(html).removeClass('d-none');
    });
  });

  $suggest.off('click.appReferred', '.tb-panel-item').on('click.appReferred', '.tb-panel-item', function () {
    const pname = String($(this).data('pname') || '').trim();
    if (pname) $input.val(pname);
    $suggest.addClass('d-none').html('');
  });

  $(document).off('click.appReferred').on('click.appReferred', function (e) {
    if (!$(e.target).closest('#ap-referred-by, #ap-referred-by-suggest').length) {
      $suggest.addClass('d-none').html('');
    }
  });
}

function wireInternalRefSuggest() {
  const $input = $("#ap-internal-ref");
  const $suggest = $("#ap-internal-ref-suggest");
  if (!$input.length || !$suggest.length) return;

  $input.off("input.appInternalRef").on("input.appInternalRef", function () {
    const q = String($(this).val() || "").trim();
    if (q.length < 2) {
      $suggest.addClass("d-none").html("");
      return;
    }
    $.get('/hhome-collection/internal-ref-users', { q, limit: 20 }, function (res) {
      const items = res?.items || [];
      if (!items.length) {
        $suggest.html('<div class="tb-panel-item">No staff found</div>').removeClass('d-none');
        return;
      }
      const html = items.map(x => `
        <div class="tb-panel-item" data-name="${escHtml(x.name || '')}">
          <strong>${escHtml(x.name || '')}</strong>
        </div>
      `).join('');
      $suggest.html(html).removeClass('d-none');
    });
  });

  $suggest.off('click.appInternalRef', '.tb-panel-item').on('click.appInternalRef', '.tb-panel-item', function () {
    const name = String($(this).data('name') || '').trim();
    if (name) $input.val(name);
    $suggest.addClass('d-none').html('');
  });

  $(document).off('click.appInternalRef').on('click.appInternalRef', function (e) {
    if (!$(e.target).closest('#ap-internal-ref, #ap-internal-ref-suggest').length) {
      $suggest.addClass('d-none').html('');
    }
  });
}
function savePatient() {
  const title = ($('#p-title').val() || '').trim();
  const fullNameCore = ($('#p-full-name').val() || '').trim();

  const data = {
    title: title || null,
    full_name: fullNameCore,
    labmate_pid: $('#p-labmate-pid').val().trim(),
    panel_company: $('#p-panel-company').val().trim(),
    card_number: $('#p-card-number').val().trim(),
    tag: selectedPatientTags.join(','),
    gender: $('#p-gender').val(),
    date_of_birth: $('#p-dob').val() || null,
    age_years: $('#p-age-years').val() || null,
    contact_mobile: $('#p-contact-mobile').val().trim(),
    alternate_mobile: $('#p-alternate-mobile').val().trim(),
    email: $('#p-email').val().trim(),
    searched_mobile: wizardData.searchedMobile
  };

  if (!data.full_name) return alert('Patient Full Name is required.');
  if (!data.gender) return alert('Gender is required.');
  if (!data.contact_mobile) return alert('Contact No is required.');
  const panelCompany = String(data.panel_company || '').trim().toUpperCase();
  const cardNumber = String(data.card_number || '').trim();
  if ((panelCompany === 'NHA CGHS' || panelCompany === 'CAPF AYUSHMAN') && !cardNumber) {
    return alert('Card no is mandatory for NHA CGHS and CAPF AYUSHMAN panel company');
  }

  const documentFiles = Array.from($('#p-patient-documents')[0]?.files || []);
  if (documentFiles.length > 5) {
    return alert('Maximum 5 patient documents per patient allowed.');
  }
  const allowedExts = ['.pdf', '.jpg', '.jpeg', '.png'];
  const invalidFile = documentFiles.find((file) => {
    const name = String(file.name || '').toLowerCase();
    return !allowedExts.some(ext => name.endsWith(ext));
  });
  if (invalidFile) {
    return alert('Only PDF, JPG, JPEG, PNG files are allowed.');
  }

  const isEdit = !!editingPatientId;
  if (!isEdit && isAppointmentLevelFlow()) {
    return alert('Adding a patient is not allowed in appointment flow.');
  }
  const formData = new FormData();
  Object.entries(data).forEach(([key, value]) => {
    formData.append(key, value == null ? '' : value);
  });
  documentFiles.forEach((file) => formData.append('patient_documents', file));

  $.ajax({
    url: isEdit ? `/hhome-collection/patient/${editingPatientId}` : '/hhome-collection/create-patient',
    method: isEdit ? 'PATCH' : 'POST',
    data: formData,
    processData: false,
    contentType: false,
    success: function (res) {
      hasCallerContext = true;
      toggleStep1Workspace(true);
      resetPatientFormState();
      applyStep1Bundle(res || {});
    },
    error: function (xhr) {
      if (handleCallerMergePrompt(xhr.responseJSON || null, () => savePatient())) return;
      alert(xhr.responseJSON?.message || 'Unable to save patient details');
    }
  });
}

function loadColonies(resetSelection, desiredColonyId = '') {
  const city = $('#a-city').val();
  const requestId = ++colonyRequestSeq;
  const prevSelected = resetSelection ? '' : ($('#a-colony').val() || '');
  $.get('/hhome-collection/colonies', { city: city }, function (res) {
    if (requestId !== colonyRequestSeq) return;
    addressColonyCatalog = Array.isArray(res.colonies) ? res.colonies : [];
    const options = ['<option value="">Select Colony</option>'];
    (addressColonyCatalog || []).forEach(c => {
      const name = String(c.colony_name || '').trim();
      if (!name) return;
      options.push(`<option value="${c.id}" data-pincode="${c.pincode}" data-route="${c.route_no}">${name}</option>`);
    });
    const $colony = $('#a-colony');
    if ($colony.hasClass('select2-hidden-accessible')) {
      $colony.select2('destroy');
    }
    $colony.html(options.join('')).select2({
      width: '100%',
      dropdownParent: $('#new-address-form')
    });

    $colony.off('select2:open.forceBelow').on('select2:open.forceBelow', function () {
      const api = $(this).data('select2');
      if (!api || !api.$dropdown || !api.$container) return;
      const $dropdown = api.$dropdown;
      const top = api.$container.position().top + api.$container.outerHeight();
      const left = api.$container.position().left;
      $dropdown.css({
        top: `${top}px`,
        left: `${left}px`,
        bottom: 'auto'
      });
      $dropdown.removeClass('select2-dropdown--above').addClass('select2-dropdown--below');
    });

    $colony.off('change').on('change', function () {
      const opt = $(this).find(':selected');
      $('#a-pincode').val(opt.data('pincode') || '');
      $('#a-route').val(opt.data('route') || '');
      const selectedText = opt.text() || 'Select Colony';
      const api = $(this).data('select2');
      if (api && api.$container) {
        api.$container
          .find('.select2-selection__rendered')
          .text(selectedText)
          .attr('title', selectedText);
      }
    });

    if (desiredColonyId && $colony.find(`option[value="${desiredColonyId}"]`).length) {
      $colony.val(desiredColonyId).trigger('change');
    } else if (prevSelected && $colony.find(`option[value="${prevSelected}"]`).length) {
      $colony.val(prevSelected).trigger('change');
    } else if (resetSelection) {
      $colony.val('').trigger('change');
    } else {
      $('#a-pincode').val('');
      $('#a-route').val('');
      $colony.trigger('change');
    }
    syncAddressColonyMode();
    if (isAddressColonyManualMode()) {
      updateRouteFromManualPincode();
    }
  });
}

function loadAddresses() {
  $.get('/hhome-collection/addresses', function (res) {
    renderAddressesState(res.addresses || [], res.selected_address_id || 0);
    bindUseAddressButtons();
    bindEditAddressButtons();
  });
}

function saveAddress() {
  if (!hasCallerContext) {
    alert('Save at least one patient first. Caller will auto-create from patient contact.');
    return;
  }
  if (!validateFloorField()) {
    alert('Enter a floor number from 1 to 99 or select one floor option.');
    return;
  }

  const data = getAddressPayload();
  const manualColony = Boolean(data.colony_not_found);
  if (!data.house_flat_no) return alert('House/Flat No is required.');
  if (!data.city) return alert('City is required.');
  if (!manualColony && !data.colony_id) return alert('Colony is required.');
  if (manualColony && !data.colony_name) return alert('Colony name is required.');
  if (!data.pincode || data.pincode.length !== 6) return alert('Enter valid 6 digit pincode.');
  if (!data.route) return alert('No route mapping found for selected city and pincode.');

  const isEdit = !!editingAddressId;
  $.ajax({
    url: isEdit ? `/hhome-collection/address/${editingAddressId}` : '/hhome-collection/create-address',
    method: isEdit ? 'PATCH' : 'POST',
    contentType: 'application/json',
    data: JSON.stringify(data),
    success: function () {
      resetAddressFormState();
      loadAddresses();
    },
    error: function (xhr) {
      alert(xhr.responseJSON?.message || 'Unable to save address details');
    }
  });
}

function goStep2() {
  ensureStep1Ready(function (aRes) {
    const line = $('#address-list .address-card.selected-address').find('div').eq(2).text() || '';
    const m = line.match(/\|\s*([^|]+)\s*\|/);
    if (m && m[1]) {
      slotSelectedRoute = m[1].trim();
    }
    const selectedId = Number(aRes.selected_address_id || 0);
    const selectedAddress = (aRes.addresses || []).find(a => Number(a.id) === selectedId) || null;
    if (!slotSelectedRoute) {
      slotSelectedRoute = (selectedAddress?.route_no || '').trim();
    }
    setStep(2);
  });
}

function localIsoDate(dateObj) {
  const d = dateObj instanceof Date ? dateObj : new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function isoToday() {
  return localIsoDate(new Date());
}

function isoTomorrow() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return localIsoDate(d);
}

function destroyDatePicker(instance) {
  if (instance && typeof instance.destroy === 'function') {
    instance.destroy();
  }
  return null;
}

function setDatePickerValue(instance, value) {
  if (instance && typeof instance.setDate === 'function') {
    instance.setDate(String(value || '').trim(), true, 'Y-m-d');
  }
}

function initWizardDatePickers(defaultDate) {
  if (typeof flatpickr !== 'function') return;

  bookingDatePicker = destroyDatePicker(bookingDatePicker);
  slotGridDatePicker = destroyDatePicker(slotGridDatePicker);

  if ($('#b-date').length) {
    bookingDatePicker = flatpickr('#b-date', {
      dateFormat: 'Y-m-d',
      altInput: true,
      altFormat: 'd/m/Y',
      allowInput: false,
      minDate: isoToday(),
      defaultDate: defaultDate || null
    });
  }

  if ($('#slot-grid-date').length) {
    slotGridDatePicker = flatpickr('#slot-grid-date', {
      dateFormat: 'Y-m-d',
      altInput: true,
      altFormat: 'd/m/Y',
      allowInput: false,
      minDate: isoToday(),
      defaultDate: defaultDate || null
    });
  }
}

function fetchLatestSelectedRoute(done) {
  const reqId = ++summaryRequestSeq;
  $.get('/hhome-collection/addresses', { _ts: Date.now() }, function (res) {
    if (reqId !== summaryRequestSeq) return;
    const selectedId = Number(res?.selected_address_id || 0);
    const selectedAddress = (res?.addresses || []).find(a => Number(a.id) === selectedId) || null;
    const route = (selectedAddress?.route_no || '').trim();
    slotSelectedRoute = route;
    if (typeof done === 'function') done(route);
  }).fail(function () {
    if (reqId !== summaryRequestSeq) return;
    if (typeof done === 'function') done('');
  });
}

function loadTestSpecimenCatalog(done) {
  if (testSpecimenCatalog) {
    done(testSpecimenCatalog);
    return;
  }

  if (testSpecimenCatalogPromise) {
    testSpecimenCatalogPromise
      .done(() => done(testSpecimenCatalog))
      .fail(() => done(null));
    return;
  }

  testSpecimenCatalogPromise = $.get('/hhome-collection/test-specimen-catalog', function (res) {
    if (res?.ok) {
      testSpecimenCatalog = {
        tests: res.tests || {},
        children_by_testcode1: res.children_by_testcode1 || {}
      };
    } else {
      testSpecimenCatalog = { tests: {}, children_by_testcode1: {} };
    }
    done(testSpecimenCatalog);
  }).fail(function () {
    testSpecimenCatalog = { tests: {}, children_by_testcode1: {} };
    done(testSpecimenCatalog);
  });
}

function normalizeTestCode(v) {
  return String(v || '').trim().toUpperCase();
}

function collectTubesForSelectedTest(testItem, catalog) {
  const testsMap = catalog?.tests || {};
  const childMap = catalog?.children_by_testcode1 || {};
  const rootCode = normalizeTestCode(testItem?.testcode1 || testItem?.booked_code || testItem?.test_code || '');
  if (!rootCode) return [];

  const seenNodes = new Set();
  const seenTubes = new Set();
  const tubes = [];

  function pushTube(code) {
    const meta = testsMap[code];
    const tube = String(meta?.specimen_name || '').trim();
    if (!tube) return;
    if (!seenTubes.has(tube)) {
      seenTubes.add(tube);
      tubes.push(tube);
    }
  }

  function dfs(code) {
    if (!code || seenNodes.has(code)) return;
    seenNodes.add(code);
    pushTube(code);
    const children = childMap[code] || [];
    children.forEach((childCode) => dfs(normalizeTestCode(childCode)));
  }

  dfs(rootCode);
  return tubes;
}

function formatCharge(v) {
  const n = Number(v || 0);
  if (!Number.isFinite(n)) return '0';
  return n.toFixed(2).replace(/\.00$/, '');
}

function getAdditionalDiscountState() {
  const ap = wizardData.appointment || {};
  const mode = String(ap.additional_discount_mode || '').toLowerCase();
  const value = Number(ap.additional_discount_value || 0);
  const amount = Number(ap.additional_discount_amount || 0);
  return {
    mode: (mode === 'amount' || mode === 'percent') ? mode : '',
    value: Number.isFinite(value) && value > 0 ? value : 0,
    amount: Number.isFinite(amount) && amount > 0 ? amount : 0
  };
}

function getDbSavedFinalDiscount() {
  const ap = wizardData.appointment || {};
  const md = wizardData.modify || {};
  const v = Number(
    md.F_dis
    ?? md.f_dis
    ?? ap.F_dis
    ?? ap.f_dis
    ?? ap.final_discount_amount
    ?? ap.final_discount
    ?? 0
  );
  return Number.isFinite(v) && v >= 0 ? v : 0;
}

function getDbSavedAdditionalDiscount() {
  const ap = wizardData.appointment || {};
  const md = wizardData.modify || {};
  const v = Number(
    md.Ad_dis
    ?? md.ad_dis
    ?? ap.Ad_dis
    ?? ap.ad_dis
    ?? ap.additional_discount_amount
    ?? 0
  );
  return Number.isFinite(v) && v >= 0 ? v : 0;
}

function computeAdditionalDiscountAmount(mode, value, subtotal) {
  const cleanValue = Number(value || 0);
  const cleanSubtotal = Number(subtotal || 0);
  if (!Number.isFinite(cleanValue) || cleanValue <= 0) return 0;
  if (mode === 'amount') return cleanValue;
  if (mode === 'percent') return (cleanSubtotal * cleanValue) / 100;
  return 0;
}

function renderReviewTestsHtml(selectedTests, catalog, chargeMode = 'P', showMrp = false) {
  const list = Array.isArray(selectedTests) ? selectedTests : [];
  if (!list.length) {
    return { html: '<div class="text-muted">No tests selected.</div>', total: 0, subtotal: 0, discountTotal: 0, tubes: [] };
  }

  const mode = normalizeChargeModeCode(chargeMode);
  const isFreeMode = mode === 'F';
  const isPayingMode = mode === 'P';
  const tubeSet = new Set();
  let total = 0;
  let subtotal = 0;
  let discountTotal = 0;
  const rows = list.map((t, idx) => {
    const code = normalizeTestCode(t?.booked_code || t?.testcode1 || t?.test_code || '');
    const desc = String(t?.description || '').trim();
    const label = [code, desc].filter(Boolean).join(' - ') || 'Test';
    const priced = pricedForPanel(t, showMrp);
    const rawMrp = Number(priced.mrp || 0);
    const rawDiscount = Number(priced.max_discount || 0);
    const mrp = isFreeMode ? 0 : rawMrp;
    const discount = isFreeMode ? 0 : (isPayingMode ? rawDiscount : 0);
    const finalCharge = isFreeMode ? 0 : Math.max(0, mrp - discount);
    subtotal += Number.isFinite(mrp) ? mrp : 0;
    discountTotal += Number.isFinite(discount) ? discount : 0;
    total += Number.isFinite(finalCharge) ? finalCharge : 0;
    collectTubesForSelectedTest(t, catalog).forEach((tube) => {
      const k = String(tube || '').trim().toLowerCase();
      if (k) tubeSet.add(tube);
    });
    return `
      <tr>
        <td><strong>${escHtml(label)}</strong></td>
        <td class="text-end">${escHtml(formatCharge(mrp))}</td>
        <td class="text-end">${escHtml(formatCharge(discount))}</td>
        <td class="text-end"><strong>${escHtml(formatCharge(finalCharge))}</strong></td>
        <td class="text-center">-</td>
      </tr>
    `;
  }).join('');

  const html = `
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
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  return { html, total, subtotal, discountTotal, tubes: Array.from(tubeSet) };
}

function getPendingTestsForPatientFromModifyContext(patientId) {
  const pid = String(patientId || '');
  if (!pid) return [];
  const pendingMap = wizardData?.modify?.pending_tests_map || {};
  const pTb = pendingMap[pid] || pendingMap[Number(pid)] || null;
  if (!pTb || typeof pTb !== 'object') return [];

  const rows = [];
  if (Array.isArray(pTb.selected_tests)) {
    rows.push(...pTb.selected_tests);
  }
  if (Array.isArray(pTb.panels)) {
    pTb.panels.forEach((sec) => {
      if (sec && typeof sec === 'object' && Array.isArray(sec.selected_tests)) {
        rows.push(...sec.selected_tests);
      }
    });
  }
  const rootCode = String(pTb?.parent?.booked_code || '').trim();
  return rows
    .filter((t) => t && typeof t === 'object' && String(t.booked_code || '').trim())
    .map((t) => {
      const code = String(t.booked_code || '').trim();
      const directRoot = String(t.root_booked_code || '').trim();
      return {
        booked_code: code,
        description: String(t.description || code).trim(),
        parent_booked_code: String(t.parent_booked_code || '').trim(),
        root_booked_code: directRoot || rootCode,
        charge: 0,
        mrp: 0,
        max_discount: 0,
        max_allowed_discount: 0
      };
    });
}

function applyPendingChildOverlay(selectedTests, pendingTests) {
  const original = Array.isArray(selectedTests) ? selectedTests : [];
  const pending = Array.isArray(pendingTests) ? pendingTests : [];
  if (!pending.length) return original;

  const pendingByParent = {};
  const pendingOrphans = [];
  pending.forEach((p) => {
    const parent = String(p.root_booked_code || p.parent_booked_code || '').trim().toUpperCase();
    if (parent) {
      pendingByParent[parent] = pendingByParent[parent] || [];
      pendingByParent[parent].push(p);
    } else {
      pendingOrphans.push(p);
    }
  });

  const replaced = [];
  const usedParents = new Set();
  original.forEach((t) => {
    const code = String(t?.booked_code || '').trim().toUpperCase();
    const repl = code ? (pendingByParent[code] || []) : [];
    if (repl.length) {
      repl.forEach((x) => replaced.push(x));
      usedParents.add(code);
    } else {
      replaced.push(t);
    }
  });
  Object.entries(pendingByParent).forEach(([parentCode, list]) => {
    if (!usedParents.has(parentCode)) {
      list.forEach((x) => replaced.push(x));
    }
  });
  pendingOrphans.forEach((x) => replaced.push(x));

  const seen = new Set();
  return replaced.filter((t) => {
    const code = String(t?.booked_code || '').trim().toUpperCase();
    if (!code || seen.has(code)) return false;
    seen.add(code);
    return true;
  });
}

function getModifyFlowType() {
  return String(wizardData?.modify?.flow_type || '').trim().toLowerCase();
}

function deepClone(obj) {
  try {
    return JSON.parse(JSON.stringify(obj || {}));
  } catch (_) {
    return {};
  }
}

function getTbTestCodes(tb) {
  const out = new Set();
  if (!tb || typeof tb !== 'object') return out;
  const pushRows = (rows) => {
    (rows || []).forEach((t) => {
      const code = String(t?.booked_code || '').trim().toUpperCase();
      if (code) out.add(code);
    });
  };
  pushRows(tb.selected_tests);
  (tb.panels || []).forEach((sec) => pushRows(sec?.selected_tests || []));
  return out;
}

function isZeroBilledSeedTest(testRow) {
  const t = testRow && typeof testRow === 'object' ? testRow : {};
  return Number(t.charge || 0) === 0 &&
    Number(t.mrp || 0) === 0 &&
    Number(t.max_discount || 0) === 0 &&
    Number(t.max_allowed_discount || 0) === 0;
}

function buildZeroSeedTestsMap(testsMap) {
  const src = testsMap && typeof testsMap === 'object' ? testsMap : {};
  const out = {};
  const filterRows = (rows) => (Array.isArray(rows) ? rows.filter((t) => isZeroBilledSeedTest(t)) : []);
  Object.keys(src).forEach((pidKey) => {
    const pid = String(pidKey || '').trim();
    if (!pid) return;
    const tb = src[pidKey] || {};
    const clone = deepClone(tb);
    clone.selected_tests = filterRows(tb.selected_tests);
    clone.panels = Array.isArray(tb.panels)
      ? tb.panels.map((sec) => ({
        ...(sec || {}),
        selected_tests: filterRows(sec?.selected_tests)
      })).filter((sec) => (sec.selected_tests || []).length)
      : [];
    if (!clone.selected_tests.length && clone.panels.length) {
      clone.selected_tests = clone.panels[0]?.selected_tests || [];
    }
    if (clone.selected_tests.length || clone.panels.length) {
      out[pid] = clone;
    }
  });
  return out;
}

function getSeedParentCodesForPatient(patientId) {
  const pid = String(patientId || '');
  if (!pid) return new Set();
  const seedMap = wizardData?.modify?.parent_context_map || wizardData?.modify?.parent_seed_tests_map || {};
  const seedTb = seedMap[pid] || seedMap[Number(pid)] || null;
  return getTbTestCodes(seedTb);
}

function extractTbCodesMap(mapObj) {
  const src = mapObj && typeof mapObj === 'object' ? mapObj : {};
  const out = {};
  Object.keys(src).forEach((pidKey) => {
    const pid = String(pidKey || '').trim();
    if (!pid) return;
    const set = getTbTestCodes(src[pidKey]);
    out[pid] = Array.from(set).sort();
  });
  return out;
}

function hasModifyTestsChanged() {
  if (!isModifyContextActive()) return false;
  const originalMap = wizardData?.modify?.original_tests_billing_map || {};
  const currentMap = wizardData?.testsBilling || {};
  const orig = extractTbCodesMap(originalMap);
  const curr = extractTbCodesMap(currentMap);
  try {
    return JSON.stringify(orig) !== JSON.stringify(curr);
  } catch (_) {
    return true;
  }
}

function hydrateStep2() {
  const defaultDate = isoTomorrow();
  const hasPermanentInState = Object.prototype.hasOwnProperty.call((wizardData.appointment || {}), 'permanent_tags');
  const hasBookingInState = Object.prototype.hasOwnProperty.call((wizardData.appointment || {}), 'booking_tags');
  const isModifyMode = Number(wizardData?.modify?.booking_id || 0) > 0;

  initWizardDatePickers(defaultDate);
  $('#b-date').val(wizardData.appointment.preferred_visit_date || defaultDate);
  setDatePickerValue(bookingDatePicker, wizardData.appointment.preferred_visit_date || defaultDate);
  $('#b-slot').val(wizardData.appointment.preferred_time_slot || '');
  $('#ap-referred-by').val(wizardData.appointment.referred_by || '');
  $('#ap-internal-ref').val(wizardData.appointment.internal_ref || '');
  $('#ap-lead-id').val(wizardData.appointment.lead_id || '');
  $('#b-remarks').val(wizardData.appointment.remarks || '');
  if (hasPermanentInState) {
    selectedPermanentTags = normalizeTagList(String(wizardData.appointment.permanent_tags || '').split(','));
  }
  if (hasBookingInState) {
    selectedBookingTags = normalizeTagList(String(wizardData.appointment.booking_tags || '').split(','));
  }
  renderSelectedTags($('#ap-permanent-tags'), selectedPermanentTags, 'ap-permanent-tag-remove');
  renderSelectedTags($('#ap-booking-tags'), selectedBookingTags, 'ap-booking-tag-remove');
  if (slotSelectedRoute) {
    $('#slot-selected-route').val(slotSelectedRoute);
  } else {
    fetchLatestSelectedRoute(function (route) {
      $('#slot-selected-route').val(route);
    });
  }

  $('#b-date').prop('disabled', isModifyMode);
  $('#b-slot').prop('disabled', isModifyMode);
  if (isModifyMode) {
    $('#btn-open-slots').addClass('d-none');
  } else {
    $('#btn-open-slots').removeClass('d-none');
  }
}

function syncAppointmentTagsFromTopBar() {
  wizardData.appointment = wizardData.appointment || {};
  wizardData.appointment.permanent_tags = normalizeTagList(selectedPermanentTags).join(',');
  wizardData.appointment.booking_tags = normalizeTagList(selectedBookingTags).join(',');
}

function syncAppointmentFromStep2Inputs() {
  if (currentStep !== 2) return;
  const hasStep2Fields = $('#b-date').length || $('#b-slot').length || $('#ap-referred-by').length || $('#ap-internal-ref').length || $('#ap-lead-id').length || $('#b-remarks').length;
  if (!hasStep2Fields) return;
  wizardData.appointment = wizardData.appointment || {};
  wizardData.appointment.preferred_visit_date = $('#b-date').val() || wizardData.appointment.preferred_visit_date || '';
  wizardData.appointment.preferred_time_slot = $('#b-slot').val() || wizardData.appointment.preferred_time_slot || '';
  wizardData.appointment.referred_by = $('#ap-referred-by').val() || '';
  wizardData.appointment.internal_ref = $('#ap-internal-ref').val() || '';
  wizardData.appointment.lead_id = ($('#ap-lead-id').val() || '').trim();
  wizardData.appointment.remarks = ($('#b-remarks').val() || '').trim();
  wizardData.appointment.permanent_tags = normalizeTagList(selectedPermanentTags).join(',');
  wizardData.appointment.booking_tags = normalizeTagList(selectedBookingTags).join(',');
}

function goStep3() {
  const prevAppt = wizardData.appointment || {};
  const appt = {
    ...prevAppt,
    preferred_visit_date: $('#b-date').val(),
    preferred_time_slot: $('#b-slot').val(),
    referred_by: $('#ap-referred-by').val(),
    internal_ref: $('#ap-internal-ref').val(),
    lead_id: ($('#ap-lead-id').val() || '').trim(),
    remarks: $('#b-remarks').val().trim(),
    permanent_tags: normalizeTagList(selectedPermanentTags).join(','),
    booking_tags: normalizeTagList(selectedBookingTags).join(',')
  };

  if (!appt.preferred_visit_date || !appt.preferred_time_slot) {
    alert('Preferred Visit Date and Time Slot are required.');
    return;
  }

  wizardData.appointment = appt;
  setStep(3);
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

function formatReviewDateDay(isoDate) {
  const d = String(isoDate || '').trim();
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return d || '-';
  const dt = new Date(`${m[1]}-${m[2]}-${m[3]}T00:00:00`);
  const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const day = days[dt.getDay()] || '-';
  return `${m[3]}-${m[2]} / ${day}`;
}

function formatSlotCompact(slotText) {
  const s = String(slotText || '').trim();
  const m = s.match(/(\d{1,2}:\d{2})\s*([AP]M)\s*to\s*(\d{1,2}:\d{2})\s*([AP]M)/i);
  if (!m) return s || '-';
  const start = m[1];
  const endTime = m[3];
  const endMeridiem = m[4].toUpperCase();
  const [endHourRaw, endMin] = endTime.split(':');
  let endHour = String(endHourRaw || '').replace(/^0+/, '');
  if (!endHour) endHour = '0';
  const endLabel = endMin === '00' ? `${endHour} ${endMeridiem}` : `${endHour}:${endMin} ${endMeridiem}`;
  return `${start} to ${endLabel}`;
}

function tbsLabel(code) {
  const c = normalizePatientTbs(code);
  const row = (PATIENT_TBS_OPTIONS || []).find((x) => Number(x.code) === Number(c));
  return row ? row.label : '-';
}

function generateHalfHourSlots() {
  const rows = [];
  for (let mins = 6 * 60; mins < 24 * 60; mins += 30) {
    rows.push({
      key: mins,
      label: `${formatMinutesTo12h(mins)} to ${formatMinutesTo12h(mins + 30)}`
    });
  }
  return rows;
}

function openSlotPlanner() {
  const directRoute = ($('#slot-selected-route').val() || slotSelectedRoute || '').trim();
  const openWithRoute = function (route) {
    if (!route) {
      alert('Please select address first so route is available.');
      return;
    }
    const modalEl = document.getElementById('slotPlannerModal');
    slotPlannerModal = new bootstrap.Modal(modalEl);
    $('#slot-selected-route').val(route);
    const openDate = $('#b-date').val() || isoTomorrow();
    $('#slot-grid-date').val(openDate);
    setDatePickerValue(slotGridDatePicker, openDate);
    slotPlannerModal.show();
    loadRouteSlotGrid(route);
  };

  if (directRoute) {
    openWithRoute(directRoute);
    return;
  }
  fetchLatestSelectedRoute(openWithRoute);
}

function loadRouteSlotGrid(forcedRoute) {
  if (forcedRoute && typeof forcedRoute !== 'string') {
    forcedRoute = '';
  }
  const dateVal = $('#slot-grid-date').val() || isoTomorrow();
  const routeVal = (forcedRoute || $('#slot-selected-route').val() || slotSelectedRoute || '').trim();
  $('#slot-grid-wrap').html('<div class="text-muted p-2">Loading slots...</div>');
  $.get('/hhome-collection/route-slot-grid', { date: dateVal, route: routeVal, _ts: Date.now() }, function (res) {
    if (!res.ok) {
      $('#slot-grid-wrap').html(`<div class="text-danger p-2">${res.message || 'Unable to load slots'}</div>`);
      return;
    }
    renderRouteSlotGrid(res);
  }).fail(function (xhr) {
    $('#slot-grid-wrap').html(`<div class="text-danger p-2">${xhr.responseJSON?.message || 'Unable to load slots'}</div>`);
  });
}

function renderRouteSlotGrid(res) {
  const routes = res.routes || [];
  const routeColors = res.route_colors || {};
  const bookings = res.bookings || [];
  const selectedRoute = (res.selected_route || slotSelectedRoute || '').trim();
  const dateVal = res.date || ($('#slot-grid-date').val() || isoTomorrow());
  $('#slot-grid-meta').text(`Total bookings: ${res.total_bookings || 0}`);

  function parseColorToRgb(input) {
    const txt = String(input || '').trim().toLowerCase();
    if (!txt) return null;
    const named = {
      red: [239, 68, 68],
      green: [34, 197, 94],
      blue: [59, 130, 246],
      yellow: [234, 179, 8],
      orange: [249, 115, 22],
      purple: [168, 85, 247],
      pink: [236, 72, 153],
      brown: [120, 72, 40],
      gray: [107, 114, 128],
      grey: [107, 114, 128],
      black: [15, 23, 42]
    };
    if (named[txt]) return named[txt];
    const hex = txt.replace('#', '');
    if (/^[0-9a-f]{3}$/i.test(hex)) {
      return [
        parseInt(hex[0] + hex[0], 16),
        parseInt(hex[1] + hex[1], 16),
        parseInt(hex[2] + hex[2], 16)
      ];
    }
    if (/^[0-9a-f]{6}$/i.test(hex)) {
      return [
        parseInt(hex.slice(0, 2), 16),
        parseInt(hex.slice(2, 4), 16),
        parseInt(hex.slice(4, 6), 16)
      ];
    }
    return null;
  }

  function routeBgStyle(routeName, isHeader, isSelected) {
    const rgb = parseColorToRgb(routeColors[routeName] || '');
    if (!rgb) return '';
    const alpha = isHeader ? (isSelected ? 0.4 : 0.28) : (isSelected ? 0.28 : 0.18);
    return ` style="background-color: rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha});"`;
  }

  const indexed = {};
  const routeCounts = {};
  const slotCounts = {};
  bookings.forEach(b => {
    const key = `${b.route || 'UNASSIGNED'}|${b.slot_key}`;
    indexed[key] = indexed[key] || [];
    indexed[key].push(b);
    const routeKey = b.route || 'UNASSIGNED';
    const slotKey = b.slot_key;
    routeCounts[routeKey] = (routeCounts[routeKey] || 0) + 1;
    slotCounts[slotKey] = (slotCounts[slotKey] || 0) + 1;
  });

  const slots = generateHalfHourSlots();
  let html = '<table class="slot-grid-table"><thead><tr><th class="slot-time-col">Time Slot</th>';
  routes.forEach(r => {
    const cls = r === selectedRoute ? 'route-selected' : '';
    html += `<th class="${cls}"${routeBgStyle(r, true, r === selectedRoute)}>${escHtml(r)} <span class="slot-count-chip">${Number(routeCounts[r] || 0)}</span></th>`;
  });
  html += '</tr></thead><tbody>';

  slots.forEach(s => {
    html += `<tr><td class="slot-time-col">${escHtml(s.label)} <span class="slot-count-chip">${Number(slotCounts[s.key] || 0)}</span></td>`;
    routes.forEach(r => {
      const cls = r === selectedRoute ? 'route-selected' : '';
      const items = indexed[`${r}|${s.key}`] || [];
      let cell = '';
      items.forEach(it => {
        const line1 = [it.area, it.city].filter(Boolean).join(', ');
        const mobile = String(it.mobile || '').trim();
        cell += `
          <span class="slot-book-item">
            <span class="slot-book-line1">${escHtml(line1 || '-')}</span>
            <span class="slot-book-line2">${escHtml(mobile || '-')}</span>
          </span>
        `;
      });
      if (r === selectedRoute) {
        cell += `<button class="btn btn-primary slot-pick-btn" data-date="${dateVal}" data-slot="${s.label}">+ Select</button>`;
      }
      html += `<td class="${cls}"${routeBgStyle(r, false, r === selectedRoute)}>${cell || '<span class="text-muted">-</span>'}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table>';

  $('#slot-grid-wrap').html(html);
  $('#slot-grid-wrap .slot-pick-btn').off('click').on('click', function () {
    const pickedDate = $('#slot-grid-date').val() || $(this).data('date');
    const pickedSlot = $(this).data('slot');
    $('#b-date').val(pickedDate);
    setDatePickerValue(bookingDatePicker, pickedDate);
    $('#b-slot').val(pickedSlot);
    if (slotPlannerModal) slotPlannerModal.hide();
  });
}

function ensureTbObject(pid) {
  const key = String(pid);
  const tb = wizardData.testsBilling[key] || {
    panel: null,
    billing: null,
    selected_tests: [],
    cce_level_tbs: null,
    referred_by: ''
  };
  if (tb.cce_level_tbs === undefined && tb.cce_level_TBS !== undefined) {
    tb.cce_level_tbs = tb.cce_level_TBS;
  }
  if (tb.cce_level_tbs === undefined) tb.cce_level_tbs = null;
  if (tb.referred_by === undefined) tb.referred_by = '';
  if (!Array.isArray(tb.panels)) {
    tb.panels = [
      normalizePanelSection({
        panel: tb.panel || null,
        billing: tb.billing || null,
        selected_tests: Array.isArray(tb.selected_tests) ? tb.selected_tests : []
      })
    ];
  }
  if (!tb.panels.length) {
    tb.panels.push(normalizePanelSection({ panel: null, billing: null, selected_tests: [] }));
  }
  tb.panels = tb.panels.map(normalizePanelSection);
  syncPrimaryPanelFields(tb);
  wizardData.testsBilling[key] = tb;
  return tb;
}

function normalizePatientTbs(raw) {
  const n = Number(raw);
  if (n >= 1 && n <= 4) return n;
  const txt = String(raw || '').trim().toLowerCase();
  if (!txt) return null;
  if (txt === 'test confirmed and booked') return 1;
  if (txt === 'prescription attached but test not booked') return 2;
  if (txt === 'no test information: ask to patient for tests') return 3;
  if (txt === 'incompleted test, phlebo verification pending to confirm and book') return 4;
  return null;
}

function patientCanBookTests(tbObj) {
  const code = normalizePatientTbs(tbObj?.cce_level_tbs);
  if (code === 3) return false;
  return true;
}

function updatePatientBookButtons(pid) {
  const patientId = String(pid || '');
  if (!patientId) return;
  const tb = ensureTbObject(patientId);
  const canBookByStatus = patientCanBookTests(tb);
  (tb.panels || []).forEach((section, idx) => {
    const key = panelDomKey(patientId, idx);
    const hasBilling = !!String(section?.billing?.comp_cat_id ?? '').trim();
    $(`#tb-book-btn-${key}`).prop('disabled', !(canBookByStatus && hasBilling));
  });
}

function updatePatientTbsRequiredState(patientId) {
  const pid = String(patientId || '');
  if (!pid) return;
  const $sel = $(`#tb-patient-tbs-${pid}`);
  if (!$sel.length) return;
  const hasValue = String($sel.val() || '').trim().length > 0;
  $sel.toggleClass('tb-status-required', !hasValue);
}

function testSelKey(t) {
  return String(t?.booked_code || '').trim().toUpperCase();
}

function autoResolveBillingFromPanel(patientId, panelName, panelIndex = 0) {
  const pid = String(patientId || '');
  const idx = Number(panelIndex || 0);
  const key = panelDomKey(pid, idx);
  const pname = String(panelName || '').trim();
  if (!pid || !pname || pname.length < 2) return;

  const section = getPanelSection(pid, idx);
  const existingCompCat = String(section?.billing?.comp_cat_id ?? '').trim();
  const existingChargeMode = String(section?.billing?.selected_charge_mode || section?.billing?.charge_mode_code || section?.billing?.charge_mode || '').trim();
  if (existingCompCat && existingCompCat !== '0' && existingChargeMode) {
    return;
  }

  $.get('/hhome-collection/panel-companies', { q: pname, limit: 20 }, function (res) {
    const items = res.items || [];
    if (!items.length) return;

    const q = pname.toLowerCase();
    let picked = items.find(x => String(x.pname || '').toLowerCase() === q);
    if (!picked) picked = items.find(x => String(x.pname || '').toLowerCase().startsWith(q));
    if (!picked) picked = items[0];
    if (!picked) return;

    section.panel = {
      center_id: String(picked.CenterID || ''),
      pname: String(picked.pname || ''),
      showmrp: Number(picked.showmrp || 0)
    };
    const allowedChargeMode = normalizeChargeModeCode(picked.BillingChargeMode || '');
    section.billing = {
      comp_cat_id: String(picked.CompCatID ?? ''),
      cat_details: String(picked.CatDetails || ''),
      allowed_charge_mode_code: allowedChargeMode,
      charge_mode: allowedChargeMode,
      charge_mode_code: allowedChargeMode,
      selected_charge_mode: '',
      showmrp: Number(picked.showmrp || 0)
    };
    syncPrimaryPanelFields(ensureTbObject(pid));

    $(`#tb-panel-input-${key}`).val(section.panel.pname);
    $(`#tb-bill-id-${key}`).val(section.billing.comp_cat_id);
    $(`#tb-bill-name-${key}`).val(section.billing.cat_details);
    renderChargeModeControl(pid, idx, section.billing);
    updatePatientBookButtons(pid);
  });
}

function bindPanelBillingEvents() {
  $(document).off('input.hcPanelTestSearch', '#panel-test-search').on('input.hcPanelTestSearch', '#panel-test-search', function () {
    const q = String($(this).val() || '').trim();
    panelTestSearchQuery = q;
    if (panelTestSearchTimer) clearTimeout(panelTestSearchTimer);
    panelTestSearchTimer = setTimeout(() => {
      loadPanelTestsForCurrentView();
    }, 250);
  });

  $('#tests-billing-sections').off('input', '.tb-panel-search').on('input', '.tb-panel-search', function () {
    const $input = $(this);
    const patientId = String($input.data('patient-id'));
    const panelIndex = Number($input.data('panel-index') || 0);
    const key = panelDomKey(patientId, panelIndex);
    const q = ($input.val() || '').trim();
    const $suggest = $(`#tb-panel-suggest-${key}`);

    if (q.length < 2) {
      $suggest.addClass('d-none').html('');
      return;
    }

    $.get('/hhome-collection/panel-companies', { q, limit: 20 }, function (res) {
      const items = res.items || [];
      if (!items.length) {
        $suggest.html('<div class="tb-panel-item">No panel found</div>').removeClass('d-none');
        return;
      }
      const rows = items.map(x => `
        <div class="tb-panel-item"
             data-patient-id="${patientId}"
             data-panel-index="${panelIndex}"
             data-center-id="${escHtml(x.CenterID)}"
             data-pname="${escHtml(x.pname)}"
             data-comp-cat-id="${escHtml(x.CompCatID ?? '')}"
             data-cat-details="${escHtml(x.CatDetails || '')}"
             data-showmrp="${escHtml(x.showmrp || 0)}"
             data-billing-charge-mode="${escHtml(x.BillingChargeMode || '')}">
          <strong>${escHtml(x.pname)}</strong>
          <span class="meta">CenterID: ${escHtml(x.CenterID)}</span>
        </div>
      `).join('');
      $suggest.html(rows).removeClass('d-none');
    });
  });

  $('#tests-billing-sections').off('click', '.tb-panel-item').on('click', '.tb-panel-item', function () {
    if ($(this).hasClass('tb-refby-item')) return;
    const patientId = String($(this).data('patient-id'));
    const panelIndex = Number($(this).data('panel-index') || 0);
    const key = panelDomKey(patientId, panelIndex);
    const centerId = String($(this).data('center-id') || '');
    const pname = String($(this).data('pname') || '');
    const compCatId = String($(this).data('comp-cat-id') ?? '');
    const catDetails = String($(this).data('cat-details') || '');
    const billingChargeMode = String($(this).data('billing-charge-mode') || '');
    const showmrp = Number($(this).data('showmrp') || 0) === 1 ? 1 : 0;

    $(`#tb-panel-input-${key}`).val(pname);
    $(`#tb-panel-suggest-${key}`).addClass('d-none').html('');

    const tb = ensureTbObject(patientId);
    const section = getPanelSection(patientId, panelIndex);
    const allowedChargeMode = normalizeChargeModeCode(billingChargeMode);
    section.panel = { center_id: centerId, pname, showmrp };
    section.billing = {
      comp_cat_id: compCatId,
      cat_details: catDetails,
      allowed_charge_mode_code: allowedChargeMode,
      charge_mode: allowedChargeMode,
      charge_mode_code: allowedChargeMode,
      selected_charge_mode: '',
      showmrp
    };
    section.selected_tests = [];
    syncPrimaryPanelFields(tb);
    renderSelectedTestsForPanel(patientId, panelIndex);
    $(`#tb-bill-id-${key}`).val(section.billing.comp_cat_id);
    $(`#tb-bill-name-${key}`).val(section.billing.cat_details);
    renderChargeModeControl(patientId, panelIndex, section.billing);
    updatePatientBookButtons(patientId);
  });

  $('#tests-billing-sections').off('input', '.tb-patient-refby').on('input', '.tb-patient-refby', function () {
    const $input = $(this);
    const pid = String($input.data('patient-id') || '');
    if (!pid) return;
    const tb = ensureTbObject(pid);
    const q = String($input.val() || '').trim();
    tb.referred_by = q;
    const $suggest = $(`#tb-patient-refby-suggest-${pid}`);
    if (q.length < 2) {
      $suggest.addClass('d-none').html('');
      return;
    }
    $.get('/hhome-collection/panel-companies', { q, limit: 20 }, function (res) {
      const items = res.items || [];
      if (!items.length) {
        $suggest.html('<div class="tb-panel-item">No suggestion found</div>').removeClass('d-none');
        return;
      }
      const rows = items.map((x) => `
        <div class="tb-panel-item tb-refby-item" data-patient-id="${escHtml(pid)}" data-refby="${escHtml(x.pname || '')}">
          <strong>${escHtml(x.pname || '')}</strong>
          <span class="meta">CenterID: ${escHtml(x.CenterID || '')}</span>
        </div>
      `).join('');
      $suggest.html(rows).removeClass('d-none');
    });
  });

  $('#tests-billing-sections').off('click', '.tb-refby-item').on('click', '.tb-refby-item', function () {
    const pid = String($(this).data('patient-id') || '');
    const refby = String($(this).data('refby') || '').trim();
    if (!pid) return;
    const tb = ensureTbObject(pid);
    tb.referred_by = refby;
    $(`#tb-patient-refby-${pid}`).val(refby);
    $(`#tb-patient-refby-suggest-${pid}`).addClass('d-none').html('');
  });

  $(document).off('click.hcPanelClose').on('click.hcPanelClose', function (e) {
    if (!$(e.target).closest('.tb-panel-wrap, .tb-refby-wrap').length) {
      $('.tb-panel-suggest').addClass('d-none').html('');
    }
  });

  $('#tests-billing-sections').off('change', '.tb-charge-mode-select').on('change', '.tb-charge-mode-select', function () {
    const pid = String($(this).data('patient-id') || '');
    const idx = Number($(this).data('panel-index') || 0);
    if (!pid) return;
    const tb = ensureTbObject(pid);
    const section = getPanelSection(pid, idx);
    section.billing = section.billing || {};
    const selected = normalizeChargeModeCode($(this).val() || '');
    section.billing.selected_charge_mode = selected;
    section.billing.charge_mode_code = selected;
    syncPrimaryPanelFields(tb);
  });

  $('#tests-billing-sections').off('change', '.tb-patient-tbs').on('change', '.tb-patient-tbs', function () {
    const pid = String($(this).data('patient-id') || '');
    if (!pid) return;
    const tb = ensureTbObject(pid);
    tb.cce_level_tbs = normalizePatientTbs($(this).val());
    updatePatientTbsRequiredState(pid);
    updatePatientBookButtons(pid);
  });

  $('#tests-billing-sections').off('click', '.tb-open-panel-tests').on('click', '.tb-open-panel-tests', function () {
    const patientId = String($(this).data('patient-id'));
    const panelIndex = Number($(this).data('panel-index') || 0);
    openPanelTestsModal(patientId, panelIndex);
  });

  $('#tests-billing-sections').off('click', '.tb-add-panel').on('click', '.tb-add-panel', function () {
    const pid = String($(this).data('patient-id') || '');
    if (!pid) return;
    const tb = ensureTbObject(pid);
    tb.panels.push(normalizePanelSection({ panel: null, billing: null, selected_tests: [] }));
    renderTestsBilling();
    const key = panelDomKey(pid, tb.panels.length - 1);
    setTimeout(() => $(`#tb-panel-input-${key}`).trigger('focus'), 0);
  });

  $('#tests-billing-sections').off('click', '.tb-remove-panel').on('click', '.tb-remove-panel', function () {
    const pid = String($(this).data('patient-id') || '');
    const idx = Number($(this).data('panel-index') || 0);
    if (!pid || idx <= 0) return;
    const tb = ensureTbObject(pid);
    tb.panels.splice(idx, 1);
    syncPrimaryPanelFields(tb);
    renderTestsBilling();
  });

  $('#tests-billing-sections').off('click', '.tb-attach-prescription').on('click', '.tb-attach-prescription', function () {
    const pid = String($(this).data('patient-id') || '');
    if (!pid) return;
    $(`#tb-prescription-input-${pid}`).trigger('click');
  });

  $('#tests-billing-sections').off('change', '.tb-prescription-input').on('change', '.tb-prescription-input', function () {
    const pid = String($(this).data('patient-id') || '');
    const files = Array.from(this.files || []);
    if (!pid || !files.length) return;

    const formData = new FormData();
    files.forEach((f) => formData.append('files', f));

    const $btn = $(`.tb-attach-prescription[data-patient-id="${pid}"]`);
    const $input = $(this);
    $btn.prop('disabled', true).text('Uploading...');

    $.ajax({
      url: `/hhome-collection/patient/${pid}/prescriptions`,
      method: 'POST',
      data: formData,
      processData: false,
      contentType: false,
      success: function () {
        const map = wizardData.prescriptionUploads || {};
        map[pid] = getLocalPrescriptionUploadCount(pid) + files.length;
        wizardData.prescriptionUploads = map;
        renderTestsBilling();
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || 'Prescription upload failed');
      },
      complete: function () {
        $btn.prop('disabled', false).text('Attach Prescription');
        $input.val('');
      }
    });
  });

  $('#tests-billing-sections').off('click', '.tb-remove-prescription').on('click', '.tb-remove-prescription', function () {
    if (isModifyContextActive()) return;
    const pid = String($(this).data('patient-id') || '');
    const relName = String($(this).data('rel-name') || '');
    if (!pid || !relName) return;

    const $btn = $(this);
    $btn.prop('disabled', true);

    $.ajax({
      url: `/hhome-collection/patient/${pid}/prescriptions/remove`,
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ rel_name: relName }),
      success: function (res) {
        renderSelectedPatientsState(res.selected_patients || []);
        renderTestsBilling();
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || 'Prescription remove failed');
        $btn.prop('disabled', false);
      }
    });
  });

  $('#tests-billing-sections').off('click', '.tb-remove-selected-test').on('click', '.tb-remove-selected-test', function () {
    const pid = String($(this).data('patient-id') || '');
    const idx = Number($(this).data('panel-index') || 0);
    const booked = String($(this).data('booked-code') || '');
    if (!pid || !booked) return;

    const tb = ensureTbObject(pid);
    const section = getPanelSection(pid, idx);
    const selected = section.selected_tests || [];
    section.selected_tests = selected.filter((t) => {
      const sameB = String(t.booked_code || '').trim() === booked;
      return !sameB;
    });
    syncPrimaryPanelFields(tb);
    renderSelectedTestsForPanel(pid, idx);
  });
}

function legacySinglePanelRenderSelectedTestsForPatient(patientId) {
  const pid = String(patientId);
  const tb = ensureTbObject(pid);
  const selected = tb.selected_tests || [];
  $(`#tb-selected-count-${pid}`).text(selected.length);
  if (!selected.length) {
    $(`#tb-selected-list-${pid}`).html('<div class="text-muted">No tests selected yet</div>');
    return;
  }
  const html = selected.map((t) => {
    const code = (t.booked_code || '').toString().trim();
    const desc = (t.description || '').toString().trim();
    const label = [code, desc].filter(Boolean).join(' - ');
    return `
      <span class="badge text-bg-light border me-1 mb-1 d-inline-flex align-items-center gap-1">
        ${escHtml(label || 'Test')}
        <button
          type="button"
          class="tb-remove-selected-test"
          aria-label="Remove"
          data-patient-id="${escHtml(pid)}"
          data-booked-code="${escHtml(code)}"
        >×</button>
      </span>
    `;
  }).join('');
  $(`#tb-selected-list-${pid}`).html(html);
}

function legacyOpenPanelTestsModal(patientId) {
  const pid = String(patientId);
  const tb = ensureTbObject(pid);
  const compCatId = tb.billing?.comp_cat_id ?? '';
  if (!compCatId) {
    alert('Select a panel company, load the billing category, then click Book Test.');
    return;
  }

  activePanelPicker = {
    patientId: pid,
    compCatId,
    billingName: tb.billing?.cat_details || '',
    selectedGcode: '',
    selectedScode: '',
    tempSelected: (tb.selected_tests || []).reduce((acc, t) => {
      acc[testSelKey(t)] = t;
      return acc;
    }, {})
  };
  panelTestSearchQuery = '';

  const patientName = $(`#tb-patient-name-${pid}`).text() || `Patient ${pid}`;
  $('#panel-modal-meta').text(`Patient: ${patientName} | CompCatID: ${compCatId} | ${activePanelPicker.billingName}`);
  $('#panel-test-search').val('');
  $('#panel-test-search-note').text('Type 2 letters to search across all tests');

  $('#panel-groups-list').html('<div class="text-muted p-2">Loading groups...</div>');
  $('#panel-subgroups-list').html('<div class="text-muted p-2">Select group</div>');
  $('#panel-tests-list').html('<div class="text-muted p-2">Select subgroup</div>');
  $('#panel-child-tests-list').html('<div class="text-muted p-2">Child Tests button se list khulegi</div>');

  const modalEl = document.getElementById('panelTestsModal');
  panelTestsModal = new bootstrap.Modal(modalEl);
  panelTestsModal.show();
  loadPanelGroups();
}

function renderPanelTestsList(tests, emptyMessage) {
  const list = filterTestsByPatientGender(tests, activePanelPicker?.patientId || '');
  if (!list.length) {
    $('#panel-tests-list').html(`<div class="text-muted p-2">${escHtml(emptyMessage || 'No tests mapped')}</div>`);
    return;
  }

  const modalSelected = activePanelPicker.tempSelected || {};
  const selectedOwners = activePanelPicker.selectedOwners || {};
  const html = list.map(t => {
    const priced = pricedForPanel(t, activePanelPicker?.showMrp);
    const mrp = Number(priced.mrp || 0);
    const discount = Number(priced.max_discount || 0);
    const finalCharge = Math.max(0, Number(priced.charge || 0));
    const key = testSelKey(t);
    const owner = selectedOwners[key];
    const disabledByOtherPanel = owner && !owner.isCurrent;
    const checked = modalSelected[key] ? 'checked' : '';
    const disabled = disabledByOtherPanel ? 'disabled' : '';
    const alreadyLine = disabledByOtherPanel
      ? `<div class="panel-test-meta text-danger">Already selected in ${escHtml(owner.panelName || 'another panel')}</div>`
      : '';
    const childBtn = t.has_children
      ? `<button type="button" class="panel-child-btn"
            data-parent-gcode="${escHtml(t.gcode)}"
            data-parent-scode="${escHtml(t.scode)}"
            data-parent-test-code="${escHtml(t.test_code || '')}">
            Child Tests
         </button>`
      : '';
    const groupLine = [t.group_description, t.subgroup_description].filter(Boolean).join(' / ');
    return `
      <label class="panel-test-item">
        <input type="checkbox" class="panel-test-check" ${checked} ${disabled}
          data-gcode="${escHtml(t.gcode)}"
          data-scode="${escHtml(t.scode)}"
          data-test-code="${escHtml(t.test_code || '')}"
          data-testcode1="${escHtml(t.testcode1 || '')}"
          data-booked-code="${escHtml(t.booked_code || '')}"
          data-gender-rule="${escHtml(t.gender_rule || '')}"
          data-description="${escHtml(t.description || '')}"
          data-charge="${escHtml(finalCharge)}"
          data-mrp="${escHtml(mrp)}"
          data-max-discount="${escHtml(discount)}"
          data-max-allowed-discount="${escHtml(t.max_allowed_discount || '')}"
        />
          <div class="panel-test-main">
            <div><strong>${escHtml(t.description || '')}</strong></div>
            <div class="panel-test-meta">
              <span>${escHtml(t.booked_code || '')}</span>
              ${groupLine ? `| <span>${escHtml(groupLine)}</span>` : ''}
            </div>
            <div class="panel-test-meta">MRP: ${escHtml(formatCharge(mrp))} | Discount: ${escHtml(formatCharge(discount))} | Charge: ${escHtml(formatCharge(finalCharge))}</div>
            ${alreadyLine}
          </div>
          ${childBtn ? `<div class="panel-test-actions">${childBtn}</div>` : ''}
        </label>
    `;
  }).join('');

  $('#panel-tests-list').html(html);

  $('#panel-tests-list .panel-test-check').off('change').on('change', function () {
    const pick = {
      gcode: String($(this).data('gcode') || ''),
      scode: String($(this).data('scode') || ''),
      test_code: String($(this).data('test-code') || ''),
      testcode1: String($(this).data('testcode1') || ''),
      booked_code: String($(this).data('booked-code') || ''),
      gender_rule: String($(this).data('gender-rule') || ''),
      description: String($(this).data('description') || ''),
      charge: Number($(this).data('charge') || 0),
      mrp: Number($(this).data('mrp') || 0),
      max_discount: Number($(this).data('max-discount') || 0),
      max_allowed_discount: Number($(this).data('max-allowed-discount') || 0)
    };
    const key = testSelKey(pick);
    activePanelPicker.tempSelected = activePanelPicker.tempSelected || {};
    if ($(this).is(':checked')) {
      activePanelPicker.tempSelected[key] = pick;
    } else {
      delete activePanelPicker.tempSelected[key];
    }
  });

  $('#panel-tests-list .panel-child-btn').off('click').on('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    const parentGcode = String($(this).data('parent-gcode') || '');
    const parentScode = String($(this).data('parent-scode') || '');
    const parentTestCode = String($(this).data('parent-test-code') || '');
    loadPanelChildTests(parentGcode, parentScode, parentTestCode);
  });
}

function loadPanelTestsForCurrentView() {
  const query = String(panelTestSearchQuery || '').trim();
  if (query.length >= 2) {
    const seq = ++panelTestSearchSeq;
    $('#panel-tests-list').html('<div class="text-muted p-2">Searching tests...</div>');
    $('#panel-child-tests-list').html('<div class="text-muted p-2">Search mode active</div>');
    $('#panel-test-search-note').text(`Searching for "${query}" across all tests`);
    $.get('/hhome-collection/panel-test-search', {
      comp_cat_id: activePanelPicker.compCatId,
      q: query,
      limit: 50
    }, function (res) {
      if (seq !== panelTestSearchSeq) return;
      const tests = res.tests || [];
      renderPanelTestsList(tests, 'No matching tests found');
    }).fail(function () {
      if (seq !== panelTestSearchSeq) return;
      $('#panel-tests-list').html('<div class="text-danger p-2">Search failed</div>');
    });
    return;
  }
  panelTestSearchSeq += 1;

  $('#panel-test-search-note').text('Type 2 letters to search across all tests');
  $('#panel-tests-list').html('<div class="text-muted p-2">Loading tests...</div>');
  $('#panel-child-tests-list').html('<div class="text-muted p-2">Child Tests button se list khulegi</div>');
  $.get('/hhome-collection/panel-tests', {
    comp_cat_id: activePanelPicker.compCatId,
    gcode: activePanelPicker.selectedGcode,
    scode: activePanelPicker.selectedScode
  }, function (res) {
    renderPanelTestsList(res.tests || [], 'No tests mapped');
  }).fail(function () {
    $('#panel-tests-list').html('<div class="text-danger p-2">Load failed</div>');
  });
}

function loadPanelGroups() {
  $.get('/hhome-collection/panel-groups', { comp_cat_id: activePanelPicker.compCatId }, function (res) {
    const groups = res.groups || [];
    if (!groups.length) {
      $('#panel-groups-list').html('<div class="text-muted p-2">No groups mapped</div>');
      return;
    }
    const html = groups.map(g => `
      <div class="panel-row-item panel-group-item" data-gcode="${escHtml(g.gcode)}">
        <span class="panel-row-code">${escHtml(g.gcode)}</span>${escHtml(g.description || '')}
      </div>
    `).join('');
    $('#panel-groups-list').html(html);

    $('#panel-groups-list .panel-group-item').off('click').on('click', function () {
      $('#panel-groups-list .panel-row-item').removeClass('active');
      $(this).addClass('active');
      activePanelPicker.selectedGcode = String($(this).data('gcode') || '');
      activePanelPicker.selectedScode = '';
      loadPanelSubgroups();
    });

    const first = groups[0];
    if (first && first.gcode) {
      activePanelPicker.selectedGcode = String(first.gcode);
      $('#panel-groups-list .panel-group-item').first().addClass('active');
      loadPanelSubgroups();
    }
  });
}

function loadPanelSubgroups() {
  $('#panel-subgroups-list').html('<div class="text-muted p-2">Loading subgroups...</div>');
  $('#panel-tests-list').html('<div class="text-muted p-2">Select subgroup</div>');
  $('#panel-child-tests-list').html('<div class="text-muted p-2">Child Tests button se list khulegi</div>');
  $.get('/hhome-collection/panel-subgroups', {
    comp_cat_id: activePanelPicker.compCatId,
    gcode: activePanelPicker.selectedGcode
  }, function (res) {
    const subgroups = res.subgroups || [];
    if (!subgroups.length) {
      $('#panel-subgroups-list').html('<div class="text-muted p-2">No subgroups mapped</div>');
      return;
    }
    const html = subgroups.map(s => `
      <div class="panel-row-item panel-subgroup-item" data-scode="${escHtml(s.scode)}">
        <span class="panel-row-code">${escHtml(s.scode)}</span>${escHtml(s.description || '')}
      </div>
    `).join('');
    $('#panel-subgroups-list').html(html);

    $('#panel-subgroups-list .panel-subgroup-item').off('click').on('click', function () {
      $('#panel-subgroups-list .panel-row-item').removeClass('active');
      $(this).addClass('active');
      activePanelPicker.selectedScode = String($(this).data('scode') || '');
      loadPanelTestsForCurrentView();
    });

    const first = subgroups[0];
    if (first && first.scode) {
      activePanelPicker.selectedScode = String(first.scode);
      $('#panel-subgroups-list .panel-subgroup-item').first().addClass('active');
      loadPanelTestsForCurrentView();
    }
  });
}

function loadPanelTests() {
  loadPanelTestsForCurrentView();
}

function loadPanelChildTests(parentGcode, parentScode, parentTestCode) {
  if (!parentGcode || !parentScode || !parentTestCode) {
    $('#panel-child-tests-list').html('<div class="text-muted p-2">No child mapping</div>');
    return;
  }

  $('#panel-child-tests-list').html('<div class="text-muted p-2">Loading child tests...</div>');

  $.get('/hhome-collection/panel-child-tests', {
    parent_gcode: parentGcode,
    parent_scode: parentScode,
    parent_test_code: parentTestCode
  }, function (res) {
    const tests = filterTestsByPatientGender(res.tests || [], activePanelPicker?.patientId || '');
    if (!tests.length) {
      $('#panel-child-tests-list').html('<div class="text-muted p-2">No child tests found</div>');
      return;
    }

    const rows = tests.map(t => {
      const childBtn = t.has_children
        ? `<button type="button" class="panel-child-next-btn"
              data-parent-gcode="${escHtml(t.gcode)}"
              data-parent-scode="${escHtml(t.scode)}"
              data-parent-test-code="${escHtml(t.test_code || '')}">
              Child Tests
           </button>`
        : '';
      return `
        <div class="panel-child-item">
          <div class="panel-child-line"><strong>${escHtml(t.booked_code || '')}</strong> - ${escHtml(t.description || '')}</div>
          ${childBtn}
        </div>
      `;
    }).join('');

    $('#panel-child-tests-list').html(rows);

    $('#panel-child-tests-list .panel-child-next-btn').off('click').on('click', function () {
      const g = String($(this).data('parent-gcode') || '');
      const s = String($(this).data('parent-scode') || '');
      const tc = String($(this).data('parent-test-code') || '');
      loadPanelChildTests(g, s, tc);
    });
  }).fail(function () {
    $('#panel-child-tests-list').html('<div class="text-danger p-2">Child tests load failed</div>');
  });
}

function renderSelectedTestsForPatient(patientId) {
  renderSelectedTestsForPanel(patientId, 0);
}

function renderSelectedTestsForPanel(patientId, panelIndex) {
  const pid = String(patientId);
  const idx = Number(panelIndex || 0);
  const key = panelDomKey(pid, idx);
  const section = getPanelSection(pid, idx);
  const selected = section.selected_tests || [];
  $(`#tb-selected-count-${key}`).text(selected.length);
  if (!selected.length) {
    $(`#tb-selected-list-${key}`).html('<div class="text-muted">No tests selected yet</div>');
    return;
  }
  const html = selected.map((t) => {
    const code = (t.booked_code || '').toString().trim();
    const desc = (t.description || '').toString().trim();
    const label = [code, desc].filter(Boolean).join(' - ');
    return `
      <span class="badge text-bg-light border me-1 mb-1 d-inline-flex align-items-center gap-1">
        ${escHtml(label || 'Test')}
        <button
          type="button"
          class="tb-remove-selected-test"
          aria-label="Remove"
          data-patient-id="${escHtml(pid)}"
          data-panel-index="${idx}"
          data-booked-code="${escHtml(code)}"
        >x</button>
      </span>
    `;
  }).join('');
  $(`#tb-selected-list-${key}`).html(html);
}

function selectedTestOwnerMap(patientId, currentPanelIndex) {
  const map = {};
  getPatientPanels(patientId).forEach((section, idx) => {
    (section.selected_tests || []).forEach((t) => {
      const code = testSelKey(t);
      if (!code) return;
      map[code] = {
        panelIndex: idx,
        panelName: section.panel?.pname || `Panel ${idx + 1}`,
        isCurrent: Number(idx) === Number(currentPanelIndex || 0)
      };
    });
  });
  return map;
}

function getSelectedPatientGender(patientId) {
  const pid = String(patientId || '').trim();
  if (!pid) return '';
  const rows = Array.isArray(wizardData.selectedPatients) ? wizardData.selectedPatients : selectedPatientsCache;
  const hit = (rows || []).find((p) => String(p?.patient_id || p?.id || '').trim() === pid);
  return String(hit?.gender || '').trim().toLowerCase();
}

function genderRuleAllowsPatient(testItem, patientGender) {
  const rule = String(testItem?.gender_rule || '').trim();
  const gender = String(patientGender || '').trim().toLowerCase();
  if (!rule || rule === '1') return true;
  if (rule === '2') return gender === 'male';
  if (rule === '3') return gender === 'female';
  return true;
}

function filterTestsByPatientGender(tests, patientId) {
  const list = Array.isArray(tests) ? tests : [];
  const gender = getSelectedPatientGender(patientId);
  if (!gender) return list;
  return list.filter((t) => genderRuleAllowsPatient(t, gender));
}

function openPanelTestsModal(patientId, panelIndex = 0) {
  const pid = String(patientId);
  const idx = Number(panelIndex || 0);
  const section = getPanelSection(pid, idx);
  const compCatId = section.billing?.comp_cat_id ?? '';
  if (!compCatId) {
    alert('Select a panel company, load the billing category, then click Book Test.');
    return;
  }

  activePanelPicker = {
    patientId: pid,
    panelIndex: idx,
    compCatId,
    billingName: section.billing?.cat_details || '',
    showMrp: panelShowMrp(section),
    selectedGcode: '',
    selectedScode: '',
    selectedOwners: selectedTestOwnerMap(pid, idx),
    tempSelected: (section.selected_tests || []).reduce((acc, t) => {
      acc[testSelKey(t)] = t;
      return acc;
    }, {})
  };
  panelTestSearchQuery = '';

  const patientName = $(`#tb-patient-name-${pid}`).text() || `Patient ${pid}`;
  const panelName = section.panel?.pname || `Panel ${idx + 1}`;
  $('#panel-modal-meta').text(`Patient: ${patientName} | Panel: ${panelName} | CompCatID: ${compCatId} | ${activePanelPicker.billingName}`);
  $('#panel-test-search').val('');
  $('#panel-test-search-note').text('Type 2 letters to search across all tests');

  $('#panel-groups-list').html('<div class="text-muted p-2">Loading groups...</div>');
  $('#panel-subgroups-list').html('<div class="text-muted p-2">Select group</div>');
  $('#panel-tests-list').html('<div class="text-muted p-2">Select subgroup</div>');
  $('#panel-child-tests-list').html('<div class="text-muted p-2">Child Tests button se list khulegi</div>');

  const modalEl = document.getElementById('panelTestsModal');
  panelTestsModal = new bootstrap.Modal(modalEl);
  panelTestsModal.show();
  loadPanelGroups();
}

function applySelectedPanelTests() {
  const pid = String(activePanelPicker.patientId || '');
  const idx = Number(activePanelPicker.panelIndex || 0);
  if (!pid) return;
  const tb = ensureTbObject(pid);
  const section = getPanelSection(pid, idx);
  section.selected_tests = Object.values(activePanelPicker.tempSelected || {}).map((t) => {
    const priced = pricedForPanel(t, activePanelPicker?.showMrp);
    return {
      booked_code: String(t.booked_code || t.testcode1 || t.test_code || '').trim(),
      description: String(t.description || '').trim(),
      charge: Number(priced.charge || 0),
      mrp: Number(priced.mrp || 0),
      max_discount: Number(priced.max_discount || 0),
      max_allowed_discount: Number(priced.max_allowed_discount || 0)
    };
  }).filter((t) => !!t.booked_code);
  syncPrimaryPanelFields(tb);
  renderSelectedTestsForPanel(pid, idx);
  if (panelTestsModal) panelTestsModal.hide();
}

function renderPanelSectionHtml(pid, panelIndex) {
  const idx = Number(panelIndex || 0);
  const key = panelDomKey(pid, idx);
  const section = getPanelSection(pid, idx);
  const panel = section.panel || {};
  const billing = section.billing || {};
  const chargeModeDisplay = chargeModeLabel(billing.selected_charge_mode || billing.charge_mode_code || billing.charge_mode || '');
  const selectedCount = (section.selected_tests || []).length;
  const removeBtn = idx > 0
    ? `<button type="button" class="btn btn-outline-danger btn-sm tb-remove-panel" data-patient-id="${escHtml(pid)}" data-panel-index="${idx}">Remove</button>`
    : '';
  const label = idx === 0 ? 'Primary Panel' : `Additional Panel ${idx}`;

  return `
    <div class="tb-panel-section border rounded p-2 mb-2" data-patient-id="${escHtml(pid)}" data-panel-index="${idx}">
      <div class="d-flex align-items-center justify-content-between mb-2">
        <strong>${label}</strong>
        ${removeBtn}
      </div>
      <div class="row g-2">
        <div class="col-md-4 tb-panel-wrap">
          <label class="form-label">Panel Company</label>
          <input id="tb-panel-input-${key}" class="form-control tb-panel-search" data-patient-id="${escHtml(pid)}" data-panel-index="${idx}" value="${escHtml(panel.pname || '')}" placeholder="Type panel company (min 2 chars)">
          <div id="tb-panel-suggest-${key}" class="tb-panel-suggest d-none"></div>
        </div>
        <div class="col-md-3">
          <label class="form-label">Billing Category ID</label>
          <input id="tb-bill-id-${key}" class="form-control" value="${escHtml(billing.comp_cat_id ?? '')}" readonly>
        </div>
        <div class="col-md-3">
          <label class="form-label">Billing Category</label>
          <input id="tb-bill-name-${key}" class="form-control" value="${escHtml(billing.cat_details || '')}" readonly>
        </div>
        <div class="col-md-2">
          <label class="form-label">Charge Mode</label>
          <div id="tb-charge-mode-${key}" class="tb-charge-mode-view">${escHtml(chargeModeDisplay)}</div>
        </div>
        <div class="col-md-12 d-flex align-items-center justify-content-between">
          <div class="small text-muted">Selected tests: <strong id="tb-selected-count-${key}">${selectedCount}</strong></div>
          <button id="tb-book-btn-${key}" class="btn btn-dark btn-sm tb-open-panel-tests" data-patient-id="${escHtml(pid)}" data-panel-index="${idx}" ${String(billing.comp_cat_id ?? '').trim() ? '' : 'disabled'}>Book Test</button>
        </div>
        <div class="col-12">
          <div id="tb-selected-list-${key}" class="tb-tests-readonly"></div>
        </div>
      </div>
    </div>
  `;
}

function renderTestsBilling() {
  $.get('/hhome-collection/selected-patients', function (res) {
    const list = res.selected_patients || [];
    if (!list.length) {
      $('#tests-billing-sections').html('<div class="alert alert-warning">No patients selected.</div>');
      return;
    }

    const html = list.map(p => {
      const pid = String(p.patient_id);
      const existing = ensureTbObject(pid);
      const selectedTbs = normalizePatientTbs(existing.cce_level_tbs);
      const patientRefBy = String(existing.referred_by || p.referred_by || '').trim();
      const localUploadCount = getLocalPrescriptionUploadCount(pid);
      const stagedPrescriptionItems = !isModifyContextActive() ? getStagedPrescriptionItems(p) : [];
      const prescriptionChipHtml = stagedPrescriptionItems.map((item) => `
        <span class="badge border me-1 mb-1 d-inline-flex align-items-center gap-1" style="background:#f3e8d7;border-color:#d6b98f !important;color:#4a3420;">
          ${escHtml(item.label || 'Prescription')}
          <button
            type="button"
            class="tb-remove-prescription"
            aria-label="Remove prescription"
            data-patient-id="${escHtml(pid)}"
            data-rel-name="${escHtml(item.rel_name)}"
            style="border:0;background:transparent;color:#dc3545;font-weight:700;line-height:1;padding:0 2px;"
          >x</button>
        </span>
      `).join('');
      const firstSection = getPanelSection(pid, 0);
      if ((!firstSection.panel || !firstSection.panel.pname) && p.panel_company) {
        firstSection.panel = firstSection.panel || {};
        firstSection.panel.pname = p.panel_company;
        syncPrimaryPanelFields(existing);
      }
      return `
      <div class="card mb-2">
        <div class="card-body">
          <div class="d-flex flex-wrap align-items-center gap-3 mb-2 tb-patient-head-row">
            <h6 id="tb-patient-name-${pid}" class="tb-patient-head-name mb-0"><span>Name:</span> ${escHtml(p.full_name || '')}</h6>
            <div class="d-flex align-items-center gap-2 tb-refby-wrap position-relative">
              <label class="form-label mb-0 fw-bold text-nowrap" for="tb-patient-refby-${pid}">Ref By:</label>
              <input id="tb-patient-refby-${pid}" class="form-control form-control-sm tb-patient-refby" data-patient-id="${escHtml(pid)}" placeholder="Type 2 letters" value="${escHtml(patientRefBy)}" style="width: 190px;">
              <div id="tb-patient-refby-suggest-${pid}" class="tb-panel-suggest d-none"></div>
            </div>
            <div class="d-flex align-items-center gap-2">
              <label class="form-label mb-0 fw-bold text-nowrap" for="tb-patient-tbs-${pid}">Test_Bkg_Status <span class="text-danger">*</span></label>
              <select id="tb-patient-tbs-${pid}" class="form-select form-select-sm tb-patient-tbs" data-patient-id="${escHtml(pid)}" style="width: 240px;">
                <option value="">Select status</option>
                ${PATIENT_TBS_OPTIONS.map((opt) => `<option value="${opt.code}" ${selectedTbs === opt.code ? 'selected' : ''}>${escHtml(opt.label)}</option>`).join('')}
              </select>
            </div>
          </div>
          <div class="tb-panel-sections">
            ${existing.panels.map((_, idx) => renderPanelSectionHtml(pid, idx)).join('')}
          </div>
          <div class="d-flex justify-content-end align-items-center gap-2 mt-2">
            <div id="tb-prescription-upload-count-${pid}" class="small text-success ${localUploadCount ? '' : 'd-none'}">
              Uploaded in this booking: ${localUploadCount}
            </div>
            <div class="tb-staged-prescription-items flex-grow-1 text-start">
              ${prescriptionChipHtml}
            </div>
            <input id="tb-prescription-input-${pid}" type="file" class="d-none tb-prescription-input" data-patient-id="${pid}" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" multiple>
            <button type="button" class="btn btn-dark btn-sm tb-attach-prescription tb-compact-action" data-patient-id="${pid}">
              Attach Prescription
            </button>
            <button type="button" class="btn btn-outline-primary btn-sm tb-add-panel tb-compact-action" data-patient-id="${pid}">+ Add Panel</button>
          </div>
        </div>
      </div>`;
    }).join('');

    $('#tests-billing-sections').html(html);
    list.forEach(p => {
      const pid = String(p.patient_id);
      const tb = ensureTbObject(pid);
      tb.panels.forEach((section, idx) => {
        renderSelectedTestsForPanel(pid, idx);
        if (section.billing) renderChargeModeControl(pid, idx, section.billing);
      });
      updatePatientTbsRequiredState(pid);
      updatePatientBookButtons(pid);
    });
    list.forEach(p => autoResolveBillingFromPanel(String(p.patient_id), p.panel_company || '', 0));
  });
}

function goStep4() {
  const tb = wizardData.testsBilling || {};

  for (const pid of Object.keys(tb)) {
    const patientTb = ensureTbObject(pid);
    const tbsCode = normalizePatientTbs(patientTb.cce_level_tbs);
    if (!tbsCode) {
      alert('Each patient must have a Test Booking Status selected.');
      return;
    }
    if (tbsCode === 3) {
      continue;
    }
    const seenCodes = {};
    for (let idx = 0; idx < patientTb.panels.length; idx += 1) {
      const section = getPanelSection(pid, idx);
      if (!section?.panel?.pname || !section?.billing?.comp_cat_id) {
        alert('For each panel section, selecting a panel company and loading the billing category is required.');
        return;
      }
      const selectedMode = normalizeChargeModeCode(section.billing.selected_charge_mode || section.billing.charge_mode_code || '');
      if (!selectedMode || selectedMode.length > 1) {
        alert('For each panel section, selecting either Credit or Paying charge mode is required.');
        return;
      }
      for (const t of section.selected_tests || []) {
        const code = testSelKey(t);
        if (!code) continue;
        if (seenCodes[code] !== undefined) {
          alert(`Test ${code} is already selected in another panel for the same patient.`);
          return;
        }
        seenCodes[code] = idx;
      }
    }
  }

  wizardData.testsBilling = tb;
  setStep(4);
}

function renderReview() {
  $.get('/hhome-collection/summary', function (res) {
    if (!res.ok) {
      $('#booking-summary').html('Summary not available');
      $('#review-appointment-line').html('');
      $('#review-net-amount').html('');
      return;
    }

    loadTestSpecimenCatalog(function (catalog) {
      const caller = res.caller || {};
      const patients = res.selected_patients || [];
      const addr = res.selected_address || {};
      const ap = wizardData.appointment || {};
      const addressParts = [
        addr.house_flat_no ? { k: 'House/Flat No', v: escHtml(addr.house_flat_no) } : null,
        (addr.floor_display || addr.floor) ? { k: 'Floor', v: escHtml(addr.floor_display || addr.floor) } : null,
        addr.block_tower_no ? { k: 'Block/Tower No', v: escHtml(addr.block_tower_no) } : null,
        (addr.street_sector || addr.street_line) ? { k: 'Street/Sector', v: escHtml(addr.street_sector || addr.street_line) } : null,
        addr.landmark ? { k: 'Landmark', v: escHtml(addr.landmark) } : null,
        addr.city ? { k: 'City', v: escHtml(addr.city) } : null,
        (addr.colony_name || addr.colony_name_snapshot) ? { k: 'Colony', v: escHtml(addr.colony_name || addr.colony_name_snapshot) } : null,
        addr.pincode ? { k: 'Pincode', v: escHtml(addr.pincode) } : null,
        (addr.route_no || addr.route_no_snapshot) ? { k: 'Route', v: escHtml(addr.route_no || addr.route_no_snapshot) } : null
      ].filter(Boolean);
      const addressRowsHtml = addressParts.length
        ? addressParts.map((x) => `<div class="hc-review-addr-item"><span class="hc-review-addr-k">${x.k}:</span> <span class="hc-review-addr-v">${x.v}</span></div>`).join('')
        : '<div class="hc-review-addr-item">-</div>';
      const mobile = String(caller.primary_mobile || '').trim() || '-';
      const apDate = formatReviewDateDay(ap.preferred_visit_date || '');
      const apSlot = formatSlotCompact(ap.preferred_time_slot || '');
      $('#review-appointment-line').html(`<span class="hc-review-appointment-chip">Appointment: ${escHtml(apDate)} | ${escHtml(apSlot)}</span>`);

      $('#booking-summary').html(`
        <div class="hc-review-grid">
          <div class="hc-review-meta">
            <div><strong>Caller:</strong> ${escHtml(mobile)} | <strong>Patients:</strong> ${patients.length}</div>
            <div><strong>Google Location:</strong> <span class="hc-review-linkish">${escHtml(addr.google_location || '-')}</span></div>
            <div><strong>Referred By:</strong> ${escHtml(ap.referred_by || '-')}</div>
            <div><strong>Internal Referred By:</strong> ${escHtml(ap.internal_ref || '-')}</div>
            <div><strong>Lead ID:</strong> ${escHtml(ap.lead_id || '-')}</div>
          </div>
          <div class="hc-review-address-card">
            <div class="hc-review-address-title">Address:-</div>
            <div class="hc-review-address-grid">${addressRowsHtml}</div>
          </div>
        </div>
      `);

      let subTotalAmount = 0;
      let discountAmount = 0;
      let maxAllowedDiscountAmount = 0;
      let creditAmount = 0;
      let payingAmount = 0;
      const patientPricingMeta = {};
      const patientAdditionalById = { ...(wizardData.appointment.additional_discount_by_patient || {}) };
      const rows = patients.map(p => {
        const pid = String(p.patient_id);
        const tb = ensureTbObject(pid);
        const modifyFlow = getModifyFlowType();
        const isAutoFollowupPendingFlow = (modifyFlow === 'auto_followup_pending_child');
        const canApplyPendingOverlay = (
          modifyFlow === 'followup_appointment' ||
          modifyFlow === 'modify_appointment' ||
          isAutoFollowupPendingFlow
        );
        const pendingTests = canApplyPendingOverlay ? getPendingTestsForPatientFromModifyContext(pid) : [];
        const patientTbs = tbsLabel(tb?.cce_level_tbs);
        const patientTubeSet = new Set();
        let patientTotal = 0;
        let patientPayingTotal = 0;
        let patientAdditionalCap = 0;
        const panelRows = tb.panels.map((section, idx) => {
          const panelName = section?.panel?.pname || `Panel ${idx + 1}`;
          const billing = section?.billing || {};
          const baseSelectedTests = section?.selected_tests || [];
          let selectedTests = [];
          if (isAutoFollowupPendingFlow) {
            const seedParentCodes = getSeedParentCodesForPatient(pid);
            const filteredCurrent = (baseSelectedTests || []).filter((t) => {
              const code = String(t?.booked_code || '').trim().toUpperCase();
              return code && !seedParentCodes.has(code);
            });
            const pendingForSection = idx === 0 ? (pendingTests || []) : [];
            const merged = [...pendingForSection, ...filteredCurrent];
            const seen = new Set();
            selectedTests = merged.filter((t) => {
              const code = String(t?.booked_code || '').trim().toUpperCase();
              if (!code || seen.has(code)) return false;
              seen.add(code);
              return true;
            });
          } else {
            selectedTests = canApplyPendingOverlay
              ? applyPendingChildOverlay(baseSelectedTests, pendingTests)
              : baseSelectedTests;
          }
          const testCount = selectedTests.length;
          const selectedMode = normalizeChargeModeCode(billing.selected_charge_mode || billing.charge_mode_code || '');
          const isPayingPanel = selectedMode === 'P';
          const isCreditPanel = selectedMode === 'C';
          const isFreePanel = selectedMode === 'F';
          const showMrp = panelShowMrp(section);
          const testsSummary = renderReviewTestsHtml(selectedTests, catalog, selectedMode, showMrp);
          const chargeMode = chargeModeLabel(selectedMode);
          const panelMaxAllowed = isFreePanel ? 0 : selectedTests.reduce((acc, t) => acc + Number(t?.max_allowed_discount || 0), 0);
          const panelAdditionalCap = isFreePanel ? 0 : selectedTests.reduce((acc, t) => {
            const allowed = Number(t?.max_allowed_discount || 0);
            const base = showMrp ? 0 : Number(t?.max_discount || 0);
            return acc + Math.max(0, allowed - base);
          }, 0);
          patientTotal += Number(testsSummary.total || 0);
          subTotalAmount += Number(testsSummary.subtotal || 0);
          discountAmount += Number(testsSummary.discountTotal || 0);
          if (isPayingPanel) {
            maxAllowedDiscountAmount += Number(panelMaxAllowed || 0);
            payingAmount += Number(testsSummary.total || 0);
            patientPayingTotal += Number(testsSummary.total || 0);
            patientAdditionalCap += Number(panelAdditionalCap || 0);
          } else if (isCreditPanel) {
            creditAmount += Number(testsSummary.total || 0);
          } else if (!isFreePanel) {
            payingAmount += Number(testsSummary.total || 0);
          }
          (testsSummary.tubes || []).forEach((tube) => {
            const k = String(tube || '').trim().toLowerCase();
            if (k) patientTubeSet.add(tube);
          });
          return `
            <div class="border rounded p-2 mb-2 bg-white">
              <div class="d-flex flex-wrap gap-3 align-items-center mb-2 hc-review-top-strip">
                <span class="hc-review-panel-chip">${escHtml(panelName)}</span>
                <span class="hc-review-charge-chip">${escHtml(chargeMode)}</span>
                <span><strong>Test_Bkg_Status:</strong> <span style="color:#0b6b2d;font-weight:700;">${escHtml(patientTbs)}</span></span>
              </div>
              <div class="mb-1"><strong>Tests (${testCount}):</strong></div>
              ${testsSummary.html}
              <div class="d-flex flex-wrap align-items-center justify-content-between mt-2 hc-review-bottom-strip">
                <div class="ms-auto text-end"><strong>Charges: ${escHtml(formatCharge(testsSummary.total))}</strong></div>
              </div>
            </div>
          `;
        }).join('');
        const patientTubes = Array.from(patientTubeSet);
        const patientTubesText = patientTubes.length ? patientTubes.join(', ') : '-';
        const patientHasPaying = (tb.panels || []).some((section) => normalizeChargeModeCode(section?.billing?.selected_charge_mode || section?.billing?.charge_mode_code || '') === 'P');
        patientPricingMeta[pid] = {
          patient_name: String(p.full_name || `Patient ${pid}`),
          hasPayingPanel: patientHasPaying,
          total: Number(patientPayingTotal || 0),
          additional_cap: Number(patientHasPaying ? patientAdditionalCap : 0)
        };
        return `
        <div class="card mb-2">
          <div class="card-body">
            <h6 class="hc-patient-name-red"><span>Patient Name:</span> ${escHtml(p.full_name || '')}</h6>
            ${panelRows || '<div class="text-muted">No panel data.</div>'}
            <div class="d-flex flex-wrap align-items-center justify-content-between mt-2 hc-review-bottom-strip hc-review-bottom-strip-patient">
              <div><strong>Sample Tubes:</strong> ${escHtml(patientTubesText)}</div>
              <div class="text-end"><strong>Total Amount: ${escHtml(formatCharge(patientTotal))}</strong></div>
            </div>
          </div>
        </div>`;
      }).join('');

      $('#review-patient-sections').html(rows || '<div class="text-muted">No patient data.</div>');
      const addlState = getAdditionalDiscountState();
      const baseDiscount = Number(discountAmount || 0);
      const subTotal = Number(subTotalAmount || 0);
      const maxAllowed = Number(maxAllowedDiscountAmount || 0);
      try {
        const isModifyFlow = isModifyContextActive();
        const testsChangedInModify = hasModifyTestsChanged();
        const savedFinalDiscount = getDbSavedFinalDiscount();
        const savedAdditionalDiscount = getDbSavedAdditionalDiscount();
        if (isModifyFlow && testsChangedInModify && savedAdditionalDiscount > 0 && !Object.keys(patientAdditionalById).length) {
          const firstPayingPatientId = Object.keys(patientPricingMeta).find((pid) => Boolean(patientPricingMeta[pid]?.hasPayingPanel));
          if (firstPayingPatientId) {
            const firstCap = Number(patientPricingMeta[firstPayingPatientId]?.additional_cap || 0);
            if (firstCap > 0) {
              patientAdditionalById[firstPayingPatientId] = Math.min(savedAdditionalDiscount, firstCap);
            }
          }
        }
        Object.keys(patientAdditionalById).forEach((pid) => {
          const meta = patientPricingMeta[String(pid)] || null;
          if (!meta || !meta.hasPayingPanel) {
            delete patientAdditionalById[pid];
            return;
          }
          const cap = Number(meta.additional_cap || 0);
          const applied = Math.min(Math.max(0, Number(patientAdditionalById[pid] || 0)), cap);
          patientAdditionalById[pid] = applied;
        });
        const aggregateAdditional = Object.keys(patientAdditionalById).reduce((acc, pid) => acc + Number(patientAdditionalById[pid] || 0), 0);
        const effectiveAdditional = Math.min(Math.max(0, aggregateAdditional), Math.max(0, maxAllowed));
        const cappedTotalDiscount = baseDiscount + effectiveAdditional;
        wizardData.appointment = wizardData.appointment || {};
        const additionalDisplay = (isModifyFlow && !testsChangedInModify)
          ? (savedAdditionalDiscount > 0 ? savedAdditionalDiscount : effectiveAdditional)
          : effectiveAdditional;
        wizardData.appointment.additional_discount_amount = additionalDisplay;
        wizardData.appointment.additional_discount_by_patient = patientAdditionalById;
        const finalDiscountDisplay = (isModifyFlow && !testsChangedInModify)
          ? (savedFinalDiscount > 0 ? savedFinalDiscount : (baseDiscount + additionalDisplay))
          : cappedTotalDiscount;
        const computedNet = Math.max(0, subTotal - Number(finalDiscountDisplay || 0));
        $('#review-net-amount').html(`
          <div class="hc-review-total-line"><strong>Sub Total:</strong> <span id="rv-subtotal-v">${escHtml(formatCharge(subTotal))}</span></div>
          <div class="hc-review-total-line"><strong>Credit Amount:</strong> <span id="rv-credit-v">${escHtml(formatCharge(creditAmount))}</span></div>
          <div class="hc-review-total-line"><strong>Paying Amount:</strong> <span id="rv-paying-v">${escHtml(formatCharge(payingAmount))}</span></div>
          <div class="hc-review-total-line"><strong>Base Discount:</strong> <span id="rv-base-discount-v">${escHtml(formatCharge(baseDiscount))}</span></div>
          <div class="hc-review-total-line"><strong>Additional:</strong> <span id="rv-additional-v">${escHtml(formatCharge(additionalDisplay))}</span></div>
          <div class="hc-review-total-line"><strong>Final Discount:</strong> <span id="rv-final-discount-v">${escHtml(formatCharge(finalDiscountDisplay))}</span></div>
        `);
        $('#review-final-amount-wrap').html(`
          <span class="hc-review-net-chip"><strong>Final Amount:</strong> <span id="rv-final-amount-v">${escHtml(formatCharge(computedNet))}</span></span>
        `);
      } catch (e) {
        console.error('review footer render failed:', e);
        const fallbackNet = Math.max(0, subTotal - Number(baseDiscount || 0));
        $('#review-net-amount').html(`
          <div class="hc-review-total-line"><strong>Sub Total:</strong> <span>${escHtml(formatCharge(subTotal))}</span></div>
          <div class="hc-review-total-line"><strong>Credit Amount:</strong> <span>${escHtml(formatCharge(creditAmount))}</span></div>
          <div class="hc-review-total-line"><strong>Paying Amount:</strong> <span>${escHtml(formatCharge(payingAmount))}</span></div>
          <div class="hc-review-total-line"><strong>Base Discount:</strong> <span>${escHtml(formatCharge(baseDiscount))}</span></div>
          <div class="hc-review-total-line"><strong>Additional:</strong> <span>0</span></div>
          <div class="hc-review-total-line"><strong>Final Discount:</strong> <span>${escHtml(formatCharge(baseDiscount))}</span></div>
        `);
        $('#review-final-amount-wrap').html(`<span class="hc-review-net-chip"><strong>Final Amount:</strong> <span>${escHtml(formatCharge(fallbackNet))}</span></span>`);
      }
      const payingPatientOptions = Object.entries(patientPricingMeta)
        .filter(([, meta]) => Boolean(meta?.hasPayingPanel))
        .map(([pid, meta]) => `<option value="${escHtml(pid)}">${escHtml(meta.patient_name || `Patient ${pid}`)}</option>`)
        .join('');
      if (payingPatientOptions) {
        $('#review-additional-wrap').html(`
          <div class="hc-addl-discount-row">
            <button type="button" id="btn-additional-discount" class="btn btn-sm hc-additional-btn">+ Additional Discount</button>
            <div id="additional-discount-controls" class="hc-addl-discount-controls d-none">
              <select id="additional-discount-patient" class="form-select form-select-sm me-2" style="max-width:220px;">
                <option value="">Select patient</option>
                ${payingPatientOptions}
              </select>
              <label class="me-2"><input type="radio" name="additional-discount-type" value="amount"> Amount</label>
              <label class="me-2"><input type="radio" name="additional-discount-type" value="percent"> Percent</label>
              <input type="number" min="0" step="0.01" id="additional-discount-value" class="form-control form-control-sm d-none" placeholder="Enter value">
              <button type="button" id="btn-apply-additional-discount" class="btn btn-primary btn-sm d-none">Apply</button>
            </div>
          </div>
        `);
      } else {
        $('#review-additional-wrap').html('');
      }
      $('#btn-additional-discount').off('click').on('click', function () {
        $('#additional-discount-controls').toggleClass('d-none');
      });
      if (addlState.mode) {
        $(`input[name=\"additional-discount-type\"][value=\"${addlState.mode}\"]`).prop('checked', true);
        $('#additional-discount-value').removeClass('d-none').val(addlState.value > 0 ? addlState.value : '');
        $('#btn-apply-additional-discount').removeClass('d-none');
      }
      $('input[name="additional-discount-type"]').off('change').on('change', function () {
        const mode = String($(this).val() || '');
        wizardData.appointment.additional_discount_mode = mode;
        $('#additional-discount-value').removeClass('d-none');
        $('#btn-apply-additional-discount').removeClass('d-none');
      });
      $('#btn-apply-additional-discount').off('click').on('click', function () {
        const selectedPid = String($('#additional-discount-patient').val() || '').trim();
        const mode = String(($('input[name="additional-discount-type"]:checked').val() || '')).toLowerCase();
        const value = Number($('#additional-discount-value').val() || 0);
        const selectedMeta = patientPricingMeta[selectedPid] || null;
        if (!selectedPid || !selectedMeta) {
          alert('Please select patient first.');
          return;
        }
        if (!mode) {
          alert('Please select Amount or Percent first.');
          return;
        }
        wizardData.appointment.additional_discount_mode = mode;
        wizardData.appointment.additional_discount_value = Number.isFinite(value) && value > 0 ? value : 0;
        const addlAmount = computeAdditionalDiscountAmount(mode, wizardData.appointment.additional_discount_value, Number(selectedMeta.total || 0));
        const cap = Number(selectedMeta.additional_cap || 0);
        if (Number(addlAmount || 0) > cap) {
          alert(`You can apply additional discount up to ${formatCharge(cap)}.`);
          return;
        }
        patientAdditionalById[selectedPid] = Number(addlAmount || 0);
        wizardData.appointment.additional_discount_by_patient = patientAdditionalById;
        const aggregateAdditional = Object.keys(patientAdditionalById).reduce((acc, pid) => {
          const m = patientPricingMeta[pid] || {};
          const patientCap = Number(m.additional_cap || 0);
          const v = Math.min(Math.max(0, Number(patientAdditionalById[pid] || 0)), patientCap);
          return acc + v;
        }, 0);
        const totalDiscount = baseDiscount + Number(aggregateAdditional || 0);
        const savedNow = getDbSavedFinalDiscount();
        const finalDiscount = (isModifyContextActive() && !hasModifyTestsChanged())
          ? (savedNow > 0 ? savedNow : totalDiscount)
          : totalDiscount;
        const finalAmount = Math.max(0, subTotal - finalDiscount);
        wizardData.appointment.additional_discount_amount = Number(aggregateAdditional || 0);
        $('#rv-additional-v').text(formatCharge(aggregateAdditional));
        $('#rv-final-discount-v').text(formatCharge(finalDiscount));
        $('#rv-final-amount-v').text(formatCharge(finalAmount));
      });
    });
  });
}

function confirmBooking() {
  syncAppointmentTagsFromTopBar();
  $.get('/hhome-collection/selected-patients', function (res) {
    const selectedPatients = res.selected_patients || [];
    for (const p of selectedPatients) {
      const pid = String(p.patient_id || '');
      const tb = ensureTbObject(pid);
      const tbsCode = normalizePatientTbs(tb.cce_level_tbs);
      if (!tbsCode) {
        alert(`Selecting Test Booking Status is mandatory for ${p.full_name || pid}.`);
        return;
      }
      const count = Number(p.prescription_file_count || 0);
      if (tbsCode === 2 && count <= 0) {
        alert(`${p.full_name || pid} prescription upload is pending...`);
        return;
      }
    }

    const testsMetaMap = {};
    selectedPatients.forEach(p => {
      const tb = ensureTbObject(String(p.patient_id));
      testsMetaMap[p.patient_id] = {
        patient_id: Number(p.patient_id || 0),
        referred_by: String($(`#tb-patient-refby-${p.patient_id}`).val() || tb.referred_by || '').trim(),
        cce_level_tbs: normalizePatientTbs(tb.cce_level_tbs),
        panel: tb.panel || null,
        billing: tb.billing || null,
        selected_tests: tb.selected_tests || [],
        panels: (tb.panels || []).map((section) => ({
          panel: section.panel || null,
          billing: section.billing || null,
          selected_tests: section.selected_tests || []
        }))
      };
    });

    const payload = {
      preferred_visit_date: wizardData.appointment.preferred_visit_date,
      preferred_time_slot: wizardData.appointment.preferred_time_slot,
      referred_by: wizardData.appointment.referred_by || '',
      intrnl_rfrncd_by: wizardData.appointment.internal_ref || '',
      lead_id: wizardData.appointment.lead_id || '',
      remarks: wizardData.appointment.remarks || '',
      permanent_tags: wizardData.appointment.permanent_tags || '',
      booking_tags: wizardData.appointment.booking_tags || '',
      additional_discount_mode: wizardData.appointment.additional_discount_mode || '',
      additional_discount_value: Number(wizardData.appointment.additional_discount_value || 0),
      additional_discount_amount: Number(wizardData.appointment.additional_discount_amount || 0),
      additional_discount_by_patient: wizardData.appointment.additional_discount_by_patient || {},
      pending_tests_map_snapshot: wizardData?.modify?.pending_tests_map || {},
      parent_context_map_snapshot: wizardData?.modify?.parent_context_map || wizardData?.modify?.parent_seed_tests_map || {},
      patient_tests_meta_map: testsMetaMap
    };

    const isModify = Number(wizardData?.modify?.booking_id || 0) > 0;
    $.ajax({
      url: isModify ? '/hhome-collection/modify-booking' : '/hhome-collection/confirm-booking',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
      success: function (r) {
        $.get(`/hhome-collection/success?booking_id=${r.booking_id}`, function (html) {
          $('#wizard-left-panel').html(html);
          setLayoutForSuccess();
          $('#linked-patients-panel').html('');
          $('#reference-addresses-panel').html('');
          $('.step-pill').removeClass('active');
          wizardData = { searchedMobile: '', appointment: {}, testsBilling: {}, prescriptionUploads: {}, modify: {} };
          selectedPatientTags = [];
          selectedPermanentTags = [];
          selectedBookingTags = [];
        });
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || 'Booking failed');
      }
    });
  });
}

$(function () {
  if (!$('#wizard-left-panel').length) return;
  bindCallerHistoryChipEvents();
  setStep(1);
  renderRightPanelState([], [], null);

  $(document).on('click', '.step-pill', function () {
    const step = Number($(this).data('step'));
    syncAppointmentFromStep2Inputs();
    if (step >= 3) {
      syncAppointmentTagsFromTopBar();
    }
    if (step < 1 || step > 4) return;
    if (step === currentStep) return;
    if (step >= 2) {
      ensureStep1Ready(() => setStep(step));
      return;
    }
    setStep(step);
  });
});
