(function () {
  let selectedCompCatId = '';
  let selectedPanelName = '';
  let panelFlags = {};
  let testFlags = {};
  let searchTimer = null;
  let pendingShowMrp = {};

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
      $('#ptm-panel-tbody').html('<tr><td colspan="6" class="text-muted text-center py-3">No panel company found.</td></tr>');
      return;
    }
    const html = rows.map((x, i) => {
      const comp = String(x.CompCatID ?? '');
      const pname = String(x.pname || '');
      const key = panelKey(comp, pname);
      if (!panelFlags[key]) {
        panelFlags[key] = {
          showmrp: Number(x.showmrp || 0) === 1
        };
      }
      const flags = panelFlags[key] || {};
      const selectedClass = selectedCompCatId && selectedCompCatId === comp ? 'ptm-selected-row' : '';
      const pendingClass = pendingShowMrp[key] ? 'ptm-pending-mrp' : '';
      return `
        <tr class="${selectedClass} ${pendingClass}" data-comp-cat-id="${esc(comp)}" data-panel-name="${esc(pname)}">
          <td>${i + 1}</td>
          <td>${esc(pname)}</td>
          <td>${esc(comp)}</td>
          <td>${esc(x.CatDetails || '')}</td>
          <td>${esc(x.BillingChargeMode || '')}</td>
          <td class="text-center"><input type="checkbox" class="form-check-input ptm-flag-panel" data-comp-cat-id="${esc(comp)}" data-panel-name="${esc(pname)}" data-flag="showmrp" ${flags.showmrp ? 'checked' : ''}></td>
        </tr>
      `;
    }).join('');
    $('#ptm-panel-tbody').html(html);
  }

  function updateApplyButton() {
    $('#ptm-apply-show-mrp').toggleClass('d-none', !Object.keys(pendingShowMrp).length);
  }

  function testKey(compCatId, bookedCode) {
    return `${String(compCatId || '')}|${String(bookedCode || '')}`;
  }

  function renderTestRows(items) {
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) {
      $('#ptm-test-tbody').html('<tr><td colspan="8" class="text-muted text-center py-3">No tests found for selected panel company.</td></tr>');
      return;
    }
    const html = rows.map((x, i) => {
      const key = testKey(selectedCompCatId, x.booked_code);
      const flags = testFlags[key] || {};
      return `
        <tr>
          <td>${i + 1}</td>
          <td>${esc(x.test_name || '')}</td>
          <td>${esc(x.mrp ?? '')}</td>
          <td>${esc(x.charge ?? '')}</td>
          <td>${esc(x.max_discount ?? '')}</td>
          <td class="text-center"><input type="checkbox" class="form-check-input ptm-flag-test" data-key="${esc(key)}" data-flag="allowed_in_hc" ${flags.allowed_in_hc ? 'checked' : ''}></td>
          <td class="text-center"><input type="checkbox" class="form-check-input ptm-flag-test" data-key="${esc(key)}" data-flag="is_tag" ${flags.is_tag ? 'checked' : ''}></td>
          <td class="text-center"><input type="checkbox" class="form-check-input ptm-flag-test" data-key="${esc(key)}" data-flag="tat" ${flags.tat ? 'checked' : ''}></td>
        </tr>
      `;
    }).join('');
    $('#ptm-test-tbody').html(html);
  }

  function loadInitialPanels() {
    $.get('/hhome-collection/panel-companies-initial', { limit: 5 }, function (res) {
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
    $.get('/hhome-collection/panel-companies', { q: text, limit: 50 }, function (res) {
      renderPanelRows(res?.items || []);
    }).fail(function () {
      renderPanelRows([]);
    });
  }

  function loadTestsForCompany(compCatId, panelName) {
    selectedCompCatId = String(compCatId || '');
    selectedPanelName = String(panelName || '');
    if (!selectedCompCatId) return;
    $('#ptm-selected-label').text(`Selected: ${selectedPanelName || '-'} | CompCatID: ${selectedCompCatId}`);
    $.get('/hhome-collection/panel-tests-by-company', { comp_cat_id: selectedCompCatId }, function (res) {
      renderTestRows(res?.tests || []);
    }).fail(function () {
      renderTestRows([]);
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
      const name = String($(this).data('panel-name') ?? '');
      $('#ptm-panel-tbody tr').removeClass('ptm-selected-row');
      $(this).addClass('ptm-selected-row');
      loadTestsForCompany(comp, name);
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
      }
    });

    $('#ptm-test-tbody').on('change', '.ptm-flag-test', function () {
      const key = String($(this).data('key') ?? '');
      const flag = String($(this).data('flag') ?? '');
      testFlags[key] = testFlags[key] || {};
      testFlags[key][flag] = $(this).is(':checked');
    });

    $('#ptm-apply-show-mrp').on('click', function () {
      const changes = Object.values(pendingShowMrp);
      if (!changes.length) return;
      if (!window.confirm('Are you sure you want to update Show Test MRP for selected panel company?')) return;
      const $btn = $(this).prop('disabled', true).text('Saving...');
      const requests = changes.map((payload) => $.ajax({
        url: '/hhome-collection/panel-company-show-mrp',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload)
      }));
      $.when.apply($, requests).done(function () {
        window.location.reload();
      }).fail(function (xhr) {
        const msg = xhr?.responseJSON?.message || 'Show Test MRP update failed';
        alert(msg);
        $btn.prop('disabled', false).text('Apply');
      });
    });
  }

  $(function () {
    if (!$('#ptm-panel-table').length) return;
    bindEvents();
    loadInitialPanels();
  });
})();
