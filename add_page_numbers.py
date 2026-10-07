
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import re
import sys


# ============================================================
# SETTINGS
# ============================================================

# Safe distance from bottom/right edge
SAFE_MARGIN_PERCENT = 0.035

# Font size relative to image width
FONT_WIDTH_RATIO = 0.025

MIN_FONT_SIZE = 32
MAX_FONT_SIZE = 100

# Page number color
NUMBER_COLOR = (0, 0, 0)

# JPEG quality
JPEG_QUALITY = 95

# Windows fonts
FONT_PATHS = [
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/calibrib.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
]


# ============================================================
# FILE NAME PATTERNS
# ============================================================
#
# Supported:
#
# page1_image.jpg
# page2_image.jpg
# page40_image.jpg
#
# Page1_text.png
# Page2_text.png
# Page40_text.png
#
# Also accepts:
#
# page1_text.png
# page2_text.png
#
# ============================================================

PAGE_PATTERNS = [
    # Accept page-number prefixes such as:
    # page1_image.jpg
    # page01_image.jpg
    # page1_text.png
    # page01_color_2550x2550_300dpi.png
    # PAGE01_COLOR_2550X2550_300DPI.PNG
    #
    # Matching is case-insensitive. The numeric portion immediately
    # after "page" is used as the page number.
    re.compile(
        r"^page(\d+)(?:_.*)?\.(jpg|jpeg|png)$",
        re.IGNORECASE
    ),
]


# ============================================================
# FIND PAGE NUMBER
# ============================================================

def get_page_number(filename):

    for pattern in PAGE_PATTERNS:

        match = pattern.match(filename)

        if match:
            return int(match.group(1))

    return None


# ============================================================
# FIND FONT
# ============================================================

def find_font(font_size):

    for font_path in FONT_PATHS:

        if font_path.exists():

            return ImageFont.truetype(
                str(font_path),
                font_size
            )

    print("WARNING: Arial Bold not found.")
    print("Using PIL default font.")

    return ImageFont.load_default()


# ============================================================
# CALCULATE FONT SIZE
# ============================================================

def calculate_font_size(image_width):

    size = int(
        image_width * FONT_WIDTH_RATIO
    )

    size = max(
        MIN_FONT_SIZE,
        size
    )

    size = min(
        MAX_FONT_SIZE,
        size
    )

    return size


# ============================================================
# ADD PAGE NUMBER
# ============================================================

def add_page_number(
    input_file,
    output_file,
    page_number
):

    with Image.open(input_file) as img:

        # ----------------------------------------------------
        # Handle image modes
        # ----------------------------------------------------

        if img.mode not in ("RGB", "RGBA"):

            img = img.convert("RGB")

        width, height = img.size

        # ----------------------------------------------------
        # Drawing object
        # ----------------------------------------------------

        draw = ImageDraw.Draw(img)

        # ----------------------------------------------------
        # Font
        # ----------------------------------------------------

        font_size = calculate_font_size(width)

        font = find_font(font_size)

        # ----------------------------------------------------
        # Page number
        # ----------------------------------------------------

        text = str(page_number)

        bbox = draw.textbbox(
            (0, 0),
            text,
            font=font
        )

        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # ----------------------------------------------------
        # Safe margins
        # ----------------------------------------------------

        horizontal_margin = int(
            width * SAFE_MARGIN_PERCENT
        )

        vertical_margin = int(
            height * SAFE_MARGIN_PERCENT
        )

        # ----------------------------------------------------
        # Bottom-right position
        # ----------------------------------------------------

        x = (
            width
            - horizontal_margin
            - text_width
        )

        y = (
            height
            - vertical_margin
            - text_height
        )

        # ----------------------------------------------------
        # Draw page number
        # ----------------------------------------------------

        draw.text(
            (x, y),
            text,
            font=font,
            fill=NUMBER_COLOR
        )

        # ----------------------------------------------------
        # Create output folder
        # ----------------------------------------------------

        output_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        # ----------------------------------------------------
        # Save according to original format
        # ----------------------------------------------------

        extension = input_file.suffix.lower()

        if extension == ".png":

            # Preserve PNG
            img.save(
                output_file,
                "PNG"
            )

        else:

            # JPEG
            if img.mode != "RGB":
                img = img.convert("RGB")

            img.save(
                output_file,
                "JPEG",
                quality=JPEG_QUALITY,
                optimize=True
            )


# ============================================================
# PROCESS BOOK
# ============================================================

def process_book(book_folder):

    print()
    print("=" * 70)
    print("KDP PAGE NUMBERING")
    print("=" * 70)

    print()
    print(f"Input : {book_folder}")

    # --------------------------------------------------------
    # Validate folder
    # --------------------------------------------------------

    if not book_folder.exists():

        print()
        print("ERROR: Folder does not exist.")
        print(book_folder)

        sys.exit(1)

    if not book_folder.is_dir():

        print()
        print("ERROR: The supplied path is not a folder.")

        sys.exit(1)

    # --------------------------------------------------------
    # Output folder
    # --------------------------------------------------------

    output_folder = (
        book_folder / "numbered"
    )

    # --------------------------------------------------------
    # Find page images
    # --------------------------------------------------------

    pages = []

    for file in book_folder.iterdir():

        if not file.is_file():
            continue

        page_number = get_page_number(
            file.name
        )

        if page_number is not None:

            pages.append(
                (
                    page_number,
                    file
                )
            )

    # --------------------------------------------------------
    # No files
    # --------------------------------------------------------

    if not pages:

        print()
        print(
            "ERROR: No supported page images found."
        )

        print()
        print("Supported examples:")

        print("  page1_image.jpg")
        print("  page2_image.jpg")
        print("  Page1_text.png")
        print("  Page2_text.png")

        sys.exit(1)

    # --------------------------------------------------------
    # Sort numerically
    # --------------------------------------------------------

    pages.sort(
        key=lambda item: item[0]
    )

    print()
    print(
        f"Found {len(pages)} page images."
    )

    print()
    print(
        f"Output: {output_folder}"
    )

    print()

    # --------------------------------------------------------
    # Process pages
    # --------------------------------------------------------

    success_count = 0

    for page_number, input_file in pages:

        output_file = (
            output_folder /
            input_file.name
        )

        print(
            f"[{page_number:02d}] "
            f"{input_file.name}"
        )

        try:

            add_page_number(
                input_file,
                output_file,
                page_number
            )

            success_count += 1

        except Exception as error:

            print(
                f"    ERROR: {error}"
            )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print("=" * 70)

    print(
        f"Completed: "
        f"{success_count}/{len(pages)}"
    )

    print()
    print(
        "Numbered images:"
    )

    print(
        output_folder
    )

    print("=" * 70)

    print()


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # Require folder argument
    # --------------------------------------------------------

    if len(sys.argv) < 2:

        print()
        print("Usage:")

        print()

        print(
            'python add_page_numbers.py "BOOK_FOLDER"'
        )

        print()

        print("Example:")

        print(
            r'python add_page_numbers.py "E:\CHOTU\CODEX\LUMI SERIES\BOOK 1"'
        )

        print()

        sys.exit(1)

    # --------------------------------------------------------
    # Get folder
    # --------------------------------------------------------

    book_folder = Path(
        sys.argv[1]
    ).resolve()

    # --------------------------------------------------------
    # Process
    # --------------------------------------------------------

    process_book(
        book_folder
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()

