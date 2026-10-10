# InPage → PDF Converter

**Convert InPage Urdu documents (`.inp`) directly to PDF — no InPage software, no
Windows, no internet.** Extracts the text from the InPage file's internal stream,
converts it from InPage's proprietary encoding to Unicode, and typesets it with a
real **Noto Nastaliq Urdu** font using HarfBuzz complex-script shaping, so the PDF
shows properly connected Nastaliq calligraphy in any PDF viewer.

> اِن پیج دستاویزات کو براہِ راست پی ڈی ایف میں تبدیل کریں — بغیر اِن پیج سافٹ ویئر کے۔

---

## Quick start

### Option A — Web interface (recommended)

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:8080**, drag your `.inp` file(s) in, and download the PDF.
Everything runs on your own machine — no uploads anywhere.

### Option B — Command line

```bash
python cli.py mydocument.inp                    # → mydocument.pdf
python cli.py *.inp -o pdfs/ --scale 1.2        # batch convert
python cli.py file.inp --letter --numbers       # US Letter + page numbers
```

### Option C — Python API

```python
from inpage2pdf.converter import convert_file
from inpage2pdf.pdf_builder import RenderOptions

res = convert_file("story.inp", "story.pdf",
                   RenderOptions(page_size="A4", scale=1.0))
print(res.ok, res.stats, res.pdf_path)
```

---

## Kisi bhi ko dena / apni site chalana (deployment & earning)

Ye tool 100% self-contained hai — kisi bhi server par chal sakta hai.

### 1. Sab se asaan — Render (free tier se shuru, 10 minute)

1. GitHub.com par free account banayein → "New repository" ( naam: `inpage2pdf`, Private ya Public dono chalein) → is folder ki files upload kar dein (ya zip ko unzip kar ke drag & drop).
2. Render.com par free account banayein → **"New +" → "Web Service"** → apna GitHub repo connect karein.
3. Settings:
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn -w 2 -b 0.0.0.0:$PORT --timeout 300 wsgi:app`
4. **Create Web Service** dabayein — 2-3 minute me site live: `https://aapka-naam.onrender.com`
5. Test karein: apni .inp file upload kar ke dekhein. Bas! 🎉

(Railway.app bilkul isi tarah kaam karta hai.)

**Free plan ki limit:** free instance 15 min idle hone par sleep ho jata hai (pehli request thodi slow). Paid plan (~$7/mah) me hamesha on rehta hai — jab traffic aa jaye tab upgrade kar lein.

### 2. Apna domain ( naam) jorna — `inpagetopdf.com` jaisa

1. Namecheap / GoDaddy / Hostinger se domain kharidein (PKR 3,000–5,000/saal).
2. Render me apni service ke **Settings → Custom Domains** me domain add karein.
3. Jo DNS records Render dikhaye (usually `A` record + `CNAME`), wo apne registrar ke DNS panel me paste karein.
4. 1-2 ghante me `https://aapki-site.com` live — **SSL (https) automatic** included hai.

### 3. SEO — Google me dikhna (tool ke andar sab tayyar hai)

Is version me ye sab already built-in hai: SEO title + meta description (English, jo log Google par search karte hain), Open Graph / Twitter cards (social share ki image), JSON-LD structured data (WebApplication + FAQ schema), canonical URL, `sitemap.xml`, `robots.txt`, static assets ka 7-din cache (fast page = better ranking), mobile-friendly design, aur Urdu + English content dono.

Bas ye karein:
1. **Google Search Console** (search.google.com/search-console) me apni site add karein → `https://aapki-site.com/sitemap.xml` submit karein.
2. **Bing Webmaster Tools** me bhi waisa hi (free me extra traffic).
3. 2-4 hafte me Google index kar lega. "inpage to pdf", "inp to pdf converter", "urdu inpage file convert" jaise searches me aane lagega.
4. Facebook/WhatsApp par Urdu groups me tool ka link share karein — ye niche bohat demand hai, word-of-mouth se traffic aayega.

### 4. Earning (jab traffic aa jaye)

- **Google AdSense:** Search Console me traffic stabilize hone ke baad apply karein (ads.txt Render me `static/ads.txt` bana kar rakh dein). Urdu/Pakistani traffic par achha CPM milta hai.
- **Premium model:** `INPAGE2PDF_API_KEY` set kar ke sirf paying users ko `?key=...` wala link dein, ya ads-free fast version bechein.
- **Direct service:** "Paper setting / typing" ke groups me apni site ka PDF-conversion service advertise karein.

