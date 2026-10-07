"""KDP Pipeline dashboard.  Run:  streamlit run app.py   (or double-click RUN_DASHBOARD.bat)"""
import csv, io, re, shutil, zipfile, json, subprocess, sys, os
from pathlib import Path
import streamlit as st
from PIL import Image
import add_page_numbers as APN        # your numbering script, unchanged
import kdp_core as K
import kdp_series as S
import step0_processor as S0
import google_flow_automation as GFA

ICON = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
ROOT = Path("workspace"); ROOT.mkdir(exist_ok=True)
st.set_page_config(page_title="KDP Pipeline", layout="wide")
st.title("KDP Pipeline")

with st.sidebar:
    book = re.sub(r'[\\/:*?"<>|]', "_", st.text_input("Book folder name", "BOOK 1", key="book_name").strip()) or "BOOK 1"
    trim = st.number_input("Trim size (in, square)", min_value=5.0, value=8.5, step=0.5, key="trim_size")
    size_mode = st.radio(
        "Page size",
        [f"{trim} x {trim} in, no bleed", f"{trim + K.BLEED} x {trim + 2 * K.BLEED} in, with bleed"],
        key="page_size_mode",
    )
    bleed = "with bleed" in size_mode
    st.caption("Use bleed only if artwork runs to the page edge. Your page numbers sit about 0.3 in from the edge, "
               "so with bleed they land closer to the trimmed edge than KDP's 0.25 in margin.")
B = ROOT / book
NUM, OUT = B / "numbered", B / "output"
for d in (NUM, OUT):
    d.mkdir(parents=True, exist_ok=True)

# Keep widget state and results isolated per book. In particular, do NOT reuse
# a file_uploader key for application results: Streamlit owns the uploader state.
BOOK_KEY = re.sub(r"[^A-Za-z0-9_-]", "_", book) or "book"
S1_UPLOAD_KEY = f"s1_upload_{BOOK_KEY}"
S1_RESULT_KEY = f"s1_result_{BOOK_KEY}"
S2_UPLOAD_KEY = f"s2_upload_{BOOK_KEY}"
S3_UPLOAD_KEY = f"s3_upload_{BOOK_KEY}"
INT_RESULT_KEY = f"int_result_{BOOK_KEY}"
COV_RESULT_KEY = f"cov_result_{BOOK_KEY}"
S0_PDF_KEY = f"s0_master_pdf_{BOOK_KEY}"
S0_TEMPLATE_KEY = f"s0_story_template_{BOOK_KEY}"
S0_RESULT_KEY = f"s0_result_{BOOK_KEY}"
S0_IMAGE_CMD_KEY = f"s0_image_cmd_{BOOK_KEY}"
S0_OLLAMA_MODEL_KEY = f"s0_ollama_model_{BOOK_KEY}"
S0_OLLAMA_URL_KEY = f"s0_ollama_url_{BOOK_KEY}"
S0_FLOW_START_BOOK_KEY = f"s0_flow_start_book_{BOOK_KEY}"
S0_FLOW_START_PAGE_KEY = f"s0_flow_start_page_{BOOK_KEY}"
S0_FLOW_END_BOOK_KEY = f"s0_flow_end_book_{BOOK_KEY}"
S0_FLOW_END_PAGE_KEY = f"s0_flow_end_page_{BOOK_KEY}"

def show(results):
    for level, message in results or []:
        st.write(f"{ICON.get(level, '•')} {message}")
t0, t1, t2, t3, t4 = st.tabs(["Step 0 · Master PDF → Prompts", "Step 1 · Number pages", "Step 2 · Interior PDF", "Step 3 · Cover PDF", "Step 4 · Series preflight + fix"])

