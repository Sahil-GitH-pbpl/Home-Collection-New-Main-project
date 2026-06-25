let marketingRows = [];
let marketingSort = { key: '', dir: 'asc' };

function escMarketing(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtMarketingMoney(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '0';
  return n.toFixed(2).replace(/\.00$/, '');
}

function amountText(row) {
  const total = fmtMarketingMoney(row?.total_amount || 0);
  const parts = Array.isArray(row?.patient_amounts)
    ? row.patient_amounts.map((x) => Number(x || 0)).filter((x) => Number.isFinite(x) && x > 0)
    : [];
  if (parts.length <= 1) return total;
  return `${total} <span class="marketing-amount-split">(${parts.map(fmtMarketingMoney).join(' + ')})</span>`;
}

function statusClass(label) {
  const key = String(label || '').trim().toLowerCase();
  if (key === 'completed') return 'marketing-status-completed';
  if (key === 'cancelled') return 'marketing-status-cancelled';
  if (key === 'pending') return 'marketing-status-pending';
  if (key === 'assigned') return 'marketing-status-assigned';
  if (key === 'started') return 'marketing-status-started';
  return 'marketing-status-default';
}

function panelCompanyText(value) {
  return String(value || '-')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
    .join(' / ') || '-';
}

function filteredMarketingRows() {
  const q = String($('#mk-search').val() || '').trim().toLowerCase();
  let rows = [...marketingRows];
  if (q) {
    rows = rows.filter((r) => {
      const haystack = `${r?.patient_names || ''} ${r?.patient_mobiles || ''}`.toLowerCase();
      return haystack.includes(q);
    });
  }
  if (marketingSort.key) {
    const dir = marketingSort.dir === 'desc' ? -1 : 1;
    rows.sort((a, b) => {
      if (marketingSort.key === 'total_amount') {
        return (Number(a?.total_amount || 0) - Number(b?.total_amount || 0)) * dir;
      }
      const av = String(a?.[marketingSort.key] || '').trim().toLowerCase();
      const bv = String(b?.[marketingSort.key] || '').trim().toLowerCase();
      return av.localeCompare(bv) * dir;
    });
  }
  return rows;
}

function updateSortMarks() {
  $('.sort-mark').text('');
  if (marketingSort.key) {
    $(`#mk-sort-${marketingSort.key}`).text(marketingSort.dir === 'desc' ? 'v' : '^');
  }
}

function renderMarketingRows() {
  const list = filteredMarketingRows();
  const totalAmount = list.reduce((sum, row) => sum + Number(row?.total_amount || 0), 0);
  $('#mk-booking-count').text(list.length);
  $('#mk-amount-total').text(fmtMarketingMoney(totalAmount));
  updateSortMarks();
  if (!list.length) {
    $('#marketing-table tbody').html('<tr><td colspan="9" class="text-center text-muted py-3">No records</td></tr>');
    return;
  }

  const html = list.map((r) => `
    <tr>
      <td class="marketing-id">${Number(r.booking_id || 0) || '-'}</td>
      <td>
        <div class="marketing-patient-main">${escMarketing(r.patient_names || '-')}</div>
        <div class="marketing-patient-sub">${escMarketing(r.patient_mobiles || '-')}</div>
      </td>
      <td class="text-center">${escMarketing(r.preferred_visit_date || '-')}</td>
      <td class="text-center">${escMarketing(r.preferred_time_slot || '-')}</td>
      <td class="marketing-panel"><span class="marketing-chip marketing-panel-chip">${escMarketing(panelCompanyText(r.panel_companies))}</span></td>
      <td>${escMarketing(r.ref_by || '-')}</td>
      <td>${escMarketing(r.internal_ref_by || '-')}</td>
      <td class="marketing-amount">${amountText(r)}</td>
      <td class="marketing-status"><span class="marketing-chip ${statusClass(r.status)}">${escMarketing(r.status || '-')}</span></td>
    </tr>
  `).join('');
  $('#marketing-table tbody').html(html);
}

function loadMarketingRows() {
  const fromDate = String($('#mk-date-from').val() || '').trim();
  const toDate = String($('#mk-date-to').val() || '').trim();
  if (fromDate && toDate && fromDate > toDate) {
    alert('From date cannot be greater than To date.');
    return;
  }
  $('#marketing-table tbody').html('<tr><td colspan="9" class="text-center text-muted py-3">Loading...</td></tr>');
  $.get('/hhome-collection/marketing-data', { date_from: fromDate, date_to: toDate }, function (res) {
    marketingRows = Array.isArray(res?.rows) ? res.rows : [];
    renderMarketingRows();
  }).fail(function () {
    $('#marketing-table tbody').html('<tr><td colspan="9" class="text-center text-danger py-3">Unable to load marketing data</td></tr>');
  });
}

$(function () {
  if (!$('#h-marketing-page').length) return;
  const today = String($('#h-marketing-page').data('default-date') || '').trim();
  $('#mk-date-from').val(today);
  $('#mk-date-to').val(today);
  $('#mk-apply-filter').on('click', loadMarketingRows);
  $('#mk-search').on('input', renderMarketingRows);
  $('.marketing-sortable').on('click', function () {
    const key = String($(this).data('sort-key') || '');
    if (!key) return;
    if (marketingSort.key === key) {
      marketingSort.dir = marketingSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
      marketingSort = { key, dir: 'asc' };
    }
    renderMarketingRows();
  });
  loadMarketingRows();
});
