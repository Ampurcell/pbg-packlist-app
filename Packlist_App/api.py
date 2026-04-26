from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from pdf_builder import build_packlist_pdf

app = FastAPI(
    title="Packlist PDF API",
    description="Generate packlist PDFs from CSV uploads.",
    version="1.0.0",
)


class GeneratePacklistRequest(BaseModel):
    event_id: str
    event_title: str
    event_client: str
    event_date: str
    location: str
    setup_by: str
    version: str
    setup_note: str = ""
    csv_file_url: str


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
    payload: GeneratePacklistRequest,
):
    parsed = urlparse(payload.csv_file_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="csv_file_url must be a valid http/https URL.")

    csv_temp_path: Path | None = None
    pdf_temp_path: Path | None = None

    try:
        with NamedTemporaryFile(delete=False, suffix=".csv") as csv_temp:
            csv_temp_path = Path(csv_temp.name)
            request = Request(payload.csv_file_url.strip(), headers={"User-Agent": "PacklistAPI/1.0"})
            try:
                with urlopen(request, timeout=20) as response:
                    if response.status >= 400:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Could not download CSV from csv_file_url (HTTP {response.status}).",
                        )
                    content = response.read()
            except HTTPError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not download CSV from csv_file_url (HTTP {exc.code}).",
                ) from exc
            except URLError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not download CSV from csv_file_url: {exc.reason}",
                ) from exc
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Could not download CSV from csv_file_url: request timed out.",
                ) from exc

            if not content:
                raise HTTPException(status_code=400, detail="Downloaded CSV is empty.")
            csv_temp.write(content)

        pdf_temp_path = Path.cwd() / "tmp" / f"packlist_{uuid4().hex}.pdf"

        build_packlist_pdf(
            csv_path=csv_temp_path,
            output_pdf_path=pdf_temp_path,
            title=payload.event_title,
            version=payload.version,
            event_id=payload.event_id,
            event_client=payload.event_client,
            event_date=payload.event_date,
            location=payload.location,
            setup_by=payload.setup_by,
            setup_note=payload.setup_note,
        )

        if not pdf_temp_path.exists():
            raise HTTPException(status_code=500, detail="PDF generation failed.")

        filename = _safe_pdf_name(event_id=payload.event_id, event_title=payload.event_title)
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