# ---------------- Step 0 ----------------
with t0:
    st.subheader("Step 0 — Master PDF → Story + Coloring Prompts")
    st.write("Attach your Master Prompt Pack and a Story Image Template. Step 0 reads the Master PDF locally, detects the books and page scenes, creates per-book folders, writes 2–3 sentence story summaries and story-image/coloring prompts, then can generate the story images into each BOOK/Story Images folder.")
    st.info("No cloud API is required for this step. The Master PDF content is extracted locally and the prompt files are generated on your PC.")

    master_up = st.file_uploader(
        "1. Attach Master PDF",
        type=["pdf"],
        accept_multiple_files=False,
        key=S0_PDF_KEY,
    )
    template_up = st.file_uploader(
        "2. Attach Story Image Template (recommended)",
        type=["txt", "md", "pdf", "docx", "png", "jpg", "jpeg"],
        accept_multiple_files=False,
        key=S0_TEMPLATE_KEY,
        help="Use a text/PDF/DOCX template for written style rules, or PNG/JPG if the template is a visual reference.",
    )

    if master_up:
        st.caption(f"Master PDF: {master_up.name}")
    if template_up:
        st.caption(f"Story Image Template: {template_up.name}")

    st.markdown("### Local AI")
    ollama_url = st.text_input("Ollama URL", value="http://localhost:11434", key=S0_OLLAMA_URL_KEY)
    ollama_model = st.text_input("Ollama model", value="qwen2.5vl:3b", key=S0_OLLAMA_MODEL_KEY)
    use_ollama = st.checkbox("Use Ollama for 2–3 sentence summaries + story-image prompts", value=True, key=f"s0_use_ollama_{BOOK_KEY}")
    ctest1, ctest2 = st.columns([1, 3])
    if ctest1.button("Test Ollama", key=f"s0_test_ollama_{BOOK_KEY}"):
        try:
            reply = S0.test_ollama(model=ollama_model.strip(), base_url=ollama_url.strip())
            st.success(f"Ollama connected. {ollama_model} replied: {reply}")
        except Exception as e:
            st.error(f"Ollama test failed: {e}")
    ctest2.caption("Uses your local Ollama installation. No cloud API or credits are used.")

    if st.button("Analyze Master PDF", disabled=not master_up, key=f"s0_analyze_{BOOK_KEY}"):
        try:
            tmp = B / "step0_input"; tmp.mkdir(exist_ok=True)
            master_path = tmp / master_up.name
            master_path.write_bytes(master_up.getvalue())
            template_path = None
            template_text = ""
            template_name = ""
            if template_up:
                template_path = tmp / template_up.name
                template_path.write_bytes(template_up.getvalue())
                template_name = template_up.name
                template_text = S0.read_template_file(template_path)
            raw = S0.extract_pdf_text(master_path)
            books = S0.parse_books(raw)
            if not books:
                raise ValueError("No Book X sections were detected.")
            st.session_state[S0_RESULT_KEY] = {
                "master_path": str(master_path),
                "template_path": str(template_path) if template_path else "",
                "template_text": template_text,
                "template_name": template_name,
                "books": books,
            }
            st.success(f"Detected {len(books)} book(s) and {sum(len(x['pages']) for x in books)} page prompt(s).")
        except Exception as e:
            st.error(f"Could not analyze Master PDF: {e}")

    s0r = st.session_state.get(S0_RESULT_KEY)
    if s0r:
        books = s0r["books"]
        st.write("### Analysis")
        st.table([{"Book": b["book"], "Title": b["title"], "Pages": len(b["pages"]), "Setting": b.get("setting", "")} for b in books])
        if s0r.get("template_name"):
            if s0r.get("template_text"):
                with st.expander("Story Image Template text", expanded=False):
                    st.text(s0r["template_text"][:12000])
            else:
                st.caption("The template is an image reference; its file will be retained with the generated book folders.")

        preview_book = st.selectbox(
            "Preview book",
            books,
            format_func=lambda b: f"Book {b['book']} — {b['title']}",
            key=f"s0_preview_book_{BOOK_KEY}",
        )
        preview_page = st.selectbox(
            "Preview page",
            preview_book["pages"],
            format_func=lambda x: f"Page {x['page']:02d} — {x['scene'][:100]}",
            key=f"s0_preview_page_{BOOK_KEY}",
        )
        with st.expander("Preview generated Step 0 content", expanded=True):
            st.write("**Story summary (2–3 sentences)**")
            st.text(S0.make_story_summary(preview_book, preview_page["scene"], preview_page["page"]))
            st.write("**Story image prompt**")
            preview_with_summary = dict(preview_page)
            preview_with_summary["story_summary"] = S0.make_story_summary(preview_book, preview_page["scene"], preview_page["page"])
            st.text(S0.make_story_prompt(preview_book, preview_with_summary, s0r.get("template_text", ""), s0r.get("template_name", "")))
            st.write("**Coloring image prompt**")
            st.text(preview_page["coloring_prompt"][:12000])

        if st.button("Create BOOK folders + all prompts", key=f"s0_generate_{BOOK_KEY}"):
            try:
                out0 = B / "STEP_0_OUTPUT"
                if out0.exists():
                    shutil.rmtree(out0)
                if use_ollama:
                    with st.spinner(f"Ollama is generating summaries and story prompts for all pages using {ollama_model}..."):
                        payload = S0.process_master_with_ollama(
                            Path(s0r["master_path"]), out0, s0r.get("template_text", ""),
                            s0r.get("template_name", ""),
                            Path(s0r["template_path"]) if s0r.get("template_path") else None,
                            model=ollama_model.strip(), base_url=ollama_url.strip(),
                        )
                else:
                    payload = S0.process_master(
                        Path(s0r["master_path"]), out0, s0r.get("template_text", ""),
                        s0r.get("template_name", ""),
                        Path(s0r["template_path"]) if s0r.get("template_path") else None,
                    )
                st.session_state[S0_RESULT_KEY]["generated"] = True
                st.session_state[S0_RESULT_KEY]["output"] = str(out0)
                engine = f"Ollama {ollama_model}" if use_ollama else "local rule-based generator"
                st.success(f"Created {payload['book_count']} BOOK folders and {payload['page_count']} page records using {engine}.")
            except Exception as e:
                st.error(f"Could not generate Step 0 output: {e}")

        out0 = B / "STEP_0_OUTPUT"
        if out0.exists() and (out0 / "MASTER_PROMPTS.json").exists():
            st.markdown("### Story image generation")
            st.caption("Prompts are created first. Then Step 0 can generate one story image for every page and place them in BOOK_XX/Story Images. This uses your local image-generation command; no paid cloud image API is built into Step 0.")
            image_cmd = st.text_area(
                "Local image-generation command",
                value=st.session_state.get(S0_IMAGE_CMD_KEY, ""),
                key=S0_IMAGE_CMD_KEY,
                height=90,
                help="Your command must create the image at {out}. Available placeholders: {prompt_file}, {ref}, {out}. Example: python local_image_generator.py --prompt {prompt_file} --ref {ref} --out {out}",
            )
            st.caption("If your Story Image Template is a PNG/JPG, it is passed through {ref}. If it is text/PDF/DOCX, its extracted style text is already included in the prompt.")
            if st.button("Generate Story Images for ALL Books", disabled=not image_cmd.strip(), key=f"s0_generate_story_images_{BOOK_KEY}"):
                try:
                    payload = __import__("json").loads((out0 / "MASTER_PROMPTS.json").read_text(encoding="utf-8"))
                    with st.spinner("Generating story images for all books..."):
                        result = S0.generate_story_images(out0, payload, provider="command", command=image_cmd, image_ext=".png")
                    if result["failed"]:
                        st.warning(f"Generated {result['count']} of {result['total']} story images. {len(result['failed'])} failed. See STORY_IMAGE_GENERATION.json for details.")
                    else:
                        st.success(f"Generated all {result['count']} story images successfully.")
                except Exception as e:
                    st.error(f"Could not generate story images: {e}")

            story_images = sorted(out0.glob("BOOK_*/Story Images/page*_story_image.*"))
            if story_images:
                st.success(f"Story Images folder contains {len(story_images)} generated image(s).")
                st.image(str(story_images[0]), width=280, caption=story_images[0].name)

        if out0.exists() and (out0 / "MASTER_PROMPTS.json").exists():
            st.write("### Step 0 output")
            book_dirs = sorted(p for p in out0.glob("BOOK_*") if p.is_dir())
            st.write(f"**{len(book_dirs)} BOOK folders created.**")
            st.code("\n".join(p.name for p in book_dirs))
            z = io.BytesIO()
            with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in out0.rglob("*"):
                    if p.is_file():
                        zf.write(p, p.relative_to(out0))
            st.download_button("Download Step 0 output (ZIP)", z.getvalue(), "STEP_0_OUTPUT.zip", key=f"download_step0_{BOOK_KEY}")
            for fn in ["MASTER_PROMPTS.xlsx", "MASTER_PROMPTS.csv", "MASTER_PROMPTS.json", "MASTER_PROMPTS.txt"]:
                fp = out0 / fn
                if fp.exists():
                    st.download_button(f"Download {fn}", fp.read_bytes(), fn, key=f"download_{fn}_{BOOK_KEY}")


        # ---------------- Google Flow coloring-image automation ----------------
        if out0.exists() and (out0 / "MASTER_PROMPTS.json").exists():
            st.markdown("### Google Flow — Autonomous Coloring Image Generation")
            st.caption(
                "Uses the original coloring-image prompts from MASTER_PROMPTS.json. "
                "One Google Flow project is created per book, then pages are generated in sequence. "
                "Images are saved as Book_X_PageN_Image.png. Google login/CAPTCHA/security checks are always completed manually."
            )

            flow_progress_file = out0 / "GOOGLE_FLOW_PROGRESS.json"
            progress = {}
            if flow_progress_file.exists():
                try:
                    progress = json.loads(flow_progress_file.read_text(encoding="utf-8"))
                except Exception:
                    progress = {}

            payload_for_flow = json.loads((out0 / "MASTER_PROMPTS.json").read_text(encoding="utf-8"))
            max_book = max([int(r["book"]) for r in payload_for_flow.get("records", [])] or [1])
            pages_by_book = {}
            for r in payload_for_flow.get("records", []):
                pages_by_book.setdefault(int(r["book"]), []).append(int(r["page"]))
            max_page = max(pages_by_book.get(1, [1]))

            fc1, fc2 = st.columns(2)
            with fc1:
                flow_start_book = st.number_input("Start Book", 1, max_book, 1, key=S0_FLOW_START_BOOK_KEY)
                flow_start_page = st.number_input("Start Page", 1, max_page, 1, key=S0_FLOW_START_PAGE_KEY)
            with fc2:
                flow_end_book = st.number_input("End Book", 1, max_book, max_book, key=S0_FLOW_END_BOOK_KEY)
                flow_end_page = st.number_input("End Page (0 = all pages in final book)", 0, 999, 0, key=S0_FLOW_END_PAGE_KEY)

            st.code(f"Output: {out0 / 'BOOK_XX' / 'Coloring Images'}\nExample: Book_1_Page1_Image.png")

            fp1, fp2, fp3 = st.columns(3)
            if fp1.button("Open Google Flow Browser / Login", key=f"flow_login_{BOOK_KEY}"):
                try:
                    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    proc = subprocess.Popen(
                        [sys.executable, str(Path(__file__).with_name("google_flow_automation.py")),
                         "--root", str(out0), "--login"],
                        creationflags=flags,
                    )
                    (out0 / "GOOGLE_FLOW_LOGIN_PID.txt").write_text(str(proc.pid), encoding="utf-8")
                    st.success("Google Flow browser opened. Log in manually if required, then close that browser window.")
                except Exception as e:
                    st.error(f"Could not open Google Flow browser: {e}")

            if fp2.button("Start Coloring Image Automation", key=f"flow_start_{BOOK_KEY}"):
                try:
                    end_page_arg = int(flow_end_page) if int(flow_end_page) > 0 else None
                    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    proc = subprocess.Popen(
                        [sys.executable, str(Path(__file__).with_name("google_flow_automation.py")),
                         "--root", str(out0),
                         "--start-book", str(int(flow_start_book)),
                         "--start-page", str(int(flow_start_page)),
                         "--end-book", str(int(flow_end_book)),
                         "--end-page", str(end_page_arg or 999999)],
                        creationflags=flags,
                    )
                    (out0 / "GOOGLE_FLOW_AUTOMATION_PID.txt").write_text(str(proc.pid), encoding="utf-8")
                    st.success(f"Automation started (PID {proc.pid}). Keep the Google Flow browser open.")
                except Exception as e:
                    st.error(f"Could not start Google Flow automation: {e}")

            if fp3.button("Stop Automation", key=f"flow_stop_{BOOK_KEY}"):
                pid_file = out0 / "GOOGLE_FLOW_AUTOMATION_PID.txt"
                if pid_file.exists():
                    try:
                        pid = int(pid_file.read_text(encoding="utf-8").strip())
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
                        else:
                            os.kill(pid, 15)
                        st.warning(f"Stopped automation process {pid}.")
                    except Exception as e:
                        st.warning(f"Could not stop automation: {e}")

            if progress:
                status = progress.get("status", "unknown")
                if status == "completed":
                    st.success(f"Google Flow automation completed: {progress.get('completed', 0)} image(s).")
                elif status == "error":
                    st.error(f"Google Flow stopped: {progress.get('error', 'Unknown error')}")
                elif status in ("running", "starting", "waiting_for_login"):
                    st.info(f"Google Flow status: {status}. Completed {progress.get('completed', 0)} / {progress.get('total', '?')}. Current: {progress.get('current', '—')}")
                    try:
                        st.progress(min(1.0, float(progress.get("completed", 0)) / max(1, float(progress.get("total", 1)))))
                    except Exception:
                        pass
                    st.caption(progress.get("message", "Automation is running in the browser."))
                    st.caption("Refresh the Streamlit page to update the live status if it does not refresh automatically.")

            image_files = sorted((out0).glob("BOOK_*/Coloring Images/Book_*_Page*_Image.png"))
            if image_files:
                st.success(f"Coloring images saved: {len(image_files)}")
                st.image(str(image_files[-1]), width=300, caption=image_files[-1].name)

