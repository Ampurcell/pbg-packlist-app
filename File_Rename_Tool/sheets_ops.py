"""
Optional Google Sheets helpers. All entry points fail softly when
credentials are missing or libraries error, so the caller can keep the UI alive.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# Column order: Original next to Proposed for manual edits; then Approve, Confidence,
# Needs Review; status columns last. Must match push_batch, preview, and apply logic.
SHEET_HEADERS: List[str] = [
    "Batch ID",
    "Full File Path",
    "Extracted Date",
    "Original Filename",
    "Proposed Filename",
    "Approve",
    "Confidence",
    "Needs Review",
    "Conflict",
    "Manual Notes",
    "Rename Status",
    "Timestamp",
]


def _col_index_to_a1(col_1based: int) -> str:
    """1-based column index to A1 letter(s)."""
    letters = ""
    n = col_1based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _strip_conditional_format_rules_for_sheet(sh: Any, sheet_id: int) -> None:
    """Remove all conditional-format rules on a sheet so re-push does not stack duplicates."""
    meta = sh.fetch_sheet_metadata(
        params={"fields": "sheets(properties(sheetId),conditionalFormats)"}
    )
    deletes: List[Dict[str, Any]] = []
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") != sheet_id:
            continue
        cfs = s.get("conditionalFormats") or []
        for i in range(len(cfs) - 1, -1, -1):
            deletes.append(
                {"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}}
            )
    if deletes:
        sh.batch_update({"requests": deletes})


def _apply_needs_review_row_highlight(sh: Any, ws: Any, num_data_rows: int) -> None:
    """Yellow row fill when Needs Review is YES (full data row, all columns)."""
    if num_data_rows <= 0:
        return
    sheet_id = ws.id
    _strip_conditional_format_rules_for_sheet(sh, sheet_id)

    needs_idx = SHEET_HEADERS.index("Needs Review")
    needs_letter = _col_index_to_a1(needs_idx + 1)
    ncols = len(SHEET_HEADERS)
    # Data rows are sheet rows 2 .. num_data_rows+1 → 0-based grid row 1 .. num_data_rows+1
    end_row_index = num_data_rows + 1
    formula = f'=${needs_letter}2="YES"'

    rule = {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": end_row_index,
                        "startColumnIndex": 0,
                        "endColumnIndex": ncols,
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {
                        "backgroundColorStyle": {
                            "rgbColor": {
                                "red": 1.0,
                                "green": 0.95,
                                "blue": 0.65,
                            }
                        }
                    },
                },
            },
            "index": 0,
        }
    }
    sh.batch_update({"requests": [rule]})


def credentials_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")


def credentials_available() -> bool:
    return os.path.isfile(credentials_path())


def try_open_client():
    """
    Return (client, None) on success, or (None, error_message).
    Never raises for missing file; may raise only unexpected bugs — caller wraps.
    """
    path = credentials_path()
    if not os.path.isfile(path):
        return None, "credentials.json not found next to app.py."

    try:
        import gspread  # noqa: F401
        from google.oauth2.service_account import Credentials
    except Exception as e:  # pragma: no cover - import guard
        return None, f"Import error (install gspread google-auth): {e}"

    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(path, scopes=scopes)
        client = __import__("gspread").authorize(creds)
        return client, None
    except Exception as e:
        return None, f"Could not authorize Google client: {e}"


def push_batch(
    spreadsheet_name: str,
    worksheet_name: str,
    rows: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    """
    Create or clear/update the worksheet named by the user with fresh data.
    rows: dicts with keys matching SHEET_HEADERS semantics.
    """
    client, err = try_open_client()
    if client is None:
        return False, err or "Unknown client error"

    title = (worksheet_name or "").strip()
    if not title:
        return False, "Google Sheet tab name is empty. Enter a tab name before Push."
    if len(title) > 99:
        title = title[:99]

    try:
        sh = client.open(spreadsheet_name)
    except Exception as e:
        return False, f"Open spreadsheet failed (share with service account?): {e}"
    try:
        ws = sh.worksheet(title)
        ws.clear()
    except Exception:
        try:
            ws = sh.add_worksheet(title=title, rows=max(1000, len(rows) + 10), cols=len(SHEET_HEADERS))
        except Exception as e:
            return False, f"Could not create worksheet: {e}"

    table: List[List[Any]] = [SHEET_HEADERS]
    for r in rows:
        table.append([r.get(h, "") for h in SHEET_HEADERS])

    try:
        ws.update(table, value_input_option="USER_ENTERED")
    except Exception as e:
        return False, f"Writing sheet failed: {e}"

    try:
        _apply_needs_review_row_highlight(sh, ws, len(rows))
    except Exception as e:
        return (
            True,
            f"Pushed {len(rows)} rows to worksheet {title!r}, but highlighting failed: {e}",
        )

    return True, f"Pushed {len(rows)} rows to worksheet {title!r}."


def read_worksheet_rows(
    spreadsheet_name: str, worksheet_name: str
) -> Tuple[Optional[List[Tuple[int, Dict[str, str]]]], str]:
    """
    Return list of (sheet_row_number_1based, row_dict) for data rows (excluding header),
    or None on failure. row_dict keys are header strings from row 1.
    """
    client, err = try_open_client()
    if client is None:
        return None, err or "Unknown client error"

    try:
        sh = client.open(spreadsheet_name)
        ws = sh.worksheet(worksheet_name)
    except Exception as e:
        return None, f"Open worksheet failed: {e}"

    try:
        values = ws.get_all_values()
    except Exception as e:
        return None, f"Reading rows failed: {e}"

    if not values:
        return [], ""

    header = [h.strip() for h in values[0]]
    out: List[Tuple[int, Dict[str, str]]] = []
    for i in range(1, len(values)):
        sheet_row = i + 1  # 1-based sheet index including header row
        row_vals = values[i]
        row_dict: Dict[str, str] = {}
        for ci, key in enumerate(header):
            if not key:
                continue
            row_dict[key] = row_vals[ci] if ci < len(row_vals) else ""
        out.append((sheet_row, row_dict))
    return out, ""


def update_row_status_timestamp(
    spreadsheet_name: str,
    worksheet_name: str,
    row_number_1based: int,
    rename_status: str,
    timestamp: str,
    error_message: str = "",
) -> Tuple[bool, str]:
    """
    Update Rename Status and Timestamp columns derived from SHEET_HEADERS.
    row_number_1based is the Google Sheet row index (header = row 1).
    """
    client, err = try_open_client()
    if client is None:
        return False, err or "Unknown client error"

    try:
        c_status = _col_index_to_a1(SHEET_HEADERS.index("Rename Status") + 1)
        c_time = _col_index_to_a1(SHEET_HEADERS.index("Timestamp") + 1)
    except ValueError:
        return False, "Sheet headers missing Rename Status or Timestamp."

    status_cell = f"{c_status}{row_number_1based}"
    time_cell = f"{c_time}{row_number_1based}"
    value = rename_status if not error_message else f"{rename_status}: {error_message}"

    try:
        sh = client.open(spreadsheet_name)
        ws = sh.worksheet(worksheet_name)
        ws.batch_update(
            [
                {"range": status_cell, "values": [[value]]},
                {"range": time_cell, "values": [[timestamp]]},
            ],
            value_input_option="USER_ENTERED",
        )
    except Exception as e:
        return False, str(e)
    return True, ""
