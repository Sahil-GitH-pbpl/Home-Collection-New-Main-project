(function () {
  function escHtml(v) {
    return String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function fmt(n) {
    return Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }

  function patientNamesHtml(row) {
    const patients = Array.isArray(row.patients) ? row.patients : [];
    if (!patients.length) return escHtml(row.patient_names || "-");
    return patients.map((p) => {
      const name = String(p.patient_name || p.full_name || p.name || "").trim() || "Patient";
      const mobile = String(p.mobile || p.contact_mobile || p.primary_mobile || "").trim();
      return `<span class="estimate-patient-name">${escHtml(name)}</span>${mobile ? ` <span class="estimate-patient-mobile">(${escHtml(mobile)})</span>` : ""}`;
    }).join(", ");
  }

  function renderRows(items) {
    if (!items.length) {
      $("#estimate-list-body").html('<tr><td colspan="6" class="estimate-empty-row">No estimates found.</td></tr>');
      return;
    }
    $("#estimate-list-body").html(items.map((row) => `
      <tr>
        <td><strong>${escHtml(row.id)}</strong></td>
        <td>${patientNamesHtml(row)}</td>
        <td>${escHtml(row.created_at || "-")}</td>
        <td>${escHtml(row.created_by_name || "-")}</td>
        <td class="text-end"><strong>${fmt(row.grand_total)}</strong></td>
        <td class="text-end">
          <div class="estimate-list-actions">
            <button type="button" class="btn btn-outline-primary btn-sm estimate-edit" data-id="${escHtml(row.id)}">Edit</button>
            <button type="button" class="btn btn-success btn-sm estimate-download" data-id="${escHtml(row.id)}">Download</button>
          </div>
        </td>
      </tr>
    `).join(""));
  }

  function loadEstimates() {
    $.get("/hhome-collection/estimate-list-data", {
      limit: 200,
      search: $("#estimate-list-search").val() || "",
      date_from: $("#estimate-list-from").val() || "",
      date_to: $("#estimate-list-to").val() || ""
    }, function (res) {
      renderRows(res.items || []);
    }).fail(function () {
      $("#estimate-list-body").html('<tr><td colspan="6" class="estimate-empty-row text-danger">Estimate list load failed.</td></tr>');
    });
  }

  function inputDateValue(date) {
    const d = date || new Date();
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }

  function todayInputValue() {
    return inputDateValue(new Date());
  }

  function twoDaysAgoInputValue() {
    const d = new Date();
    d.setDate(d.getDate() - 2);
    return inputDateValue(d);
  }

  $(function () {
    $("#estimate-list-from").val(twoDaysAgoInputValue());
    $("#estimate-list-to").val(todayInputValue());
    loadEstimates();

    $("#estimate-list-apply").on("click", loadEstimates);
    $("#estimate-list-search").on("keydown", function (e) {
      if (e.key === "Enter") loadEstimates();
    });

    $("#estimate-list-body").on("click", ".estimate-edit", function () {
      const id = Number($(this).data("id") || 0);
      if (id) window.location.href = `/hhome-collection/estimate?estimate_id=${id}`;
    });

    $("#estimate-list-body").on("click", ".estimate-download", function () {
      const id = Number($(this).data("id") || 0);
      if (!id) return;
      $.get(`/hhome-collection/estimate/${id}`, function (res) {
        if (res.ok && res.estimate && window.HCEstimate?.downloadRecord) {
          window.HCEstimate.downloadRecord(res.estimate);
        } else {
          alert(res.message || "Estimate download failed.");
        }
      }).fail(function () {
        alert("Estimate download failed.");
      });
    });
  });
})();
