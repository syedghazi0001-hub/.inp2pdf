# 🚀 Website Live Karne Ki Mukammal Guide (GitHub + Render + Netlify)

Ye guide aap ko **step-by-step** le jati hai — zero se live public website tak.
Har step me exact click/commands diye hain. Aaram se, order me karein.

**Total time:** 15–20 minute (first time)

---

## Cheezein jo pehle se ready hain (is ZIP me)

| File/Folder | Kaam |
|---|---|
| `site/` | Website ka frontend (Netlify par host hoga) — bilkul wahi design |
| `backend/` | Conversion engine (Render par chalega) — poora Python app + fonts |
| `netlify.toml` | Netlify config — `/api/*` ko backend par proxy karta hai |
| `render.yaml` | Render config — backend ka one-click blueprint |
| `dev/local_netlify_sim.py` | Deploy se pehle poori website locally test karne ka tool |

---

## STEP 1 — GitHub par project daalein (5 minute)

1. **GitHub.com** par free account banayein (agar nahin hai).
2. Right-upar **`+` → New repository** dabayein.
   - Name: `inpage2pdf` (ya jo chahein)
   - **Public** select karein (Private bhi chalega, lekin Netlify/Render dono ko access dena padega)
   - "Add a README" ka tick **hata** dein (humara README pehle se hai)
   - **Create repository** dabayein
3. Page par "**uploading an existing file**" link par click karein.
4. Is ZIP ke **saari files/folders drag** kar dein (ZIP extract kar ke — `site/`,
   `backend/`, `netlify.toml`, `render.yaml`, `README.md`, `README-DEPLOY.md`, `dev/`).
   > Windows tip: folder ke andar ki cheezein select karein, folder ke bahar se nahin —
   > repo ke root me `site/` aur `backend/` directly honi chahiye.
5. **Commit changes** dabayein.

---

## STEP 2 — Backend live karein (Render, free) (5 minute)

1. **Render.com** par free account banayein (GitHub se sign-in karein).
2. Dashboard par **"New +" → "Web Service"** dabayein.
3. Apna GitHub account connect karein → `inpage2pdf` repo select karein.
4. Settings (ya to ye likhein, ya `render.yaml` se Blueprint use karein — dono same):
   - **Name:** `inpage2pdf-backend` (koi bhi)
   - **Region:** Singapore / Frankfurt (Pakistan ke sab se qareeb)
   - **Branch:** `main`
   - **Root Directory:** `backend`
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn -w 2 -b 0.0.0.0:$PORT --timeout 300 wsgi:app`
   - **Instance Type:** `Free`
5. **Deploy Web Service** dabayein — 3–5 minute me green ho jaye ga.
6. Upar URL milega, maslan: `https://inpage2pdf-backend-a1b2.onrender.com`
7. **Test:** us URL ke aakhir me `/api/health` lagaa kar browser me kholein —
   `{"status":"ok",...}` dikhna chahiye. ✅ (Pehli baar 1–2 minute lag sakte hain)
8. **Ye URL copy kar ke kahin note kar lein** — Step 3 me chahiye.

> Free plan ki limit: 15 minute idle hone par backend sota hai; agli request par
> 30–60 second me jaag jata hai (website auto-retry kar leti hai, user ko bas ek
> dafa "Server jaag raha hai…" dikh sakta hai). Traffic aane par $7/month par
> upgrade kar lein.

---

## STEP 3 — netlify.toml me backend URL daalein (1 minute)

1. GitHub repo me **`netlify.toml`** file kholein (pencil icon → edit).
2. `YOUR-BACKEND-ON-RENDER` ko apne asli Render URL se replace karein:
   ```toml
   to = "https://inpage2pdf-backend-a1b2.onrender.com/api/:splat"
   ```
3. **Commit changes** (Netlify baad me khud pull kar lega).

