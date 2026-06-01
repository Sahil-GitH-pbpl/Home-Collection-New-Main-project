from __future__ import annotations

import json
import os
import re
import zipfile
from functools import lru_cache
from io import BytesIO
from typing import Any
import xml.etree.ElementTree as ET

import requests
from flask import Blueprint, abort, current_app, render_template, request, send_file
from markupsafe import Markup

consentform_bp = Blueprint("consentform", __name__)

API_URL = "http://10.1.1.252:8000/reportapi/LabmatePatRegistration.svc/Getpatientdatabymobileno"
REQUEST_TIMEOUT = 15
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"a": MAIN_NS, "r": REL_NS}


def _workbook_path() -> str:
    override = os.getenv("CONSENT_WORKBOOK_PATH", "").strip()
    if override:
        return override
    return os.path.join(current_app.root_path, "data", "consent forms.xlsx")


def normalize_api_payload(data: Any) -> Any:
    if isinstance(data, dict) and "d" in data:
        return normalize_api_payload(data["d"])

    if isinstance(data, dict) and set(data.keys()) == {"data"}:
        return normalize_api_payload(data["data"])

    if isinstance(data, str):
        text = data.strip()
        if not text:
            return text
        try:
            return normalize_api_payload(json.loads(text))
        except json.JSONDecodeError:
            return data

    return data