### 2. Docker (VPS / koi bhi host)
```bash
docker build -t inpage2pdf .
docker run -d -p 8080:8080 -e INPAGE2PDF_API_KEY="mera-secret-key" inpage2pdf
```

### 3. Security (zaroori for earning)
- **API key:** environment variable `INPAGE2PDF_API_KEY` set karein. Phir conversion
  sirf us link se chalegi jis me key ho: `https://aapki-site/?key=mera-secret-key`
  (page khud ba khud key yaad rakhta hai). Key hata dein to site sab ke liye khuli
  rehti hai. **Paying customers ke sath sirf ye link share karein.**
- **Rate limits:** built-in — har IP se convert/font requests per-minute capped
  hain (429), taake abuse/DoS na ho sake.
- **Upload validation:** sirf asli InPage (OLE2 `D0 CF 11 E0` magic) files aur sirf
  asli `.ttf` fonts accept hote hain; filenames sanitized; path traversal blocked.
- **Jobs:** temp me 24 ghante baad khud delete; job IDs 12-hex (guess karna namumkin).
- **HTTPS:** Render/Railway par automatic. VPS par reverse proxy (nginx/caddy) ke
  sath SSL lagayein.
- **Backups:** `converted/` folder aur `fonts/` ka backup rakhein.
- Public deployment se pehle zaroor test karein ke sab kuch kaam kar raha hai.

---

## How the tool is built — components

```
                ┌────────────────────────────────────────────────────────┐
                │                     INTERFACES (3 ways in)              │
                │   Web UI (app.py)   ·   CLI (cli.py)   ·   Python API   │
                └───────────────┬────────────────────────────────────────┘
                                │  .inp file + options
                ┌───────────────▼────────────────────────────────────────┐
                │ 1. CONTAINER READER            (olefile)                │
                │    .inp = OLE2 compound file → opens the InPage100      │
                │    stream (where InPage stores the story data).         │
                ├────────────────────────────────────────────────────────┤
                │ 2. STRUCTURE WALKER            (inp_reader.py)          │
                │    Walks the stream: 0x04-pairs = Urdu characters,      │
                │    0x0D+4 = paragraph links, bare ASCII = Latin text,    │
                │    structure regions = layout data. Recovers the        │
                │    character-run style table (font size 1/2000 pt,      │
                │    bold) that follows the story.                        │
                ├────────────────────────────────────────────────────────┤
                │ 3. CHARACTER DECODER           (mapping.py, engine.py)  │
                │    InPage's proprietary byte codes → Unicode, ~110      │
                │    ordered rules (ligature rules first). Unknown codes   │
                │    are REPORTED, never guessed.  (textfix.py repairs    │
                │    known conversion artifacts: hamza joins, spacing.)   │
                ├────────────────────────────────────────────────────────┤
                │ 4. LAYOUT ENGINE                (pdf_builder.py)        │
                │    a) style recovery — real font sizes, bold, headings  │
                │    b) table detection — repeating serial rows signal a  │
                │       table document → rebuilt as a bordered RTL table  │
                │       with header row repeated on every page            │
                │    c) otherwise — flowing paragraphs, justified, RTL    │
                ├────────────────────────────────────────────────────────┤
                │ 5. PDF TYPESETTER               (fpdf2 + HarfBuzz +     │
                │    Noto Nastaliq Urdu font)                             │
                │    Full OpenType shaping (GSUB/GPOS) so Nastaliq is     │
                │    properly connected & diagonal; Unicode bidi for      │
                │    mixed Urdu/English; embeds the font in the PDF so    │
                │    it looks identical on every device.                  │
                └────────────────────────────────────────────────────────┘
                                │
                                ▼
                    PDF (pages + borders + RTL alignment)
```

Design rule: **every stage is honest** — if the decoder meets an unknown
code or the layout engine cannot confidently detect a table, it says so
in the conversion report instead of inventing content.

## What it does — pipeline

