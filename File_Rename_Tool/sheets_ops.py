"""
Optional Google Sheets helpers. All entry points fail softly when
credentials are missing or libraries error, so the caller can keep the UI alive.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# Column order must match read/apply logic (status columns at end).
SHEET_HEADERS: List[str] = [
    "Batch ID",
    "Full File Path",
    "Original Filename",
    "Extracted Date",
    "Version",
    "Flag",
    "Cleaned Event Name",
    "Proposed Filename",
    "Confidence",
    "Needs Review",
    "Conflict",
    "Approve",
    "Manual Notes",
    "Rename Status",
    "Timestamp",
]


def _column_letter_1based(col: int) -> str:
    """Spreadsheet column letter from 1-based index (A=1)."""
    s = ""
    while col > 0:
        col, r = divmod(col - 1, 26)
        s = chr(65 + r) + s
    return s


def _status_and_timestamp_letters() -> Tuple[str, str]:
    idx_status = SHEET_HEADERS.index("Rename Status") + 1
    idx_ts = SHEET_HEADERS.index("Timestamp") + 1
    return _column_letter_1based(idx_status), _column_letter_1based(idx_ts)


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


def worksheet_title(start_index: int, batch_size: int) -> str:
    return f"Start {start_index} Size {batch_size}"


def push_batch(
    spreadsheet_name: str,
    start_index: int,
    batch_size: int,
    rows: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    """
    Create or clear/update the worksheet for this batch with fresh data.
    rows: dicts with keys matching SHEET_HEADERS semantics.
    """
    client, err = try_open_client()
    if client is None:
        return False, err or "Unknown client error"

    try:
        sh = client.open(spreadsheet_name)
    except Exception as e:
        return False, f"Open spreadsheet failed (share with service account?): {e}"

    title = worksheet_title(start_index, batch_size)
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
        table.append(
            [
                r.get("Batch ID", ""),
                r.get("Full File Path", ""),
                r.get("Original Filename", ""),
                r.get("Extracted Date", ""),
                r.get("Version", ""),
                r.get("Flag", ""),
                r.get("Cleaned Event Name", ""),
                r.get("Proposed Filename", ""),
                r.get("Confidence", ""),
                r.get("Needs Review", ""),
                r.get("Conflict", ""),
                r.get("Approve", ""),
                r.get("Manual Notes", ""),
                r.get("Rename Status", ""),
                r.get("Timestamp", ""),
            ]
        )

    # RAW = store literals; USER_ENTERED makes Sheets parse "2012-06-17 - Name" as a date
    # and strip hyphens / change spacing in the Proposed Filename column.
    try:
        ws.update(table, value_input_option="RAW")
    except Exception as e:
        return False, f"Writing sheet failed: {e}"

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
    Update Rename Status and Timestamp columns (positions follow SHEET_HEADERS).
    row_number_1based is the Google Sheet row index (header = row 1).
    """
    client, err = try_open_client()
    if client is None:
        return False, err or "Unknown client error"

    col_status, col_ts = _status_and_timestamp_letters()
    status_cell = f"{col_status}{row_number_1based}"
    time_cell = f"{col_ts}{row_number_1based}"
    value = rename_status if not error_message else f"{rename_status}: {error_message}"

    try:
        sh = client.open(spreadsheet_name)
        ws = sh.worksheet(worksheet_name)
        ws.batch_update(
            [
                {"range": status_cell, "values": [[value]]},
                {"range": time_cell, "values": [[timestamp]]},
            ],
            value_input_option="RAW",
        )
    except Exception as e:
        return False, str(e)
    return True, ""
