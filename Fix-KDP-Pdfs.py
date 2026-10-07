#!/usr/bin/env python
"""Create KDP-sized PDFs for an 8.5 x 8.5 in B&W, white-paper paperback series."""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from tempfile import NamedTemporaryFile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from pypdf import Transformation
from PIL import Image
from reportlab.pdfgen import canvas as pdf_canvas


TRIM_IN = 8.5
POINTS_PER_INCH = 72.0
PAGE_COUNT_MIN = 24
PAGE_COUNT_MAX = 828
WHITE_PAPER_SPINE_PER_PAGE_IN = 0.002252
COVER_BLEED_IN = 0.125
GUTTER_IN = 0.375
OUTER_MARGIN_IN = 0.25
TOP_MARGIN_IN = 0.25
BOTTOM_MARGIN_IN = 0.25
OUTPUT_DPI = 300
POPPLER_BIN = Path(r"C:\Users\lodhi\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin")
WORK_RENDER_ROOT = Path(__file__).resolve().parents[2] / "work" / "kdp-renders"


def write_pdf(writer: PdfWriter, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, suffix=".pdf.tmp", delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        with temp_path.open("wb") as stream:
            writer.write(stream)
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def find_pdftoppm() -> str:
    command = shutil.which("pdftoppm.exe") or shutil.which("pdftoppm")
    if command:
        return command
    bundled = POPPLER_BIN / "pdftoppm.exe"
    if bundled.is_file():
        return str(bundled)
    raise FileNotFoundError("pdftoppm.exe was not found; install Poppler and add it to PATH")


def rasterize_at_300_dpi(vector_pdf: Path, output_pdf: Path, page_count: int, page_size_points: tuple[float, float], grayscale: bool) -> tuple[int, int]:
    WORK_RENDER_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kdp-", dir=WORK_RENDER_ROOT) as temp_dir:
        temp = Path(temp_dir)
        prefix = temp / "render"
        command = [find_pdftoppm(), "-r", str(OUTPUT_DPI), "-png", "-f", "1", "-l", str(page_count)]
        if grayscale:
            command.append("-gray")
        command.extend([str(vector_pdf), str(prefix)])
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Poppler could not render the corrected PDF")

        images = sorted(prefix.parent.glob("render-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
        if len(images) != page_count:
            raise RuntimeError(f"Rendered {len(images)} pages; expected {page_count}")

        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=output_pdf.parent, suffix=".pdf.tmp", delete=False) as tmp:
            temp_output = Path(tmp.name)
        first_size = None
        try:
            pdf = pdf_canvas.Canvas(str(temp_output), pagesize=page_size_points, pageCompression=1)
            for image_path in images:
                with Image.open(image_path) as image:
                    current_size = image.size
                    if first_size is None:
                        first_size = current_size
                    min_width = page_size_points[0] / 72 * OUTPUT_DPI - 1
                    min_height = page_size_points[1] / 72 * OUTPUT_DPI - 1
                    if current_size[0] < min_width or current_size[1] < min_height:
                        raise RuntimeError(f"Rendered page is below {OUTPUT_DPI} dpi: {current_size} pixels")
                pdf.setPageSize(page_size_points)
                pdf.drawImage(str(image_path), 0, 0, width=page_size_points[0], height=page_size_points[1])
                pdf.showPage()
            pdf.save()
            os.replace(temp_output, output_pdf)
        finally:
            if temp_output.exists():
                temp_output.unlink()
        return first_size or (0, 0)


def transform_page_in_place(source_page, width: float, height: float, scale_x: float, scale_y: float, tx: float, ty: float):
    llx = float(source_page.mediabox.left)
    lly = float(source_page.mediabox.bottom)
    transform = Transformation(ctm=(scale_x, 0, 0, scale_y, tx - llx * scale_x, ty - lly * scale_y))
    source_page.add_transformation(transform, expand=False)
    rect = RectangleObject((0, 0, width, height))
    source_page.mediabox = rect
    source_page.cropbox = RectangleObject((0, 0, width, height))
    source_page.trimbox = RectangleObject((0, 0, width, height))
    source_page.bleedbox = RectangleObject((0, 0, width, height))
    source_page.artbox = RectangleObject((0, 0, width, height))
    return source_page


def fix_cover(source: Path, destination: Path, page_count: int) -> tuple[float, float, float, float]:
    if not PAGE_COUNT_MIN <= page_count <= PAGE_COUNT_MAX:
        raise ValueError(f"Page count {page_count} is outside the configured paperback range")
    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted:
        raise ValueError("Cover PDF is encrypted")
    if len(reader.pages) != 1:
        raise ValueError(f"Cover must have one page; found {len(reader.pages)}")

    target_width = (2 * TRIM_IN + page_count * WHITE_PAPER_SPINE_PER_PAGE_IN + 2 * COVER_BLEED_IN) * POINTS_PER_INCH
    target_height = (TRIM_IN + 2 * COVER_BLEED_IN) * POINTS_PER_INCH
    page = reader.pages[0]
    source_width = float(page.mediabox.width)
    source_height = float(page.mediabox.height)
    scale_x = target_width / source_width
    scale_y = target_height / source_height

    writer = PdfWriter(clone_from=reader)
    page = writer.pages[0]
    fixed_page = transform_page_in_place(
        page,
        target_width,
        target_height,
        scale_x,
        scale_y,
        (target_width - source_width * scale_x) / 2,
        (target_height - source_height * scale_y) / 2,
    )

    write_pdf(writer, destination)
    return source_width / POINTS_PER_INCH, source_height / POINTS_PER_INCH, target_width / POINTS_PER_INCH, target_height / POINTS_PER_INCH


def fix_interior(source: Path, destination: Path) -> tuple[int, set[tuple[float, float]]]:
    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted:
        raise ValueError("Interior PDF is encrypted")
    page_count = len(reader.pages)
    if not PAGE_COUNT_MIN <= page_count <= PAGE_COUNT_MAX:
        raise ValueError(f"Interior page count {page_count} is outside the configured paperback range")

    target = TRIM_IN * POINTS_PER_INCH
    safe_width = (TRIM_IN - GUTTER_IN - OUTER_MARGIN_IN) * POINTS_PER_INCH
    safe_height = (TRIM_IN - TOP_MARGIN_IN - BOTTOM_MARGIN_IN) * POINTS_PER_INCH
    writer = PdfWriter(clone_from=reader)
    source_sizes: set[tuple[float, float]] = set()

    for index, source_page in enumerate(writer.pages):
        source_page.transfer_rotation_to_content()
        source_width = float(source_page.mediabox.width)
        source_height = float(source_page.mediabox.height)
        source_sizes.add((source_width, source_height))
        scale = min(safe_width / source_width, safe_height / source_height)
        content_width = source_width * scale
        content_height = source_height * scale

        # Physical page 1 is a right-hand page: its gutter is on the left.
        is_right_hand_page = index % 2 == 0
        left_margin = GUTTER_IN * POINTS_PER_INCH if is_right_hand_page else OUTER_MARGIN_IN * POINTS_PER_INCH
        y_offset = (target - content_height) / 2

        fixed_page = transform_page_in_place(source_page, target, target, scale, scale, left_margin, y_offset)
    write_pdf(writer, destination)
    return page_count, source_sizes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Series folder containing BOOK1 ... BOOK10")
    parser.add_argument("--output", required=True, help="Destination folder for corrected KDP_READY books")
    args = parser.parse_args()
    root = Path(args.root)
    output = Path(args.output)
    if not root.is_dir():
        raise SystemExit(f"Series folder does not exist: {root}")

    books = sorted((p for p in root.iterdir() if p.is_dir() and p.name.startswith("BOOK") and p.name[4:].isdigit()),
                   key=lambda p: int(p.name[4:]))
    if not books:
        raise SystemExit(f"No BOOK<number> folders found in {root}")

    report_rows = []
    for book in books:
        source_cover = book / "KDP_COVER.pdf"
        source_interior = book / "KDP_INTERIOR.pdf"
        if not source_cover.is_file() or not source_interior.is_file():
            raise SystemExit(f"{book.name} is missing KDP_COVER.pdf or KDP_INTERIOR.pdf")

        target_book = output / book.name
        target_book.mkdir(parents=True, exist_ok=True)
        page_count = len(PdfReader(str(source_interior), strict=False).pages)
        old_cover = fix_cover(source_cover, target_book / "KDP_COVER.pdf", page_count)
        fixed_pages, old_interior_sizes = fix_interior(source_interior, target_book / "KDP_INTERIOR.pdf")
        report_rows.append({
            "Book": book.name,
            "Pages": fixed_pages,
            "OriginalCoverInches": f"{old_cover[0]:.4f} x {old_cover[1]:.4f}",
            "FixedCoverInches": f"{old_cover[2]:.4f} x {old_cover[3]:.4f}",
            "OriginalInteriorSizesPoints": "; ".join(f"{w:.2f} x {h:.2f}" for w, h in sorted(old_interior_sizes)),
            "FixedInteriorSizeInches": "8.5000 x 8.5000",
            "InteriorHandling": "Scaled to no-bleed safe area with alternating gutter margins",
        })
        print(f"{book.name}: prepared {fixed_pages} pages; cover {old_cover[0]:.3f} x {old_cover[1]:.3f} -> {old_cover[2]:.3f} x {old_cover[3]:.3f} in")

    report_path = output.parent / "KDP_PIPELINE_OUTPUT" / "KDP_FIX_SUMMARY.csv"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report_rows[0].keys()))
        writer.writeheader()
        writer.writerows(report_rows)
    print(f"Corrected pairs saved to: {output}")
    print(f"Fix summary saved to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
