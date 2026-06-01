function escLb(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderLeaderboardRows($tbody, rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) {
    $tbody.html('<tr><td colspan="3" class="text-center text-muted py-3">No data</td></tr>');
    return;
  }
  const html = list.map((r, idx) => `
    <tr>
      <td>${Number(r.sr_no || idx + 1)}</td>
      <td>${escLb(r.name || '-')}</td>
      <td><span class="lb-count-chip">${Number(r.count || 0)}</span></td>
    </tr>
  `).join('');
  $tbody.html(html);
}

function todayIsoLocal() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function loadLeaderboard() {
  const $creatorBody = $('#lb-booking-creator-table tbody');
  const $phleboBody = $('#lb-phlebo-completion-table tbody');
  const fromDate = String($('#lb-date-from').val() || '').trim();
  const toDate = String($('#lb-date-to').val() || '').trim();
  $('#lb-range-text').text(`Showing: ${fromDate || '-'} to ${toDate || '-'}`);
  $.get('/hhome-collection/leaderboard-data', { date_from: fromDate, date_to: toDate }, function (res) {
    renderLeaderboardRows($creatorBody, res?.booking_creators || []);
    renderLeaderboardRows($phleboBody, res?.phlebo_completions || []);
  }).fail(function () {
    $creatorBody.html('<tr><td colspan="3" class="text-center text-danger py-3">Unable to load leaderboard</td></tr>');
    $phleboBody.html('<tr><td colspan="3" class="text-center text-danger py-3">Unable to load leaderboard</td></tr>');
  });
}

$(function () {
  const $creatorBody = $('#lb-booking-creator-table tbody');
  const $phleboBody = $('#lb-phlebo-completion-table tbody');
  if (!$creatorBody.length || !$phleboBody.length) return;

  const today = todayIsoLocal();
  $('#lb-date-from').val(today);
  $('#lb-date-to').val(today);

  $('#lb-apply-filter').off('click').on('click', function () {
    const fromDate = String($('#lb-date-from').val() || '').trim();
    const toDate = String($('#lb-date-to').val() || '').trim();
    if (fromDate && toDate && fromDate > toDate) {
      alert('From date cannot be greater than To date.');
      return;
    }
    loadLeaderboard();
  });

  // TEMP FEATURE: leaderboard data source endpoint, remove later with template/route cleanup.
  loadLeaderboard();
});