# ---------------- Step 1 ----------------
with t1:
    st.write("Attach your page images (names like `page1_image.jpg`, `Page1_text.png`). Numbers are added with your "
             "add_page_numbers.py code. Originals are kept, and the numbered copies go to a separate folder.")
    ups = st.file_uploader(
        "Page images",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=S1_UPLOAD_KEY,
    )
    if st.button("Add page numbers", disabled=not ups, key=f"add_numbers_{BOOK_KEY}"):
        src = B / "originals"
        src.mkdir(exist_ok=True)

        # Rebuild numbered/ from the current upload every time.
        # This prevents pages removed from a later upload (for example page02)
        # from remaining as stale files from an earlier run.
        for old_file in NUM.iterdir():
            if old_file.is_file():
                try:
                    old_file.unlink()
                except Exception as e:
                    st.warning(f"Could not remove old numbered file {old_file.name}: {e}")

        done, skipped, bar = [], [], st.progress(0.0)

        for k, up in enumerate(ups, 1):
            n = APN.get_page_number(up.name)
            if n is None:
                skipped.append(up.name)
            else:
                f = src / up.name
                try:
                    f.write_bytes(up.getvalue())
                    APN.add_page_number(f, NUM / up.name, n)
                    done.append(up.name)
                except Exception as e:
                    skipped.append(f"{up.name} ({e})")
            bar.progress(k / len(ups))

        st.session_state[S1_RESULT_KEY] = (done, skipped)

    done, skipped = st.session_state.get(S1_RESULT_KEY, ([], []))
    if done: st.success(f"{len(done)} numbered image(s) saved in {NUM.resolve()}")
    if skipped: st.warning("Skipped (name must look like page1_image.jpg or page1_text.png): " + ", ".join(skipped))
    existing = sorted(p for p in NUM.iterdir() if p.is_file())
    if existing:
        st.caption(f"{len(existing)} file(s) in the numbered folder")
        st.image(str(existing[0]), width=260, caption=existing[0].name)
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as zf:
            for p in existing: zf.write(p, p.name)
        st.download_button(
            "Download numbered images (zip)",
            z.getvalue(),
            "numbered.zip",
            key=f"download_numbered_{BOOK_KEY}",
        )

