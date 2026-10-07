"""KDP production core: interior + cover builders, image generation, KDP checks."""
import io, re, subprocess, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader
try:
    import fitz  # PyMuPDF, optional: enables margin / barcode / spine-text checks
except Exception:
    fitz = None

DPI, BLEED = 300, 0.125


class _Canvas(canvas.Canvas):
    """reportlab normally lists a Helvetica font on every page; skip it so the PDF has no unembedded fonts."""
    def _make_preamble(self): self._preamble = "1 0 0 1 0 0 cm"

# Spine thickness per page (inches). KDP's cover template generator is the authority:
# use the "spine width override" in the app to paste its exact figure.
PAPER = {"B&W - white": 0.002252, "B&W - cream": 0.0025,
         "Color - standard": 0.002252, "Color - premium": 0.002347}
MIN_PAGES, MAX_PAGES, SPINE_TEXT_MIN, MAX_MB = 24, 828, 79, 650


def gutter(n): return .375 if n <= 150 else .5 if n <= 300 else .625 if n <= 500 else .75 if n <= 700 else .875
def spine_width(pages, paper, override=0): return round(override or pages * PAPER[paper], 4)
def cover_size(trim, spine): return trim * 2 + spine + 2 * BLEED, trim + 2 * BLEED
def px(inches): return round(inches * DPI)
def split_pages(txt): return [p.strip() for p in re.split(r"\n\s*-{3,}\s*\n", "\n" + txt + "\n") if p.strip()]


def font(size):
    for f in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf"):
        try: return ImageFont.truetype(f, size)
        except OSError: pass
    return ImageFont.load_default(size)


def wrap(d, text, f, maxw):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split():
            t = (cur + " " + w).strip()
            if d.textlength(t, font=f) <= maxw: cur = t
            else: lines.append(cur); cur = w
        lines.append(cur)
    return lines


# ---------- image generation (no cloud API) ----------
def generate_image(prompt, out, ref=None, provider="placeholder", command=""):
    """provider 'command' runs YOUR shell command with {prompt_file}, {ref} and {out} filled in (quoted for you);
    the command must write the image to {out}. 'placeholder' draws a stand-in for testing."""
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    if provider == "command":
        pf = out.with_suffix(".prompt.txt"); pf.write_text(prompt, encoding="utf-8")
        subprocess.run(command.format(prompt_file=f'"{pf}"', ref=f'"{ref or ""}"', out=f'"{out}"'), shell=True, check=True)
        if not out.exists(): raise RuntimeError(f"Command ran but {out.name} was not created")
        return out
    im = Image.new("RGB", (1024, 1024), (225, 235, 245))
    ImageDraw.Draw(im).text((60, 60), textwrap.fill(prompt[-300:], 38), font=font(32), fill=(40, 60, 90))
    im.save(out); return out


# ---------- interior ----------
def _fit_text(d, text, box_w, box_h):
    for s in range(130, 28, -4):
        f = font(s); lines = wrap(d, text, f, box_w)
        if len(lines) * s * 1.25 <= box_h: break
    return f, s, lines


