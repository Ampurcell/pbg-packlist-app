from pathlib import Path
import pandas as pd
from fpdf import FPDF

REQUIRED_COLUMNS = [
    "Element (c)",
    "Name",
    "Event Name / Client",
    "Department",
    "PACKLIST NOTES",
    "CATEGORY",
    "Item Note",
    "Requested QTY",
]


def clean_text(text):
    if isinstance(text, str):
        return text.encode("ascii", "ignore").decode("ascii")
    return text


class PacklistPDF(FPDF):
    def __init__(
        self,
        title: str,
        version: str,
        event_id: str,
        event_client: str,
        event_date: str,
        location: str,
        setup_by: str,
        setup_note: str,
        section_color: tuple[int, int, int],
    ):
        super().__init__()
        self.title = title
        self.version = version
        self.event_id = event_id
        self.event_client = event_client
        self.event_date = event_date
        self.location = location
        self.setup_by = setup_by
        self.setup_note = setup_note
        self.section_color = section_color

    def header(self):
        self.set_text_color(0, 82, 204)
        self.set_font("Arial", "B", 18)
        self.cell(0, 8, self.title, new_x="LMARGIN", new_y="NEXT", align="C")

        self.set_text_color(0, 0, 0)
        self.set_font("Arial", "", 8)

        details = (
            f"ID: {self.event_id} | Client: {self.event_client} | Date: {self.event_date} | "
            f"Location: {self.location} | Setup By: {self.setup_by} | Version: {self.version}"
        )
        # multi_cell wraps safely when event metadata is long.
        self.multi_cell(0, 4, details, align="C")

        if self.setup_note:
            self.set_font("Arial", "I", 7)
            self.multi_cell(0, 4, f"Setup Note: {self.setup_note}", align="C")
        self.ln(1)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 10)
        self.cell(0, 10, f"Page {self.page_no()} of {{nb}}", align="C")

    def add_element_box(self, element_name, items, x_offset, y_offset):
        box_width = 90
        line_height = 3.5

        self.set_xy(x_offset, y_offset)
        self.set_font("Arial", "B", 10)
        self.set_text_color(*self.section_color)
        self.multi_cell(box_width, 6, str(element_name), border=1, align="C")

        self.set_text_color(0, 0, 0)
        self.set_font("Arial", "", 8)

        sorted_items = sorted(
            items,
            key=lambda x: (str(x.get("CATEGORY", "")) != "TASK ITEM", str(x.get("Name", ""))),
        )

        for item in sorted_items:
            self.set_xy(x_offset, self.get_y())

            category = str(item.get("CATEGORY", "")).strip()
            requested_qty = item.get("Requested QTY", "")
            qty_text = f"{requested_qty}"

            if category == "TASK ITEM":
                self.set_font("Arial", "B", 8)
                qty_text = ""
            elif category.upper() == "PURCHASE":
                self.set_font("Arial", "B", 8)
                self.set_text_color(0, 128, 0)
            else:
                self.set_font("Arial", "", 8)
                self.set_text_color(0, 0, 0)

            self.set_text_color(0, 0, 0)
            self.cell(10, line_height, qty_text, border=0)

            name_text = str(item.get("Name", "")).lstrip("-").strip()
            self.multi_cell(box_width - 10, line_height, name_text, border=0)

            note = str(item.get("Item Note", "")).strip()
            if note:
                self.set_x(x_offset + 10)
                self.set_font("Arial", "I", 7)
                self.multi_cell(box_width - 10, line_height, note, border=0)
                self.set_font("Arial", "", 8)


def build_packlist_pdf(
    csv_path: Path,
    output_pdf_path: Path,
    title: str,
    version: str,
    event_id: str,
    event_client: str,
    event_date: str,
    location: str,
    setup_by: str,
    setup_note: str = "",
) -> Path:
    data = pd.read_csv(csv_path)

    # Basic required column check
    missing = [c for c in REQUIRED_COLUMNS if c not in data.columns]
    if missing:
        raise ValueError(f"Missing required CSV columns: {', '.join(missing)}")

    # Clean and prep data
    data["Element (c)"] = data["Element (c)"].apply(clean_text)
    data["Name"] = data["Name"].apply(clean_text)
    data["Event Name / Client"] = data["Event Name / Client"].apply(clean_text)
    data["Department"] = data["Department"].apply(clean_text)
    data["PACKLIST NOTES"] = data["PACKLIST NOTES"].apply(clean_text)
    data["CATEGORY"] = data["CATEGORY"].fillna("").astype(str).apply(clean_text)
    data["Item Note"] = data["Item Note"].fillna("").astype(str).apply(clean_text)

    data["Requested QTY"] = data["Requested QTY"].apply(
        lambda x: "X" if pd.isna(x) else int(float(x)) if isinstance(x, (int, float)) else x
    )

    pdf = PacklistPDF(
        title=title,
        version=version,
        event_id=event_id,
        event_client=event_client,
        event_date=event_date,
        location=location,
        setup_by=setup_by,
        setup_note=setup_note,
        section_color=(0, 0, 139),
    )
    pdf.alias_nb_pages()
    pdf.add_page()

    grouped_elements = data.groupby("Element (c)")
    x_offsets = [10, 105]
    y_offset = max(pdf.get_y() + 2, 24)
    current_y = [y_offset, y_offset]

    for element, items in grouped_elements:
        items_list = items.to_dict("records")
        col = 0 if current_y[0] <= current_y[1] else 1
        pdf.add_element_box(element, items_list, x_offsets[col], current_y[col])
        current_y[col] = pdf.get_y() + 2

    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_pdf_path))
    return output_pdf_path