# ---------------- Step 2 ----------------
with t2:
    src_mode = st.radio("Images to merge", ["Numbered images from Step 1", "Attach images"], horizontal=True)
    if src_mode == "Attach images":
        SRC = B / "interior_src"; SRC.mkdir(exist_ok=True)
        ups2 = st.file_uploader(
            "Interior page images",
            type=["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"],
            accept_multiple_files=True,
            key=S2_UPLOAD_KEY,
        )
        names = {u.name for u in ups2 or []}
        for p in SRC.iterdir():
            if p.name not in names: p.unlink()
        for u in ups2 or []: (SRC / u.name).write_bytes(u.getvalue())
        pool = [p for p in SRC.iterdir() if p.is_file()]
    else:
        pool = [p for p in NUM.iterdir() if p.is_file()]
    seq, ignored = K.order_pages(pool)
    pw, ph = trim + (K.BLEED if bleed else 0), trim + (2 * K.BLEED if bleed else 0)
    blank1 = st.checkbox("Add a blank page at the start (before the first interior page)", False)
    if ignored: st.warning("Ignored (not PAGEn_TEXT / PAGEn_IMAGE): " + ", ".join(ignored))
    if seq:
        dpis, crop = [], []
        for p in seq:
            with Image.open(p) as im: w, h = im.size
            dpis.append(min(w / pw, h / ph))
            if abs(w / h - pw / ph) > .02: crop.append(p.name)
        st.write(f"**{len(seq)}** pages in order (text first, then image for each page number). Lowest source resolution: **{min(dpis):.0f} DPI** at {pw} x {ph} in.")
        if min(dpis) < 300: st.warning("Some images are below 300 DPI at this page size. They are enlarged to 300 DPI, but they will not gain detail.")
        if crop: st.warning(f"These images have a different shape from the page and will be cropped to fill it: {crop[:8]}")
        with st.expander("Page order"): st.table([{"Page": i, "File": n} for i, n in enumerate((["(blank page)"] if blank1 else []) + [p.name for p in seq], 1)])
    pad = st.checkbox("Add blank pages at the end if under 24 (KDP minimum)", True)
    if st.button("Merge into KDP_INTERIOR.pdf", disabled=not seq, key=f"merge_interior_{BOOK_KEY}"):
        try:
            bar = st.progress(0.0)
            items = ([(None, "", "image", None)] if blank1 else []) + [
                (p, "", "image", None) for p in seq
            ]
            out = K.build_interior(
                items,
                OUT / "KDP_INTERIOR.pdf",
                trim,
                bleed,
                bar.progress,
                pad,
                "KDP_INTERIOR",
            )
            st.session_state[INT_RESULT_KEY] = K.validate(
                out.read_bytes(), "interior", trim, 0
            )
            st.success(
                f"Saved {out.resolve()} ({len(seq) + int(blank1)} pages before any padding to 24)"
            )
        except Exception as e:
            st.error(f"Could not build the interior PDF: {e}")

    if (OUT / "KDP_INTERIOR.pdf").exists():
        st.download_button(
            "Download KDP_INTERIOR.pdf",
            (OUT / "KDP_INTERIOR.pdf").read_bytes(),
            "KDP_INTERIOR.pdf",
            key=f"download_interior_{BOOK_KEY}",
        )
        show(st.session_state.get(INT_RESULT_KEY, []))

