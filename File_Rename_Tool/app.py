"""
Local Streamlit worker for previewing file renames and applying ONLY rows
approved as YES in Google Sheets. Never renames without explicit approval.

Run: streamlit run app.py
"""

from __future__ import annotations

import csv
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from rename_engine import analyze_row, batch_slice, iter_files
from sheets_ops import (
    credentials_available,
    push_batch,
    read_worksheet_rows,
    update_row_status_timestamp,
    worksheet_title,
)

# ---------------------------------------------------------------------------
# Setup paths (same folder as this file)
# ---------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _log_csv_path(start: int, size: int) -> str:
    return os.path.join(APP_DIR, f"rename_log_start_{start}_size_{size}.csv")


def _append_csv_log(
    path: str,
    original_full: str,
    new_full: str,
    status: str,
    error_message: str,
) -> None:
    """Append one backup log line (creates file with header if missing)."""
    fieldnames = [
        "Original Full Path",
        "New Full Path",
        "Status",
        "Error Message",
        "Timestamp",
    ]
    ts = datetime.now().isoformat(timespec="seconds")
    row = {
        "Original Full Path": original_full,
        "New Full Path": new_full,
        "Status": status,
        "Error Message": error_message,
        "Timestamp": ts,
    }
    new_file = not os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            w.writeheader()
        w.writerow(row)


def _is_safe_proposed_filename(name: str) -> Tuple[bool, str]:
    """Reject path injection or odd paths; proposed must be a plain filename."""
    if not name or name.strip() != name:
        return False, "Proposed filename is empty or has leading/trailing spaces."
    base = os.path.basename(name)
    if base != name or name in (".", ".."):
        return False, "Proposed filename must not contain path separators."
    if "/" in name or "\\" in name or ".." in name:
        return False, "Invalid characters in proposed filename."
    return True, ""


def _normalize_yes(value: str) -> bool:
    return (value or "").strip().upper() == "YES"


def _scan_folder(
    folder: str, include_subfolders: bool, status_placeholder
) -> List[str]:
    """Walk the tree and return sorted file paths with lightweight progress text."""
    folder = os.path.expanduser(folder.strip())
    if not folder or not os.path.isdir(folder):
        raise FileNotFoundError(f"Not a valid folder: {folder!r}")

    paths: List[str] = []
    for fp in iter_files(folder, include_subfolders):
        paths.append(fp)
        if len(paths) % 500 == 0:
            status_placeholder.caption(f"Scanning… {len(paths):,} files found so far")

    status_placeholder.caption(f"Sorting {len(paths):,} paths…")
    paths.sort(key=lambda p: p.lower())
    return paths