def fetch_patient_data(patient_id: int) -> Any:
    payload = {"mobileno": "", "patientid": patient_id}
    response = requests.post(API_URL, json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return normalize_api_payload(response.json())


def extract_patient_record(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        return data

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                return item

    return None


def infer_template_id(patient: dict[str, Any] | None) -> str:
    if not patient:
        return "template-a"

    ordered_test = str(patient.get("ordertest") or "").lower()
    panel = str(patient.get("panel") or "").lower()
    combined = f"{ordered_test} {panel}"

    if "pap" in combined or "lbc" in combined or "smear" in combined:
        return "template-b"
    if "fnac" in combined or "aspiration" in combined:
        return "template-c"
    if "hiv" in combined:
        return "template-a"
    return "template-a"


def excel_col_to_index(col_ref: str) -> int:
    result = 0
    for char in col_ref:
        result = result * 26 + (ord(char.upper()) - ord("A") + 1)
    return result


def split_cell_ref(cell_ref: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", cell_ref)
    if not match:
        raise ValueError(f"Invalid cell reference: {cell_ref}")
    col_letters, row_number = match.groups()
    return int(row_number), excel_col_to_index(col_letters)


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def format_cell_html(value: str) -> str:
    escaped = escape_html(value).replace("\n", "<br>")
    escaped = re.sub(
        r"^(?:✅|☑|✔|✓)\s*",
        '<span class="excel-check" aria-hidden="true"></span>',
        escaped,
    )
    return escaped or "&nbsp;"


def rgb_to_css(value: str | None) -> str | None:
    if not value:
        return None
    value = value[-6:]
    if len(value) != 6:
        return None
    return f"#{value}"


def excel_width_to_px(width: float) -> int:
    return max(60, int(round(width * 7 + 5)))


def points_to_px(points: float) -> int:
    return max(18, int(round(points * 96 / 72)))


@lru_cache(maxsize=4)
def load_workbook_template(workbook_path: str) -> dict[str, Any]:
    workbook: dict[str, Any] = {"styles": [], "sheets": {}}

    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root:
                text = "".join(t.text or "" for t in si.iter(f"{{{MAIN_NS}}}t"))
                shared_strings.append(text)

        styles_root = ET.fromstring(archive.read("xl/styles.xml"))
        fonts = styles_root.find("a:fonts", NS)
        fills = styles_root.find("a:fills", NS)
        borders = styles_root.find("a:borders", NS)
        cell_xfs = styles_root.find("a:cellXfs", NS)

        font_list: list[dict[str, Any]] = []
        for font in fonts or []:
            font_data: dict[str, Any] = {}
            if font.find(f"{{{MAIN_NS}}}b") is not None:
                font_data["font-weight"] = "700"
            if font.find(f"{{{MAIN_NS}}}i") is not None:
                font_data["font-style"] = "italic"
            size = font.find(f"{{{MAIN_NS}}}sz")
            if size is not None and size.attrib.get("val"):
                font_data["font-size"] = f"{size.attrib['val']}pt"
            family = font.find(f"{{{MAIN_NS}}}name")
            if family is not None and family.attrib.get("val"):
                font_data["font-family"] = family.attrib["val"]
            color = font.find(f"{{{MAIN_NS}}}color")
            if color is not None:
                rgb = rgb_to_css(color.attrib.get("rgb"))
                if rgb:
                    font_data["color"] = rgb
            font_list.append(font_data)

        fill_list: list[dict[str, Any]] = []
        for fill in fills or []:
            fill_data: dict[str, Any] = {}
            pattern_fill = fill.find(f"{{{MAIN_NS}}}patternFill")
            if pattern_fill is not None:
                fg = pattern_fill.find(f"{{{MAIN_NS}}}fgColor")
                rgb = rgb_to_css(fg.attrib.get("rgb")) if fg is not None else None
                if rgb:
                    fill_data["background-color"] = rgb
            fill_list.append(fill_data)

        border_styles: list[dict[str, Any]] = []
        border_map = {
            "thin": "1px solid #000",
            "medium": "2px solid #000",
            "thick": "3px solid #000",
            "double": "3px double #000",
            "dashed": "1px dashed #000",
            "dotted": "1px dotted #000",
        }
        for border in borders or []:
            border_data: dict[str, Any] = {}
            for side_name, css_name in {
                "left": "border-left",
                "right": "border-right",
                "top": "border-top",
                "bottom": "border-bottom",
            }.items():
                side = border.find(f"{{{MAIN_NS}}}{side_name}")
                if side is not None:
                    style = side.attrib.get("style")
                    if style:
                        border_data[css_name] = border_map.get(style, "1px solid #000")
            border_styles.append(border_data)

        xf_styles: list[dict[str, str]] = []
        for xf in cell_xfs or []:
            css: dict[str, str] = {
                "padding": "3px 6px",
                "vertical-align": "top",
                "white-space": "nowrap",
            }

            font_id = int(xf.attrib.get("fontId", "0"))
            fill_id = int(xf.attrib.get("fillId", "0"))
            border_id = int(xf.attrib.get("borderId", "0"))

            css.update(font_list[font_id] if font_id < len(font_list) else {})
            css.update(fill_list[fill_id] if fill_id < len(fill_list) else {})
            css.update(border_styles[border_id] if border_id < len(border_styles) else {})

            alignment = xf.find(f"{{{MAIN_NS}}}alignment")
            if alignment is not None:
                horizontal = alignment.attrib.get("horizontal")
                vertical = alignment.attrib.get("vertical")
                if horizontal:
                    css["text-align"] = horizontal
                if vertical:
                    css["vertical-align"] = vertical
            xf_styles.append(css)

        workbook["styles"] = xf_styles

        wb_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rel_root}

        for sheet in wb_root.find("a:sheets", NS) or []:
            name = sheet.attrib["name"]
            rel_id = sheet.attrib[f"{{{REL_NS}}}id"]
            target = "xl/" + rel_map[rel_id]
            ws_root = ET.fromstring(archive.read(target))

            dimension = ws_root.find("a:dimension", NS)
            start_ref, end_ref = (dimension.attrib.get("ref", "A1:A1").split(":") + ["A1"])[:2]
            min_row, min_col = split_cell_ref(start_ref)
            max_row, max_col = split_cell_ref(end_ref)

            col_widths: dict[int, float] = {}
            cols_parent = ws_root.find("a:cols", NS)
            if cols_parent is not None:
                for col in cols_parent.findall("a:col", NS):
                    min_idx = int(col.attrib.get("min", "1"))
                    max_idx = int(col.attrib.get("max", str(min_idx)))
                    width = float(col.attrib.get("width", "8.43"))
                    for idx in range(min_idx, max_idx + 1):
                        col_widths[idx] = width

            row_heights: dict[int, float] = {}
            cells: dict[tuple[int, int], dict[str, Any]] = {}
            sheet_data = ws_root.find("a:sheetData", NS)
            if sheet_data is not None:
                for row in sheet_data.findall("a:row", NS):
                    row_idx = int(row.attrib["r"])
                    if row.attrib.get("ht"):
                        row_heights[row_idx] = float(row.attrib["ht"])

                    for cell in row.findall("a:c", NS):
                        ref = cell.attrib.get("r")
                        if not ref:
                            continue
                        cell_row, cell_col = split_cell_ref(ref)
                        value = ""
                        cell_type = cell.attrib.get("t")
                        cell_value = cell.find("a:v", NS)
                        inline_str = cell.find("a:is", NS)
                        if cell_type == "s" and cell_value is not None:
                            value = shared_strings[int(cell_value.text or "0")]
                        elif cell_type == "inlineStr" and inline_str is not None:
                            value = "".join(t.text or "" for t in inline_str.iter(f"{{{MAIN_NS}}}t"))
                        elif cell_value is not None and cell_value.text is not None:
                            value = cell_value.text

                        style_id = int(cell.attrib.get("s", "0"))
                        cells[(cell_row, cell_col)] = {"value": value, "style_id": style_id}

            merges: dict[tuple[int, int], tuple[int, int]] = {}
            covered_cells: set[tuple[int, int]] = set()
            merge_parent = ws_root.find("a:mergeCells", NS)
            if merge_parent is not None:
                for merge in merge_parent.findall("a:mergeCell", NS):
                    start_merge, end_merge = merge.attrib["ref"].split(":")
                    start_row, start_col = split_cell_ref(start_merge)
                    end_row, end_col = split_cell_ref(end_merge)
                    merges[(start_row, start_col)] = (
                        end_row - start_row + 1,
                        end_col - start_col + 1,
                    )
                    for row_idx in range(start_row, end_row + 1):
                        for col_idx in range(start_col, end_col + 1):
                            if (row_idx, col_idx) != (start_row, start_col):
                                covered_cells.add((row_idx, col_idx))

            workbook["sheets"][name] = {
                "min_row": min_row,
                "max_row": max_row,
                "min_col": min_col,
                "max_col": max_col,
                "col_widths": col_widths,
                "row_heights": row_heights,
                "cells": cells,
                "merges": merges,
                "covered_cells": covered_cells,
            }

    return workbook


def build_template_overrides(patient: dict[str, Any]) -> dict[str, dict[str, str]]:
    patient_name = str(patient.get("patientname") or "-")
    age_gender = " ".join(
        part for part in [str(patient.get("age") or "").strip(), str(patient.get("gender") or "").strip()] if part
    ).strip() or "-"
    mobile = str(patient.get("mobileno") or "-")
    lab_id = str(patient.get("patientid") or "-")
    date_value = str(patient.get("bdate") or "-")
    doctor = str(patient.get("doctor") or "-")

    def fill_blank(template_text: str, value: str) -> str:
        match = re.search(r"_{3,}", template_text)
        if not match:
            return template_text
        safe_value = value if value else "-"
        return template_text[:match.start()] + safe_value + template_text[match.end():]

    return {
        "FR (5)": {
            "A3": fill_blank("Patient Name: __________________________________________________________________", patient_name),
            "A4": fill_blank("Age/Gender: __________________", age_gender),
            "E4": fill_blank("Mobile No: __________________", mobile),
            "A5": fill_blank("Lab Id: __________________", lab_id),
            "E5": fill_blank("Date: __________________", date_value),
            "A31": "Patient Signature: _____________________",
            "A33": fill_blank("Date : ______________________________", date_value),
        },
        "FR (4)": {
            "A3": fill_blank("Patient Name: __________________________________________________________________", patient_name),
            "A4": fill_blank("Age/Gender: __________________", age_gender),
            "E4": fill_blank("Mobile No: __________________", mobile),
            "A5": fill_blank("Lab Id: __________________", lab_id),
            "E5": fill_blank("Date: __________________", date_value),
            "A38": "Patient Signature: _____________________",
            "F38": "Witness Signature: _____________________",
            "F39": "Name of Witness: ______________________",
            "A40": fill_blank("Date : ______________________________", date_value),
            "F40": "Relationship to Patient: _____________________",
            "A42": fill_blank("Name of Doctor Explaining Procedure: ________________________", doctor),
            "A43": "Signature: ________________________________________________",
        },
        "FR (3)": {
            "A3": fill_blank("Patient Name: __________________________________________________________________", patient_name),
            "A4": fill_blank("Age/Gender: __________________", age_gender),
            "E4": fill_blank("Mobile No: __________________", mobile),
            "A5": fill_blank("Lab Id: __________________", lab_id),
            "E5": fill_blank("Date: __________________", date_value),
            "F83": "Name of Witness: ______________________",
            "A84": fill_blank("Date : ______________________________", date_value),
            "F84": "Relationship to Patient: _____________________",
            "A86": fill_blank("Name of Doctor Explaining Procedure: ________________________", doctor),
            "A87": "Signature: ________________________________________________",
        },
    }


def render_sheet_html(sheet_name: str, patient: dict[str, Any]) -> Markup:
    workbook = load_workbook_template(_workbook_path())
    sheet = workbook["sheets"][sheet_name]
    styles = workbook["styles"]
    overrides = build_template_overrides(patient).get(sheet_name, {})

    used_rows = set()
    for (row_idx, _col_idx), cell in sheet["cells"].items():
        if str(cell.get("value", "")).strip():
            used_rows.add(row_idx)
    for cell_ref, value in overrides.items():
        if str(value).strip():
            row_idx, _col_idx = split_cell_ref(cell_ref)
            used_rows.add(row_idx)

    min_row = min(used_rows) if used_rows else sheet["min_row"]
    max_row = max(used_rows) if used_rows else sheet["max_row"]
    min_col = sheet["min_col"]
    max_col = sheet["max_col"]

    colgroup_parts = []
    for col_idx in range(min_col, max_col + 1):
        width = sheet["col_widths"].get(col_idx, 8.43)
        colgroup_parts.append(f'<col style="width:{excel_width_to_px(width)}px;">')

    body_parts = []
    for row_idx in range(min_row, max_row + 1):
        row_style = ""
        row_height = sheet["row_heights"].get(row_idx)
        if sheet_name == "FR (5)" and row_idx in {22, 23, 24, 25}:
            row_height = 12
        if row_height:
            row_style = f' style="height:{points_to_px(row_height)}px;"'
        row_parts = [f"<tr{row_style}>"]

        for col_idx in range(min_col, max_col + 1):
            if (row_idx, col_idx) in sheet["covered_cells"]:
                continue

            merge_span = sheet["merges"].get((row_idx, col_idx), (1, 1))
            cell = sheet["cells"].get((row_idx, col_idx), {"value": "", "style_id": 0})

            temp_col = col_idx
            letters = []
            while temp_col:
                temp_col, remainder = divmod(temp_col - 1, 26)
                letters.append(chr(65 + remainder))
            a1_ref = "".join(reversed(letters)) + str(row_idx)

            value = overrides.get(a1_ref, cell["value"])
            style_map = styles[cell["style_id"]] if cell["style_id"] < len(styles) else {}
            lower_value = str(value).strip().lower()
            if a1_ref in {"A3", "A4", "E4", "A5", "E5"}:
                style_map = {
                    **style_map,
                    "font-size": "21px",
                    "font-weight": "700",
                    "padding-top": "10px",
                    "padding-bottom": "10px",
                }
            if (
                "signature" in lower_value
                or "date :" in lower_value
                or "name of witness" in lower_value
                or "relationship to patient" in lower_value
                or "name of doctor explaining procedure" in lower_value
            ):
                style_map = {
                    **style_map,
                    "padding-top": "14px",
                    "padding-bottom": "14px",
                    "font-size": "17px",
                    "line-height": "1.2",
                }
            if sheet_name == "FR (3)" and a1_ref in {"F82", "F83", "F84"}:
                top_offsets = {
                    "F82": "10px",
                    "F83": "38px",
                    "F84": "66px",
                }
                style_map = {
                    **style_map,
                    "position": "relative",
                    "left": "-210px",
                    "top": top_offsets[a1_ref],
                    "padding-top": "0px",
                    "padding-bottom": "0px",
                    "text-align": "left",
                    "display": "block",
                    "width": "340px",
                    "white-space": "nowrap",
                }
            style_text = "; ".join(f"{key}: {val}" for key, val in style_map.items() if val)
            attrs = []
            if merge_span[0] > 1:
                attrs.append(f'rowspan="{merge_span[0]}"')
            if merge_span[1] > 1:
                attrs.append(f'colspan="{merge_span[1]}"')
            if style_text:
                attrs.append(f'style="{style_text}"')
            attr_text = (" " + " ".join(attrs)) if attrs else ""
            row_parts.append(f"<td{attr_text}>{format_cell_html(value)}</td>")

        row_parts.append("</tr>")
        body_parts.append("".join(row_parts))

    html = (
        '<table class="excel-sheet">'
        f"<colgroup>{''.join(colgroup_parts)}</colgroup>"
        f"<tbody>{''.join(body_parts)}</tbody>"
        "</table>"
    )
    return Markup(html)


@consentform_bp.route("/consentform/workbook-media/<path:filename>")
def workbook_media(filename: str):
    safe_name = filename.replace("\\", "/")
    path = f"xl/media/{safe_name}"
    try:
        with zipfile.ZipFile(_workbook_path()) as archive:
            data = archive.read(path)
    except Exception:
        abort(404)

    content_type = "image/png"
    if safe_name.lower().endswith((".jpg", ".jpeg")):
        content_type = "image/jpeg"
    elif safe_name.lower().endswith(".gif"):
        content_type = "image/gif"

    return send_file(BytesIO(data), mimetype=content_type)


@consentform_bp.route("/consentform", methods=["GET", "POST"])
def index():
    patient_id = ""
    api_response: Any = None
    patient_record: dict[str, Any] | None = None
    rendered_templates: dict[str, Markup] = {}
    selected_template = "template-a"
    error_message = ""

    if request.method == "POST":
        patient_id = request.form.get("patient_id", "").strip()

        if not patient_id:
            error_message = "Patient ID is required."
        elif not patient_id.isdigit():
            error_message = "Patient ID must be numeric."
        else:
            try:
                api_response = fetch_patient_data(int(patient_id))
                patient_record = extract_patient_record(api_response)
                if patient_record:
                    selected_template = infer_template_id(patient_record)
                    rendered_templates = {
                        "template-a": render_sheet_html("FR (5)", patient_record),
                        "template-b": render_sheet_html("FR (4)", patient_record),
                        "template-c": render_sheet_html("FR (3)", patient_record),
                    }
            except requests.HTTPError as exc:
                error_message = f"API error: {exc.response.status_code} {exc.response.reason}"
            except requests.RequestException as exc:
                error_message = f"Request failed: {exc}"
            except ValueError:
                error_message = "The API returned invalid JSON."

    return render_template(
        "consentform.html",
        patient_id=patient_id,
        api_response=api_response,
        patient_record=patient_record,
        rendered_templates=rendered_templates,
        selected_template=selected_template,
        error_message=error_message,
    )