def compose_page(img_path, text, n, total, trim=8.5, bleed=True, mode="overlay", number=None):
    """mode: 'overlay' = text panel over the image, 'text' = text page, 'image' = image only."""
    b = BLEED if bleed else 0
    W, H = px(trim + b), px(trim + 2 * b)
    have = bool(img_path) and Path(img_path).exists()
    im = (ImageOps.fit(Image.open(img_path).convert("RGB"), (W, H), Image.LANCZOS) if have
          else Image.new("RGB", (W, H), "#fffaf0" if mode == "text" else "white"))
    recto = n % 2 == 1                         # odd pages: binding on the left
    x0 = 0 if recto else b                     # left edge of the trim box
    g, o = gutter(total), .5
    left, right = x0 + (g if recto else o), x0 + trim - (o if recto else g)
    d = ImageDraw.Draw(im, "RGBA")
    if text and mode in ("overlay", "text") and not (mode == "text" and have):
        top, bot = (b + trim - 2.0, b + trim - .5) if mode == "overlay" else (b + .75, b + trim - .75)
        if mode == "overlay":
            d.rounded_rectangle((px(left - .12), px(top - .1), px(right + .12), px(bot + .1)), 40, fill=(255, 255, 255, 205))
        f, s, lines = _fit_text(d, text, px(right - left), px(bot - top))
        y = px(top) + (px(bot - top) - int(len(lines) * s * 1.25)) // 2
        for ln in lines:
            x = px(left) if mode == "overlay" else px(left) + (px(right - left) - int(d.textlength(ln, font=f))) // 2
            d.text((x, y), ln, font=f, fill=(20, 20, 20, 255)); y += int(s * 1.25)
    if number is not None:                     # bottom right, kept inside the margins
        f = font(max(32, int(W * .025))); t = str(number); bb = d.textbbox((0, 0), t, font=f)
        rx = x0 + trim - (o if recto else g + .1)
        d.text((px(rx) - (bb[2] - bb[0]), px(b + trim - .45) - (bb[3] - bb[1])), t, font=f, fill=(0, 0, 0, 255))
    return im.convert("RGB")


def build_interior(items, out_pdf, trim=8.5, bleed=True, progress=None, pad=True, title="KDP_INTERIOR"):
    """items: [(image_path|None, text, mode, number|None)]. Pads with blank pages to KDP's 24-page minimum."""
    items = list(items) + ([(None, "", "image", None)] * max(0, MIN_PAGES - len(items)) if pad else [])
    b = BLEED if bleed else 0
    w, h = (trim + b) * 72, (trim + 2 * b) * 72
    c = _Canvas(str(out_pdf), pagesize=(w, h)); c.setTitle(title)
    for i, (img, text, mode, num) in enumerate(items, 1):
        im = compose_page(img, text, i, len(items), trim, bleed, mode, num)
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=92, dpi=(DPI, DPI)); buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, w, h); c.showPage()
        if progress: progress(i / len(items))
    c.save(); return out_pdf


