"""Build original engineering fixtures for Technical RAG."""
import argparse
from pathlib import Path
import subprocess
import tempfile

import pymupdf
from docx import Document

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def write_pdf(folder):
    doc = pymupdf.open()
    doc.set_metadata({"title": "Mounting and heat load", "author": "Technical RAG demo corpus"})
    page = doc.new_page()
    page.insert_text((55, 75), "Mounting and heat load", fontsize=20)
    page.insert_text((55, 120), "The radiator HX-9 dissipates 28 W.", fontsize=11)
    page.insert_text((55, 147), "Use four M4 fasteners.", fontsize=11)
    page.insert_text((325, 120), "The heat shield is 2.4 mm thick.", fontsize=11)
    page.insert_text((325, 147), "The bracket is made of Al 6061.", fontsize=11)
    page = doc.new_page()
    page.insert_text((55, 75), "Acceptance limits", fontsize=20)
    page.insert_text((55, 124), "The thermal interface resistance is 0.35 K/W.", fontsize=11)
    x, y, column_width, row_height = 55, 185, 145, 28
    rows = (("Test", "Requirement", "Unit"), ("Load", "28", "W"), ("Resistance", "0.35", "K/W"))
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            left = x + column_index * column_width
            top = y + row_index * row_height
            page.draw_rect((left, top, left + column_width, top + row_height), width=0.7)
            page.insert_text((left + 8, top + 18), text, fontsize=11)
    doc.save(folder / "mounting.pdf", garbage=4, deflate=True, no_new_id=True)
    doc.close()


def write_docx(folder):
    document = Document()
    document.core_properties.title = "Методика калибровки"
    document.core_properties.author = "Technical RAG demo corpus"
    document.core_properties.language = "ru"
    document.add_heading("Калибровка датчика CL-8", 1)
    document.add_paragraph("Перед калибровкой датчик CL-8 прогревают 18 минут.")
    table = document.add_table(rows=2, cols=2)
    for row, values in zip(table.rows, [("Параметр", "Значение"), ("Верхняя точка", "80 °C")], strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    document.add_paragraph("Таблица 1. Калибровочные точки.", style="Caption")
    document.add_heading("Проверка", 2)
    document.add_paragraph("Запишите показание в журнал CL-8.", style="List Bullet")
    document.save(folder / "calibration.docx")


def write_doc(folder, executable):
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        document = Document()
        document.core_properties.title = "Legacy power supply instructions"
        document.add_heading("Power supply SP-32", 1)
        document.add_paragraph("The SP-32 supply provides 24 V DC and has a 2.5 A current limit.")
        document.add_heading("Startup checklist", 2)
        document.add_paragraph("Wait 12 seconds before enabling the thermal load.", style="List Bullet")
        document.save(temp / "power.docx")
        subprocess.run(
            [executable, f"-env:UserInstallation={(temp / 'profile').as_uri()}",
             "--headless", "--convert-to", "doc:MS Word 97", "--outdir", str(folder), str(temp / "power.docx")],
            check=True, timeout=60,
        )
        if not (folder / "power.doc").read_bytes().startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
            raise RuntimeError("LibreOffice did not create a binary Word document")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--soffice")
    arguments = cli.parse_args()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    write_pdf(FIXTURES)
    write_docx(FIXTURES)
    if arguments.soffice:
        write_doc(FIXTURES, arguments.soffice)
