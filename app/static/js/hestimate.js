(function () {
  let caller = null;
  let searchMobile = "";
  let linkedPatients = [];
  let selectedPatients = [];
  let addresses = [];
  let referenceAddresses = [];
  let selectedAddressId = 0;
  let selectedAddress = null;
  let addressColonyCatalog = [];
  let colonyRequestSeq = 0;
  let panelTestsModal = null;
  let panelTestSearchTimer = null;
  let panelTestSearchQuery = "";
  let panelTestSearchSeq = 0;
  let activePanelPicker = null;
  let editingEstimateId = Number(window.HC_ESTIMATE_ID || 0);
  let editingPatientId = null;

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

  function patientId(p) {
    return Number(p?.id || p?.patient_id || 0);
  }

  function testKey(t) {
    return String(t?.booked_code || t?.testcode1 || t?.test_code || "").trim();
  }

  function normalizeChargeModeCode(raw) {
    const src = String(raw || "").toUpperCase().trim();
    const out = [];
    for (const ch of src) {
      if ((ch === "C" || ch === "P" || ch === "F") && !out.includes(ch)) out.push(ch);
    }
    return ["C", "P", "F"].filter((ch) => out.includes(ch));
  }

  function dataString($el, key) {
    const value = $el.data(key);
    return value == null ? "" : String(value);
  }

  const TITLE_MASTER = [
    { title: "Mr.", gender: "Male" },
    { title: "Mrs.", gender: "Female" },
    { title: "Dr", gender: "Male" },
    { title: "Dr (Ms)", gender: "Female" },
    { title: "Master", gender: "Male" },
    { title: "Baby", gender: "Female" },
    { title: "Daughter of", gender: "Female" },
    { title: "Son Of", gender: "Male" },
    { title: "Miss", gender: "Female" },
    { title: "MS.", gender: "Female" },
    { title: "Mr", gender: "Male" },
    { title: "MST.", gender: "Male" },
    { title: "Mrs", gender: "Female" },
    { title: "Mst", gender: "Male" },
    { title: "Ms", gender: "Female" },
    { title: "Care Of", gender: "Other" },
    { title: "CARE", gender: "Other" },
    { title: "PROF.", gender: "Male" },
    { title: "CAPT.", gender: "Male" },
    { title: "Prof", gender: "Male" },
    { title: "COL.", gender: "Male" },
    { title: "BRIG.", gender: "Male" },
    { title: "MAJ.", gender: "Male" },
    { title: "MAJ.GEN", gender: "Male" },
    { title: "JUSTIC", gender: "Male" },
    { title: "DSD", gender: "Other" }
  ];
  const titleGenderMap = TITLE_MASTER.reduce((acc, row) => {
    acc[row.title] = row.gender;
    return acc;
  }, {});

  function modeLabel(mode) {
    if (mode === "C") return "Credit";
    if (mode === "F") return "Free";
    return "Paying";
  }

  function displayPatientName(p) {
    return String(p?.full_name || p?.name || "").trim() || "Patient";
  }

  function patientMeta(p) {
    const parts = [];
    if (p?.age) parts.push(`Age - ${p.age}`);
    else if (p?.age_years) parts.push(`Age - ${p.age_years}`);
    if (p?.gender) parts.push(`Gender - ${p.gender}`);
    return parts.join(" / ") || "-";
  }

  function patientAgeOnly(p) {
    return String(p?.age || p?.age_years || "").trim();
  }

  function addressLine(a) {
    if (!a) return "";
    const line1 = [a.house_flat_no, a.floor_display || a.floor, a.block_tower_no, a.street_sector || a.street_line]
      .filter(Boolean)
      .join(", ");
    const line2 = [a.colony_name, a.pincode, a.route_no, a.city]
      .filter(Boolean)
      .join(", ");
    return [line1, line2].filter(Boolean).join(" | ");
  }

  function referenceAddressLine(r) {
    if (!r) return "";
    return [r.address, r.area, r.pincode, r.routename, r.city].filter(Boolean).join(" | ");
  }

  function priced(t, patient) {
    const mode = patient?.mode || "P";
    const mrp = Number(t?.mrp || 0);
    if (mode === "C") return { mrp, discount: 0, finalRate: mrp };
    if (mode === "F") return { mrp, discount: mrp, finalRate: 0 };
    const finalRate = Math.max(0, Number(t?.charge || 0));
    return { mrp, discount: Number(t?.max_discount || 0), finalRate };
  }

  function totals(patient) {
    const tests = patient?.tests || [];
    return {
      mrp: tests.reduce((sum, t) => sum + priced(t, patient).mrp, 0),
      discount: tests.reduce((sum, t) => sum + priced(t, patient).discount, 0),
      final: tests.reduce((sum, t) => sum + priced(t, patient).finalRate, 0)
    };
  }

  function updateSummary() {
    const testCount = selectedPatients.reduce((sum, p) => sum + (p.tests || []).length, 0);
    $("#estimate-selected-count").text(`Patients: ${selectedPatients.length}`);
    $("#estimate-selected-tests").text(`Tests: ${testCount}`);
    $("#estimate-save").prop("disabled", selectedPatients.length < 1 || testCount < 1);
  }

  function renderPatientsList() {
    const list = linkedPatients || [];
    const selectedIds = new Set(selectedPatients.map((p) => patientId(p)));
    if ($("#selected-patient-tags").length) {
      const selectedHtml = selectedPatients.length
        ? selectedPatients.map((p) => `
          <div class="selected-patient-card">
            <div class="selected-patient-card-top">
              <div class="selected-patient-card-name">${escHtml(displayPatientName(p))}</div>
              <button class="rm-patient" data-patient-id="${p.id}" title="Remove">x</button>
            </div>
            <div class="selected-patient-card-meta">
              <span><strong>Gender:</strong> ${escHtml(p.gender || "-")}</span>
              <span><strong>Age:</strong> ${escHtml(p.age || p.age_years || "-")}</span>
              <span><strong>Panel:</strong> ${escHtml(p.panel_company || p.panel?.pname || "-")}</span>
            </div>
          </div>`).join("")
        : '<div class="text-muted">No patients selected yet.</div>';
      $("#selected-patient-tags").html(selectedHtml);
    }
    if ($("#linked-patients-panel").length) {
      const linkedHtml = list.length
        ? list.map((p) => {
          const id = patientId(p);
          return `
            <div class="chip estimate-linked-patient ${selectedIds.has(id) ? "selected" : ""}" data-patient-id="${id}">
              <div class="linked-patient-main">
                <span><strong>${escHtml(displayPatientName(p))}</strong> (${escHtml(p.age || "-")})</span>
              </div>
              <small>${escHtml(p.default_address || p.patient_code || "")}</small>
            </div>`;
        }).join("")
        : '<div class="text-muted small">No linked patients yet.</div>';
      $("#linked-patients-panel").html(linkedHtml);
    }
    if ($("#reference-addresses-panel").length) {
      const refHtml = referenceAddresses.length
        ? referenceAddresses.map((r) => `
          <div class="reference-address-card">
            <div class="reference-address-top">
              <div class="reference-address-title">${escHtml([r.area, r.city].filter(Boolean).join(", ") || "Reference Address")}</div>
              <span class="reference-address-status">REF</span>
            </div>
            <div class="reference-address-line">${escHtml(referenceAddressLine(r) || "-")}</div>
          </div>`).join("")
        : '<div class="text-muted small">No reference addresses.</div>';
      $("#reference-addresses-panel").html(refHtml);
    }
    if (!list.length) {
      if ($("#estimate-patient-list").length) $("#estimate-patient-list").html('<div class="estimate-empty">No linked patients found</div>');
      return;
    }
    if (!$("#estimate-patient-list").length) return;
    const selected = selectedIds;
    $("#estimate-patient-list").html(list.map((p) => {
      const id = patientId(p);
      const checked = selected.has(id) ? "checked" : "";
      const cls = selected.has(id) ? " selected" : "";
      return `
        <div class="estimate-pick${cls}">
          <label class="estimate-pick-select">
            <input type="checkbox" class="estimate-patient-check" data-patient-id="${id}" ${checked}>
          </label>
          <span class="estimate-pick-main">
            <span class="estimate-pick-title">${escHtml(displayPatientName(p))}${patientAgeOnly(p) ? ` <span class="estimate-pick-meta">Age - ${escHtml(patientAgeOnly(p))}</span>` : ""}</span>
            <span class="estimate-pick-actions">
              <button type="button" class="estimate-patient-edit-btn" data-patient-id="${id}">Edit</button>
              <button type="button" class="estimate-patient-inactive-btn" data-patient-id="${id}">Inactive</button>
            </span>
          </span>
        </div>`;
    }).join(""));
  }

  function renderPatientTitleOptions() {
    $("#p-title").html('<option value="">Select</option>' + TITLE_MASTER.map((x) => `<option value="${escHtml(x.title)}">${escHtml(x.title)}</option>`).join(""));
  }

  function resetPatientForm() {
    editingPatientId = null;
    $("#new-patient-form").addClass("d-none");
    $("#btn-show-patient-form").text("+ Add New Patient");
    $("#btn-save-patient").text("Save Patient");
    $("#p-title").val("");
    $("#p-full-name,#p-labmate-pid,#p-panel-company,#p-card-number,#p-dob,#p-age-years,#p-contact-mobile,#p-alternate-mobile,#p-email").val("");
    $("#p-panel-company-suggest").addClass("d-none").html("");
    $("#p-gender").val("Male");
  }

  function resetAddressForm() {
    $("#new-address-form").addClass("d-none");
    $("#btn-show-address-form").text("+ Add New Address");
    $("#a-type").val("Home");
    $("#a-house,#a-floor,#a-block,#a-street,#a-landmark,#a-pincode,#a-route,#a-google-location,#a-access,#a-colony-free").val("");
    $("#a-floor-special,#a-city,#a-colony").val("");
    $("#a-colony-manual").prop("checked", false);
    syncAddressColonyMode();
  }

  function autoFillAgeFromDob() {
    const dobStr = $("#p-dob").val();
    if (!dobStr) return;
    const dob = new Date(`${dobStr}T00:00:00`);
    if (Number.isNaN(dob.getTime())) return;
    const today = new Date();
    let age = today.getFullYear() - dob.getFullYear();
    const monthDiff = today.getMonth() - dob.getMonth();
    if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < dob.getDate())) age -= 1;
    $("#p-age-years").val(Math.max(0, age));
  }

  function autoDobFromAge() {
    const raw = String($("#p-age-years").val() || "").trim();
    if (!raw) return;
    const age = parseInt(raw, 10);
    if (Number.isNaN(age) || age < 0) return;
    $("#p-dob").val(`${new Date().getFullYear() - age}-01-01`);
  }

  function renderAddressList() {
    const list = addresses || [];
    if ($("#address-list").length) {
      if (!list.length) {
        $("#address-list").html('<div class="text-muted">No addresses linked yet.</div>');
      } else {
        $("#address-list").html(list.map((a) => {
          const id = Number(a.id || a.address_id || 0);
          return `
            <div class="address-card ${selectedAddressId === id ? "selected-address" : ""}">
              <div><strong>${escHtml(a.address_type || "Address")}</strong></div>
              <div>${escHtml([a.house_flat_no, a.floor_display || a.floor || "", a.block_tower_no || "", a.street_sector || a.street_line || ""].filter(Boolean).join(", "))}</div>
              <div>${escHtml([a.colony_name, a.pincode, a.route_no, a.city].filter(Boolean).join(" | "))}</div>
              <div class="address-card-actions">
                <button class="btn btn-sm btn-outline-success btn-use-address" data-address-id="${id}">Use This Address</button>
              </div>
            </div>`;
        }).join(""));
      }
    }
    if (!$("#estimate-address-list").length) return;
    if (!list.length) {
      $("#estimate-address-list").html('<div class="estimate-empty">No saved address</div>');
      return;
    }
    $("#estimate-address-list").html(list.map((a) => {
      const id = Number(a.id || a.address_id || 0);
      const cls = selectedAddressId === id ? " selected" : "";
      return `
        <div class="estimate-pick estimate-address-pick${cls}" data-address-id="${id}">
          <span class="estimate-pick-main">
            <span class="estimate-pick-title">${escHtml(a.address_type || "Address")}</span>
            <span class="estimate-pick-meta">${escHtml(addressLine(a) || "-")}</span>
          </span>
        </div>`;
    }).join(""));
  }

  function renderChargeModeOptions(patient) {
    const modes = patient?.panel?.allowedModes || [];
    if (!modes.length) return '<option value="">Select panel first</option>';
    const selectedMode = modes.includes(patient.mode) ? patient.mode : modes[0];
    patient.mode = selectedMode;
    return modes.map((m) => `<option value="${m}" ${m === selectedMode ? "selected" : ""}>${modeLabel(m)}</option>`).join("");
  }

  function renderPatientTests(patient) {
    const tests = patient.tests || [];
    if (!tests.length) return '<div class="estimate-empty">No tests added for this patient</div>';
    const rows = tests.map((t, idx) => {
      const p = priced(t, patient);
      const remove = `<button type="button" class="btn btn-outline-danger btn-sm estimate-remove-test" data-patient-id="${patient.id}" data-key="${escHtml(testKey(t))}">Remove</button>`;
      return `<tr>
        <td>${idx + 1}</td>
        <td><strong>${escHtml(t.description || "")}</strong><div class="text-muted small">${escHtml(testKey(t))}</div></td>
        <td>${escHtml(t.test_tat || "-")}</td>
        <td class="text-end">${fmt(p.mrp)}</td>
        <td class="text-end">${remove}</td>
      </tr>`;
    }).join("");
    const head = '<tr><th>#</th><th>Test Name</th><th>Test TAT</th><th class="text-end">MRP</th><th class="text-end">Action</th></tr>';
    const t = totals(patient);
    const total = patient.mode === "C"
      ? `<div class="estimate-total"><span>Total MRP: ${fmt(t.mrp)}</span></div>`
      : patient.mode === "P"
        ? `<div class="estimate-total"><span>Total MRP: ${fmt(t.mrp)}</span><span>Total Discount: ${fmt(t.discount)}</span><span>Final Total: ${fmt(t.final)}</span></div>`
        : `<div class="estimate-total"><span>Total MRP: ${fmt(t.mrp)}</span><span>Final Total: ${fmt(t.final)}</span></div>`;
    return `
      <div class="table-responsive">
        <table class="table table-bordered table-sm estimate-tests-table mb-0">
          <thead>${head}</thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${total}`;
  }

  function renderPatientEstimateCards() {
    if (!selectedPatients.length) {
      $("#estimate-patient-estimates").html('<div class="estimate-empty">Select one or more patients to create estimate.</div>');
      updateSummary();
      return;
    }
    $("#estimate-patient-estimates").html(selectedPatients.map((p) => `
      <div class="estimate-patient-card" data-patient-id="${p.id}">
        <div class="estimate-patient-head">
          <div>
            <div class="estimate-patient-name">${escHtml(displayPatientName(p))}</div>
            <div class="estimate-patient-meta">${escHtml(patientMeta(p))}</div>
          </div>
          ${editingEstimateId ? "" : `<button type="button" class="btn btn-outline-danger btn-sm estimate-remove-patient" data-patient-id="${p.id}">Remove</button>`}
        </div>
        <div class="estimate-patient-body">
          <div class="estimate-patient-controls">
            <div>
              <label class="estimate-mini-label">Panel Company</label>
              <div class="estimate-panel-wrap">
                <div class="estimate-inputbox">
                  <input type="text" class="form-control estimate-panel-search" data-patient-id="${p.id}" value="${escHtml(p.panel?.pname || "")}" placeholder="Search panel company..." autocomplete="off" ${editingEstimateId ? "disabled" : ""}>
                </div>
                <div class="estimate-suggest d-none estimate-panel-suggest" data-patient-id="${p.id}"></div>
              </div>
            </div>
            <div>
              <label class="estimate-mini-label">Charge Mode</label>
              <div class="estimate-inputbox">
                <select class="form-select estimate-charge-mode" data-patient-id="${p.id}" ${p.panel && !editingEstimateId ? "" : "disabled"}>
                  ${renderChargeModeOptions(p)}
                </select>
              </div>
            </div>
          </div>
          <div class="estimate-patient-actions">
            <button type="button" class="btn btn-primary estimate-open-tests" data-patient-id="${p.id}">+ Add Tests</button>
            <button type="button" class="btn btn-outline-secondary estimate-clear-tests" data-patient-id="${p.id}">Clear</button>
          </div>
          <div class="mt-2">${renderPatientTests(p)}</div>
        </div>
      </div>`).join(""));
    updateSummary();
  }

  function findSelectedPatient(id) {
    return selectedPatients.find((p) => Number(p.id) === Number(id)) || null;
  }

  function searchCaller() {
    const mobile = String($("#estimate-mobile-search").val() || $("#search-mobile").val() || "").trim();
    if (!mobile) {
      alert("Enter mobile number.");
      return;
    }
    resetEstimateData(false);
    searchMobile = mobile;
    $("#estimate-caller-status").text("Searching...");
    $.ajax({
      url: "/hhome-collection/search-caller",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ mobile }),
      success: function (res) {
        caller = res.caller || null;
        linkedPatients = res.linked_patients || [];
        referenceAddresses = res.reference_addresses || [];
        addresses = res.addresses || [];
        selectedAddressId = Number(res.selected_address_id || 0);
        selectedAddress = addresses.find((a) => Number(a.id || a.address_id || 0) === selectedAddressId) || null;
        if (res.found) {
          const callerName = caller?.full_name ? `${caller.full_name} - ` : "";
          $("#estimate-caller-status").text(`${callerName}${res.mobile || caller?.primary_mobile || mobile}`);
        } else {
          $("#estimate-caller-status").text("Caller not found. Create patient in booking Form 1, then search again.");
          $("#p-contact-mobile").val(res.mobile || mobile);
        }
        renderPatientsList();
        renderAddressList();
        renderPatientEstimateCards();
        resetPatientForm();
        resetAddressForm();
      },
      error: function (xhr) {
        $("#estimate-caller-status").text(xhr.responseJSON?.message || "Search failed");
      }
    });
  }

  function resetEstimateData(clearMobile) {
    caller = null;
    linkedPatients = [];
    selectedPatients = [];
    addresses = [];
    referenceAddresses = [];
    selectedAddressId = 0;
    selectedAddress = null;
    if (clearMobile) $("#estimate-mobile-search,#search-mobile").val("");
    $(".estimate-panel-suggest").addClass("d-none").html("");
    renderPatientsList();
    renderAddressList();
    renderPatientEstimateCards();
    resetPatientForm();
    resetAddressForm();
  }

  function mergeSelectedPatientsFromBundle(bundle) {
    if (Array.isArray(bundle.linked_patients)) linkedPatients = bundle.linked_patients;
    if (Array.isArray(bundle.reference_addresses)) referenceAddresses = bundle.reference_addresses;
    if (Array.isArray(bundle.addresses)) addresses = bundle.addresses;
    selectedAddressId = Number(bundle.selected_address_id || selectedAddressId || 0);
    selectedAddress = addresses.find((a) => Number(a.id || a.address_id || 0) === selectedAddressId) || selectedAddress;
    const selectedIds = new Set((bundle.selected_patients || []).map((p) => Number(p.patient_id || p.id || 0)).filter(Boolean));
    if (selectedIds.size) {
      selectedIds.forEach((id) => {
        if (findSelectedPatient(id)) return;
        const source = linkedPatients.find((p) => patientId(p) === id) || (bundle.selected_patients || []).find((p) => Number(p.patient_id || p.id || 0) === id);
        addPatient(source, false);
      });
    }
    renderPatientsList();
    renderAddressList();
    renderPatientEstimateCards();
  }

  function addPatient(patient, syncServer = true) {
    const id = patientId(patient);
    if (!id || findSelectedPatient(id)) return;
    const base = {
      id,
      patient_code: patient.patient_code || "",
      full_name: displayPatientName(patient),
      age: patient.age || "",
      age_years: patient.age_years || "",
      gender: patient.gender || "",
      contact_mobile: patient.contact_mobile || patient.mobile || patient.primary_mobile || "",
      alternate_mobile: patient.alternate_mobile || "",
      panel_company: patient.panel_company || "",
      panel: null,
      mode: "P",
      tests: []
    };
    selectedPatients.push(base);
    if (syncServer) {
      $.ajax({
        url: "/hhome-collection/select-patient",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ patient_id: id }),
        success: mergeSelectedPatientsFromBundle,
        error: function (xhr) {
          removePatient(id, false);
          alert(xhr.responseJSON?.message || "Unable to select patient.");
        }
      });
    }
    $.get(`/hhome-collection/patient/${id}`, function (res) {
      const detail = res.patient || {};
      const hit = findSelectedPatient(id);
      if (!hit) return;
      hit.full_name = [detail.title, detail.full_name].filter(Boolean).join(" ").trim() || hit.full_name;
      hit.gender = detail.gender || hit.gender;
      hit.age_years = detail.age_years || hit.age_years;
      hit.age = detail.age_years ? `${detail.age_years}y` : hit.age;
      hit.panel_company = detail.panel_company || hit.panel_company || "";
      if (!hit.panel && hit.panel_company) autoLoadPatientPanel(hit);
      renderPatientsList();
      renderPatientEstimateCards();
    });
  }

  function autoLoadPatientPanel(patient) {
    const q = String(patient?.panel_company || "").trim();
    if (!q) return;
    $.get("/hhome-collection/panel-companies", { q, limit: 10 }, function (res) {
      const items = res.items || [];
      const qNorm = q.toLowerCase();
      const found = items.find((x) => String(x.pname || "").trim().toLowerCase() === qNorm) || items[0];
      const hit = findSelectedPatient(patient.id);
      if (!hit || !found) return;
      hit.panel = {
        centerId: String(found.CenterID ?? ""),
        pname: String(found.pname ?? ""),
        compCatId: String(found.CompCatID ?? ""),
        catDetails: String(found.CatDetails ?? ""),
        allowedModes: normalizeChargeModeCode(String(found.BillingChargeMode ?? "")),
        showmrp: Number(found.showmrp || 0)
      };
      hit.mode = hit.panel.allowedModes.includes(hit.mode) ? hit.mode : (hit.panel.allowedModes[0] || "P");
      renderPatientEstimateCards();
    });
  }

  function removePatient(id, syncServer = true) {
    selectedPatients = selectedPatients.filter((p) => Number(p.id) !== Number(id));
    if (syncServer) {
      $.ajax({
        url: "/hhome-collection/remove-selected-patient",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ patient_id: id }),
        success: mergeSelectedPatientsFromBundle
      });
    }
    renderPatientsList();
    renderPatientEstimateCards();
  }

  function refreshAddresses() {
    $.get("/hhome-collection/addresses", { _ts: Date.now() }, function (res) {
      addresses = res.addresses || [];
      selectedAddressId = Number(res.selected_address_id || 0);
      selectedAddress = addresses.find((a) => Number(a.id || a.address_id || 0) === selectedAddressId) || null;
      renderAddressList();
    });
  }

  function patientPayload() {
    const title = String($("#p-title").val() || "").trim();
    return {
      title: title || null,
      full_name: String($("#p-full-name").val() || "").trim(),
      labmate_pid: String($("#p-labmate-pid").val() || "").trim(),
      panel_company: String($("#p-panel-company").val() || "").trim(),
      card_number: String($("#p-card-number").val() || "").trim(),
      tag: "",
      gender: String($("#p-gender").val() || "").trim(),
      date_of_birth: $("#p-dob").val() || null,
      age_years: $("#p-age-years").val() || null,
      contact_mobile: String($("#p-contact-mobile").val() || "").trim(),
      alternate_mobile: String($("#p-alternate-mobile").val() || "").trim(),
      email: String($("#p-email").val() || "").trim(),
      searched_mobile: searchMobile || String($("#estimate-mobile-search").val() || $("#search-mobile").val() || "").trim()
    };
  }

  function syncSelectedPatientFromLinked(targetPatientId) {
    const source = linkedPatients.find((p) => patientId(p) === Number(targetPatientId));
    const hit = findSelectedPatient(targetPatientId);
    if (!source || !hit) return;
    hit.full_name = displayPatientName(source);
    hit.age = source.age || hit.age || "";
    hit.age_years = source.age_years || hit.age_years || "";
    hit.gender = source.gender || hit.gender || "";
    hit.contact_mobile = source.contact_mobile || source.mobile || hit.contact_mobile || "";
    hit.alternate_mobile = source.alternate_mobile || hit.alternate_mobile || "";
    hit.panel_company = source.panel_company || hit.panel_company || "";
    if (hit.panel_company) autoLoadPatientPanel(hit);
  }

  function startEditPatient(patientId) {
    $.get(`/hhome-collection/patient/${patientId}`, function (res) {
      const p = res?.patient || {};
      editingPatientId = Number(p.id || patientId);
      $("#new-patient-form").removeClass("d-none");
      $("#btn-save-patient").text("Update Patient");
      $("#btn-show-patient-form").text("Cancel Edit");
      renderPatientTitleOptions();
      $("#p-title").val(p.title || "");
      $("#p-full-name").val(p.full_name || "");
      $("#p-labmate-pid").val(p.labmate_pid || "");
      $("#p-panel-company").val(p.panel_company || "");
      $("#p-card-number").val(p.card_number || "");
      $("#p-gender").val(p.gender || "Male");
      $("#p-dob").val(p.date_of_birth || "");
      $("#p-age-years").val(p.age_years || "");
      $("#p-contact-mobile").val(p.contact_mobile || "");
      $("#p-alternate-mobile").val(p.alternate_mobile || "");
      $("#p-email").val(p.email || "");
    }).fail(function (xhr) {
      alert(xhr.responseJSON?.message || "Unable to load patient details");
    });
  }

  function savePatient() {
    const data = patientPayload();
    if (!data.title) return alert("Title is required.");
    if (!data.full_name) return alert("Patient Full Name is required.");
    if (!data.gender) return alert("Gender is required.");
    if (!data.contact_mobile) return alert("Contact No is required.");
    $.ajax({
      url: editingPatientId ? `/hhome-collection/patient/${editingPatientId}` : "/hhome-collection/create-patient",
      method: editingPatientId ? "PATCH" : "POST",
      contentType: "application/json",
      data: JSON.stringify(data),
      success: function (res) {
        const updatedPatientId = editingPatientId;
        caller = res.caller || caller;
        mergeSelectedPatientsFromBundle(res || {});
        if (updatedPatientId) syncSelectedPatientFromLinked(updatedPatientId);
        resetPatientForm();
        $("#estimate-caller-status").text(`${updatedPatientId ? "Patient updated" : "Patient saved"} for ${data.contact_mobile}`);
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || "Unable to save patient details");
      }
    });
  }

  function sanitizePincodeInput(raw) {
    return String(raw || "").replace(/\D+/g, "").slice(0, 6);
  }

  function isAddressColonyManualMode() {
    return $("#a-colony-manual").is(":checked");
  }

  function syncAddressColonyMode() {
    const manual = isAddressColonyManualMode();
    $("#a-colony").toggleClass("d-none", manual).prop("required", !manual);
    $("#a-colony-free").toggleClass("d-none", !manual).prop("required", manual);
    $("#a-pincode").prop("readonly", !manual);
  }

  function updateRouteFromManualPincode() {
    if (!isAddressColonyManualMode()) return;
    const city = String($("#a-city").val() || "").trim().toLowerCase();
    const pincode = sanitizePincodeInput($("#a-pincode").val());
    $("#a-pincode").val(pincode);
    if (pincode.length !== 6) {
      $("#a-route").val("");
      return;
    }
    const mapped = (addressColonyCatalog || []).find((c) => {
      return String(c.city || "").trim().toLowerCase() === city &&
        String(c.pincode || "").trim() === pincode &&
        String(c.route_no || "").trim();
    });
    $("#a-route").val(mapped ? String(mapped.route_no || "").trim() : "");
  }

  function loadColonies(resetSelection) {
    const city = $("#a-city").val();
    const requestId = ++colonyRequestSeq;
    $.get("/hhome-collection/colonies", { city }, function (res) {
      if (requestId !== colonyRequestSeq) return;
      addressColonyCatalog = Array.isArray(res.colonies) ? res.colonies : [];
      const options = ['<option value="">Select Colony</option>'];
      addressColonyCatalog.forEach((c) => {
        const name = String(c.colony_name || "").trim();
        if (!name) return;
        options.push(`<option value="${escHtml(c.id)}" data-pincode="${escHtml(c.pincode || "")}" data-route="${escHtml(c.route_no || "")}">${escHtml(name)}</option>`);
      });
      $("#a-colony").html(options.join(""));
      if (resetSelection) {
        $("#a-colony").val("");
        $("#a-pincode,#a-route").val("");
      }
      syncAddressColonyMode();
      updateRouteFromManualPincode();
    });
  }

  function validateFloorField() {
    const special = String($("#a-floor-special").val() || "").trim();
    if (special) return true;
    const floor = String($("#a-floor").val() || "").trim();
    return /^[1-9][0-9]?$/.test(floor);
  }

  function addressPayload() {
    const selectedSpecial = String($("#a-floor-special").val() || "").trim();
    const manualColony = isAddressColonyManualMode();
    return {
      address_type: $("#a-type").val(),
      house_flat_no: String($("#a-house").val() || "").trim(),
      full_house: Boolean(selectedSpecial),
      floor: selectedSpecial || String($("#a-floor").val() || "").trim(),
      block_tower_no: String($("#a-block").val() || "").trim(),
      street_sector: String($("#a-street").val() || "").trim(),
      landmark: String($("#a-landmark").val() || "").trim(),
      city: $("#a-city").val(),
      colony_id: manualColony ? "" : $("#a-colony").val(),
      colony_not_found: manualColony,
      colony_name: manualColony ? String($("#a-colony-free").val() || "").trim() : "",
      pincode: sanitizePincodeInput($("#a-pincode").val()),
      route: String($("#a-route").val() || "").trim(),
      google_location: String($("#a-google-location").val() || "").trim(),
      access_notes: String($("#a-access").val() || "").trim()
    };
  }

  function saveAddress() {
    if (!selectedPatients.length) return alert("Select at least one patient first.");
    if (!validateFloorField()) return alert("Enter a floor number from 1 to 99 or select one floor option.");
    const data = addressPayload();
    const manualColony = Boolean(data.colony_not_found);
    if (!data.house_flat_no) return alert("House/Flat No is required.");
    if (!data.city) return alert("City is required.");
    if (!manualColony && !data.colony_id) return alert("Colony is required.");
    if (manualColony && !data.colony_name) return alert("Colony name is required.");
    if (!data.pincode || data.pincode.length !== 6) return alert("Enter valid 6 digit pincode.");
    if (!data.route) return alert("No route mapping found for selected city and pincode.");
    $.ajax({
      url: "/hhome-collection/create-address",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(data),
      success: function () {
        resetAddressForm();
        refreshAddresses();
      },
      error: function (xhr) {
        alert(xhr.responseJSON?.message || "Unable to save address details");
      }
    });
  }

  function renderPanelSuggestions(patientId, items) {
    const box = $(`.estimate-panel-suggest[data-patient-id="${patientId}"]`);
    if (!items.length) {
      box.html('<div class="estimate-panel-item">No panel found</div>').removeClass("d-none");
      return;
    }
    box.html(items.map((x) => `
      <div class="estimate-panel-item"
        data-patient-id="${patientId}"
        data-center-id="${escHtml(x.CenterID || "")}"
        data-pname="${escHtml(x.pname || "")}"
        data-comp-cat-id="${escHtml(x.CompCatID ?? "")}"
        data-cat-details="${escHtml(x.CatDetails || "")}"
        data-billing-charge-mode="${escHtml(x.BillingChargeMode || "")}"
        data-showmrp="${escHtml(x.showmrp || 0)}">
        <strong>${escHtml(x.pname || "")}</strong>
        <span>CenterID: ${escHtml(x.CenterID || "")} | CompCatID: ${escHtml(x.CompCatID ?? "")}</span>
      </div>`).join("")).removeClass("d-none");
  }

  function openPanelTestsModal(patientId) {
    const patient = findSelectedPatient(patientId);
    if (!String(patient?.panel?.compCatId ?? "").trim()) {
      if (String(patient?.panel_company || patient?.panel?.pname || "").trim()) {
        autoLoadPatientPanel(patient);
        alert("Panel company loading. Click Add Tests again.");
      } else {
        alert("Select panel company first.");
      }
      return;
    }
    activePanelPicker = {
      patientId: Number(patientId),
      compCatId: patient.panel.compCatId,
      centerId: patient.panel.centerId || "",
      selectedGcode: "",
      selectedScode: "",
      tempSelected: (patient.tests || []).reduce((acc, t) => {
        acc[testKey(t)] = t;
        return acc;
      }, {})
    };
    panelTestSearchQuery = "";
    $("#btn-apply-panel-tests").text("Add To Patient");
    $("#panel-modal-meta").text(`Patient: ${displayPatientName(patient)} | Panel: ${patient.panel.pname} | Mode: ${modeLabel(patient.mode)}`);
    $("#panel-test-search").val("");
    $("#panel-test-search-note").text("Type 2 letters to search across all tests");
    $("#panel-groups-list").html('<div class="text-muted p-2">Loading groups...</div>');
    $("#panel-subgroups-list").html('<div class="text-muted p-2">Select group</div>');
    $("#panel-tests-list").html('<div class="text-muted p-2">Select subgroup</div>');
    $("#panel-child-tests-list").html('<div class="text-muted p-2">Child Tests button se list khulegi</div>');
    panelTestsModal = new bootstrap.Modal(document.getElementById("panelTestsModal"));
    panelTestsModal.show();
    loadPanelGroups();
  }

  function renderPanelTestsList(tests, emptyMessage) {
    const patient = findSelectedPatient(activePanelPicker?.patientId);
    const list = filterTestsByPatientGender(tests, patient);
    if (!list.length) {
      $("#panel-tests-list").html(`<div class="text-muted p-2">${escHtml(emptyMessage || "No tests mapped")}</div>`);
      return;
    }
    $("#panel-tests-list").html(list.map((t) => {
      const p = priced(t, patient);
      const key = testKey(t);
      const checked = activePanelPicker.tempSelected[key] ? "checked" : "";
      const childBtn = t.has_children
        ? `<button type="button" class="panel-child-btn" data-parent-gcode="${escHtml(t.gcode)}" data-parent-scode="${escHtml(t.scode)}" data-parent-test-code="${escHtml(t.test_code || "")}">Child Tests</button>`
        : "";
      const groupLine = [t.group_description, t.subgroup_description].filter(Boolean).join(" / ");
      const priceLine = patient?.mode === "C"
        ? `MRP: ${fmt(p.mrp)}`
        : patient?.mode === "F"
          ? `MRP: ${fmt(p.mrp)} | Final: ${fmt(p.finalRate)}`
          : `MRP: ${fmt(p.mrp)} | Discount: ${fmt(p.discount)} | Final: ${fmt(p.finalRate)}`;
      return `
        <label class="panel-test-item">
          <input type="checkbox" class="panel-test-check" ${checked}
            data-gcode="${escHtml(t.gcode)}"
            data-scode="${escHtml(t.scode)}"
            data-test-code="${escHtml(t.test_code || "")}"
            data-testcode1="${escHtml(t.testcode1 || "")}"
            data-booked-code="${escHtml(t.booked_code || "")}"
            data-gender-rule="${escHtml(t.gender_rule || "")}"
            data-includes-type="${escHtml(t.has_children ? "Profile" : "Single")}"
            data-description="${escHtml(t.description || "")}"
            data-charge="${escHtml(t.charge || 0)}"
            data-mrp="${escHtml(t.mrp || 0)}"
            data-max-discount="${escHtml(t.max_discount || 0)}" />
          <div class="panel-test-main">
            <div><strong>${escHtml(t.description || "")}</strong></div>
            <div class="panel-test-meta"><span>${escHtml(key)}</span>${groupLine ? ` | <span>${escHtml(groupLine)}</span>` : ""}</div>
            <div class="panel-test-meta">${escHtml(priceLine)}</div>
          </div>
          ${childBtn ? `<div class="panel-test-actions">${childBtn}</div>` : ""}
        </label>`;
    }).join(""));
  }

  function loadPanelTestsForCurrentView() {
    const query = String(panelTestSearchQuery || "").trim();
    if (query.length >= 2) {
      const seq = ++panelTestSearchSeq;
      $("#panel-tests-list").html('<div class="text-muted p-2">Searching tests...</div>');
      $("#panel-child-tests-list").html('<div class="text-muted p-2">Search mode active</div>');
      $("#panel-test-search-note").text(`Searching for "${query}" across all tests`);
      $.get("/hhome-collection/panel-test-search", {
        comp_cat_id: activePanelPicker.compCatId,
        center_id: activePanelPicker.centerId,
        q: query,
        limit: 30
      }, function (res) {
        if (seq !== panelTestSearchSeq) return;
        renderPanelTestsList(res.tests || [], "No matching tests found");
      }).fail(function () {
        if (seq !== panelTestSearchSeq) return;
        $("#panel-tests-list").html('<div class="text-danger p-2">Search failed</div>');
      });
      return;
    }

    panelTestSearchSeq += 1;
    $("#panel-test-search-note").text("Type 2 letters to search across all tests");
    $("#panel-tests-list").html('<div class="text-muted p-2">Loading tests...</div>');
    $("#panel-child-tests-list").html('<div class="text-muted p-2">Child Tests button se list khulegi</div>');
    $.get("/hhome-collection/panel-tests", {
      comp_cat_id: activePanelPicker.compCatId,
      center_id: activePanelPicker.centerId,
      gcode: activePanelPicker.selectedGcode,
      scode: activePanelPicker.selectedScode
    }, function (res) {
      renderPanelTestsList(res.tests || [], "No tests mapped");
    }).fail(function () {
      $("#panel-tests-list").html('<div class="text-danger p-2">Load failed</div>');
    });
  }

  function loadPanelGroups() {
    $.get("/hhome-collection/panel-groups", {
      comp_cat_id: activePanelPicker.compCatId,
      center_id: activePanelPicker.centerId
    }, function (res) {
      const groups = res.groups || [];
      if (!groups.length) {
        $("#panel-groups-list").html('<div class="text-muted p-2">No groups mapped</div>');
        return;
      }
      $("#panel-groups-list").html(groups.map((g) => `
        <div class="panel-row-item panel-group-item" data-gcode="${escHtml(g.gcode)}">
          <span class="panel-row-code">${escHtml(g.gcode)}</span>${escHtml(g.description || "")}
        </div>`).join(""));
      $("#panel-groups-list .panel-group-item").first().addClass("active");
      activePanelPicker.selectedGcode = String(groups[0].gcode || "");
      loadPanelSubgroups();
    });
  }

  function loadPanelSubgroups() {
    $("#panel-subgroups-list").html('<div class="text-muted p-2">Loading subgroups...</div>');
    $("#panel-tests-list").html('<div class="text-muted p-2">Select subgroup</div>');
    $.get("/hhome-collection/panel-subgroups", {
      comp_cat_id: activePanelPicker.compCatId,
      center_id: activePanelPicker.centerId,
      gcode: activePanelPicker.selectedGcode
    }, function (res) {
      const subgroups = res.subgroups || [];
      if (!subgroups.length) {
        $("#panel-subgroups-list").html('<div class="text-muted p-2">No subgroups mapped</div>');
        return;
      }
      $("#panel-subgroups-list").html(subgroups.map((s) => `
        <div class="panel-row-item panel-subgroup-item" data-scode="${escHtml(s.scode)}">
          <span class="panel-row-code">${escHtml(s.scode)}</span>${escHtml(s.description || "")}
        </div>`).join(""));
      $("#panel-subgroups-list .panel-subgroup-item").first().addClass("active");
      activePanelPicker.selectedScode = String(subgroups[0].scode || "");
      loadPanelTestsForCurrentView();
    });
  }

  function loadPanelChildTests(parentGcode, parentScode, parentTestCode) {
    $("#panel-child-tests-list").html('<div class="text-muted p-2">Loading child tests...</div>');
    $.get("/hhome-collection/panel-child-tests", {
      parent_gcode: parentGcode,
      parent_scode: parentScode,
      parent_test_code: parentTestCode
    }, function (res) {
      const tests = res.tests || [];
      if (!tests.length) {
        $("#panel-child-tests-list").html('<div class="text-muted p-2">No child tests found</div>');
        return;
      }
      $("#panel-child-tests-list").html(tests.map((t) => `
        <div class="panel-child-item">
          <div class="panel-child-line"><strong>${escHtml(t.booked_code || "")}</strong> - ${escHtml(t.description || "")}</div>
        </div>`).join(""));
    }).fail(function () {
      $("#panel-child-tests-list").html('<div class="text-danger p-2">Child tests load failed</div>');
    });
  }

  function enrichTestsWithTat(tests, done) {
    (tests || []).forEach((t) => { t.test_tat = ""; });
    if (typeof done === "function") done();
  }

  function applySelectedPanelTests() {
    const patient = findSelectedPatient(activePanelPicker?.patientId);
    if (!patient) return;
    const map = (patient.tests || []).reduce((acc, t) => {
      acc[testKey(t)] = t;
      return acc;
    }, {});
    Object.values(activePanelPicker?.tempSelected || {}).forEach((t) => {
      const key = testKey(t);
      if (!key) return;
      map[key] = {
        booked_code: key,
        gender_rule: String(t.gender_rule || "").trim(),
        includes_type: includesType(t),
        description: String(t.description || "").trim(),
        test_tat: String(t.test_tat || "").trim(),
        charge: Number(t.charge || 0),
        mrp: Number(t.mrp || 0),
        max_discount: Number(t.max_discount || 0)
      };
    });
    patient.tests = Object.values(map);
    enrichTestsWithTat(patient.tests, renderPatientEstimateCards);
    $("#panel-test-search").val("");
    if (panelTestsModal) panelTestsModal.hide();
  }

  function imageToDataUrl(src, transparentWhite = false, renderSize = 0, tintRgb = null, opacity = 1) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = function () {
        try {
          const canvas = document.createElement("canvas");
          const sourceW = img.naturalWidth || renderSize || 64;
          const sourceH = img.naturalHeight || renderSize || 64;
          const scale = renderSize ? (renderSize / Math.max(sourceW, sourceH)) : 1;
          canvas.width = Math.max(1, Math.round(sourceW * scale));
          canvas.height = Math.max(1, Math.round(sourceH * scale));
          const ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          if (transparentWhite || tintRgb || opacity < 1) {
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const data = imageData.data;
            for (let i = 0; i < data.length; i += 4) {
              if (data[i] > 238 && data[i + 1] > 238 && data[i + 2] > 238) data[i + 3] = 0;
              if (tintRgb && data[i + 3] > 0) {
                data[i] = tintRgb[0];
                data[i + 1] = tintRgb[1];
                data[i + 2] = tintRgb[2];
              }
              if (opacity < 1) data[i + 3] = Math.round(data[i + 3] * opacity);
            }
            ctx.putImageData(imageData, 0, 0);
          }
          resolve(canvas.toDataURL("image/png"));
        } catch (e) {
          resolve("");
        }
      };
      img.onerror = function () { resolve(""); };
      img.crossOrigin = "anonymous";
      img.src = src;
    });
  }

  function patientMobile(patient) {
    return String(patient?.mobile || patient?.contact_mobile || patient?.primary_mobile || "-").trim() || "-";
  }

  function patientMobileForSave(patient) {
    const value = String(patient?.mobile || patient?.contact_mobile || patient?.primary_mobile || "").trim();
    return value === "-" ? "" : value;
  }

  function includesType(test) {
    const value = String(test?.includes_type || test?.includes || "").trim().toLowerCase();
    if (value === "profile" || value === "package") return "Profile";
    if (value === "single" || value === "single test") return "Single";
    if (test?.has_children === true || test?.has_children === 1 || String(test?.has_children || "") === "1") return "Profile";
    return "Single";
  }

  function patientPanelName(patient) {
    return String(patient?.panel?.pname || patient?.panel || patient?.panel_company || "-").trim() || "-";
  }

  function genderRuleAllowsPatient(testItem, patientGender) {
    const rule = String(testItem?.gender_rule || "").trim();
    const gender = String(patientGender || "").trim().toLowerCase();
    if (!rule || rule === "1") return true;
    if (rule === "2") return gender === "male";
    if (rule === "3") return gender === "female";
    return true;
  }

  function filterTestsByPatientGender(tests, patient) {
    const list = Array.isArray(tests) ? tests : [];
    const gender = String(patient?.gender || "").trim().toLowerCase();
    if (!gender) return list;
    return list.filter((t) => genderRuleAllowsPatient(t, gender));
  }

  function buildEstimatePayload() {
    const patients = selectedPatients.filter((p) => (p.tests || []).length > 0).map((p) => {
      const t = totals(p);
      return {
        patient_id: patientId(p),
        patient_name: displayPatientName(p),
        age: p.age || p.age_years || "",
        gender: p.gender || "",
        mobile: patientMobileForSave(p),
        panel: patientPanelName(p),
        panel_center_id: p.panel?.centerId || "",
        panel_comp_cat_id: p.panel?.compCatId || "",
        panel_cat_details: p.panel?.catDetails || "",
        panel_allowed_modes: (p.panel?.allowedModes || []).join(""),
        panel_show_mrp: Number(p.panel?.showmrp || 0),
        mode: modeLabel(p.mode),
        mode_code: p.mode || "P",
        mrp: t.mrp,
        discount: t.discount,
        total: t.final
      };
    });
    const tests = [];
    selectedPatients.forEach((p) => {
      (p.tests || []).forEach((t) => {
        tests.push({
          patient_id: patientId(p),
          booked_code: testKey(t),
          gender_rule: String(t.gender_rule || "").trim(),
          test_name: String(t.description || t.test_name || "").trim(),
          includes_type: includesType(t),
          test_tat: String(t.test_tat || "").trim(),
          mrp: Number(t.mrp || 0),
          charge: Number(t.charge || 0),
          max_discount: Number(t.max_discount || 0)
        });
      });
    });
    const grand = selectedPatients.reduce((acc, p) => {
      const t = totals(p);
      acc.mrp += t.mrp;
      acc.discount += t.discount;
      acc.total += t.final;
      return acc;
    }, { mrp: 0, discount: 0, total: 0 });
    return {
      caller_id: caller?.id || null,
      address_text: selectedAddress ? addressLine(selectedAddress) : "",
      patients,
      tests,
      grand_mrp: grand.mrp,
      grand_discount: grand.discount,
      grand_total: grand.total
    };
  }

  function hydratePatientsFromEstimate(record) {
    const tests = record?.tests || [];
    return (record?.patients || []).map((p) => {
      const pid = Number(p.patient_id || p.id || 0);
      return {
        id: pid,
        full_name: p.patient_name || "",
        age: p.age || "",
        gender: p.gender || "",
        contact_mobile: p.mobile || "",
        panel_company: p.panel || "",
        panel: {
          pname: p.panel || "",
          centerId: p.panel_center_id || "",
          compCatId: p.panel_comp_cat_id || "",
          catDetails: p.panel_cat_details || "",
          allowedModes: normalizeChargeModeCode(p.panel_allowed_modes || p.mode_code || "CPF"),
          showmrp: Number(p.panel_show_mrp || 0)
        },
        mode: p.mode_code || "P",
        tests: tests.filter((t) => Number(t.patient_id || 0) === pid).map((t) => ({
          booked_code: t.booked_code || "",
          gender_rule: t.gender_rule || "",
          description: t.test_name || t.description || "",
          includes_type: includesType(t),
          test_tat: t.test_tat || "",
          mrp: Number(t.mrp || 0),
          charge: Number(t.charge || 0),
          max_discount: Number(t.max_discount || 0)
        }))
      };
    });
  }

  function lockEstimateIdentity() {
    $("#estimate-mobile-search,#estimate-search-caller,#btn-show-patient-form,#btn-show-address-form,#estimate-reset").prop("disabled", true);
    $("#estimate-caller-status").text(`Editing estimate #${editingEstimateId}. Patient and address are locked; tests can be changed.`);
  }

  function loadEstimateForEdit(id) {
    $.get(`/hhome-collection/estimate/${id}`, function (res) {
      if (!res.ok || !res.estimate) {
        $("#estimate-caller-status").text(res.message || "Estimate load failed.");
        return;
      }
      const record = res.estimate;
      caller = { id: record.caller_id };
      selectedAddress = null;
      selectedAddressId = 0;
      addresses = record.address_text ? [{ id: 0, address: record.address_text }] : [];
      selectedPatients = hydratePatientsFromEstimate(record);
      selectedPatients.forEach((patient) => {
        if (!String(patient?.panel?.compCatId || "").trim() && String(patient.panel_company || "").trim()) {
          autoLoadPatientPanel(patient);
        }
      });
      renderPatientsList();
      renderAddressList();
      renderPatientEstimateCards();
      lockEstimateIdentity();
    }).fail(function () {
      $("#estimate-caller-status").text("Estimate load failed.");
    });
  }

  async function downloadEstimateRecord(record) {
    const patientsWithTests = hydratePatientsFromEstimate(record).filter((p) => (p.tests || []).length > 0);
    if (!patientsWithTests.length) return;
    const jsPDF = window.jspdf?.jsPDF;
    if (!jsPDF) {
      window.print();
      return;
    }
    const doc = new jsPDF({ unit: "pt", format: "a4" });
    const pageW = doc.internal.pageSize.getWidth();
    const pageH = doc.internal.pageSize.getHeight();
    const margin = 16;
    const contentBottom = pageH - 126;
    const [logo, nablLogo, img247, watermark] = await Promise.all([
      imageToDataUrl(window.HC_ESTIMATE_LOGO_URL || "/static/assets/Logo1.png"),
      imageToDataUrl("/static/nabl_cropped.png", false, 512),
      imageToDataUrl("/static/247img.png", false, 256),
      imageToDataUrl("/static/watermark.png", false, 1000, [87, 191, 151], 0.72)
    ]);
    const iconTeal = [5, 78, 93];
    const iconWhite = [255, 255, 255];
    const [phoneIcon, globeIcon, pinIcon, saveIcon, beforeIcon, helpIcon, assuranceIcon] = await Promise.all([
      imageToDataUrl("https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/icons/telephone-fill.svg", false, 256, iconTeal),
      imageToDataUrl("https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/icons/globe2.svg", false, 256, iconTeal),
      imageToDataUrl("https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/icons/geo-alt-fill.svg", false, 256, iconTeal),
      imageToDataUrl("https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/icons/cash-coin.svg", false, 256, iconWhite),
      imageToDataUrl("https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/icons/clipboard2-check.svg", false, 256, iconTeal),
      imageToDataUrl("https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/icons/telephone-forward-fill.svg", false, 256, iconTeal),
      imageToDataUrl("https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/icons/shield-check.svg", false, 256, iconTeal)
    ]);
    const estimateId = record?.id || "";
    const createdAt = record?.created_at || "";
    const createdBy = record?.created_by_name || "";
    const estimateDate = String(createdAt || "").split(" ").slice(0, 1).join(" ") || "-";
    const validUntil = estimateDate !== "-" ? addDaysText(estimateDate, 7) : "-";
    const teal = [5, 78, 93];
    const green = [87, 191, 151];
    const navy = [5, 78, 93];

    function addDaysText(dateText, days) {
      const parts = String(dateText || "").match(/^(\d{2})-(\d{2})-(\d{4})$/);
      if (!parts) return "-";
      const dt = new Date(Number(parts[3]), Number(parts[2]) - 1, Number(parts[1]));
      dt.setDate(dt.getDate() + days);
      return dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
    }

    function estimateDisplayId() {
      const idNum = Number(estimateId || 0);
      if (!idNum) return "-";
      const parts = String(estimateDate || "").match(/^(\d{2})-(\d{2})-(\d{4})$/);
      const year = parts ? Number(parts[3]) : new Date().getFullYear();
      const month = parts ? Number(parts[2]) : (new Date().getMonth() + 1);
      const fyStartYear = month >= 4 ? year : year - 1;
      return `${String(fyStartYear).slice(-2)}-${1000 + idNum}`;
    }

    function drawRoundedCard(x, y, w, h, fill = [244, 249, 252], stroke = [220, 234, 242]) {
      doc.setFillColor(...fill);
      doc.setDrawColor(...stroke);
      doc.roundedRect(x, y, w, h, 6, 6, "FD");
    }

    function drawWatermark() {
      if (!watermark) return;
      const w = 340;
      const h = 430;
      doc.addImage(watermark, "PNG", (pageW - w) / 2, (pageH - h) / 2, w, h);
    }

    function drawPageHeader() {
      let y = 16;
      doc.setFillColor(232, 247, 240);
      doc.rect(0, 0, pageW, 5, "F");
      doc.setFillColor(240, 250, 246);
      doc.rect(0, 5, pageW, 5, "F");
      doc.setFillColor(248, 253, 251);
      doc.rect(0, 10, pageW, 5, "F");
      if (logo) doc.addImage(logo, "PNG", margin + 2, y + 2, 184, 61);
      const titleX = (pageW / 2) + 22;
      doc.setTextColor(...navy);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(21);
      doc.text("Cost Estimate", titleX, y + 38, { align: "center" });
      if (nablLogo) doc.addImage(nablLogo, "PNG", pageW - margin - 130, y + 10, 122, 61);
      doc.setDrawColor(184, 224, 207);
      doc.setLineWidth(0.8);
      doc.line(margin, 100, pageW - margin, 100);
      return 118;
    }

    function drawPatientInfo(patient, y) {
      const boxW = pageW - (margin * 2);
      const cols = [168, 160, 128, boxW - 456];
      const boxH = 62;
      drawRoundedCard(margin, y, boxW, boxH, [239, 247, 251]);
      doc.setFont("helvetica", "normal");
      doc.setFontSize(9);
      doc.setTextColor(22, 34, 46);
      doc.setDrawColor(220, 232, 240);
      let dividerX = margin;
      cols.slice(0, 3).forEach((w) => {
        dividerX += w;
        doc.line(dividerX, y + 12, dividerX, y + boxH - 12);
      });
      const patientX = margin + 8;
      const estimateX = margin + cols[0] + 8;
      const tariffX = margin + cols[0] + cols[1] + 8;
      const summaryX = margin + cols[0] + cols[1] + cols[2] + 8;
      doc.setFont("helvetica", "bold");
      doc.text(doc.splitTextToSize(`Patient: ${displayPatientName(patient)}`, cols[0] - 16).slice(0, 1), patientX, y + 16);
      doc.setFont("helvetica", "normal");
      doc.text(`Age / Gender: ${patient.age || patient.age_years || "-"} / ${patient.gender || "-"}`, patientX, y + 34);
      doc.text(`Mobile: ${patientMobile(patient)}`, patientX, y + 50);
      doc.text(`Estimate ID: ${estimateDisplayId()}`, estimateX, y + 16);
      doc.text(`Estimate Date: ${estimateDate}`, estimateX, y + 34);
      doc.text(`Valid Until: ${validUntil} (7 days)`, estimateX, y + 50);
      doc.text(doc.splitTextToSize(`Panel: ${patientPanelName(patient)}`, cols[2] - 16).slice(0, 2), tariffX, y + 18);
      doc.text(`Payment Mode: ${modeLabel(patient.mode)}`, tariffX, y + 50);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(7.6);
      doc.text(`Total Patients: ${fmt(patientsWithTests.length)}`, summaryX, y + 13);
      if (Number(record?.grand_discount || 0) > 0) {
        doc.text(`Grand MRP: Rs ${fmt(record?.grand_mrp || 0)}`, summaryX, y + 26);
        doc.text(`Grand Discount: Rs ${fmt(record?.grand_discount || 0)}`, summaryX, y + 39);
      } else {
        doc.text(`Grand Charge: Rs ${fmt(record?.grand_total || record?.grand_mrp || 0)}`, summaryX, y + 31);
      }
      doc.setFont("helvetica", "bold");
      doc.setFontSize(8.4);
      doc.text(`Grand Total: Rs ${fmt(record?.grand_total || 0)}`, summaryX, y + 54);
      return y + boxH + 14;
    }

    function drawTestsTable(patient, y) {
      const widths = [28, 210, 68, 170, 87];
      const heads = ["#", "Test Name / Package", "Includes", "Test TAT", "Amount"];
      const tableW = widths.reduce((a, b) => a + b, 0);
      doc.setFillColor(...teal);
      doc.setTextColor(255, 255, 255);
      doc.roundedRect(margin, y, tableW, 24, 4, 4, "F");
      doc.setFont("helvetica", "bold");
      doc.setFontSize(9);
      let x = margin;
      heads.forEach((h, i) => {
        if (i === 4) doc.text(h, x + widths[i] - 8, y + 16, { align: "right" });
        else doc.text(h, x + 6, y + 16);
        x += widths[i];
      });
      y += 24;
      doc.setTextColor(0, 0, 0);
      doc.setFont("helvetica", "normal");
      (patient.tests || []).forEach((t, idx) => {
        if (y + 30 > contentBottom) {
          doc.addPage();
          y = drawPageHeader();
          y = drawPatientInfo(patient, y);
        }
        const p = priced(t, patient);
        const nameLines = doc.splitTextToSize(String(t.description || t.test_name || "-"), widths[1] - 10);
        const tatLines = doc.splitTextToSize(String(t.test_tat || "-"), widths[3] - 10);
        const rowH = Math.max(28, (Math.max(nameLines.length, tatLines.length) * 10) + 12);
        doc.setDrawColor(220, 226, 235);
        doc.rect(margin, y, tableW, rowH);
        let cx = margin;
        const row = [String(idx + 1), nameLines, includesType(t), tatLines, fmt(p.mrp)];
        row.forEach((v, i) => {
          if (Array.isArray(v)) doc.text(v, cx + 6, y + 14);
          else if (i === 4) doc.text(String(v), cx + widths[i] - 8, y + 17, { align: "right" });
          else doc.text(String(v), cx + 6, y + 17);
          cx += widths[i];
        });
        y += rowH;
      });
      return y + 12;
    }

    function drawPatientBottom(patient, y) {
      const pt = totals(patient);
      const hasDiscount = Number(pt.discount || 0) > 0;
      const totalW = 205;
      const tx = pageW - margin - totalW;
      if (hasDiscount) {
        const saveW = 112;
        const sx = tx - saveW - 12;
        doc.setFillColor(...teal);
        doc.setDrawColor(0, 96, 88);
        doc.roundedRect(sx, y + 20, saveW, 42, 21, 21, "FD");
        doc.setFillColor(0, 108, 101);
        doc.circle(sx + 23, y + 41, 14, "F");
        if (saveIcon) doc.addImage(saveIcon, "PNG", sx + 15, y + 33, 16, 16);
        doc.setTextColor(255, 255, 255);
        doc.setFont("helvetica", "bold");
        doc.setTextColor(255, 224, 102);
        doc.setFontSize(10);
        doc.text("You Save", sx + 45, y + 34);
        doc.setTextColor(255, 255, 255);
        doc.setFontSize(12.5);
        doc.text(`Rs ${fmt(pt.discount)}`, sx + 45, y + 48);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(6);
        doc.text("on this estimate", sx + 45, y + 57);
      }

      drawRoundedCard(tx, y, totalW, hasDiscount ? 70 : 46, [242, 249, 252]);
      doc.setTextColor(15, 23, 42);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(10);
      if (!hasDiscount) {
        doc.setFillColor(...teal);
        doc.roundedRect(tx, y + 12, totalW, 24, 4, 4, "F");
        doc.setTextColor(255, 255, 255);
        doc.text("Total Charge", tx + 14, y + 28);
        doc.text(`Rs ${fmt(pt.final)}`, tx + totalW - 14, y + 28, { align: "right" });
        return;
      }
      doc.text("Total MRP", tx + 14, y + 18);
      doc.text(`Rs ${fmt(pt.mrp)}`, tx + totalW - 14, y + 18, { align: "right" });
      doc.text("Total Discount", tx + 14, y + 36);
      doc.text(`Rs ${fmt(pt.discount)}`, tx + totalW - 14, y + 36, { align: "right" });
      doc.setFillColor(...teal);
      doc.roundedRect(tx, y + 48, totalW, 22, 3, 3, "F");
      doc.setTextColor(255, 255, 255);
      doc.text("Estimated Payable", tx + 14, y + 63);
      doc.text(`Rs ${fmt(pt.final)}`, tx + totalW - 14, y + 63, { align: "right" });
    }

    function drawInfoBoxes(y) {
      const gap = 10;
      const boxW = (pageW - (margin * 2) - (gap * 2)) / 3;
      const items = [
        {
          icon: beforeIcon,
          title: "Before Your Test",
          lines: [
            "Some tests require fasting or preparation.",
            "Please confirm requirements with our team."
          ]
        },
        {
          icon: helpIcon,
          title: "Need Help?",
          lines: [
            "Call / WhatsApp",
            "93111 93111",
            "We're here 24 x 7",
            "Home collection support"
          ]
        },
        {
          icon: assuranceIcon,
          title: "Our Assurance",
          lines: [
            "NABL Accredited Laboratory",
            "Home Collection Available",
            "Patient Safety & Confidentiality"
          ]
        }
      ];
      items.forEach((item, idx) => {
        const x = margin + (idx * (boxW + gap));
        const textX = x + 46;
        const textW = boxW - 56;
        drawRoundedCard(x, y, boxW, 72, [235, 246, 249], [201, 222, 234]);
        if (item.icon) {
          doc.setFillColor(217, 240, 236);
          doc.circle(x + 23, y + 36, 16, "F");
          doc.addImage(item.icon, "PNG", x + 13, y + 26, 20, 20);
        }
        doc.setTextColor(...navy);
        doc.setFont("helvetica", "bold");
        doc.setFontSize(8.2);
        doc.text(item.title, textX, y + 18);
        doc.setTextColor(22, 34, 46);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(6.6);
        let ly = y + 32;
        item.lines.forEach((line, lineIdx) => {
          if (idx === 2) {
            doc.setFillColor(...teal);
            doc.circle(x + 50, ly - 2, 1.2, "F");
            const lines = doc.splitTextToSize(line, textW - 10);
            doc.text(lines, x + 56, ly);
            ly += (lines.length - 1) * 7;
          } else if (idx === 1 && lineIdx === 1) {
            doc.setFont("helvetica", "bold");
            doc.setFontSize(8);
            doc.text(line, textX, ly);
            doc.setFont("helvetica", "normal");
            doc.setFontSize(6.6);
          } else {
            const lines = doc.splitTextToSize(line, textW);
            doc.text(lines, textX, ly);
            ly += (lines.length - 1) * 7;
          }
          ly += 9;
        });
      });
    }

    function drawSummaryPage(grandMrp, grandDiscount, grandFinal) {
      doc.addPage();
      let y = drawPageHeader();
      const conditionsCardH = 418;
      drawRoundedCard(margin, y, pageW - (margin * 2), conditionsCardH, [255, 255, 255], [220, 234, 242]);
      doc.setTextColor(...teal);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(12);
      doc.text("General Conditions", margin + 16, y + 24);
      const conditions = [
        "This document is an estimate only and not a final bill/receipt. The final amount payable will depend upon the tests actually registered and performed.",
        "This estimate is valid for 7 days from the date of issue, unless otherwise specified. Test prices, packages and promotional discounts may change after the validity period.",
        "Please verify the patient name, age, gender, tests/packages and tariff mentioned above before proceeding.",
        "Package/profile names represent the tests included in the package as applicable on the date of the estimate. Test inclusions can be confirmed with the laboratory before registration.",
        "Turnaround Time (TAT) is indicative and is calculated from receipt/registration of an acceptable sample at the laboratory, unless otherwise specified. TAT may change due to repeat/confirmatory testing, technical issues.",
        "Home collection/visit charges are included in the estimate unless specifically stated otherwise.",
        "Discounts shown are applicable only to the tests/packages and tariff mentioned in this estimate and cannot necessarily be combined with other discounts, schemes or contractual rates.",
        "Rates under CGHS/ECHS/other institutional or contracted tariffs are subject to applicable eligibility, documentation and prevailing tariff rules. If eligibility is not established, the applicable self-pay tariff may be charged.",
        "If additional, reflex or confirmatory tests are clinically/laboratorily required and are not included in the quoted package, the patient will be informed of any additional charge before billing wherever applicable.",
        "In certain cases, based on test findings or medical requirements, we may request an additional or repeat sample to ensure accurate reporting. This may affect the reporting time.",
        "Any request for test cancellation or refund must be made within 60 minutes of sample collection. Requests received after this period may not be accepted.",
        "All approved refunds will be processed exclusively through NEFT/bank transfer or UPI to the beneficiary details provided. Refunds will not be made in cash.",
        "Additional tests may be requested after sample collection, subject to sufficient sample being available and suitable for testing. Whether the test can be added will depend on the sample type, test requirements and the time elapsed since collection.",
        "For assistance regarding this estimate, test preparation, sample requirements or home collection, please contact Dr Bhasin's Lab at 9311193111."
      ];
      doc.setTextColor(34, 44, 60);
      doc.setFont("helvetica", "normal");
      doc.setFontSize(8.2);
      let cy = y + 44;
      conditions.forEach((text, idx) => {
        const lines = doc.splitTextToSize(text, pageW - (margin * 2) - 72);
        doc.text(`${idx + 1}.`, margin + 24, cy);
        doc.text(lines, margin + 48, cy);
        cy += Math.max(17, lines.length * 10 + 6);
      });
      drawInfoBoxes(Math.max(y + conditionsCardH + 14, cy + 10));
    }

    function drawPdfFooter(pageNum, totalPages) {
      const footerTop = pageH - 76;
      if (img247) doc.addImage(img247, "PNG", margin + 20, footerTop - 60, 54, 54);
      doc.setFillColor(226, 234, 241);
      doc.setDrawColor(211, 224, 235);
      doc.roundedRect(margin, footerTop, pageW - (margin * 2), 60, 4, 4, "FD");
      doc.setFillColor(...teal);
      doc.roundedRect(margin, footerTop, pageW - (margin * 2), 24, 4, 4, "F");
      doc.setTextColor(255, 255, 255);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(8);
      function drawContactIcon(iconData, x, y) {
        doc.setFillColor(255, 255, 255);
        doc.circle(x, y, 6, "F");
        if (iconData) doc.addImage(iconData, "PNG", x - 4, y - 4, 8, 8);
      }
      const contacts = [
        {
          icon: phoneIcon,
          text: "Call / WhatsApp: 93111 93111",
          x: margin + 24,
          url: "tel:9311193111"
        },
        {
          icon: globeIcon,
          text: "bhasinpathlabs.com",
          x: margin + 280,
          url: "https://bhasinpathlabs.com/"
        },
        {
          icon: pinIcon,
          text: "S-35 Greater Kailash Part 1",
          x: pageW - margin - 140,
          url: "https://www.google.com/maps/search/?api=1&query=Dr%20Bhasin%27s%20Lab%20S-35%20Greater%20Kailash%20Part%201%20New%20Delhi"
        }
      ];
      contacts.forEach(({ icon, text, x, url }) => {
        drawContactIcon(icon, x - 9, footerTop + 12);
        doc.setTextColor(255, 255, 255);
        doc.setFontSize(8);
        doc.text(text, x, footerTop + 15);
        if (url) {
          const linkW = doc.getTextWidth(text) + 22;
          doc.link(x - 18, footerTop + 4, linkW, 17, { url });
        }
      });
      doc.setTextColor(15, 23, 42);
      doc.setFont("helvetica", "normal");
      doc.setFontSize(8);
      doc.text("Patient First | Empathy | Accuracy | Honesty | Safe & Hygienic", margin + 72, footerTop + 44);
      doc.text(`Created At: ${createdAt || "-"}`, pageW - margin - 12, footerTop + 39, { align: "right" });
      doc.text(`Created By: ${createdBy || "-"}`, pageW - margin - 12, footerTop + 52, { align: "right" });
      doc.setFont("helvetica", "bold");
      doc.setFontSize(7);
      doc.setTextColor(76, 91, 110);
      doc.text(`Page ${pageNum} of ${totalPages}`, pageW / 2, pageH - 6, { align: "center" });
    }

    let grandMrp = 0;
    let grandDiscount = 0;
    let grandFinal = 0;
    patientsWithTests.forEach((patient, pIdx) => {
      if (pIdx > 0) doc.addPage();
      let y = drawPageHeader();
      y = drawPatientInfo(patient, y);
      y = drawTestsTable(patient, y);
      if (y + 84 > contentBottom) {
        doc.addPage();
        y = drawPageHeader();
        y = drawPatientInfo(patient, y);
      }
      drawPatientBottom(patient, y);
      const pt = totals(patient);
      grandMrp += pt.mrp;
      grandDiscount += pt.discount;
      grandFinal += pt.final;
    });

    drawSummaryPage(grandMrp, grandDiscount, grandFinal);

    const pageCount = doc.internal.getNumberOfPages();
    for (let i = 1; i <= pageCount; i += 1) {
      doc.setPage(i);
      drawWatermark();
      drawPdfFooter(i, pageCount);
    }
    doc.save(`estimate-${estimateId || Date.now()}.pdf`);
  }

  function saveEstimate() {
    const payload = buildEstimatePayload();
    if (!payload.patients.length || !payload.tests.length) return;
    $("#estimate-save").prop("disabled", true).text(editingEstimateId ? "Updating..." : "Saving...");
    $.ajax({
      url: editingEstimateId ? `/hhome-collection/estimate/${editingEstimateId}` : "/hhome-collection/estimate-save",
      method: editingEstimateId ? "PATCH" : "POST",
      contentType: "application/json",
      data: JSON.stringify(payload)
    }).done(function (res) {
      if (res.ok) window.location.href = "/hhome-collection/estimate-list";
      else alert(res.message || "Estimate save failed.");
    }).fail(function (xhr) {
      alert(xhr.responseJSON?.message || "Estimate save failed.");
    }).always(function () {
      $("#estimate-save").prop("disabled", false).text(editingEstimateId ? "Update Estimate" : "Save Estimate");
    });
  }

  $(function () {
    if (!$("#estimate-patient-estimates").length) return;
    renderPatientsList();
    renderAddressList();
    renderPatientEstimateCards();

    $("#estimate-search-caller,#btn-search-caller").on("click", searchCaller);
    $("#estimate-mobile-search,#search-mobile").on("keydown", function (e) {
      if (e.key === "Enter") searchCaller();
    });
    $("#p-dob").on("change", autoFillAgeFromDob);
    $("#p-age-years").on("input change", autoDobFromAge);

    $("#estimate-patient-list").on("change", ".estimate-patient-check", function () {
      const id = Number($(this).data("patient-id") || 0);
      const patient = linkedPatients.find((p) => patientId(p) === id);
      if ($(this).is(":checked")) addPatient(patient);
      else removePatient(id);
      renderPatientsList();
      renderPatientEstimateCards();
    });

    $("#estimate-patient-list").on("click", ".estimate-patient-edit-btn", function (e) {
      e.preventDefault();
      e.stopPropagation();
      const id = Number($(this).data("patient-id") || 0);
      if (!id) return;
      if (!window.confirm("Are you sure want to edit the patient?")) return;
      startEditPatient(id);
    });

    $("#estimate-patient-list").on("click", ".estimate-patient-inactive-btn", function (e) {
      e.preventDefault();
      e.stopPropagation();
      const id = Number($(this).data("patient-id") || 0);
      if (!id) return;
      if (!window.confirm("Are you sure you want to mark this patient as inactive?")) return;
      $.ajax({
        url: `/hhome-collection/patient/${encodeURIComponent(id)}/inactive`,
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({}),
        success: function (res) {
          selectedPatients = selectedPatients.filter((p) => patientId(p) !== id);
          mergeSelectedPatientsFromBundle(res || {});
          resetPatientForm();
          renderPatientEstimateCards();
        },
        error: function (xhr) {
          alert(xhr.responseJSON?.message || "Unable to mark patient inactive");
        }
      });
    });

    $("#linked-patients-panel").on("click", ".estimate-linked-patient", function () {
      const id = Number($(this).data("patient-id") || 0);
      const patient = linkedPatients.find((p) => patientId(p) === id);
      if (findSelectedPatient(id)) removePatient(id);
      else addPatient(patient);
    });

    $("#selected-patient-tags").on("click", ".rm-patient", function () {
      removePatient(Number($(this).data("patient-id") || 0));
    });

    $("#estimate-address-list,#address-list").on("click", ".estimate-address-pick,.btn-use-address", function (e) {
      e.preventDefault();
      selectedAddressId = Number($(this).data("address-id") || $(this).closest(".address-card").data("address-id") || 0);
      selectedAddress = addresses.find((a) => Number(a.id || a.address_id || 0) === selectedAddressId) || null;
      renderAddressList();
      if (selectedAddressId) {
        $.ajax({
          url: "/hhome-collection/select-address",
          method: "POST",
          contentType: "application/json",
          data: JSON.stringify({ address_id: selectedAddressId })
        });
      }
    });

    $("#estimate-address-list,#address-list").on("click", "#estimate-clear-address", function () {
      selectedAddressId = 0;
      selectedAddress = null;
      renderAddressList();
    });

    $("#p-panel-company").on("input", function () {
      const q = String($(this).val() || "").trim();
      const $suggest = $("#p-panel-company-suggest");
      if (q.length < 2) {
        $suggest.addClass("d-none").html("");
        return;
      }
      $.get("/hhome-collection/panel-companies", { q, limit: 20, atype: "C" }, function (res) {
        const items = res.items || [];
        if (!items.length) {
          $suggest.html('<div class="estimate-panel-item"><strong>No panel found</strong></div>').removeClass("d-none");
          return;
        }
        $suggest.html(items.map((x) => `
          <div class="estimate-panel-item" data-pname="${escHtml(x.pname || "")}">
            <strong>${escHtml(x.pname || "")}</strong>
            <span>CenterID: ${escHtml(x.CenterID || "")}</span>
          </div>
        `).join("")).removeClass("d-none");
      });
    });

    $("#p-panel-company-suggest").on("click", ".estimate-panel-item", function () {
      const pname = dataString($(this), "pname");
      if (pname) $("#p-panel-company").val(pname);
      $("#p-panel-company-suggest").addClass("d-none").html("");
    });

    $("#estimate-patient-estimates").on("input", ".estimate-panel-search", function () {
      const q = String($(this).val() || "").trim();
      const pid = Number($(this).data("patient-id") || 0);
      const patient = findSelectedPatient(pid);
      if (!patient) return;
      patient.panel = null;
      patient.tests = [];
      if (q.length < 2) {
        $(`.estimate-panel-suggest[data-patient-id="${pid}"]`).addClass("d-none").html("");
        return;
      }
      $.get("/hhome-collection/panel-companies", { q, limit: 10 }, function (res) {
        renderPanelSuggestions(pid, res.items || []);
      });
    });

    $("#estimate-patient-estimates").on("click", ".estimate-panel-item", function () {
      const pid = Number($(this).data("patient-id") || 0);
      const patient = findSelectedPatient(pid);
      if (!patient) return;
      patient.panel = {
        centerId: dataString($(this), "center-id"),
        pname: dataString($(this), "pname"),
        compCatId: dataString($(this), "comp-cat-id"),
        catDetails: dataString($(this), "cat-details"),
        allowedModes: normalizeChargeModeCode(dataString($(this), "billing-charge-mode")),
        showmrp: Number(dataString($(this), "showmrp") || 0)
      };
      patient.mode = patient.panel.allowedModes.includes(patient.mode) ? patient.mode : (patient.panel.allowedModes[0] || "P");
      patient.tests = [];
      renderPatientEstimateCards();
    });

    $("#estimate-patient-estimates").on("change", ".estimate-charge-mode", function () {
      const patient = findSelectedPatient(Number($(this).data("patient-id") || 0));
      if (!patient) return;
      patient.mode = String($(this).val() || "P").toUpperCase();
      renderPatientEstimateCards();
    });

    $("#estimate-patient-estimates").on("click", ".estimate-open-tests", function () {
      openPanelTestsModal(Number($(this).data("patient-id") || 0));
    });

    $("#estimate-patient-estimates").on("click", ".estimate-clear-tests", function () {
      const patient = findSelectedPatient(Number($(this).data("patient-id") || 0));
      if (!patient) return;
      patient.tests = [];
      renderPatientEstimateCards();
    });

    $("#estimate-patient-estimates").on("click", ".estimate-remove-test", function () {
      const patient = findSelectedPatient(Number($(this).data("patient-id") || 0));
      const key = String($(this).data("key") || "");
      if (!patient) return;
      patient.tests = (patient.tests || []).filter((t) => testKey(t) !== key);
      renderPatientEstimateCards();
    });

    $("#estimate-patient-estimates").on("click", ".estimate-remove-patient", function () {
      removePatient(Number($(this).data("patient-id") || 0));
    });

    $("#estimate-reset").on("click", function () {
      resetEstimateData(true);
      $("#estimate-caller-status").text("Search mobile number to load linked patients and addresses.");
    });
    $("#estimate-save").on("click", saveEstimate);
    $("#btn-apply-panel-tests").on("click", applySelectedPanelTests);

    renderPatientTitleOptions();
    resetPatientForm();
    resetAddressForm();

    $("#btn-show-patient-form").on("click", function () {
      if (editingPatientId) {
        resetPatientForm();
        return;
      }
      $("#new-patient-form").toggleClass("d-none");
      const opened = !$("#new-patient-form").hasClass("d-none");
      $(this).text(opened ? "Cancel" : "+ Add New Patient");
      if (!opened) resetPatientForm();
      if (opened && !$("#p-contact-mobile").val()) {
        $("#p-contact-mobile").val(searchMobile || $("#estimate-mobile-search").val().trim() || $("#search-mobile").val().trim());
      }
    });

    $("#p-title").on("change", function () {
      const gender = titleGenderMap[String($(this).val() || "").trim()];
      if (gender) $("#p-gender").val(gender);
    });

    $("#btn-save-patient").on("click", savePatient);

    $("#btn-show-address-form").on("click", function () {
      $("#new-address-form").toggleClass("d-none");
      const opened = !$("#new-address-form").hasClass("d-none");
      $(this).text(opened ? "Cancel" : "+ Add New Address");
      if (opened && !addressColonyCatalog.length && $("#a-city").val()) loadColonies(false);
    });

    $("#a-city").on("change", function () {
      $("#a-pincode,#a-route").val("");
      loadColonies(true);
    });

    $("#a-colony").on("change", function () {
      const opt = $(this).find(":selected");
      $("#a-pincode").val(opt.data("pincode") || "");
      $("#a-route").val(opt.data("route") || "");
    });

    $("#a-colony-manual").on("change", function () {
      if ($(this).is(":checked")) {
        $("#a-colony").val("");
      } else {
        $("#a-colony-free,#a-pincode,#a-route").val("");
      }
      syncAddressColonyMode();
      updateRouteFromManualPincode();
    });

    $("#a-pincode").on("input", updateRouteFromManualPincode);
    $("#btn-save-address").on("click", saveAddress);

    $(document).on("input", "#panel-test-search", function () {
      panelTestSearchQuery = String($(this).val() || "").trim();
      if (panelTestSearchTimer) clearTimeout(panelTestSearchTimer);
      panelTestSearchTimer = setTimeout(loadPanelTestsForCurrentView, 250);
    });

    $(document).on("click", "#panel-groups-list .panel-group-item", function () {
      $("#panel-groups-list .panel-row-item").removeClass("active");
      $(this).addClass("active");
      activePanelPicker.selectedGcode = String($(this).data("gcode") || "");
      activePanelPicker.selectedScode = "";
      loadPanelSubgroups();
    });

    $(document).on("click", "#panel-subgroups-list .panel-subgroup-item", function () {
      $("#panel-subgroups-list .panel-row-item").removeClass("active");
      $(this).addClass("active");
      activePanelPicker.selectedScode = String($(this).data("scode") || "");
      loadPanelTestsForCurrentView();
    });

    $(document).on("change", "#panel-tests-list .panel-test-check", function () {
      const pick = {
        gcode: String($(this).data("gcode") || ""),
        scode: String($(this).data("scode") || ""),
        test_code: String($(this).data("test-code") || ""),
        testcode1: String($(this).data("testcode1") || ""),
        booked_code: String($(this).data("booked-code") || ""),
        gender_rule: String($(this).data("gender-rule") || ""),
        includes_type: String($(this).data("includes-type") || "Single"),
        description: String($(this).data("description") || ""),
        charge: Number($(this).data("charge") || 0),
        mrp: Number($(this).data("mrp") || 0),
        max_discount: Number($(this).data("max-discount") || 0)
      };
      const key = testKey(pick);
      if ($(this).is(":checked")) activePanelPicker.tempSelected[key] = pick;
      else delete activePanelPicker.tempSelected[key];
    });

    $(document).on("click", "#panel-tests-list .panel-child-btn", function (e) {
      e.preventDefault();
      e.stopPropagation();
      loadPanelChildTests(
        String($(this).data("parent-gcode") || ""),
        String($(this).data("parent-scode") || ""),
        String($(this).data("parent-test-code") || "")
      );
    });

    $(document).on("click", function (e) {
      if (!$(e.target).closest(".estimate-panel-wrap").length) {
        $(".estimate-panel-suggest").addClass("d-none");
      }
    });

    if (editingEstimateId) {
      $("#estimate-save").text("Update Estimate");
      loadEstimateForEdit(editingEstimateId);
    }
  });

  window.HCEstimate = {
    downloadRecord: downloadEstimateRecord
  };
})();
