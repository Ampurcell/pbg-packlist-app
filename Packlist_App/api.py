from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from pdf_builder import build_packlist_pdf

app = FastAPI(
    title="Packlist PDF API",
    description="Generate packlist PDFs from CSV uploads.",
    version="1.0.0",
)


def _cleanup_files(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # If deletion fails, skip silently to avoid breaking responses.
            pass


def _safe_pdf_name(event_id: str, event_title: str) -> str:
    raw = f"{event_id}_{event_title}".strip() or "packlist"
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in raw)
    return f"{cleaned[:80]}.pdf"


@app.post("/generate-packlist")
async def generate_packlist(
    event_id: str = Form(...),
    event_title: str = Form(...),
    event_client: str = Form(...),
    event_date: str = Form(...),
    location: str = Form(...),
    setup_by: str = Form(...),
    version: str = Form(...),
    setup_note: str = Form(""),
    csv_file: UploadFile = File(...),
):
    if not csv_file.filename:
        raise HTTPException(status_code=400, detail="CSV file is required.")
    if not csv_file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a CSV.")

    csv_temp_path: Path | None = None
    pdf_temp_path: Path | None = None

    try:
        with NamedTemporaryFile(delete=False, suffix=".csv") as csv_temp:
            csv_temp_path = Path(csv_temp.name)
            content = await csv_file.read()
            if not content:
                raise HTTPException(status_code=400, detail="CSV file is empty.")
            csv_temp.write(content)

        pdf_temp_path = Path.cwd() / "tmp" / f"packlist_{uuid4().hex}.pdf"

        build_packlist_pdf(
            csv_path=csv_temp_path,
            output_pdf_path=pdf_temp_path,
            title=event_title,
            version=version,
            event_id=event_id,
            event_client=event_client,
            event_date=event_date,
            location=location,
            setup_by=setup_by,
            setup_note=setup_note,
        )

        if not pdf_temp_path.exists():
            raise HTTPException(status_code=500, detail="PDF generation failed.")

        filename = _safe_pdf_name(event_id=event_id, event_title=event_title)
        return FileResponse(
            path=str(pdf_temp_path),
            media_type="application/pdf",
            filename=filename,
            background=BackgroundTask(_cleanup_files, pdf_temp_path, csv_temp_path),
        )

    except HTTPException:
        if csv_temp_path or pdf_temp_path:
            _cleanup_files(*(p for p in (csv_temp_path, pdf_temp_path) if p is not None))
        raise
    except (ValueError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        if csv_temp_path or pdf_temp_path:
            _cleanup_files(*(p for p in (csv_temp_path, pdf_temp_path) if p is not None))
        raise HTTPException(status_code=400, detail=f"Invalid CSV input: {exc}") from exc
    except Exception as exc:
        if csv_temp_path or pdf_temp_path:
            _cleanup_files(*(p for p in (csv_temp_path, pdf_temp_path) if p is not None))
        raise HTTPException(status_code=500, detail=f"Unexpected server error: {exc}") from exc
    finally:
        await csv_file.close()