| Stage | What happens |
|---|---|
| 1. Container | `.inp` is an **OLE2 compound document** (like old `.doc`); the tool opens it with `olefile` and reads the `InPage100` stream that holds the story data. |
| 2. Structure | Walks the stream: `0x04`-prefixed byte pairs are Urdu characters, `0x0D` + 4 bytes is a paragraph record, bare ASCII is literal Latin text, long binary regions are page-layout structure. It also recovers the **character-run style table** (font size in 1/2000 pt, bold flag) that follows the story. |
| 3. Encoding | Converts InPage's proprietary byte codes to Unicode using a 100+ rule ordered mapping (multi-byte ligature rules first). **Unrecognized codes are reported, never guessed.** Optional artifact fixes (hamza joins, missing spaces) mirror the reference converter. |
| 4. Typesetting | Shapes and paginates the Unicode text with **HarfBuzz** (the same shaper used by Chrome/Firefox/Android) and embeds **Noto Nastaliq Urdu** (SIL OFL 1.1). Headings, bold and font sizes are preserved from the InPage style runs where recoverable. |
| 5. Table reconstruction | InPage table documents (scheme-of-work sheets, date sheets, result cards) are detected by their repeating serial-row structure and **rebuilt as real bordered tables** — right-to-left column order, header row repeated on every page, content-proportional column widths. Non-table documents render as clean flowing text. |

Supported scripts: **Urdu** (primary), Arabic, Persian, Pashto, Sindhi, Punjabi
(Shahmukhi) and mixed Urdu/English content.

---

## Honest limitations (please read)

This is the only fully open, offline way to convert InPage files — but the `.inp`
format is **closed and undocumented**, so no third-party tool (including this one)
can promise 100% layout fidelity:

- **Text + basic styling is preserved** — paragraphs, order, font size, bold, headings.
- **Page geometry is not preserved** — the original page's text boxes, columns,
  images, tables and decorative objects are not re-laid-out; text re-flows into a
  clean A4/Letter document.
- **Images and graphics inside the .inp are not extracted** (they live in separate
  binary layout records).
- Rare InPage versions/streams may contain codes the mapping table doesn't cover —
  the tool reports them (`unmapped codes`) instead of guessing.
- If you need a pixel-perfect copy of an InPage page (e.g., for print proofs), the
  only guaranteed route remains InPage itself (File → Print → Microsoft Print to PDF).

## Troubleshooting

| Symptom | Meaning |
|---|---|
| "Not an InPage document (missing OLE2 signature)" | The file is another kind of `.inp` (Abaqus/EPANET engineering input), or corrupted. |
| "No InPage100 stream" | Very old/very new InPage version, or not an InPage file. |
| Unmapped-code warnings | Some characters used codes outside the known table; they're skipped and listed. |
| Everything converted but preview looks "reversed" in some PDF readers | The PDF itself is fine — that's just the reader's text-copy behavior for shaped scripts; rendering is correct. |

---

## Project layout

```
inpage2pdf/
├── app.py                     # Flask web UI  (python app.py)
├── cli.py                     # command line  (python cli.py ...)
├── requirements.txt
├── fonts/                     # Noto Nastaliq Urdu (SIL OFL 1.1)
├── templates/index.html       # web UI
├── inpage2pdf/
│   ├── inp_reader.py          # .inp OLE2 + InPage100 stream parser + style runs
│   ├── mapping.py             # InPage byte-code → Unicode rules (ordered)
│   ├── engine.py              # conversion engine + unmapped-code reporting
│   ├── textfix.py             # conversion-artifact fixes
│   ├── pdf_builder.py         # HarfBuzz-shaped Nastaliq PDF renderer
│   └── converter.py           # end-to-end orchestration
└── RESEARCH.md                # full reverse-engineering write-up of the .inp format
```

## Credits & licenses

- **`.inp` parsing, byte-mapping and artifact-fix logic** are adapted from the
  open-source **Herf** project (github.com/talhabinmunir/herf), whose mapping
  derives from **ltrc/inPageToUnicode** (IIIT-Hyderabad LTRC, **GPL-2.0**), with
  independent cross-checks against **KamalAbdali/InpageToUnicode**,
  **umer0586/unicode-inpage-converter** and **zanysoft/unicode-inpage-converter**.
- **Noto Nastaliq Urdu** font: © Google, **SIL Open Font License 1.1**.
- PDF rendering: **fpdf2** (LGPL-3.0) + **uharfbuzz** (MIT shaping engine).
- Consequently this project is distributed under the **GPL-2.0** license.

**No AI hallucination policy:** the converter never *guesses* text. Anything it
cannot decode is reported in the conversion report, so you always know exactly
what you got.
