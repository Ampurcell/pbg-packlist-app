from pathlib import Path
import sys
import pandas as pd
from fpdf import FPDF

# Base directory = folder where this script lives
BASE_DIR = Path(__file__).resolve().parent

# Usage:
#   python3 packlist_core.py
#   python3 packlist_core.py input.csv
#   python3 packlist_core.py input.csv output.pdf
#
# Defaults:
#   input  = testpacklist.csv
#   output = <input-stem>.pdf
input_name = sys.argv[1] if len(sys.argv) > 1 else "testpacklist.csv"
output_name = sys.argv[2] if len(sys.argv) > 2 else f"{Path(input_name).stem}.pdf"

file_path = BASE_DIR / input_name
output_path = BASE_DIR / output_name

if not file_path.exists():
    raise FileNotFoundError(
        f"CSV file not found: {file_path}\n"
        "Put the CSV in the same folder as this script, or pass a valid filename."
    )

data = pd.read_csv(file_path)


def clean_text(text):
    if isinstance(text, str):
        return text.encode("ascii", "ignore").decode("ascii")
    return text


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


class PacklistPDF(FPDF):
    def __init__(self, title1, title2, title_color, section_color):
        super().__init__()
        self.title1 = title1
        self.title2 = title2
        self.title_color = title_color
        self.section_color = section_color

    def header(self):
        self.set_text_color(*self.title_color)
        self.set_font("Arial", "B", 16)
        self.cell(0, 10, self.title1, new_x="LMARGIN", new_y="NEXT", align="C")

        self.ln(1)
        self.set_text_color(0, 0, 0)
        self.set_font("Arial", "I", 10)
        self.cell(0, 6, self.title2, new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 10)
        self.cell(0, 10, f"Page {self.page_no()} of {{nb}}", align="C")

    def add_element_box(self, element_name, items, x_offset, y_offset):
        box_width = 90
        line_height = 4

        self.set_xy(x_offset, y_offset)
        self.set_font("Arial", "B", 10)
        self.set_text_color(*self.section_color)
        self.multi_cell(box_width, 8, str(element_name), border=1, align="C")

        self.set_text_color(0, 0, 0)
        self.set_font("Arial", "", 8)

        sorted_items = sorted(
            items, key=lambda x: (str(x.get("CATEGORY", "")) != "TASK ITEM", str(x.get("Name", "")))
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


pdf = PacklistPDF("TITLE", "VERSION", (0, 0, 139), (0, 0, 139))
pdf.alias_nb_pages()

grouped_elements = data.groupby("Element (c)")
x_offsets = [10, 105]
y_offset = 30
current_y = [y_offset, y_offset]

pdf.add_page()

for element, items in grouped_elements:
    items_list = items.to_dict("records")
    col = 0 if current_y[0] <= current_y[1] else 1
    pdf.add_element_box(element, items_list, x_offsets[col], current_y[col])
    current_y[col] = pdf.get_y() + 2

pdf.output(str(output_path))
print(f"PDF saved to: {output_path}")