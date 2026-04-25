from datetime import datetime, date
import json
from pathlib import Path
import re

import pandas as pd
import streamlit as st

from pdf_builder import build_packlist_pdf

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_ROOT = BASE_DIR / "uploads"
OUTPUT_ROOT = BASE_DIR / "outputs"
LOGO_PATH = BASE_DIR / "PBG Event Group Logo.png"
DATE_FMT = "%m/%d/%Y"
SETUP_TIME_DEFAULT = datetime.strptime("05:00 PM", "%I:%M %p").time()
LOCATION_OPTIONS = ["Chameleon", "EMBEJC", "Merrick Jewish Center", "Custom"]

UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "event"


def is_valid_event_id(raw: str) -> bool:
    """Event ID: digits only, or the internal label 'Internal PL' / 'Internal PL!' (case-insensitive)."""
    value = raw.strip()
    if not value:
        return False
    if re.fullmatch(r"\d+", value):
        return True
    return bool(re.fullmatch(r"(?i)internal pl!?", value))


def get_recent_pdfs(limit: int = 10) -> list[Path]:
    pdf_paths = [path for path in OUTPUT_ROOT.rglob("*.pdf") if path.is_file()]
    pdf_paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return pdf_paths[:limit]


def get_recent_jobs(limit: int = 15) -> list[dict]:
    jobs: list[dict] = []
    meta_files = sorted(
        OUTPUT_ROOT.rglob("job_meta.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for meta_file in meta_files:
        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        pdf_path = Path(metadata.get("pdf_path", ""))
        if not pdf_path.is_absolute():
            pdf_path = BASE_DIR / pdf_path
        if not pdf_path.exists():
            continue
        jobs.append(
            {
                "label": f"{metadata.get('event_client', 'Event')} - {metadata.get('version', 'Version')}",
                "metadata": metadata,
            }
        )
        if len(jobs) >= limit:
            break
    return jobs


def bump_version(version: str) -> str:
    match = re.search(r"(\d+)(?!.*\d)", version)
    if not match:
        return f"{version}.1"
    old_number = match.group(1)
    new_number = str(int(old_number) + 1).zfill(len(old_number))
    return f"{version[:match.start(1)]}{new_number}{version[match.end(1):]}"


def extract_event_client_from_csv(csv_path: Path) -> str:
    """Fallback: pick first non-empty Event Name / Client from CSV."""
    try:
        data = pd.read_csv(csv_path, usecols=["Event Name / Client"])
    except Exception:
        return ""
    for value in data["Event Name / Client"].dropna():
        text = str(value).strip()
        if text:
            return text
    return ""


def format_recent_label(pdf_path: Path) -> str:
    stem = pdf_path.stem
    base_stem = re.sub(r"_\d{10}$", "", stem)
    if "_v" in base_stem:
        event_part, version_part = base_stem.rsplit("_v", 1)
        event_display = event_part.replace("-", " ").title()
        version_display = f"v{version_part.replace('_', '.')}"
        return f"{event_display} - {version_display}"
    return base_stem.replace("-", " ").title()


def initialize_form_state() -> None:
    defaults = {
        "event_id": "",
        "event_client": "",
        "event_date": date.today(),
        "location_option": "Chameleon",
        "custom_location": "",
        "setup_by_date": date.today(),
        "setup_by_time": SETUP_TIME_DEFAULT,
        "setup_note": "",
        "version": "V1.0",
        "loaded_source_csv_path": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def apply_job_metadata_to_form(metadata: dict, auto_bump: bool) -> None:
    st.session_state["event_id"] = metadata.get("event_id", "")
    st.session_state["event_client"] = metadata.get("event_client", "")
    try:
        st.session_state["event_date"] = datetime.strptime(
            metadata.get("event_date", ""),
            DATE_FMT,
        ).date()
    except ValueError:
        st.session_state["event_date"] = date.today()
    loaded_location = metadata.get("location", "")
    if loaded_location in LOCATION_OPTIONS:
        st.session_state["location_option"] = loaded_location
        st.session_state["custom_location"] = ""
    else:
        st.session_state["location_option"] = "Custom"
        st.session_state["custom_location"] = loaded_location

    try:
        setup_by_value = metadata.get("setup_by", "")
        setup_dt = datetime.strptime(setup_by_value, f"{DATE_FMT} %I:%M %p")
        st.session_state["setup_by_date"] = setup_dt.date()
        st.session_state["setup_by_time"] = setup_dt.time()
    except ValueError:
        st.session_state["setup_by_date"] = date.today()
        st.session_state["setup_by_time"] = SETUP_TIME_DEFAULT

    st.session_state["setup_note"] = metadata.get("setup_note", "")
    loaded_version = metadata.get("version", "V1.0")
    st.session_state["version"] = bump_version(loaded_version) if auto_bump else loaded_version


st.set_page_config(page_title="Packlist PDF Generator", page_icon="📄")
if LOGO_PATH.exists():
    left_col, center_col, right_col = st.columns([1, 2, 1])
    with center_col:
        st.image(str(LOGO_PATH), width=240)
st.title("Packlist PDF Generator")
st.caption("Upload a CSV, enter event details, generate and download a PDF.")
initialize_form_state()

recent_jobs = get_recent_jobs(limit=15)
with st.expander("Reuse previous event", expanded=False):
    if not recent_jobs:
        st.caption("No saved templates yet. Generate one PDF first.")
    else:
        selected_job_index = st.selectbox(
            "Choose recent job",
            options=list(range(len(recent_jobs))),
            format_func=lambda idx: recent_jobs[idx]["label"],
        )
        auto_bump = st.checkbox("Auto bump version", value=True)
        if st.button("Use as template"):
            selected_metadata = recent_jobs[selected_job_index]["metadata"]
            apply_job_metadata_to_form(selected_metadata, auto_bump)
            st.session_state["loaded_source_csv_path"] = selected_metadata.get("csv_path", "")
            st.success("Template loaded.")

csv_file = st.file_uploader("CSV File", type=["csv"])
event_id = st.text_input(
    "Event ID (numbers only, or Internal PL)",
    placeholder='e.g. 12345 or Internal PL',
    key="event_id",
    help="Use digits only (any length), or type Internal PL (optional !) for internal events.",
)
event_client = st.text_input(
    "Event Client (optional - auto from CSV if blank)",
    placeholder="Acme Wedding",
    key="event_client",
)
event_date = st.date_input("Event Date", key="event_date")
location_option = st.selectbox(
    "Location",
    LOCATION_OPTIONS,
    key="location_option",
)
custom_location = st.session_state.get("custom_location", "")
if location_option == "Custom":
    custom_location = st.text_input("Custom Location", placeholder="Enter custom location", key="custom_location")

setup_by_date = st.date_input("Setup By (Date)", key="setup_by_date")
setup_by_time = st.time_input("Setup By (Time)", key="setup_by_time")
setup_note = st.text_area("Setup Note (optional)", placeholder="Special setup instructions...", key="setup_note")
version = st.text_input("Version", key="version")

if st.session_state.get("loaded_source_csv_path"):
    loaded_csv_name = Path(st.session_state["loaded_source_csv_path"]).name
    st.caption(f"Template CSV fallback: {loaded_csv_name}")

if st.button("Generate PDF"):
    if not event_id.strip():
        st.error("Event ID is required.")
    elif not is_valid_event_id(event_id):
        st.error(
            "Event ID must be numbers only (e.g. 12345), or exactly Internal PL or Internal PL! "
            "(spacing as shown; case does not matter)."
        )
    elif not version.strip():
        st.error("Version is required.")
    elif location_option == "Custom" and not custom_location.strip():
        st.error("Please enter a custom location.")
    else:
        source_csv_bytes = None
        source_csv_name = ""
        source_csv_path = ""
        if csv_file is not None:
            source_csv_bytes = csv_file.getvalue()
            source_csv_name = csv_file.name
        else:
            source_csv_path = st.session_state.get("loaded_source_csv_path", "")
            if source_csv_path:
                try:
                    fallback_path = Path(source_csv_path)
                    if not fallback_path.is_absolute():
                        fallback_path = BASE_DIR / fallback_path
                    source_csv_bytes = fallback_path.read_bytes()
                    source_csv_name = fallback_path.name
                except FileNotFoundError:
                    st.error("Template CSV not found. Please upload a CSV.")
                    st.stop()

        if source_csv_bytes is None or not source_csv_name:
            st.error("Please upload a CSV file, or use a saved template.")
            st.stop()

        location = custom_location.strip() if location_option == "Custom" else location_option
        seed_name = event_client.strip() or "event"
        event_slug = slugify(seed_name)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        run_upload_dir = UPLOAD_ROOT / event_slug / timestamp
        run_output_dir = OUTPUT_ROOT / event_slug / timestamp
        run_upload_dir.mkdir(parents=True, exist_ok=True)
        run_output_dir.mkdir(parents=True, exist_ok=True)

        csv_path = run_upload_dir / source_csv_name
        csv_path.write_bytes(source_csv_bytes)

        # Resolve client name from CSV when form field is blank.
        resolved_event_client = event_client.strip() or extract_event_client_from_csv(csv_path)
        if not resolved_event_client:
            st.error("Could not determine Event Client. Enter it manually or include 'Event Name / Client' in CSV.")
            st.stop()

        safe_title = slugify(resolved_event_client).replace("-", "_")
        safe_version = slugify(version).replace("-", "_")
        pdf_name = f"{safe_title}_{safe_version}.pdf"
        pdf_path = run_output_dir / pdf_name
        metadata = {
            "event_id": event_id.strip(),
            "event_client": resolved_event_client,
            "event_date": event_date.strftime(DATE_FMT),
            "location": location,
            "setup_by": f"{setup_by_date.strftime(DATE_FMT)} {setup_by_time.strftime('%I:%M %p')}",
            "setup_note": setup_note.strip(),
            "version": version.strip(),
            "pdf_path": str(pdf_path.relative_to(BASE_DIR)),
            "csv_path": str(csv_path.relative_to(BASE_DIR)),
        }

        try:
            build_packlist_pdf(
                csv_path=csv_path,
                output_pdf_path=pdf_path,
                title=resolved_event_client,
                version=version.strip(),
                event_id=event_id.strip(),
                event_client=resolved_event_client,
                event_date=event_date.strftime(DATE_FMT),
                location=location,
                setup_by=f"{setup_by_date.strftime(DATE_FMT)} {setup_by_time.strftime('%I:%M %p')}",
                setup_note=setup_note.strip(),
            )
            (run_output_dir / "job_meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            pdf_bytes = pdf_path.read_bytes()
            st.success(f"PDF generated: {pdf_name}")
            st.download_button(
                label="Download PDF",
                data=pdf_bytes,
                file_name=pdf_name,
                mime="application/pdf",
            )
        except Exception as exc:
            st.error(f"Failed to generate PDF: {exc}")

st.divider()
st.subheader("Recent generated files")
recent_pdfs = get_recent_pdfs(limit=10)

if not recent_pdfs:
    st.caption("No PDFs generated yet.")
else:
    for pdf_path in recent_pdfs:
        file_label = format_recent_label(pdf_path)
        relative_path = pdf_path.relative_to(BASE_DIR)
        st.download_button(
            label=file_label,
            data=pdf_path.read_bytes(),
            file_name=pdf_path.name,
            mime="application/pdf",
            key=f"recent_{relative_path.as_posix()}",
        )