# ---------------- Step 3 ----------------
with t3:
    mode = st.radio("Output", ["Two-page PDF: front, then back (like MERGE_TWO_IMAGES_ONE_PDF)", "Single-page KDP wrap: back + spine + front"])
    st.caption("KDP's paperback cover upload wants ONE page with back cover, spine and front cover together. "
               "Use the single-page wrap for KDP; the two-page PDF is for your own use.")
    ups3 = st.file_uploader(
        "Front_Cover_Page and Back_Cover_Page (png or jpg)",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=S3_UPLOAD_KEY,
    )
    by = {Path(u.name).stem.lower(): u for u in ups3 or []}
    ok3 = len(ups3 or []) == 2 and set(by) == {"front_cover_page", "back_cover_page"}
    if ups3 and not ok3: st.error("Attach exactly two files named Front_Cover_Page and Back_Cover_Page (.png, .jpg or .jpeg).")
    wrap = mode.startswith("Single")
    if wrap:
        c1, c2, c3 = st.columns(3)
        pages = c1.number_input("Interior page count", K.MIN_PAGES, K.MAX_PAGES, 24)
        paper = c2.selectbox("Paper / ink", list(K.PAPER))
        ov = c3.number_input("Spine width override (in, 0 = auto; paste KDP's figure)", min_value=0.0, value=0.0, step=0.001, format="%.4f")
        barcode = c1.selectbox("Keep barcode zone clear at", ["left", "right", "none"])
        spc = c2.color_picker("Spine colour", "#ffffff")
        st.info(f"Spine {K.spine_width(pages, paper, ov):.4f} in · cover {K.cover_size(trim, K.spine_width(pages, paper, ov))[0]:.4f} x {trim + 2 * K.BLEED:.3f} in")
    if st.button("Merge cover files", disabled=not ok3, key=f"merge_cover_{BOOK_KEY}"):
        try:
            CS = B / "cover_src"
            shutil.rmtree(CS, ignore_errors=True)
            CS.mkdir()

            f = CS / by["front_cover_page"].name
            bk = CS / by["back_cover_page"].name
            f.write_bytes(by["front_cover_page"].getvalue())
            bk.write_bytes(by["back_cover_page"].getvalue())

            if wrap:
                out = K.build_cover(
                    f, bk, OUT / "KDP_COVER.pdf", pages, paper, trim,
                    ov, spc, "", "#000000", barcode
                )
                st.session_state[COV_RESULT_KEY] = K.validate(
                    out.read_bytes(), "cover", trim, pages, paper, ov, barcode
                )
            else:
                out = K.build_interior(
                    [(f, "", "image", None), (bk, "", "image", None)],
                    OUT / "FRONT_BACK_COVER.pdf",
                    trim, bleed, None, False, "FRONT_BACK_COVER"
                )
                st.session_state[COV_RESULT_KEY] = [
                    (
                        "PASS",
                        f"Page 1 = {f.name}, page 2 = {bk.name}, "
                        f"each {pw:.3f} x {ph:.3f} in at 300 DPI"
                    )
                ]

            st.success(f"Saved {out.resolve()}")
        except Exception as e:
            st.error(f"Could not build the cover PDF: {e}")

    for n in ("KDP_COVER.pdf", "FRONT_BACK_COVER.pdf"):
        if (OUT / n).exists():
            st.download_button(
                f"Download {n}",
                (OUT / n).read_bytes(),
                n,
                key=f"download_{n}_{BOOK_KEY}",
            )

    show(st.session_state.get(COV_RESULT_KEY, []))

