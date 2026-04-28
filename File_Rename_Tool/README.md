# File Rename Tool (Streamlit + Google Sheets)

Conservative, human-in-the-loop renamer for large local archives (for example OneDrive/SharePoint exports). **Nothing is renamed on disk until you set `Approve` to YES in Google Sheets** and run **Apply Approved Renames** in the Streamlit app.

## Safety rules

- No automatic renames during scan or push.
- Proposed names are never applied unless `Approve` is **YES** in the sheet row.
- Existing files are never overwritten; conflicts are skipped and logged.
- If `credentials.json` is missing or Google APIs fail, you can still scan and preview locally.

## Quick start

```bash
cd file_rename_tool
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Google Cloud setup (beginner-friendly)

1. Install Python packages (above) with `pip install streamlit pandas gspread google-auth`.
2. In [Google Cloud Console](https://console.cloud.google.com/), create a project (any name).
3. Enable APIs for that project: **Google Sheets API** and **Google Drive API** (APIs & Services → Library → search each → Enable).
4. Create a **service account**: APIs & Services → Credentials → Create credentials → Service account. You do not need to grant it roles in the wizard for basic Sheets access.
5. Open the service account → **Keys** → Add key → JSON. Download the file.
6. Rename the downloaded JSON to **`credentials.json`** and place it in the **same folder as `app.py`** (`file_rename_tool/`).
7. In Google Sheets, create or open the spreadsheet you will use (default title in the app: `File Rename Tool`).
8. Click **Share**, paste the service account email (looks like `something@project-id.iam.gserviceaccount.com`), and grant **Editor**.

The Streamlit app will open that spreadsheet by **title** (exact match). Share the sheet with the service account before using **Push** or **Apply**.

## How to use the app

1. Enter the **folder path**, choose whether to **include subfolders**, set **batch size** (default 250) and **start index** (default 0).
2. Click **Scan Files**. Large trees update a live “Scanned N files…” caption while walking the disk.
3. Review the **local preview** table (dates, confidence, proposed names, conflicts).
4. Enter your **Google Sheet tab name** in the sidebar, then click **Push to Google Sheet** to create/refresh that tab. Edit **Proposed Filename** and set **Approve** to YES only where you are sure. **Apply** uses the same tab name.
5. Click **Apply Approved Renames** after selecting the correct **Worksheet name (for apply)**. Each row is evaluated; statuses and timestamps are written back to the sheet when possible.

Local backup CSVs are appended automatically:

`rename_log_start_{start}_size_{size}.csv`

## Filename rules (summary)

- Detects dates in the basename only, in forms `M.D.YY`, `MM.DD.YYYY`, etc., with dots as separators.
- Months 1–12, days 1–31, and real calendar dates (e.g. not Feb 30).
- Two-digit years: `00–30` → 2000–2030; `31–99` → 1931–1999.
- Proposed pattern with a detected date: `YYYY-MM-DD - Cleaned Name.ext` (dots/underscores become spaces; duplicate spaces collapsed; leading/trailing spaces and dashes trimmed). Without a date, only the cleaned name + extension is proposed.

Always test on a small copy of your data first.
