"""Step 0 processor for the KDP dashboard.

Reads a Master Prompt Pack PDF and creates per-book/per-page story summaries,
story-image prompts, and coloring-image prompts. No cloud API is required.
"""
from __future__ import annotations

import json
import re
import base64
import requests
from pathlib import Path
from typing import Dict, List, Tuple

from pypdf import PdfReader

# Master Prompt Packs can use different punctuation/heading styles, e.g.
# "Book 1 — Title", "BOOK 01: Title", or simply "Book 1".  Do not make
# the parser depend on the exact book title or on a particular separator.
BOOK_RE = re.compile(
    r"(?im)^\s*book\s*0*(\d{1,2})(?:\s*[-:–—]\s*(.*?))?\s*$"
)
PAGE_RE = re.compile(
    r"(?im)^\s*page\s*0*(\d{1,3})(?:\s*[-:–—]\s*(.*?))?\s*$"
)


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _section_between(text: str, start: int, end: int) -> str:
    return text[start:end].strip()


def _normalize_extracted_text(text: str) -> str:
    # PDF extraction can contain non-breaking spaces and Unicode dash variants.
    # Normalize those before heading detection while preserving line boundaries.
    import unicodedata
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def parse_books(text: str) -> List[Dict]:
    text = _normalize_extracted_text(text)
    matches = list(BOOK_RE.finditer(text))

    # A few PDF generators wrap headings. If normal line-start detection finds
    # nothing, use a conservative fallback for headings beginning with BOOK.
    if not matches:
        fallback = re.compile(r"(?im)^\s*book\s*0*(\d{1,2})\b[^\n]*")
        matches = list(fallback.finditer(text))

    books: List[Dict] = []
    seen = set()
    for i, m in enumerate(matches):
        number = int(m.group(1))
        if number in seen:
            continue
        seen.add(number)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = _section_between(text, start, end)
        raw_title = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
        title = clean(raw_title) or f"Book {number}"
        books.append(parse_book(number, title, section))
    books.sort(key=lambda b: b["book"])
    return books


def parse_book(number: int, title: str, section: str) -> Dict:
    # The source pack has stable headings. Keep the original master prompt and
    # cover prompt available, but page prompts are parsed separately.
    mm = re.search(r"BOOK-LEVEL MASTER PROMPT\s*(.*?)(?=COVER MASTER PROMPT|PAGE-BY-PAGE GENERATION PROMPTS)", section, re.I | re.S)
    cm = re.search(r"COVER MASTER PROMPT\s*(.*?)(?=PAGE-BY-PAGE GENERATION PROMPTS)", section, re.I | re.S)
    pm = re.search(r"PAGE-BY-PAGE GENERATION PROMPTS\s*(.*)$", section, re.I | re.S)
    master_prompt = clean(mm.group(1)) if mm else ""
    cover_prompt = clean(cm.group(1)) if cm else ""
    page_section = pm.group(1) if pm else section

    pages = []
    page_matches = list(PAGE_RE.finditer(page_section))
    for i, m in enumerate(page_matches):
        pstart = m.start()
        pend = page_matches[i + 1].start() if i + 1 < len(page_matches) else len(page_section)
        raw = page_section[pstart:pend].strip()
        # Stop at the next Book heading if extraction/layout made it leak in.
        raw = re.split(r"\n\s*Book\s+0*\d+\b[^\n]*", raw, maxsplit=1, flags=re.I)[0].strip()
        scene = clean(m.group(2) or "")
        coloring_prompt = clean(raw)
        # Remove accidental repeated heading text from the prompt while retaining
        # the complete source wording.
        pages.append({
            "page": int(m.group(1)),
            "scene": scene,
            "coloring_prompt": coloring_prompt,
        })

    setting = ""
    sm = re.search(r"The setting is\s+(.+?)(?:\.\s+Build a gentle visual story|\.\s+Create exactly 40)", master_prompt, re.I)
    if sm:
        setting = clean(sm.group(1))
    characters = ""
    cmatch = re.search(r"The recurring main character is\s+(.+?)(?:\.\s+Supporting recurring friends|\.\s+The setting is)", master_prompt, re.I)
    if cmatch:
        characters = clean(cmatch.group(1))
    friends = ""
    fmatch = re.search(r"Supporting recurring friends may include\s+(.+?)\.", master_prompt, re.I)
    if fmatch:
        friends = clean(fmatch.group(1))

    return {
        "book": number,
        "title": title,
        "master_prompt": master_prompt,
        "cover_prompt": cover_prompt,
        "setting": setting,
        "characters": characters,
        "friends": friends,
        "pages": pages,
    }


OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:3b"


def ollama_models(base_url: str = OLLAMA_URL, timeout: int = 5) -> List[str]:
    r = requests.get(base_url.rstrip("/") + "/api/tags", timeout=timeout)
    r.raise_for_status()
    return [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]


def ollama_generate(prompt: str, model: str = DEFAULT_OLLAMA_MODEL, base_url: str = OLLAMA_URL,
                    system: str = "", timeout: int = 180) -> str:
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    r = requests.post(base_url.rstrip("/") + "/api/generate", json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return (data.get("response") or "").strip()


def test_ollama(model: str = DEFAULT_OLLAMA_MODEL, base_url: str = OLLAMA_URL) -> str:
    names = ollama_models(base_url)
    if model not in names:
        raise RuntimeError(f"Ollama is reachable, but model '{model}' was not found. Installed models: {', '.join(names) or 'none'}")
    return ollama_generate("Reply with exactly: OLLAMA_OK", model=model, base_url=base_url, timeout=60)


def ollama_story_summary(book: Dict, scene: str, page_no: int, model: str, base_url: str,
                         template_text: str = "") -> str:
    prompt = f"""You are preparing a gentle children's storybook from an existing Master Prompt Pack.
Write a story summary for BOOK {book['book']} — {book['title']}, PAGE {page_no}.
Use ONLY the supplied source information. Do not invent a new event, character, location, object, danger, or outcome.
Write exactly 2 or 3 natural sentences. Keep the wording simple, warm, visual, and suitable for children. Do not use bullet points, headings, quotes, or story text that would appear inside the illustration.

BOOK MASTER CONTEXT:
{book.get('master_prompt','')[:8000]}

SOURCE SCENE:
{scene}
"""
    if template_text:
        prompt += f"\nSTYLE/TEMPLATE CONTEXT (use only for tone/visual continuity):\n{template_text[:5000]}\n"
    return ollama_generate(prompt, model=model, base_url=base_url, timeout=180)


def ollama_story_prompt(book: Dict, page: Dict, template_text: str, model: str, base_url: str) -> str:
    prompt = f"""Create a production-ready story-image prompt for a children's storybook illustration.
Preserve the source scene exactly; expand only visual details that are consistent with the book master context.
Do not add text, letters, logos, copyrighted characters, scary/violent/adult content, or events not supported by the source.
The prompt must clearly describe subject, action, setting, composition, character continuity, and child-friendly visual style.
Return ONLY the image prompt, no heading and no explanation.

BOOK: {book['title']}
SETTING: {book.get('setting','')}
CHARACTER CONTINUITY: {book.get('characters','')}
SUPPORTING FRIENDS: {book.get('friends','')}
SOURCE SCENE: {page['scene']}
STORY SUMMARY: {page.get('story_summary','')}

STORY IMAGE TEMPLATE / STYLE GUIDE:
{template_text[:8000]}
"""
    return ollama_generate(prompt, model=model, base_url=base_url, timeout=180)


def process_master_with_ollama(pdf_path: Path, output_root: Path, template_text: str = "", template_name: str = "",
                              template_path: Path | None = None, model: str = DEFAULT_OLLAMA_MODEL,
                              base_url: str = OLLAMA_URL) -> Dict:
    """Same output as process_master, but uses the local Ollama model for 2–3 sentence summaries and story prompts."""
    text = extract_pdf_text(pdf_path)
    books = parse_books(text)
    if not books:
        raise ValueError("No Book sections were detected. Expected headings such as 'Book 1 — Title', 'Book 01: Title', or 'BOOK 1'.")
    output_root.mkdir(parents=True, exist_ok=True)
    records = []
    for book in books:
        book_dir = output_root / f"BOOK_{book['book']:02d}"
        book_dir.mkdir(parents=True, exist_ok=True)
        (book_dir / "book_master_prompt.txt").write_text(book["master_prompt"], encoding="utf-8")
        (book_dir / "cover_master_prompt.txt").write_text(book["cover_prompt"], encoding="utf-8")
        if template_path and template_path.exists():
            import shutil
            shutil.copy2(template_path, book_dir / f"STORY_IMAGE_TEMPLATE{template_path.suffix.lower()}")
        for page in book["pages"]:
            pno = page["page"]
            page_dir = book_dir / f"PAGE_{pno:02d}"
            page_dir.mkdir(parents=True, exist_ok=True)
            summary = ollama_story_summary(book, page["scene"], pno, model, base_url, template_text)
            page_for_prompt = dict(page, story_summary=summary)
            story_prompt = ollama_story_prompt(book, page_for_prompt, template_text, model, base_url)
            (page_dir / "story_summary.txt").write_text(summary, encoding="utf-8")
            (page_dir / "story_image_prompt.txt").write_text(story_prompt, encoding="utf-8")
            (page_dir / "coloring_image_prompt.txt").write_text(page["coloring_prompt"], encoding="utf-8")
            (page_dir / "source_scene.txt").write_text(page["scene"], encoding="utf-8")
            records.append({"book":book["book"],"book_title":book["title"],"page":pno,
                            "source_scene":page["scene"],"story_summary":summary,
                            "story_image_prompt":story_prompt,"coloring_image_prompt":page["coloring_prompt"]})
    payload={"source_pdf":pdf_path.name,"template_name":template_name,"ollama_model":model,
             "book_count":len(books),"page_count":len(records),
             "books":[{"book":b["book"],"title":b["title"],"page_count":len(b["pages"]),"setting":b["setting"]} for b in books],
             "records":records}
    (output_root/"MASTER_PROMPTS.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    # Reuse the export section from the normal processor by writing the same human-readable/CSV/XLSX outputs.
    lines=[]
    for r in records:
        lines += [f"BOOK {r['book']:02d} — {r['book_title']} — PAGE {r['page']:02d}","STORY SUMMARY:",r['story_summary'],"STORY IMAGE PROMPT:",r['story_image_prompt'],"COLORING IMAGE PROMPT:",r['coloring_image_prompt'],"="*80]
    (output_root/"MASTER_PROMPTS.txt").write_text("\n".join(lines),encoding="utf-8")
    import csv
    with (output_root/"MASTER_PROMPTS.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=["book","book_title","page","source_scene","story_summary","story_image_prompt","coloring_image_prompt"]); w.writeheader(); w.writerows(records)
    try:
        from openpyxl import Workbook
        wb=Workbook(); ws=wb.active; ws.title="Prompts"; ws.append(["Book","Book Title","Page","Source Scene","Story Summary","Story Image Prompt","Coloring Image Prompt"])
        for r in records: ws.append([r["book"],r["book_title"],r["page"],r["source_scene"],r["story_summary"],r["story_image_prompt"],r["coloring_image_prompt"]])
        ws.freeze_panes="A2"; wb.save(output_root/"MASTER_PROMPTS.xlsx")
    except Exception: pass
    return payload


def make_story_summary(book: Dict, scene: str, page_no: int) -> str:
    """Create a concise 2-3 sentence narrative grounded in the source scene."""
    s = scene.rstrip(".")
    title = book["title"]
    setting = book.get("setting") or "the story setting"
    # Keep the actual source scene as the factual anchor; the remaining lines
    # provide gentle connective narration rather than adding unsupported events.
    subject = s
    if subject.lower().startswith("lumi"):
        line1 = f"Lumi begins this moment of her gentle adventure: {subject}."
    else:
        line1 = f"A new moment unfolds as {subject}."
    line2 = f"The scene takes place in {setting}, giving the page a clear sense of place and helping the gentle story unfold."
    line3 = f"It becomes one small step in the warm, visually understandable journey through {title}."
    return " ".join([line1, line2, line3])


def make_story_prompt(book: Dict, page: Dict, template_text: str = "", template_name: str = "") -> str:
    template_block = ""
    if template_text.strip():
        template_block = (
            "\n\nSTORY IMAGE TEMPLATE / STYLE GUIDE\n"
            + template_text.strip()
            + "\n\nApply this template consistently to the visual style, composition, and character presentation."
        )
    elif template_name:
        template_block = (
            f"\n\nSTORY IMAGE TEMPLATE REFERENCE: {template_name}\n"
            "Use the attached template as the visual style reference. Preserve its visual language consistently."
        )

    character = book.get("characters") or "the recurring characters described in the Master Prompt Pack"
    friends = book.get("friends") or "the supporting friends described in the Master Prompt Pack"
    setting = book.get("setting") or "the setting described in the Master Prompt Pack"
    return (
        "Create an ORIGINAL child-friendly storybook illustration for this page of the series.\n\n"
        f"BOOK: {book['title']}\n"
        f"PAGE: {page['page']:02d}\n"
        f"SCENE: {page['scene']}\n"
        f"STORY SUMMARY: {page.get('story_summary', '')}\n\n"
        f"CHARACTER CONTINUITY: {character}. Supporting friends: {friends}.\n"
        f"SETTING: {setting}.\n\n"
        "NARRATIVE PURPOSE: Show the supplied scene clearly so the image communicates the story without requiring written text. "
        "Keep the focal subject easy to recognize, with a simple supporting environment and a meaningful sense of place.\n\n"
        "VISUAL REQUIREMENTS: warm wholesome children's storybook illustration; original characters and artwork; consistent character proportions "
        "and accessories; clear foreground subject; readable facial expressions; simple background context; balanced composition; generous "
        "negative space; no frightening, violent, adult, branded, copyrighted, or recognizable third-party characters; no logos or unwanted text."
        + template_block
    )


def generate_story_images(output_root: Path, payload: Dict, provider: str = "command", command: str = "", image_ext: str = ".png") -> Dict:
    """Generate one story image per page into BOOK_XX/Story Images.

    Uses kdp_core.generate_image so the actual image generation stays local.
    The command may use {prompt_file}, {ref}, and {out}.
    """
    if provider != "command" or not command.strip():
        raise ValueError("A local image-generation command is required. Configure it in Step 0 before generating story images.")
    import kdp_core as K

    ext = image_ext if image_ext.startswith(".") else "." + image_ext
    generated = []
    failed = []
    total = len(payload.get("records", []))
    for idx, r in enumerate(payload.get("records", []), 1):
        book_dir = output_root / f"BOOK_{int(r['book']):02d}"
        images_dir = book_dir / "Story Images"
        images_dir.mkdir(parents=True, exist_ok=True)
        out = images_dir / f"page{int(r['page']):02d}_story_image{ext}"
        # Keep a copy of the exact prompt next to the image for reproducibility.
        prompt = (
            r["story_image_prompt"].rstrip()
            + "\n\nSTORY SUMMARY TO VISUALIZE:\n"
            + r["story_summary"].strip()
        )
        prompt_file = images_dir / f"page{int(r['page']):02d}_story_image.prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        ref = ""
        template_name = payload.get("template_name", "")
        if template_name:
            candidate = book_dir / f"STORY_IMAGE_TEMPLATE{Path(template_name).suffix.lower()}"
            if candidate.exists():
                ref = str(candidate)
        try:
            K.generate_image(prompt, out, ref=ref, provider="command", command=command)
            generated.append(str(out))
        except Exception as e:
            failed.append({"book": r["book"], "page": r["page"], "error": str(e)})
    result = {"generated": generated, "failed": failed, "count": len(generated), "total": total}
    (output_root / "STORY_IMAGE_GENERATION.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def process_master(pdf_path: Path, output_root: Path, template_text: str = "", template_name: str = "", template_path: Path | None = None) -> Dict:
    text = extract_pdf_text(pdf_path)
    books = parse_books(text)
    if not books:
        raise ValueError("No Book sections were detected. Expected headings such as 'Book 1 — Title', 'Book 01: Title', or 'BOOK 1'.")

    output_root.mkdir(parents=True, exist_ok=True)
    records = []
    for book in books:
        book_dir = output_root / f"BOOK_{book['book']:02d}"
        book_dir.mkdir(parents=True, exist_ok=True)
        # Keep book-level source context for traceability.
        (book_dir / "book_master_prompt.txt").write_text(book["master_prompt"], encoding="utf-8")
        (book_dir / "cover_master_prompt.txt").write_text(book["cover_prompt"], encoding="utf-8")
        if template_path and template_path.exists():
            (book_dir / "story_image_template_reference.txt").write_text(str(template_path), encoding="utf-8")
            try:
                import shutil
                shutil.copy2(template_path, book_dir / f"STORY_IMAGE_TEMPLATE{template_path.suffix.lower()}")
            except Exception:
                pass

        for page in book["pages"]:
            pno = page["page"]
            page_dir = book_dir / f"PAGE_{pno:02d}"
            page_dir.mkdir(parents=True, exist_ok=True)
            summary = make_story_summary(book, page["scene"], pno)
            page_for_prompt = dict(page)
            page_for_prompt["story_summary"] = summary
            story_prompt = make_story_prompt(book, page_for_prompt, template_text, template_name)
            (page_dir / "story_summary.txt").write_text(summary, encoding="utf-8")
            (page_dir / "story_image_prompt.txt").write_text(story_prompt, encoding="utf-8")
            (page_dir / "coloring_image_prompt.txt").write_text(page["coloring_prompt"], encoding="utf-8")
            (page_dir / "source_scene.txt").write_text(page["scene"], encoding="utf-8")
            records.append({
                "book": book["book"],
                "book_title": book["title"],
                "page": pno,
                "source_scene": page["scene"],
                "story_summary": summary,
                "story_image_prompt": story_prompt,
                "coloring_image_prompt": page["coloring_prompt"],
            })

    # Master machine-readable output.
    payload = {
        "source_pdf": pdf_path.name,
        "template_name": template_name,
        "book_count": len(books),
        "page_count": len(records),
        "books": [
            {"book": b["book"], "title": b["title"], "page_count": len(b["pages"]), "setting": b["setting"]}
            for b in books
        ],
        "records": records,
    }
    (output_root / "MASTER_PROMPTS.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Human-readable master text.
    lines = []
    for r in records:
        lines += [
            f"BOOK {r['book']:02d} — {r['book_title']} — PAGE {r['page']:02d}",
            "STORY SUMMARY:", r["story_summary"],
            "STORY IMAGE PROMPT:", r["story_image_prompt"],
            "COLORING IMAGE PROMPT:", r["coloring_image_prompt"],
            "=" * 80,
        ]
    (output_root / "MASTER_PROMPTS.txt").write_text("\n".join(lines), encoding="utf-8")

    # CSV/XLSX are optional convenience exports.
    try:
        import csv
        with (output_root / "MASTER_PROMPTS.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["book", "book_title", "page", "source_scene", "story_summary", "story_image_prompt", "coloring_image_prompt"])
            w.writeheader(); w.writerows(records)
    except Exception:
        pass
    try:
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.title = "Prompts"
        headers = ["Book", "Book Title", "Page", "Source Scene", "Story Summary", "Story Image Prompt", "Coloring Image Prompt"]
        ws.append(headers)
        for r in records:
            ws.append([r["book"], r["book_title"], r["page"], r["source_scene"], r["story_summary"], r["story_image_prompt"], r["coloring_image_prompt"]])
        ws.freeze_panes = "A2"
        ws.column_dimensions["A"].width = 10; ws.column_dimensions["B"].width = 32; ws.column_dimensions["C"].width = 10
        for col in "DEFG": ws.column_dimensions[col].width = 55
        wb.save(output_root / "MASTER_PROMPTS.xlsx")
    except Exception:
        pass

    return payload


def read_template_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        return extract_pdf_text(path)
    if ext == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(str(path)).paragraphs if p.text.strip())
    # Image template: there may be no extractable text; the caller can still
    # keep the image reference and prompts will say to use it as visual reference.
    return ""