def main() -> None:
    st.set_page_config(page_title="File Rename Tool", layout="wide")
    st.title("File Rename Tool (Google Sheets approval)")
    st.warning(
        "Always test on a small folder first before running on large archives. "
        "This app never renames until you set **Approve** to YES in Google Sheets "
        "and click **Apply Approved Renames**."
    )

    has_creds = credentials_available()
    if not has_creds:
        st.error(
            "**Google Sheets disabled:** `credentials.json` was not found next to `app.py`. "
            "You can still **Scan Files** and preview locally. "
            "See README.md for how to add a service account."
        )

    # ---- Session defaults ----
    if "all_files" not in st.session_state:
        st.session_state.all_files = []
    if "last_scan_folder" not in st.session_state:
        st.session_state.last_scan_folder = ""

    # ---- Sidebar / inputs ----
    st.sidebar.header("Inputs")
    folder = st.sidebar.text_input("Folder path", value=st.session_state.last_scan_folder)
    include_sub = st.sidebar.checkbox("Include subfolders", value=True)
    batch_size = st.sidebar.number_input("Batch size", min_value=1, value=250, step=1)
    start_index = st.sidebar.number_input("Start index", min_value=0, value=0, step=1)
    sheet_name = st.sidebar.text_input("Google Sheet name", value="File Rename Tool")

    default_ws = worksheet_title(int(start_index), int(batch_size))
    apply_ws = st.sidebar.text_input(
        "Worksheet name (for apply)",
        value=default_ws,
        help="Must match the tab created by “Push to Google Sheet”, e.g. Start 0 Size 250",
    )

    st.sidebar.markdown("---")
    scan_btn = st.sidebar.button("Scan Files", type="primary")
    push_btn = st.sidebar.button(
        "Push to Google Sheet",
        disabled=not has_creds,
        help="Requires credentials.json and network access.",
    )
    apply_btn = st.sidebar.button(
        "Apply Approved Renames",
        disabled=not has_creds,
        help="Reads the worksheet and renames only rows with Approve = YES.",
    )

    if not has_creds:
        st.sidebar.caption("Push / Apply are disabled until credentials.json is present.")

    # ---- Scan action ----
    if scan_btn:
        prog = st.empty()
        try:
            files = _scan_folder(folder, include_sub, prog)
        except FileNotFoundError as e:
            st.error(str(e))
        else:
            st.session_state.all_files = files
            st.session_state.last_scan_folder = folder
            # New scan → new Batch ID on next preview build
            st.session_state.pop("_batch_key2", None)
            prog.caption("")
            st.success(f"Scan complete: {len(files):,} files.")

    all_files: List[str] = st.session_state.all_files
    total = len(all_files)
    start_i = int(start_index)
    size_i = int(batch_size)
    batch_files = batch_slice(all_files, start_i, size_i)
    end_exclusive = start_i + len(batch_files)
    suggested_next = end_exclusive if total else 0

    # ---- Summary metrics ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total files found", f"{total:,}")
    c2.metric("Current batch range", f"{start_i} – {end_exclusive - 1}" if batch_files else "—")
    c3.metric("Files in this batch", f"{len(batch_files):,}")
    c4.metric("Suggested next start index", f"{suggested_next:,}")

    if not batch_files and total > 0:
        st.info("Start index is past the end of the file list. Lower **Start index** or scan again.")

    # ---- Build preview table ----
    rows_for_df: List[Dict[str, Any]] = []
    # Tie stable Batch ID to the last completed scan path, not transient sidebar typing.
    folder_for_key = (
        st.session_state.last_scan_folder
        if st.session_state.all_files
        else folder.strip()
    )
    batch_key = (folder_for_key, total, start_i, size_i, tuple(batch_files))
    if st.session_state.get("_batch_key2") != batch_key:
        st.session_state["_batch_key2"] = batch_key
        st.session_state["_stable_batch_id"] = str(uuid.uuid4())
    batch_id = st.session_state.get("_stable_batch_id") or str(uuid.uuid4())

    for fp in batch_files:
        r = analyze_row(fp)
        r["Batch ID"] = batch_id
        r["Approve"] = r["approve_default"]
        r["Manual Notes"] = ""
        r["Rename Status"] = ""
        r["Timestamp"] = ""
        # Friendly column names for display / sheet
        rows_for_df.append(
            {
                "Batch ID": r["Batch ID"],
                "Full File Path": r["full_path"],
                "Original Filename": r["original_filename"],
                "Extracted Date": r["extracted_date"],
                "Version": r["version"],
                "Flag": r["flag"],
                "Cleaned Event Name": r["cleaned_event_name"],
                "Proposed Filename": r["proposed"],
                "Confidence": r["confidence"],
                "Needs Review": r["needs_review"],
                "Conflict": r["conflict"],
                "Approve": r["Approve"],
                "Manual Notes": "",
                "Rename Status": "",
                "Timestamp": "",
            }
        )

    st.subheader("Local preview (current batch)")
    if rows_for_df:
        st.dataframe(pd.DataFrame(rows_for_df), use_container_width=True, height=400)
    else:
        st.caption("Run **Scan Files** to populate the preview.")

    st.session_state["_last_batch_rows"] = rows_for_df
    st.session_state["_last_batch_meta"] = {
        "batch_id": batch_id,
        "start": start_i,
        "size": size_i,
        "sheet_name": sheet_name.strip(),
        "worksheet": apply_ws.strip(),
    }

    # ---- Push to Google Sheet ----
    if push_btn:
        if not rows_for_df:
            st.warning("Nothing to push — scan a folder and ensure the batch has files.")
        else:
            ts_push = datetime.now().isoformat(timespec="seconds")
            for row in rows_for_df:
                row["Timestamp"] = ts_push
            with st.spinner("Pushing batch to Google Sheets…"):
                ok, msg = push_batch(sheet_name.strip(), start_i, size_i, rows_for_df)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    # ---- Apply from Google Sheet ----
    if apply_btn:
        log_path = _log_csv_path(start_i, size_i)
        with st.status("Applying approved renames…", expanded=True) as apply_status:
            apply_status.write("Loading worksheet from Google Sheets…")
            data_rows, err = read_worksheet_rows(sheet_name.strip(), apply_ws.strip())
            if err:
                apply_status.update(label="Could not read worksheet", state="error", expanded=True)
                st.error(err)
            elif not data_rows:
                apply_status.update(label="No data rows in worksheet", state="error", expanded=True)
                st.warning("Worksheet is empty or only has a header row.")
            else:
                n = len(data_rows)
                apply_status.write(
                    f"Processing **{n}** row(s). Backup log: `{os.path.basename(log_path)}`"
                )
                progress = st.progress(0.0, text=f"Starting… 0 / {n}")
                results: List[str] = []
                counts = {"renamed": 0, "skipped": 0, "conflict": 0, "error": 0}

                for idx, (sheet_row, rd) in enumerate(data_rows):
                    done = idx + 1
                    progress.progress(
                        min(1.0, done / max(1, n)),
                        text=f"Row {done} / {n} (sheet row {sheet_row})…",
                    )
                    row_ts = datetime.now().isoformat(timespec="seconds")

                    approve_val = rd.get("Approve", "")
                    orig_path = rd.get("Full File Path", "").strip()
                    proposed = rd.get("Proposed Filename", "").strip()

                    if not _normalize_yes(approve_val):
                        counts["skipped"] += 1
                        _append_csv_log(log_path, orig_path, "", "Skipped", "")
                        ok_upd, err_upd = update_row_status_timestamp(
                            sheet_name.strip(),
                            apply_ws.strip(),
                            sheet_row,
                            "Skipped",
                            row_ts,
                            "",
                        )
                        if not ok_upd:
                            results.append(f"Row {sheet_row}: sheet update failed: {err_upd}")
                        continue

                    safe, why = _is_safe_proposed_filename(proposed)
                    if not safe:
                        counts["error"] += 1
                        _append_csv_log(log_path, orig_path, "", "Error", why)
                        update_row_status_timestamp(
                            sheet_name.strip(), apply_ws.strip(), sheet_row, "Error", row_ts, why
                        )
                        results.append(f"Row {sheet_row}: {why}")
                        continue

                    if not orig_path or not os.path.isfile(orig_path):
                        counts["error"] += 1
                        em = "Original file no longer exists"
                        _append_csv_log(log_path, orig_path, "", "Error", em)
                        update_row_status_timestamp(
                            sheet_name.strip(), apply_ws.strip(), sheet_row, "Error", row_ts, em
                        )
                        continue

                    target_dir = os.path.dirname(orig_path)
                    target_path = os.path.join(target_dir, proposed)

                    if os.path.basename(orig_path) == proposed:
                        counts["skipped"] += 1
                        _append_csv_log(log_path, orig_path, target_path, "Skipped", "Already named")
                        update_row_status_timestamp(
                            sheet_name.strip(),
                            apply_ws.strip(),
                            sheet_row,
                            "Skipped",
                            row_ts,
                            "Already named",
                        )
                        continue

                    if os.path.exists(target_path):
                        try:
                            same = os.path.samefile(target_path, orig_path)
                        except OSError:
                            same = False
                        if not same:
                            counts["conflict"] += 1
                            em = "Target filename already exists"
                            _append_csv_log(
                                log_path, orig_path, target_path, "Conflict", em
                            )
                            update_row_status_timestamp(
                                sheet_name.strip(),
                                apply_ws.strip(),
                                sheet_row,
                                "Conflict",
                                row_ts,
                                em,
                            )
                            continue

                    try:
                        os.rename(orig_path, target_path)
                    except OSError as e:
                        counts["error"] += 1
                        em = str(e)
                        _append_csv_log(log_path, orig_path, target_path, "Error", em)
                        update_row_status_timestamp(
                            sheet_name.strip(), apply_ws.strip(), sheet_row, "Error", row_ts, em
                        )
                        results.append(f"Row {sheet_row}: {em}")
                        continue

                    counts["renamed"] += 1
                    _append_csv_log(log_path, orig_path, target_path, "Renamed", "")
                    update_row_status_timestamp(
                        sheet_name.strip(), apply_ws.strip(), sheet_row, "Renamed", row_ts, ""
                    )

                progress.progress(1.0, text=f"Finished {n} / {n}")
                summary_md = (
                    f"**Renamed:** {counts['renamed']} · **Skipped:** {counts['skipped']} · "
                    f"**Conflict:** {counts['conflict']} · **Errors:** {counts['error']}"
                )
                summary_plain = (
                    f"Renamed: {counts['renamed']}, Skipped: {counts['skipped']}, "
                    f"Conflict: {counts['conflict']}, Errors: {counts['error']}"
                )
                apply_status.write(summary_md)
                apply_status.update(
                    label="Apply finished",
                    state="complete",
                    expanded=bool(results),
                )
                progress.empty()

                st.success(
                    f"Apply finished. {summary_plain} — backup log: `{os.path.basename(log_path)}`"
                )
                try:
                    st.toast(
                        f"Done: {counts['renamed']} renamed, {counts['skipped']} skipped.",
                        icon="✅",
                    )
                except Exception:
                    pass
                if results:
                    with st.expander("Errors / notes"):
                        st.write("\n".join(results))

    # ---- Footer help ----
    with st.expander("Local setup (quick reference)"):
        st.markdown(
            """
1. Install: `pip install streamlit pandas gspread google-auth`
2. Google Cloud: create a project → enable **Google Sheets API** and **Google Drive API**
3. Create a **service account** → download JSON key
4. Rename the key to `credentials.json` and place it next to `app.py`
5. Create/open your spreadsheet and **share** it with the service account email (**Editor**)
6. Run: `streamlit run app.py`

See **README.md** in this folder for the full walkthrough.
            """
        )


if __name__ == "__main__":
    main()
