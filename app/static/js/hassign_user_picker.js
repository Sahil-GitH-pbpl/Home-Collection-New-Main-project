(function (window, $) {
  if (!$) return;

  function esc(v) {
    return String(v ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function initHcAssignUserPicker(opts) {
    const inputSel = opts?.inputSelector;
    const suggestSel = opts?.suggestSelector;
    const chipWrapSel = opts?.chipContainerSelector;
    const saveBtnSel = opts?.saveBtnSelector;
    const onSelect = typeof opts?.onSelect === 'function' ? opts.onSelect : function () {};
    const searchUrl = opts?.searchUrl || '/hhome-collection/internal-ref-users';
    const limit = Number(opts?.limit || 20);
    const debounceMs = Number(opts?.debounceMs || 200);
    if (!inputSel || !suggestSel || !chipWrapSel) return { destroy: function () {} };

    const ns = `.hcAssignPicker_${Date.now()}_${Math.floor(Math.random() * 9999)}`;
    const nsOutside = `${ns}_outside`;
    const $doc = $(document);
    const cache = new Map();
    let timer = null;
    let xhr = null;
    let reqSeq = 0;

    function hideSuggest() {
      const $s = $(suggestSel);
      $s.addClass('d-none').html('');
    }

    function renderItems(items) {
      const $s = $(suggestSel);
      if (!items.length) {
        $s.html('<div class="asg-phlebo-suggest-item asg-empty-suggest">No staff found</div>').removeClass('d-none');
        return;
      }
      $s.html(
        items
          .map((x) => `<button type="button" class="asg-phlebo-suggest-item" data-user-id="${x.id}" data-name="${esc(x.name || '')}"><strong>${esc(x.name || '')}</strong></button>`)
          .join('')
      ).removeClass('d-none');
    }

    function markSelectedChip(userId) {
      const uid = Number(userId || 0);
      if (!uid) return;
      $(`${chipWrapSel} .assign-chip`).removeClass('active');
      const $chip = $(`${chipWrapSel} .assign-chip[data-user-id="${uid}"]`);
      if ($chip.length) $chip.addClass('active');
    }

    function pickUser(userId, name) {
      const uid = Number(userId || 0);
      if (!uid) return;
      const cleanName = String(name || '').trim();
      if (cleanName) $(inputSel).val(cleanName);
      if (saveBtnSel) $(saveBtnSel).prop('disabled', false);
      markSelectedChip(uid);
      onSelect(uid, cleanName);
      hideSuggest();
    }

    $doc.off(`click${ns}`, `${chipWrapSel} .assign-chip`).on(`click${ns}`, `${chipWrapSel} .assign-chip`, function () {
      pickUser($(this).data('user-id'), ($(this).text() || '').trim());
    });

    $doc.off(`input${ns}`, inputSel).on(`input${ns}`, inputSel, function () {
      const q = String($(this).val() || '').trim();
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        if (q.length < 2) {
          hideSuggest();
          return;
        }
        const key = q.toLowerCase();
        if (cache.has(key)) {
          renderItems(cache.get(key) || []);
          return;
        }
        const curReq = ++reqSeq;
        if (xhr && xhr.readyState !== 4) xhr.abort();
        xhr = $.get(searchUrl, { q, limit }, function (res) {
          if (curReq !== reqSeq) return;
          const items = res?.items || [];
          cache.set(key, items);
          renderItems(items);
        }).fail(function (_xhr, statusText) {
          if (curReq !== reqSeq || statusText === 'abort') return;
          const $s = $(suggestSel);
          $s.html('<div class="asg-phlebo-suggest-item asg-empty-suggest">Search failed</div>').removeClass('d-none');
        });
      }, debounceMs);
    });

    $doc.off(`click${ns}`, `${suggestSel} .asg-phlebo-suggest-item`).on(`click${ns}`, `${suggestSel} .asg-phlebo-suggest-item`, function () {
      if ($(this).hasClass('asg-empty-suggest')) return;
      pickUser($(this).data('user-id'), $(this).data('name'));
    });

    $doc.off(`click${nsOutside}`).on(`click${nsOutside}`, function (e) {
      if ($(e.target).closest(`${inputSel}, ${suggestSel}`).length) return;
      hideSuggest();
    });

    return {
      destroy: function () {
        if (timer) clearTimeout(timer);
        if (xhr && xhr.readyState !== 4) xhr.abort();
        $doc.off(ns);
        $doc.off(nsOutside);
      },
      hideSuggest,
      pickUser,
    };
  }

  function renderAlphaChipGroups(phlebos, opts) {
    const options = opts || {};
    const selectedUserId = Number(options.selectedUserId || 0);
    const assignedSet = options.assignedSet instanceof Set ? options.assignedSet : new Set();
    const grouped = {};
    (phlebos || []).forEach((u) => {
      const name = String(u?.full_name || u?.name || '').trim();
      if (!name) return;
      const letter = (name.charAt(0) || '#').toUpperCase();
      if (!grouped[letter]) grouped[letter] = [];
      grouped[letter].push({ id: Number(u.id), full_name: name });
    });
    return Object.keys(grouped).sort((a, b) => a.localeCompare(b)).map((letter) => {
      const users = grouped[letter].sort((a, b) => (a.full_name || '').localeCompare(b.full_name || ''));
      const chips = users.map((u) => {
        const uid = Number(u.id);
        const isActive = selectedUserId === uid;
        const isAssigned = assignedSet.has(uid);
        return `<button type="button" class="assign-chip ${isAssigned ? 'assigned-used' : ''} ${isActive ? 'active' : ''}" data-user-id="${uid}">${esc(u.full_name)}</button>`;
      }).join('');
      return `<div class="assign-alpha-row"><div class="assign-alpha-label">${esc(letter)}:</div><div class="assign-alpha-chip-list">${chips}</div></div>`;
    }).join('');
  }

  window.initHcAssignUserPicker = initHcAssignUserPicker;
  window.renderHcAssignAlphaChipGroups = renderAlphaChipGroups;
})(window, window.jQuery);
