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

from rename_engine import (
    analyze_row,
    batch_slice,
    filter_paths_skip_standardized,
    iter_files,
)
from sheets_ops import (
    SHEET_HEADERS,
    credentials_available,
    push_batch,
    read_worksheet_rows,
    update_row_status_timestamp,
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
    skip_standardized = st.sidebar.checkbox(
        "Skip already standardized filenames",
        value=True,
        help='Hide files whose names already start like "YYYY-MM-DD - …" so batches only list work left to do.',
    )
    batch_size = st.sidebar.number_input("Batch size", min_value=1, value=250, step=1)
    start_index = st.sidebar.number_input("Start index", min_value=0, value=0, step=1)
    sheet_name = st.sidebar.text_input("Google Sheet name", value="File Rename Tool")

    # One tab name for both Push and Apply; `key` keeps it stable across reruns and batch changes.
    ws_tab_name = st.sidebar.text_input(
        "Google Sheet tab name",
        value="Renames",
        key="google_sheet_tab_name",
        help="**Push** writes this tab (creates it if missing). **Apply** reads the same tab. Set whenever you like; it does not auto-change when batch settings change.",
    ).strip()

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

    st.sidebar.markdown(
        "**Batching** uses the **filtered scan list** (after optional skips). "
        "Each batch is a slice by **Start index** and **Batch size** on that list."
    )

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

    raw_paths: List[str] = st.session_state.all_files
    work_paths, n_standardized_skipped = filter_paths_skip_standardized(
        raw_paths, skip_standardized
    )
    total_found = len(raw_paths)
    remaining = len(work_paths)
    start_i = int(start_index)
    size_i = int(batch_size)
    batch_files = batch_slice(work_paths, start_i, size_i)
    end_exclusive = start_i + len(batch_files)
    suggested_next = end_exclusive if remaining else 0

    if total_found > 0:
        st.warning(
            "**After applying renames, click Scan again from Start index 0.** "
            "Renamed files change sort order, so old batch positions no longer line up with the same files."
        )
        st.info(
            "**Safest loop:** Scan (0) → Push → edit sheet → Apply → **Scan again from 0** → repeat. "
            "With **Skip already standardized** on, files that already match `YYYY-MM-DD - …` stay out of the batch list."
        )

    # ---- Summary metrics ----
    r1c1, r1c2, r1c3 = st.columns(3)
    r1c1.metric("Total files found (on disk)", f"{total_found:,}")
    r1c2.metric("Already standardized (skipped)", f"{n_standardized_skipped:,}")
    r1c3.metric("Remaining (in batch list)", f"{remaining:,}")

    r2c1, r2c2, r2c3 = st.columns(3)
    r2c1.metric(
        "Current batch index range",
        f"{start_i} – {end_exclusive - 1}" if batch_files else "—",
        help="Indices into the **remaining** list above (not raw disk order).",
    )
    r2c2.metric("Files in this batch", f"{len(batch_files):,}")
    r2c3.metric("Suggested next start index", f"{suggested_next:,}")

    st.caption(
        "**Suggested next start index** only applies to this scan, before more renames. "
        "After **Apply approved renames**, set **Start index** back to **0** and **Scan Files** again."
    )

    if not batch_files and remaining > 0:
        st.info(
            "Start index is past the end of the **remaining** list. Lower **Start index** or scan again."
        )
    elif not batch_files and total_found > 0 and remaining == 0:
        st.success(
            "Every file in this folder already looks standardized for the current filter, or the list is empty."
        )

    # ---- Build preview table ----
    rows_for_df: List[Dict[str, Any]] = []
    # Tie stable Batch ID to the last completed scan path, not transient sidebar typing.
    folder_for_key = (
        st.session_state.last_scan_folder
        if st.session_state.all_files
        else folder.strip()
    )
    batch_key = (
        folder_for_key,
        skip_standardized,
        remaining,
        start_i,
        size_i,
        tuple(batch_files),
    )
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
                "Extracted Date": r["extracted_date"],
                "Original Filename": r["original_filename"],
                "Proposed Filename": r["proposed"],
                "Approve": r["Approve"],
                "Confidence": r["confidence"],
                "Needs Review": r["needs_review"],
                "Conflict": r["conflict"],
                "Manual Notes": "",
                "Rename Status": "",
                "Timestamp": "",
            }
        )

    st.subheader("Local preview (current batch)")
    st.caption(
        "**Approve** is only prefilled **YES** for HIGH confidence; all other rows stay **blank** "
        "until you type YES (or leave blank to skip). In Google Sheets, use **Data → Create a filter**, "
        "then filter **Approve** to **blanks** to see rows you have not decided yet."
    )
    if rows_for_df:
        df_preview = pd.DataFrame(rows_for_df)
        for h in SHEET_HEADERS:
            if h not in df_preview.columns:
                df_preview[h] = ""
        st.dataframe(
            df_preview[SHEET_HEADERS],
            use_container_width=True,
            height=400,
        )
    else:
        st.caption("Run **Scan Files** to populate the preview.")

    st.session_state["_last_batch_rows"] = rows_for_df
    st.session_state["_last_batch_meta"] = {
        "batch_id": batch_id,
        "start": start_i,
        "size": size_i,
        "sheet_name": sheet_name.strip(),
        "worksheet": ws_tab_name,
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
                ok, msg = push_batch(sheet_name.strip(), ws_tab_name, rows_for_df)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    # ---- Apply from Google Sheet ----
    if apply_btn:
        log_path = _log_csv_path(start_i, size_i)
        with st.spinner("Reading worksheet and applying approved renames…"):
            data_rows, err = read_worksheet_rows(sheet_name.strip(), ws_tab_name)
        if err:
            st.error(err)
        elif not data_rows:
            st.warning("Worksheet is empty or only has a header row.")
        else:
            progress = st.progress(0.0)
            n = len(data_rows)
            results: List[str] = []

            for idx, (sheet_row, rd) in enumerate(data_rows):
                progress.progress(min(1.0, (idx + 1) / max(1, n)))
                row_ts = datetime.now().isoformat(timespec="seconds")

                approve_val = rd.get("Approve", "")
                orig_path = rd.get("Full File Path", "").strip()
                proposed = rd.get("Proposed Filename", "").strip()

                if not _normalize_yes(approve_val):
                    _append_csv_log(log_path, orig_path, "", "Skipped", "")
                    ok_upd, err_upd = update_row_status_timestamp(
                        sheet_name.strip(),
                        ws_tab_name,
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
                    _append_csv_log(log_path, orig_path, "", "Error", why)
                    update_row_status_timestamp(
                        sheet_name.strip(), ws_tab_name, sheet_row, "Error", row_ts, why
                    )
                    results.append(f"Row {sheet_row}: {why}")
                    continue

                if not orig_path or not os.path.isfile(orig_path):
                    em = "Original file no longer exists"
                    _append_csv_log(log_path, orig_path, "", "Error", em)
                    update_row_status_timestamp(
                        sheet_name.strip(), ws_tab_name, sheet_row, "Error", row_ts, em
                    )
                    continue

                target_dir = os.path.dirname(orig_path)
                target_path = os.path.join(target_dir, proposed)

                if os.path.basename(orig_path) == proposed:
                    _append_csv_log(log_path, orig_path, target_path, "Skipped", "Already named")
                    update_row_status_timestamp(
                        sheet_name.strip(),
                        ws_tab_name,
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
                        em = "Target filename already exists"
                        _append_csv_log(
                            log_path, orig_path, target_path, "Conflict", em
                        )
                        update_row_status_timestamp(
                            sheet_name.strip(),
                            ws_tab_name,
                            sheet_row,
                            "Conflict",
                            row_ts,
                            em,
                        )
                        continue

                try:
                    os.rename(orig_path, target_path)
                except OSError as e:
                    em = str(e)
                    _append_csv_log(log_path, orig_path, target_path, "Error", em)
                    update_row_status_timestamp(
                        sheet_name.strip(), ws_tab_name, sheet_row, "Error", row_ts, em
                    )
                    results.append(f"Row {sheet_row}: {em}")
                    continue

                _append_csv_log(log_path, orig_path, target_path, "Renamed", "")
                update_row_status_timestamp(
                    sheet_name.strip(), ws_tab_name, sheet_row, "Renamed", row_ts, ""
                )

            progress.empty()
            st.success(
                f"Apply pass finished. Local backup log: `{os.path.basename(log_path)}`"
            )
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
