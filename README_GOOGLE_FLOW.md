# Step 0 + Google Flow Coloring Image Automation

## Install

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

## Run dashboard

```powershell
streamlit run app.py
```

## Google Flow workflow

1. Analyze the Master PDF.
2. Create BOOK folders + prompts.
3. Click **Open Google Flow Browser / Login**.
4. Complete Google login/security checks manually if requested.
5. Close the login browser window.
6. Click **Start Coloring Image Automation**.
7. The worker creates one Flow project per book and feeds the original coloring prompt for each page.
8. Generated downloads are converted to PNG and saved as:

```text
BOOK_01/Coloring Images/Book_1_Page1_Image.png
BOOK_01/Coloring Images/Book_1_Page2_Image.png
...
BOOK_02/Coloring Images/Book_2_Page1_Image.png
```

A matching `.prompt.txt` file is stored next to each image.

The automation does not bypass CAPTCHA, login, security challenges, or access controls. If Flow's UI changes and a selector cannot be found, it stops safely and saves a screenshot in the Step 0 output folder.


### Browser note
This build uses the installed Google Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe` when available, so Playwright's managed Chromium download is not required. Set `GOOGLE_FLOW_BROWSER` to another browser executable if needed.
