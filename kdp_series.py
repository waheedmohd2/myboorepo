"""Series preflight + fixer, built on your Fix-KDP-Pdfs.py."""
import re
import shutil
from pathlib import Path
from pypdf import PdfReader
import kdp_core as K

_FIX = None


def fixer():
    """Loads Fix-KDP-Pdfs.py as it is, except one line: its work-folder path uses parents[2], which fails on short paths."""
    global _FIX
    if _FIX is None:
        f = Path(__file__).with_name("Fix-KDP-Pdfs.py")
        src = f.read_text(encoding="utf-8").replace("Path(__file__).resolve().parents[2]", "Path(__file__).resolve().parent")
        ns = {"__file__": str(f.resolve()), "__name__": "kdp_fixer"}
        exec(compile(src, str(f), "exec"), ns); _FIX = ns
    return _FIX


def natural(p): return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def find_pair(book):
    for sub in ("output", "."):
        c, i = Path(book) / sub / "KDP_COVER.pdf", Path(book) / sub / "KDP_INTERIOR.pdf"
        if c.is_file() and i.is_file(): return c, i
    return None


def preflight(book, trim=8.5, paper="B&W - white", barcode="left"):
    """One report row for a book folder, or None if it has no KDP_COVER.pdf + KDP_INTERIOR.pdf pair."""
    pair = find_pair(book)
    if not pair: return None
    c, i = pair
    try:
        pages = len(PdfReader(str(i), strict=False).pages)
        ri = K.validate(i.read_bytes(), "interior", trim, pages, paper, 0, barcode)
        rc = K.validate(c.read_bytes(), "cover", trim, pages, paper, 0, barcode)
    except Exception as e:
        return {"Book": Path(book).name, "Pages": "?", "Cover": "FAIL", "Interior": "FAIL", "Issues": f"Unreadable: {e}"}
    st = lambda r: "FAIL" if any(l == "FAIL" for l, _ in r) else "WARN" if any(l == "WARN" for l, _ in r) else "PASS"
    return {"Book": Path(book).name, "Pages": pages, "Cover": st(rc), "Interior": st(ri),
            "Issues": " | ".join(m for l, m in rc + ri if l == "FAIL")}


def fix_cover_only(book, out_root, trim=8.5, paper="B&W - white"):
    """Fix ONLY the cover size using the actual interior page count.

    The interior PDF is copied unchanged. This is important: the interior does
    not need a spine, and a cover-size correction must not shrink or rewrite
    the interior artwork.
    """
    pair = find_pair(book)
    if not pair:
        raise FileNotFoundError(f"{Path(book).name}: KDP_COVER.pdf + KDP_INTERIOR.pdf not found")
    cover, interior = pair
    pages = len(PdfReader(str(interior), strict=False).pages)
    if not K.MIN_PAGES <= pages <= K.MAX_PAGES:
        raise ValueError(f"{Path(book).name}: interior has {pages} pages; KDP paperback requires {K.MIN_PAGES}-{K.MAX_PAGES}")

    reader = PdfReader(str(cover), strict=False)
    if reader.is_encrypted:
        raise ValueError(f"{Path(book).name}: cover PDF is encrypted")
    if len(reader.pages) != 1:
        raise ValueError(f"{Path(book).name}: cover must contain exactly one page")

    spine = K.spine_width(pages, paper)
    target_w, target_h = K.cover_size(trim, spine)
    page = reader.pages[0]
    source_w = float(page.mediabox.width) / 72.0
    source_h = float(page.mediabox.height) / 72.0
    target_wp = target_w * 72.0
    target_hp = target_h * 72.0

    from pypdf import PdfWriter, Transformation
    from pypdf.generic import RectangleObject
    scale_x = target_wp / float(page.mediabox.width)
    scale_y = target_hp / float(page.mediabox.height)
    llx = float(page.mediabox.left)
    lly = float(page.mediabox.bottom)
    page.add_transformation(Transformation(ctm=(scale_x, 0, 0, scale_y, -llx * scale_x, -lly * scale_y)), expand=False)
    rect = RectangleObject((0, 0, target_wp, target_hp))
    page.mediabox = rect
    page.cropbox = rect
    page.trimbox = rect
    page.bleedbox = rect
    page.artbox = rect

    dest = Path(out_root) / Path(book).name
    dest.mkdir(parents=True, exist_ok=True)
    cover_dest = dest / "KDP_COVER.pdf"
    interior_dest = dest / "KDP_INTERIOR.pdf"
    tmp = cover_dest.with_suffix('.tmp.pdf')
    writer = PdfWriter()
    writer.add_page(page)
    with tmp.open('wb') as f:
        writer.write(f)
    tmp.replace(cover_dest)
    shutil.copy2(interior, interior_dest)

    return {
        "Book": Path(book).name,
        "Pages": pages,
        "OldCover": f"{source_w:.4f} x {source_h:.4f}",
        "NewCover": f"{target_w:.4f} x {target_h:.4f}",
        "Spine": f"{spine:.4f}",
        "Interior": "Copied unchanged",
        "Output": str(dest),
    }


def fix_book(book, out_root):
    """Fix cover and interior using the actual interior page count.

    The cover gets the correct KDP spine width. The interior is scaled into
    the configured safe margin area with alternating gutter margins.
    """
    c, i = find_pair(book)
    fx = fixer()
    dest = Path(out_root) / Path(book).name
    dest.mkdir(parents=True, exist_ok=True)
    pages = len(PdfReader(str(i), strict=False).pages)

    old_cover = fx["fix_cover"](c, dest / "KDP_COVER.pdf", pages)
    fixed_pages, old_interior_sizes = fx["fix_interior"](
        i, dest / "KDP_INTERIOR.pdf"
    )

    return {
        "OldCover": f"{old_cover[0]:.4f} x {old_cover[1]:.4f}",
        "NewCover": f"{old_cover[2]:.4f} x {old_cover[3]:.4f}",
        "Spine": f"{pages * K.PAPER['B&W - white']:.4f}",
        "Pages": fixed_pages,
        "InteriorSizes": "; ".join(
            f"{w/72:.4f} x {h/72:.4f}" for w, h in sorted(old_interior_sizes)
        ),
        "InteriorHandling": "Scaled to KDP safe area with alternating gutter margins",
    }


def fix_cover_only(book, out_root, trim=8.5, paper="B&W - white"):
    """Backward-compatible cover-only fix."""
    c, i = find_pair(book)
    fx = fixer()
    dest = Path(out_root) / Path(book).name
    dest.mkdir(parents=True, exist_ok=True)
    pages = len(PdfReader(str(i), strict=False).pages)
    old = fx["fix_cover"](c, dest / "KDP_COVER.pdf", pages)
    shutil.copy2(i, dest / "KDP_INTERIOR.pdf")
    return {
        "OldCover": f"{old[0]:.4f} x {old[1]:.4f}",
        "NewCover": f"{old[2]:.4f} x {old[3]:.4f}",
        "Spine": f"{pages * K.PAPER[paper]:.4f}",
    }