> Optional (SEO): `site/index.html` aur `site/sitemap.xml` me
> `YOUR-SITE.netlify.app` ko apne asli Netlify URL se replace kar dein
> (Step 4 ke baad pata chalega). Zaroori nahin — sirf search engines ke liye behtar.

---

## STEP 4 — Website live karein (Netlify, free) (5 minute)

1. **Netlify.com** par free account banayein (GitHub se sign-in).
2. **"Add new site" → "Import an existing project"**.
3. **GitHub** select karein → `inpage2pdf` repo → authorize karein.
4. Settings:
   - **Branch:** `main`
   - **Build command:** (khali chhor dein)
   - **Publish directory:** `site`
   (Agar `netlify.toml` parh liya jaye to ye khud bhar jate hain — phir bhi check kar lein.)
5. **Deploy site** dabayein — 1–2 minute me live:
   `https://aapna-naam.netlify.app` 🎉
6. **TEST (zaroori):** apni koi `.inp` file upload kar ke convert + download
   karein. Preview me PDF nazar aani chahiye aur download button kaam karna
   chahiye.

> Site ka naam change karna ho: Site settings → **Change site name**.

---

## STEP 5 — Apna domain jorna (optional)

1. Namecheap / GoDaddy / Hostinger se domain lein (PKR 3,000–5,000/saal).
2. Netlify → Site settings → **Domain management → Add custom domain**.
3. Jo DNS records Netlify dikhaye (ek `A` record + ek `CNAME`), wo apne
   registrar ke DNS panel me daal dein.
4. 1–2 ghante me `https://aapki-site.com` live — **SSL (https) automatic**.
5. Phir `site/index.html` + `site/sitemap.xml` me `YOUR-SITE.netlify.app`
   ko apne domain se replace kar ke GitHub par commit kar dein (SEO ke liye).

---

## STEP 6 — Google me dikhna (SEO)

1. **Google Search Console** kholein → apni site add karein →
   `https://aapki-site.com/sitemap.xml` submit karein.
2. **Bing Webmaster Tools** me bhi wohi karein (free extra traffic).
3. 2–4 hafte me Google index kar dega — "inpage to pdf", "inp to pdf converter"
   jaise searches me aane lagega (SEO meta/FAQ/schema sab pehle se laga hua hai).
4. Facebook/WhatsApp ke Urdu teachers/paper-setting groups me site share karein.

---

## Troubleshooting

| Masla | Hal |
|---|---|
| Convert par "Server jaag raha hai…" | Free backend sleep se jaag raha hai — 15–30 second rukein, auto-retry ho ga |
| `502 Bad Gateway` | `netlify.toml` ka backend URL ghalat hai — Step 3 dobara karein |
| `{"error": ...}` API par | Render service check karein (deploy green hai?) — `https://...onrender.com/api/health` kholein |
| Netlify par "Page not found" | Publish directory `site` hai? (Step 4.4) |
| Render deploy fail | Build/start commands bilkul waise hain jaise Step 2.4 me? Root directory `backend`? |
| Pehli request bohat slow | Free tier cold start — normal hai; permanent hal $7/mah upgrade |

---

## Deploy se PEHLE local test (bina internet)

```bash
# terminal 1 — backend chalayein
cd backend
pip install -r requirements.txt
gunicorn -w 2 -b 127.0.0.1:8081 --timeout 300 wsgi:app

# terminal 2 — "Netlify simulator" (static site + /api proxy)
cd ..
python dev/local_netlify_sim.py
```
Browser me `http://localhost:8080` kholein — **bilkul wahi website** chalegi
jo production me chalegi (upload → convert → preview → download). ✅

---

## Security recap (pehle se on)

- Rate limits har IP par (convert 60/15min, uploads 10/hr)
- Sirf asli InPage (OLE2) + asli .ttf files accept
- Path-traversal blocked, filenames sanitized
- Jobs 24 ghante baad auto-delete
- Optional API key: Render me environment variable `INPAGE2PDF_API_KEY` set
  karein → phir conversion sirf `?key=...` link se chalegi (paying users ke liye)