# ---------- cover ----------
def build_cover(front, back, out_pdf, pages, paper, trim=8.5, spine_override=0, spine_color="#ffffff",
                title="", text_color="#000000", barcode="none"):
    sp = spine_width(pages, paper, spine_override); wi, hi = cover_size(trim, sp)
    W, H = px(wi), px(hi); bx, fx = px(BLEED + trim), px(BLEED + trim + sp)
    im = Image.new("RGB", (W, H), spine_color)
    if back: im.paste(ImageOps.fit(Image.open(back).convert("RGB"), (bx, H), Image.LANCZOS), (0, 0))
    if front: im.paste(ImageOps.fit(Image.open(front).convert("RGB"), (W - fx, H), Image.LANCZOS), (fx, 0))
    d = ImageDraw.Draw(im)
    if title and pages >= SPINE_TEXT_MIN:
        s = int((fx - bx) * .5)
        while s > 8 and d.textlength(title, font=font(s)) > px(trim) * .8: s -= 2
        f = font(s); tmp = Image.new("RGBA", (int(d.textlength(title, font=f)) + 4, int(s * 1.4)), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).text((0, 0), title, font=f, fill=text_color)
        tmp = tmp.rotate(-90, expand=True)         # reads top to bottom
        im.paste(tmp, (bx + (fx - bx - tmp.width) // 2, (H - tmp.height) // 2), tmp)
    if barcode in ("left", "right"):               # leave the barcode zone clear (2 x 1.2 in)
        x1 = px(BLEED + .25) if barcode == "left" else bx - px(.25) - px(2)
        y1 = H - px(BLEED + .25) - px(1.2)
        d.rectangle((x1, y1, x1 + px(2), y1 + px(1.2)), fill="white")
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=93, dpi=(DPI, DPI)); buf.seek(0)
    c = _Canvas(str(out_pdf), pagesize=(wi * 72, hi * 72)); c.setTitle("KDP_COVER")
    c.drawImage(ImageReader(buf), 0, 0, wi * 72, hi * 72); c.save(); return out_pdf


# ---------- KDP checker ----------
def _o(x): return x.get_object() if x is not None else {}


def _scan(rd):
    per, trans, bad = [], False, set()
    for pg in rd.pages:
        r = _o(pg.get("/Resources")); imgs = []
        for x in _o(r.get("/XObject")).values():
            x = x.get_object()
            if x.get("/Subtype") == "/Image":
                imgs.append((int(x["/Width"]), int(x["/Height"]))); trans |= "/SMask" in x
        for g in _o(r.get("/ExtGState")).values():
            g = g.get_object()
            trans |= float(g.get("/ca", 1)) < 1 or float(g.get("/CA", 1)) < 1 or g.get("/SMask", "/None") != "/None"
        for f in _o(r.get("/Font")).values():
            f = f.get_object()
            if f.get("/Subtype") == "/Type0": f = f["/DescendantFonts"][0].get_object()
            fd = _o(f.get("/FontDescriptor"))
            if not any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3")): bad.add(str(f.get("/BaseFont")))
        per.append((imgs, float(pg.mediabox.width) / 72, float(pg.mediabox.height) / 72))
    return per, trans, bad


def validate(data, kind, trim=8.5, pages=0, paper="B&W - white", spine_override=0, barcode="none"):
    """kind: 'interior' or 'cover'. Returns [(PASS|WARN|FAIL, message)]."""
    R = []
    def add(ok, msg, bad="FAIL"): R.append(("PASS" if ok else bad, msg))
    mb = len(data) / 1048576; add(mb <= MAX_MB, f"File size {mb:.1f} MB (max {MAX_MB} MB)")
    try: rd = PdfReader(io.BytesIO(data))
    except Exception as e: return R + [("FAIL", f"Cannot read PDF: {e}")]
    if rd.is_encrypted: return R + [("FAIL", "PDF is encrypted; KDP needs an unprotected file")]
    per, trans, bad = _scan(rd); n = len(per)
    sizes = {(round(p[1], 3), round(p[2], 3)) for p in per}; mode = None
    if kind == "interior":
        add(MIN_PAGES <= n <= MAX_PAGES, f"{n} pages (KDP allows {MIN_PAGES}-{MAX_PAGES})")
        opts = {"no bleed": (trim, trim), "bleed": (trim + BLEED, trim + 2 * BLEED)}
        if len(sizes) != 1: add(False, f"Pages have mixed sizes: {sorted(sizes)}")
        else:
            w, h = next(iter(sizes))
            mode = next((k for k, (a, b) in opts.items() if abs(a - w) < .01 and abs(b - h) < .01), None)
            add(bool(mode), f"Page size {w} x {h} in " + (f"matches trim {trim} ({mode})" if mode else
                f"matches neither {opts['no bleed']} (no bleed) nor {opts['bleed']} (bleed)"))
    else:
        add(n == 1, f"{n} page(s); the cover must be a single page")
        sp = spine_width(pages, paper, spine_override); ew, eh = cover_size(trim, sp); w, h = next(iter(sizes))
        ok = abs(w - ew) < .02 and abs(h - eh) < .02
        add(ok, f"Size {w} x {h} in; expected {ew:.3f} x {eh:.3f} for {pages} pages (spine {sp:.4f} in)" +
            ("" if abs(w - ew) < .02 else f"; this file implies a spine of {w - 2 * trim - 2 * BLEED:.4f} in"))
    add(not bad, "All fonts embedded" if not bad else f"Fonts not embedded: {', '.join(sorted(bad))}")
    add(not trans, "No transparency found" if not trans else "Transparency found; flatten the PDF", "WARN")
    low = min((max(min(iw / w, ih / h) for iw, ih in imgs) for imgs, w, h in per if imgs), default=None)
    if low: add(low >= 299, f"Lowest image resolution about {low:.0f} DPI at placed size (KDP recommends 300)", "WARN")
    if not fitz:
        R.append(("WARN", "PyMuPDF not installed: margin, barcode-zone and spine-text checks were skipped")); return R
    doc = fitz.open(stream=data, filetype="pdf"); live = False
    if kind == "interior" and mode:
        b = BLEED if mode == "bleed" else 0; g = gutter(n); badp = []
        for i, pg in enumerate(doc, 1):
            recto = i % 2 == 1; x0 = 0 if recto else b
            L, Rr = x0 + (g if recto else .25), x0 + trim - (.25 if recto else g)
            for bl in (t for t in pg.get_text("blocks") if t[6] == 0):
                live = True
                if bl[0] / 72 < L - .01 or bl[2] / 72 > Rr + .01 or bl[1] / 72 < b + .25 - .01 or bl[3] / 72 > b + trim - .25 + .01:
                    badp.append(i); break
        if live: add(not badp, f"Live text inside margins (gutter {g} in, outside 0.25 in)" if not badp else f"Text too close to the edge or gutter on pages: {badp[:15]}")
    elif kind == "cover" and n == 1:
        pg = doc[0]; w, h = pg.rect.width / 72, pg.rect.height / 72
        bx = BLEED + trim; spw = w - 2 * trim - 2 * BLEED
        blocks = [t for t in pg.get_text("blocks") if t[6] == 0]; live = bool(blocks)
        if pages < SPINE_TEXT_MIN:
            on = [t for t in blocks if t[0] / 72 < bx + spw and t[2] / 72 > bx]
            add(not on, f"No text on the spine ({pages} pages is under {SPINE_TEXT_MIN})" if not on else f"Text on the spine, but KDP needs {SPINE_TEXT_MIN}+ pages for spine text")
        edge = [t for t in blocks if t[0] / 72 < BLEED + .125 or t[2] / 72 > w - BLEED - .125 or t[1] / 72 < BLEED + .125 or t[3] / 72 > h - BLEED - .125]
        if live: add(not edge, "Live text clear of the trim edges" if not edge else "Text within 0.125 in of the trim edge", "WARN")
        if barcode in ("left", "right"):
            import numpy as np
            x1 = BLEED + .25 if barcode == "left" else bx - .25 - 2; y1 = h - BLEED - .25 - 1.2
            pix = pg.get_pixmap(matrix=fitz.Matrix(.7, .7), clip=fitz.Rect(x1 * 72, y1 * 72, (x1 + 2) * 72, (y1 + 1.2) * 72), colorspace=fitz.csRGB)
            frac = (np.frombuffer(pix.samples, np.uint8).reshape(-1, 3).min(1) >= 240).mean()
            add(frac >= .97, f"Barcode zone ({barcode} corner of the back cover) is clear" if frac >= .97 else "Artwork in the barcode zone; KDP prints a white barcode box there", "WARN")
    if not live: R.append(("WARN", "No live text found (text is baked into images), so margins can't be checked here; confirm in KDP's Previewer"))
    return R


# ---------- file ordering ----------
EXTS = ("png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff")

# Accept page1/page01 filenames with any suffix, including generated names
# such as page01_color_2550x2550_300dpi.png. Matching is case-insensitive.
PAGE_RE = re.compile(
    r"^page(?P<number>\d+)(?:_(?P<label>[^.]+))?\.(?P<ext>"
    + "|".join(EXTS)
    + r")$",
    re.I,
)


def order_pages(paths):
    """Order page images numerically and accept page1/page01 with any suffix.

    Explicit *_text files come first, then *_image files, then other
    page files such as *_color.
    """
    paths = [Path(p) for p in paths]
    parsed = []
    ignored = []

    for p in paths:
        match = PAGE_RE.match(p.name)
        if not match:
            ignored.append(p.name)
            continue

        number = int(match.group("number"))
        label = (match.group("label") or "").lower()

        # For each page number, story/text content comes first, followed by
        # the coloring/image content. This supports names such as:
        # page01_story_image / page1_text -> page01_image / page1_coloring_image.
        if label == "story_image":
            priority = 0
        elif label == "text":
            priority = 0
        elif label in ("image", "coloring_image"):
            priority = 1
        else:
            priority = 2

        parsed.append((number, priority, p.name.lower(), p))

    parsed.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in parsed], ignored
