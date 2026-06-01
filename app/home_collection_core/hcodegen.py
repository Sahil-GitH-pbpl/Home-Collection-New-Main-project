import json
from datetime import date, datetime


def hcode_from_id(prefix: str, row_id: int, width: int = 6) -> str:
    return f"{prefix}{str(row_id).zfill(width)}"


def hto_json(value) -> str:
    return json.dumps(value, ensure_ascii=True, default=str)


def hcalculate_age_parts(dob):
    if not dob:
        return None, None
    if isinstance(dob, str):
        dob = datetime.strptime(dob, "%Y-%m-%d").date()
    today = date.today()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    months = (today.month - dob.month) % 12
    return max(years, 0), months


def hage_label(age_years, dob):
    if age_years is not None:
        return f"{age_years}y"
    if dob:
        years, _ = hcalculate_age_parts(dob)
        return f"{years}y"
    return "NA"