# ---------------- Step 4 ----------------
with t4:
    st.caption("Series preflight and fixer from your own scripts. It looks for KDP_COVER.pdf + KDP_INTERIOR.pdf in each book folder (or its output sub-folder).")
    root = Path(st.text_input("Series folder (one sub-folder per book)", str(ROOT.resolve())))
    c1, c2 = st.columns(2)
    p4, bc4 = c1.selectbox("Paper / ink", list(K.PAPER), key="p4"), c2.selectbox("Barcode zone", ["left", "right", "none"], key="bc4")
    if st.button("Run preflight", key=f"run_preflight_{BOOK_KEY}"):
        books = (
            sorted((p for p in root.iterdir() if p.is_dir()), key=S.natural)
            if root.is_dir()
            else []
        )
        rows = [r for b in books if (r := S.preflight(b, trim, p4, bc4))]
        st.session_state[f"pre_{BOOK_KEY}"] = rows
        if rows:
            rep = root / "KDP_PIPELINE_OUTPUT"; rep.mkdir(exist_ok=True)
            with (rep / "KDP_PREFLIGHT.csv").open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
            st.success(f"Report saved to {rep / 'KDP_PREFLIGHT.csv'}")
        else: st.warning("No book folders with both KDP_COVER.pdf and KDP_INTERIOR.pdf were found.")
    rows = st.session_state.get(f"pre_{BOOK_KEY}", [])
    if rows: st.table(rows)
    bad = [r["Book"] for r in rows if "FAIL" in (r["Cover"], r["Interior"])]
    if bad:
        st.warning("Your fixer is set for 8.5 x 8.5 in, B&W white paper, no bleed. It stretches the cover to the exact size (artwork distorts if the shape is off) "
                   "and shrinks every interior page into the margin box, so the art ends up smaller with white borders. Use it only on books that failed.")
        pick = st.multiselect("Books to fix", bad, default=bad)
        if st.button("Fix selected into KDP_READY", key=f"fix_selected_{BOOK_KEY}"):
            after = []
            for name in pick:
                try:
                    o = S.fix_book(root / name, root / "KDP_READY")
                    if abs((o[2] / o[3]) / (o[0] / o[1]) - 1) > .02: st.warning(f"{name}: cover shape changed by {abs((o[2] / o[3]) / (o[0] / o[1]) - 1) * 100:.0f}%, so the artwork is stretched.")
                    after.append(S.preflight(root / "KDP_READY" / name, 8.5, "B&W - white", bc4))
                except Exception as e: st.error(f"{name}: {e}")
            if after: st.write("After fixing (saved in KDP_READY):"); st.table([r for r in after if r])
