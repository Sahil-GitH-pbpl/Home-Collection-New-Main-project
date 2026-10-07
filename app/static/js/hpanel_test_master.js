(function () {
  let selectedCompCatId = '';
  let panelFlags = {};
  let currentTests = [];
  let tatTemplates = [];
  let selectedTatTemplateId = '';
  let activeTatRow = null;
  let activeTatMode = 'routine';
  let searchTimer = null;
  let pendingShowMrp = {};
  let pendingShowInHc = {};

  function esc(v) {
    return String(v ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function panelKey(compCatId, panelName) {
    return `${String(compCatId || '')}|${String(panelName || '').trim().toLowerCase()}`;
  }

  function renderPanelRows(items) {
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) {
      $('#ptm-panel-tbody').html('<tr><td colspan="7" class="text-muted text-center py-3">No panel company found.</td></tr>');
      return;
    }
    const html = rows.map((x, i) => {
      const comp = String(x.CompCatID ?? '');
      const pname = String(x.pname || '');
      const key = panelKey(comp, pname);
      if (!panelFlags[key]) {
        panelFlags[key] = {
          showmrp: Number(x.showmrp || 0) === 1,
          showinHC: Number(x.showinHC || x.showinhc || 0) === 1
        };
      }
      const flags = panelFlags[key] || {};
      const selectedClass = selectedCompCatId && selectedCompCatId === comp ? 'ptm-selected-row' : '';
      const pendingClass = (pendingShowMrp[key] || pendingShowInHc[key]) ? 'ptm-pending-mrp' : '';
      return `
        <tr class="${selectedClass} ${pendingClass}" data-comp-cat-id="${esc(comp)}" data-panel-name="${esc(pname)}">
          <td>${i + 1}</td>
          <td>${esc(pname)}</td>
          <td>${esc(comp)}</td>
          <td>${esc(x.CatDetails || '')}</td>
          <td>${esc(x.BillingChargeMode || '')}</td>
          <td class="text-center"><input type="checkbox" class="form-check-input ptm-flag-panel" data-comp-cat-id="${esc(comp)}" data-panel-name="${esc(pname)}" data-flag="showmrp" ${flags.showmrp ? 'checked' : ''}></td>
          <td class="text-center"><input type="checkbox" class="form-check-input ptm-flag-panel" data-comp-cat-id="${esc(comp)}" data-panel-name="${esc(pname)}" data-flag="showinHC" ${flags.showinHC ? 'checked' : ''}></td>
        </tr>
      `;
    }).join('');
    $('#ptm-panel-tbody').html(html);
  }

  function updateApplyButton() {
    const hasChanges = Object.keys(pendingShowMrp).length || Object.keys(pendingShowInHc).length;
    $('#ptm-apply-show-mrp').toggleClass('d-none', !hasChanges);
  }

  function formatScheduleType(v) {
    const raw = String(v || '');
    if (raw === 'all_days') return 'All Days';
    if (raw === 'scheduled_days') return 'Scheduled Days';
    return '';
  }

  function formatTime(v) {
    const raw = String(v || '').trim();
    if (!raw) return '';
    const parts = raw.split(':');
    let hour = Number(parts[0] || 0);
    const minute = String(parts[1] || '00').padStart(2, '0');
    const ampm = hour >= 12 ? 'PM' : 'AM';
    hour = hour % 12 || 12;
    return `${hour}:${minute} ${ampm}`;
  }

  function normalizeReportDays(v) {
    if (Array.isArray(v)) return v.filter(Boolean);
    return String(v || '').split(',').map((x) => x.trim()).filter(Boolean);
  }

  function daysText(days) {
    const arr = normalizeReportDays(days);
    if (!arr.length) return '';
    const short = {
      monday: 'Mon',
      tuesday: 'Tue',
      wednesday: 'Wed',
      thursday: 'Thu',
      friday: 'Fri',
      saturday: 'Sat',
      sunday: 'Sun'
    };
    return arr.map((day) => short[String(day || '').toLowerCase()] || day).join('/');
  }

  function formatProcessTime(value, unit) {
    const num = Number(value || 0);
    if (!Number.isFinite(num) || num <= 0) return '';
    const raw = String(unit || 'hours').toLowerCase();
    const rawUnit = ['minutes', 'hours', 'days'].includes(raw) ? raw : 'hours';
    const label = rawUnit === 'days' ? 'day' : (rawUnit === 'minutes' ? 'minute' : 'hour');
    return `${num} ${num === 1 ? label : `${label}s`}`;
  }

  function selectedTatTemplate() {
    const id = String(selectedTatTemplateId || '');
    if (!id) return null;
    return tatTemplates.find((x) => String(x.id || '') === id) || null;
  }

  function clearTatTemplateSelection() {
    if (!selectedTatTemplateId) return;
    selectedTatTemplateId = '';
    renderTatTemplates();
  }

  function renderTatTemplates() {
    const rows = tatTemplates.map((x) => {
      const id = String(x.id || '');
      const active = selectedTatTemplateId && selectedTatTemplateId === id;
      return `
        <tr class="${active ? 'table-primary' : ''}">
          <td><strong>${esc(x.template_name || '')}</strong></td>
          <td>${esc(x.final_tat_text || '')}</td>
          <td>${esc(x.note || '')}</td>
          <td><button type="button" class="tat-template-select${active ? ' is-selected' : ''}" data-template-id="${esc(id)}">${active ? 'Selected' : 'Select'}</button></td>
        </tr>
      `;
    }).join('');
    $('#tat-template-tbody').html(rows || '<tr><td colspan="4" class="text-muted text-center py-2">No templates found.</td></tr>');
  }

  function buildFinalTatText(scheduleType, cutoffTime, processValue, reportDays, processUnit) {
    const cutoff = formatTime(cutoffTime);
    const processText = formatProcessTime(processValue, processUnit);
    if (!scheduleType || !processText) return '';
    if (scheduleType === 'all_days') {
      return cutoff
        ? `Daily | Cutoff ${cutoff} | Report after ${processText} | After cutoff: next day`
        : `Daily | Report after ${processText}`;
    }
    if (scheduleType === 'scheduled_days') {
      const dtext = daysText(reportDays);
      if (!dtext) return '';
      return cutoff
        ? `${dtext} | Cutoff ${cutoff} | Report after ${processText} | After cutoff: next report day`
        : `${dtext} | Report after ${processText}`;
    }
    return '';
  }

  function hasUrgentTat(row) {
    return Number(row?.urgent_tat || 0) === 1 && String(row?.urgent_tat_text || '').trim().length > 0;
  }

  function hasNormalTat(row) {
    return String(row?.final_tat_text || '').trim().length > 0;
  }

  function renderTestRows(items) {
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) {
      $('#ptm-test-tbody').html('<tr><td colspan="9" class="text-muted text-center py-3">No tests found.</td></tr>');
      return;
    }
    const html = rows.map((x, i) => {
      const isProfile = Number(x.is_profile || 0) === 1 || Number(x.has_children || 0) === 1;
      const hasTat = hasUrgentTat(x) || hasNormalTat(x);
      const displaySchedule = x.schedule_type;
      const displayCutoff = x.cutoff_time;
      const displayProcess = x.process_time_value;
      const displayUnit = x.process_time_unit;
      const displayDays = x.report_days;
      const displayFinal = x.final_tat_text;
      const actionHtml = isProfile
        ? `<button type="button" class="ptm-edit-tat ptm-view-tat" data-test-code="${esc(x.test_code || '')}">View TAT</button>`
        : `<button type="button" class="ptm-edit-tat${hasTat ? ' ptm-edit-tat-existing' : ''}" data-test-code="${esc(x.test_code || '')}">${hasTat ? 'Edit' : 'Create TAT'}</button>`;
      return `
        <tr data-test-code="${esc(x.test_code || '')}">
          <td>${i + 1}</td>
          <td class="mono">${esc(x.test_code || '')}</td>
          <td>${esc(x.test_name || '')}</td>
          <td>${isProfile && !hasTat ? '<span class="text-muted">-</span>' : (esc(formatScheduleType(displaySchedule)) || '<span class="text-muted">-</span>')}</td>
          <td>${isProfile && !hasTat ? '<span class="text-muted">-</span>' : (esc(formatTime(displayCutoff)) || '<span class="text-muted">-</span>')}</td>
          <td>${isProfile && !hasTat ? '<span class="text-muted">-</span>' : (esc(formatProcessTime(displayProcess, displayUnit)) || '<span class="text-muted">-</span>')}</td>
          <td>${isProfile && !hasTat ? '<span class="text-muted">-</span>' : (esc(String(displayDays || '').replaceAll(',', ', ')) || '<span class="text-muted">-</span>')}</td>
          <td class="ptm-final-cell">${renderTatSummaryCell(x, isProfile, hasTat, displayFinal)}</td>
          <td>${actionHtml}</td>
        </tr>
      `;
    }).join('');
    $('#ptm-test-tbody').html(html);
  }

  function renderTatSummaryCell(row, isProfile, hasTat, displayFinal) {
    if (isProfile && !hasTat) return '<span class="text-muted">Child test-wise TAT</span>';
    return displayFinal ? esc(displayFinal) : '<span class="text-muted">-</span>';
  }

  function filterCurrentTests() {
    const q = String($('#ptm-test-search').val() || '').trim().toLowerCase();
    if (q.length < 2) {
      renderTestRows(currentTests);
      return;
    }
    renderTestRows(currentTests.filter((x) => String(x.test_name || '').toLowerCase().includes(q)));
  }

  function loadTatTests() {
    $('#ptm-test-tbody').html('<tr><td colspan="9" class="text-muted text-center py-3">Loading TAT master...</td></tr>');
    $.get('/hhome-collection/test-tat-master', function (res) {
      currentTests = res?.items || [];
      renderTestRows(currentTests);
    }).fail(function (xhr) {
      const msg = xhr?.responseJSON?.message || 'Failed to load TAT master';
      $('#ptm-test-tbody').html(`<tr><td colspan="9" class="text-danger text-center py-3">${esc(msg)}</td></tr>`);
    });
  }

  function loadTatTemplates() {
    $.get('/hhome-collection/tat-templates', function (res) {
      tatTemplates = res?.items || [];
      renderTatTemplates();
    }).fail(function () {
      tatTemplates = [];
      renderTatTemplates();
    });
  }

  function loadInitialPanels() {
    $.get('/hhome-collection/panel-companies-initial', { limit: 5, master: 1 }, function (res) {
      renderPanelRows(res?.items || []);
    }).fail(function () {
      renderPanelRows([]);
    });
  }

  function searchPanels(q) {
    const text = String(q || '').trim();
    if (text.length < 2) {
      loadInitialPanels();
      return;
    }
    $.get('/hhome-collection/panel-companies', { q: text, limit: 50, master: 1 }, function (res) {
      renderPanelRows(res?.items || []);
    }).fail(function () {
      renderPanelRows([]);
    });
  }

  function selectedTatDays() {
    return $('.tat-day:checked').map(function () {
      return String($(this).val() || '');
    }).get();
  }

  function refreshTatPreview() {
    const selectedTemplate = selectedTatTemplate();
    if (selectedTemplate) {
      const text = String(selectedTemplate.final_tat_text || '').trim();
      const note = String(selectedTemplate.note || '').trim();
      $('#tat-template-selected').text(`${selectedTemplate.template_name || 'Template'}: ${note ? `${text} | ${note}` : text}`);
      $('#tat-template-inline-status').text(`${selectedTemplate.template_name || 'Template'} selected`);
      $('#tat-final-preview').text(text || 'Selected template has blank TAT.');
      return text;
    }
    $('#tat-template-selected').text('No template selected.');
    $('#tat-template-inline-status').text('Manual TAT');
    const scheduleType = String($('#tat-type').val() || '');
    const cutoff = String($('#tat-cutoff-time').val() || '');
    const processValue = String($('#tat-process-hours').val() || '');
    const processUnit = String($('#tat-process-unit').val() || 'hours');
    $('#tat-report-days-wrap').toggleClass('ptm-invisible', scheduleType !== 'scheduled_days');
    if (scheduleType === 'all_days') $('.tat-day').prop('checked', false);
    const text = buildFinalTatText(scheduleType, cutoff, processValue, selectedTatDays(), processUnit);
    const existingText = activeTatMode === 'urgent'
      ? String(activeTatRow?.urgent_tat_text || '').trim()
      : String(activeTatRow?.final_tat_text || '').trim();
    const previewText = text || existingText;
    $('#tat-final-preview').text(previewText || 'Select schedule type and process time.');
    return previewText;
  }

  function applyTatModeToForm(mode) {
    if (!activeTatRow) return;
    activeTatMode = mode || 'routine';
    const useUrgent = activeTatMode === 'urgent';
    $('.ptm-tat-mode-btn').removeClass('active');
    $(`.ptm-tat-mode-btn[data-mode="${activeTatMode}"]`).addClass('active');
    selectedTatTemplateId = String(useUrgent ? (activeTatRow.urgent_template_id || '') : (activeTatRow.routine_template_id || ''));
    $('#tat-template-box').addClass('d-none');
    $('.ptm-manual-section').removeClass('d-none');
    $('#tat-template-selected').text('No template selected.');
    $('#tat-template-inline-status').text('Manual TAT');
    $('#tat-template-open').removeClass('active');
    renderTatTemplates();
    const scheduleType = useUrgent ? (activeTatRow.urgent_schedule_type || '') : (activeTatRow.schedule_type || '');
    $('#tat-type').val(scheduleType);
    $('input[name="tat_schedule_type"]').prop('checked', false);
    if (scheduleType) $(`input[name="tat_schedule_type"][value="${scheduleType}"]`).prop('checked', true);
    $('#tat-process-hours').val(useUrgent ? (activeTatRow.urgent_process_time_value || '') : (activeTatRow.process_time_value || ''));
    $('#tat-process-unit').val(useUrgent ? (activeTatRow.urgent_process_time_unit || 'hours') : (activeTatRow.process_time_unit || 'hours'));
    $('#tat-cutoff-time').val(useUrgent ? (activeTatRow.urgent_cutoff_time || '') : (activeTatRow.cutoff_time || ''));
    const daySet = new Set(normalizeReportDays(useUrgent ? activeTatRow.urgent_report_days : activeTatRow.report_days));
    $('.tat-day').each(function () {
      $(this).prop('checked', daySet.has(String($(this).val() || '')));
    });
    $('#tat-modal-save').text('Save');
    refreshTatPreview();
  }

  function openTatModal(row) {
    activeTatRow = row || null;
    if (!activeTatRow) return;
    const hasTat = hasUrgentTat(activeTatRow) || hasNormalTat(activeTatRow);
    $('#tat-modal-title').text(hasTat ? 'Edit TAT' : 'Create TAT');
    $('#tat-test-code').val(activeTatRow.test_code || '');
    $('#tat-test-name').val(activeTatRow.test_name || '');
    applyTatModeToForm('routine');
    $('#tat-modal-backdrop, #tat-modal').removeClass('d-none');
  }

  function closeTatModal() {
    activeTatRow = null;
    activeTatMode = 'routine';
    selectedTatTemplateId = '';
    $('#tat-template-box').addClass('d-none');
    $('#tat-template-inline-status').text('Manual TAT');
    $('#tat-template-open').removeClass('active');
    $('#tat-modal-backdrop, #tat-modal').addClass('d-none');
  }

  function openProfileTatModal(row) {
    if (!row) return;
    $('#profile-tat-modal-title').text(`${row.test_name || row.test_code || 'Profile'} - TAT`);
    $('#profile-tat-list').html('<div class="text-muted py-2">Loading child TAT...</div>');
    $('#profile-tat-override').data('test-code', row.test_code || '');
    $('#tat-modal-backdrop, #profile-tat-modal').removeClass('d-none');
    $.get(`/hhome-collection/test-tat-master/profile/${encodeURIComponent(row.test_code || '')}`, function (res) {
      if (String(res?.override_tat_text || '').trim()) {
        $('#profile-tat-list').html(`
          <div class="ptm-profile-override-box">
            <div class="text-muted small mb-1">Profile override TAT</div>
            <strong>${esc(res.override_tat_text)}</strong>
          </div>
        `);
        return;
      }
      const groups = Array.isArray(res?.groups) ? res.groups : [];
      if (!groups.length) {
        $('#profile-tat-list').html('<div class="text-muted py-2">No child tests found.</div>');
        return;
      }
      const html = groups.map((group) => {
        const tests = Array.isArray(group.tests) ? group.tests : [];
        return `
          <div class="ptm-profile-tat-group">
            <strong>${esc(group.tat_text || 'TAT not set')}</strong>
            <div>${tests.map((name) => `<div>${esc(name)}</div>`).join('')}</div>
          </div>
        `;
      }).join('');
      $('#profile-tat-list').html(html);
    }).fail(function (xhr) {
      $('#profile-tat-list').html(`<div class="text-danger py-2">${esc(xhr?.responseJSON?.message || 'Failed to load child TAT')}</div>`);
    });
  }

  function closeProfileTatModal() {
    $('#tat-modal-backdrop, #profile-tat-modal').addClass('d-none');
  }

  function updateTatRow(saved) {
    const code = String(saved?.test_code || activeTatRow?.test_code || '');
    const idx = currentTests.findIndex((x) => String(x.test_code || '') === code);
    if (idx >= 0) {
      currentTests[idx] = Object.assign({}, currentTests[idx], saved || {});
      filterCurrentTests();
    }
  }

  function saveTat() {
    if (!activeTatRow) return;
    const scheduleType = String($('#tat-type').val() || '');
    const cutoff = String($('#tat-cutoff-time').val() || '');
    const processValue = Number($('#tat-process-hours').val() || 0);
    const processUnit = String($('#tat-process-unit').val() || 'hours');
    const reportDays = selectedTatDays();
    const urgentMode = activeTatMode === 'urgent';
    const selectedTemplate = selectedTatTemplate();
    if (!selectedTemplate && !scheduleType) {
      alert('Select schedule type');
      return;
    }
    if (!selectedTemplate && (!Number.isInteger(processValue) || processValue <= 0)) {
      alert('Enter process time');
      return;
    }
    if (!selectedTemplate && scheduleType === 'scheduled_days' && !reportDays.length) {
      alert('Select report days');
      return;
    }
    const finalText = refreshTatPreview();
    if (!finalText) {
      alert('Final TAT could not be generated');
      return;
    }
    const $btn = $('#tat-modal-save').prop('disabled', true).text('Saving...');
    $.ajax({
      url: '/hhome-collection/test-tat-master',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({
        test_code: activeTatRow.test_code,
        schedule_type: scheduleType,
        cutoff_time: cutoff,
        process_time_value: processValue,
        process_time_unit: processUnit,
        report_days: reportDays,
        urgent_mode: urgentMode,
        tat_template_id: selectedTemplate ? selectedTemplate.id : ''
      })
    }).done(function (res) {
      updateTatRow(res?.item || {});
      closeTatModal();
    }).fail(function (xhr) {
      alert(xhr?.responseJSON?.message || 'TAT save failed');
    }).always(function () {
      $btn.prop('disabled', false).text('Save');
    });
  }

  function bindEvents() {
    $('#ptm-panel-search').on('input', function () {
      const q = $(this).val();
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => searchPanels(q), 250);
    });

    $('#ptm-panel-tbody').on('click', 'tr[data-comp-cat-id]', function (e) {
      if ($(e.target).is('input[type="checkbox"]')) return;
      const comp = String($(this).data('comp-cat-id') ?? '');
      selectedCompCatId = comp;
      $('#ptm-panel-tbody tr').removeClass('ptm-selected-row');
      $(this).addClass('ptm-selected-row');
    });

    $('#ptm-panel-tbody').on('change', '.ptm-flag-panel', function () {
      const comp = String($(this).data('comp-cat-id') ?? '');
      const panelName = String($(this).data('panel-name') ?? '');
      const flag = String($(this).data('flag') ?? '');
      const key = panelKey(comp, panelName);
      panelFlags[key] = panelFlags[key] || {};
      panelFlags[key][flag] = $(this).is(':checked');
      if (flag === 'showmrp') {
        pendingShowMrp[key] = { comp_cat_id: comp, panel_name: panelName, showmrp: $(this).is(':checked') };
        $(this).closest('tr').addClass('ptm-pending-mrp');
        updateApplyButton();
      } else if (flag === 'showinHC') {
        pendingShowInHc[key] = { comp_cat_id: comp, panel_name: panelName, showinHC: $(this).is(':checked') };
        $(this).closest('tr').addClass('ptm-pending-mrp');
        updateApplyButton();
      }
    });

    $('#ptm-test-tbody').on('click', '.ptm-edit-tat', function () {
      const code = String($(this).data('test-code') ?? '');
      const row = currentTests.find((x) => String(x.test_code || '') === code);
      if ($(this).hasClass('ptm-view-tat')) {
        openProfileTatModal(row);
        return;
      }
      openTatModal(row);
    });

    $('#ptm-test-search').on('input', filterCurrentTests);

    $('input[name="tat_schedule_type"]').on('change', function () {
      clearTatTemplateSelection();
      $('#tat-type').val(String($(this).val() || ''));
      refreshTatPreview();
    });
    $('#tat-cutoff-time, #tat-process-hours, #tat-process-unit').on('change input', function () {
      clearTatTemplateSelection();
      refreshTatPreview();
    });
    $('.ptm-tat-mode-btn').on('click', function () {
      applyTatModeToForm(String($(this).data('mode') || '') || 'routine');
    });
    $('#tat-template-open').on('click', function () {
      $('#tat-template-box').toggleClass('d-none');
      const templateMode = !$('#tat-template-box').hasClass('d-none');
      $('#tat-template-open').toggleClass('active', templateMode);
      $('.ptm-manual-section').toggleClass('d-none', templateMode);
      if (!templateMode) clearTatTemplateSelection();
      renderTatTemplates();
      refreshTatPreview();
    });
    $('#tat-template-tbody').on('click', '.tat-template-select', function () {
      selectedTatTemplateId = String($(this).data('template-id') || '');
      renderTatTemplates();
      refreshTatPreview();
    });
    $('#tat-report-days-wrap').on('change', '.tat-day', function () {
      clearTatTemplateSelection();
      refreshTatPreview();
    });
    $('#tat-modal-close, #tat-modal-cancel').on('click', closeTatModal);
    $('#profile-tat-modal-close, #profile-tat-modal-done').on('click', closeProfileTatModal);
    $('#profile-tat-override').on('click', function () {
      const code = String($(this).data('test-code') || '');
      const row = currentTests.find((x) => String(x.test_code || '') === code);
      closeProfileTatModal();
      openTatModal(row);
    });
    $('#tat-modal-backdrop').on('click', function () {
      closeTatModal();
      closeProfileTatModal();
    });
    $('#tat-modal-save').on('click', saveTat);
    $('#ptm-apply-show-mrp').on('click', function () {
      const mrpChanges = Object.values(pendingShowMrp);
      const hcChanges = Object.values(pendingShowInHc);
      const changes = mrpChanges.concat(hcChanges);
      if (!changes.length) return;
      if (!window.confirm('Are you sure you want to apply selected changes?')) return;
      const $btn = $(this).prop('disabled', true).text('Saving...');
      const mrpRequests = mrpChanges.map((payload) => $.ajax({
        url: '/hhome-collection/panel-company-show-mrp',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload)
      }));
      const hcRequests = hcChanges.map((payload) => $.ajax({
        url: '/hhome-collection/panel-company-show-in-hc',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload)
      }));
      const requests = mrpRequests.concat(hcRequests);
      $.when.apply($, requests).done(function () {
        window.location.reload();
      }).fail(function (xhr) {
        const msg = xhr?.responseJSON?.message || 'Panel company update failed';
        alert(msg);
        $btn.prop('disabled', false).text('Apply');
      });
    });
  }

  $(function () {
    if (!$('#ptm-panel-table').length && !$('#ptm-test-table').length) return;
    bindEvents();
    if ($('#ptm-panel-table').length) loadInitialPanels();
    if ($('#ptm-test-table').length) {
      loadTatTemplates();
      loadTatTests();
    }
  });
})();
