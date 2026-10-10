# InPage → PDF Converter

Urdu InPage (`.inp`) documents ko PDF me convert karne wala **free online tool** —
original **Jameel Noori Nastaleeq** font, **bold** text, font sizes, line breaks,
vertical gaps, **MCQ option spacing** aur **tables** — sab original file jaisa
preserved. InPage software ya Windows ki zaroorat nahi, mobile par bhi chalta hai.

**Live website architecture:**

```
        Browser (user)
             │
             ▼
   ┌──────────────────┐        ┌──────────────────────────┐
   │  Netlify (site/) │ proxy  │  Render (backend/)        │
   │  HTML/CSS/JS     │──────▶ │  Python + Flask + gunicorn│
   │  pdf.js preview  │ /api/* │  InPage parser (OLE2)     │
   └──────────────────┘        │  fpdf2 + HarfBuzz shaper  │
                              │  Jameel Noori Nastaleeq    │
                              └──────────────────────────┘
```

- **`site/`** — Netlify par host hota hai (static frontend, same design)
- **`backend/`** — Render par chalta hai (conversion engine; `render.yaml` ready)
- **`netlify.toml`** — `/api/*` ko backend par proxy karta hai

## Deploy kaise karein?

👉 **`README-DEPLOY.md`** kholen — GitHub upload, Render backend, Netlify website
— sab steps wahan hain (10–15 minute).

## Local test (poori website, bina deploy)

```bash
# terminal 1 — backend
cd backend && pip install -r requirements.txt
gunicorn -w 2 -b 127.0.0.1:8081 --timeout 300 wsgi:app

# terminal 2 — Netlify jaisa local server (static + /api proxy)
cd ..
python dev/local_netlify_sim.py
# -> http://localhost:8080
```

## Features

- Original Jameel Noori Nastaleeq (MIT-licensed copy bundled)
- Bold (font-weight 700 detection + synthetic bold rendering)
- Exact line breaks, vertical gaps, horizontal space runs (MCQ options)
- Tables rebuilt as bordered RTL tables (header repeat)
- Urdu digit-order correction (InPage stores digits reversed)
- Batch convert (30 files), page images fallback, 24h auto-delete
- SEO ready: meta/OG/JSON-LD, sitemap, robots, FAQ schema

## Credits & licenses

- Format research: herf / ltrc InPageToUnicode (GPL-2.0, attributed in
  `backend/inpage2pdf/inp_reader.py`), community specs from ImadC9/inpage-format
- Jameel Noori Nastaleeq font: MIT (Urdu-Nastaleeq-Font project)
- Noto Nastaliq Urdu: SIL OFL 1.1
- pdf.js (Mozilla, Apache-2.0) self-hosted in `site/static/pdfjs/`

File-level license headers dekhein. Ye tool InPage (Concept Software) ka
replacement nahin — sirf uski file format ko parh kar PDF banata hai.
