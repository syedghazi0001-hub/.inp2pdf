# How InPage Works — Complete Technical Research Notes

*Everything documented below was researched and cross-verified while building this
converter. Sources: InPage's own behavior (via security-research reverse
engineering), four independent open-source conversion projects, the CVE-2017-12824
exploit analysis by QiAnXin Threat Intelligence, and direct binary inspection of a
real `.inp` file.*

---

## 1. What InPage is

| Fact | Detail |
|---|---|
| Product | InPage — word processor / desktop-publishing software for right-to-left languages |
| Publisher | Concept Software Pvt. Ltd. (Dehradun, India) |
| First release | 1994 |
| Platforms | Windows only (a Mac build existed briefly; none for Linux) |
| Purpose | Urdu, Arabic, Persian, Pashto, Sindhi, Punjabi (Shahmukhi), Saraiki, Balti, Balochi, Kashmiri, Burushaski, Shina + English |
| Dominant use | The *de-facto* standard for Urdu newspapers, books, magazines, invitation cards, and official documents in Pakistan — for 30 years |
| Famous font | **Noori Nastaliq** — the Nastaliq typeface InPage ships with, originally developed in 1981 in collaboration with Monotype Imaging |

InPage matters culturally: virtually the entire Urdu print industry of the 1990s–2010s
produced `.inp` files. That's why so many people hold `.inp` files they can no longer
open (InPage is paid, Windows-only, and has no free viewer).

## 2. The `.inp` file format — container level

`.inp` is **not** a simple binary blob. Measured across real-world files,
**~70% of `.inp` files are Microsoft OLE2 Compound Documents** — the exact same
container format as legacy `.doc` (Word 97) and `.xls` files:

```
Magic bytes (first 8):  D0 CF 11 E0 A1 B1 1A E1   ← "DOCFILE" signature
```

Inside the OLE2 container, InPage stores named **streams**. On a real file:

| Stream | Contents |
|---|---|
| `InPage100` | **The story**: all text (in InPage's proprietary encoding), paragraph records, and the character-run style table. This is the stream everything is extracted from. |
| `DocumentInfo` | In analyzed files: all zeros (metadata placeholder). |
| (other streams) | Page-layout, graphics and master-page data — proprietary, not needed for text extraction. |

The remaining ~30% of `.inp` files (older versions or non-standard saves) use
other layouts; some files start with bytes `2A 48`. A tiny fraction of `.inp`
files floating around are not InPage at all — the extension is shared with
**Abaqus** and **EPANET** engineering software (the converter detects and rejects these).

### 2.1 Security footnote: CVE-2017-12824

InPage became a genuine APT (state-attack) target. CVE-2017-12824 is an
out-of-bounds read in InPage's `InPage100` parser: the stream contains
**typed records** — a two-byte type selector (`Type1`, e.g. `0x7E`, and `Type2`,
e.g. `0x72`) that indexes function-pointer dispatch tables
(`dword_656A28[Type1]` → `dword_656E60[Type2]`). InPage never validated the Type
range (the second table only has 30 entries), so crafted documents indexed far
outside the table and executed attacker shellcode (InPage 2015 shipped with
**no DEP and no ASLR**). The BITTER APT group weaponized this against Pakistani
targets from 2016–2018. Two lessons for us: (a) never open random `.inp` files in
InPage; (b) the exploit write-up documents exactly how InPage walks the stream —
typed records processed sequentially by `ParseInPage100_432750` reading through an
`InPage100Read_440ED0` callback — which is precisely the traversal model this
converter reimplements (safely, in Python).

## 3. The `InPage100` stream — story level

The stream is a linear sequence mixing text and structure. Verified facts
(cross-checked against a 92 KB real file, 570 paragraph records, and against
InPage's own exports):

| Byte pattern | Meaning |
|---|---|
| `0x04` + code byte | **One Urdu/Perso-Arabic character** in InPage's proprietary encoding (see §4). Two bytes per character. |
| `0x0D` + 4 bytes | **Paragraph mark.** The 4 bytes are a little-endian `uint32` = byte length of the *next* paragraph + 1 — a skip-ahead link. Verified exact on all 570 paragraph pairs of the reference file. |
| `0x09` | Tab (bare byte) |
| `0x20–0x7E` | Literal ASCII (English text) — stored as single bytes |
| long runs of `0x00`s / structure bytes | Layout/style structure regions — **not text**; ≥16 consecutive junk bytes mark a structure region and the end of a text block |
| `00 00 FF FF FF FF` | Marker immediately after the last paragraph: **the character-run style table begins** |

Text appears in **blocks**: block 0 is the main story; blocks found after structure
gaps are additional text frames (footnotes, text boxes, captions). The parser
resumes scanning at the next long run of `0x04`-pairs (≥20 consecutive).

### 3.1 The character-run style table

Right after the story text (following the last `0x0D`+link), the format is:

```
[00 00 FF FF FF FF]                      table marker
then, for every styled run:
  run_bytes : uint32   ← bytes this run covers, counted exactly as stored
                        (marker pair = 2, ASCII/tab = 1, paragraph CR = 1)
  reclen    : uint32   ← always 6 + bloblen
  0x0001    : uint16   ← record flag
  bloblen   : uint32
  blob      : bloblen bytes of (opcode uint16, value) pairs
```

Decoded blob opcodes (widths proven by clean parses of 1300+ run blobs):

| Opcode | Width | Meaning |
|---|---|---|
| `0x7f00` | uint32 | **font size in 1/2000 pt** (48000 = 24 pt — matches InPage's own PDF export) |
| `0x7f08` | uint16 | **bold flag** |
| `0x7f80` | uint16 | font index (font table itself not yet located in the format) |
| `0x0080`, `0x7f06`, `0x7f8a` | uint32 | structurally known, meaning unknown |
| `0x0040`, `0x7f02`, `0x0201`, `0x0202` | uint16 | structurally known, meaning unknown |

### 3.2 Layout records (inpage2pdf follow-up research, 2026-09-10)

Further reverse engineering of real school documents (multi-frame, table-based
layouts) revealed the layout layer that InPage uses for page geometry:

**Stream directory (head of `InPage100`).** The first ~0x40 bytes hold a
directory of `(u32 offset, u16 index)` entries. Consistently across files:
entries 1–3 → the ASCII marker `"InPage Arabic Document"`, entry 4 → the
document settings record, entries 5–6 → a `fd ff ff ff` terminator near the
stream end.

**Settings record (follows the marker string).** Layout:
`01 00 02 00 00 00 00 00 [u32 A] [u32 A] 01 00 00 00 06 00 [u8 B] …`
followed by a default-attribute table of `(u8 tag, u8 0x02, u16 value)`
triples (tags 2–18; values 0,100,150,70,400,70,10,10,12,14,16,18,20,22,10,0 —
document-wide defaults, identical across different files).

**Object attribute records.** Between text runs, the stream carries
`(u16 opcode, u32 value)` records whose opcode high byte is **0x7E or 0x7C**
— matching the "Type1 = 0x7E" dispatch table from the CVE-2017-12824
analysis. Confirmed meanings:

| Opcode | Value observed | Interpretation |
|---|---|---|
| `0x7c04`–`0x7c06` | 71,999 | character style (≈ 36 pt in 1/2000 pt units — the school-name heading size) |
| `0x7e00` | 486,934 | **x coordinate** of a text frame (486,934 / 2000 = 243.5 pt) |
| `0x7e01` | 662,268 | **y coordinate** (331.1 pt) |
| `0x7e02` | 0xFFFFF104 | width/extent (negative-encoded?) |
| `06 00 01 00 NN 00` | NN = 2,3,4,… | sequential cell/paragraph index |
| `82 00 LL LL` | 3, 61, 66, 197 | object IDs |

Table-cell texts are separated by ~160–195-byte blocks of these records —
one block per cell. This is the road map for full geometric reconstruction
(positioned text frames / real tables) in a future version; the coordinate
system is confirmed to be **1/2000 pt** (the same unit as the font sizes in
the style table).

## 4. InPage's text encoding — character level

Crucial fact: **old InPage (pre-3.0) is not Unicode**. Text is stored in a
proprietary byte encoding tied to the Noori Nastaliq font glyph set. Every
Urdu character is a 2-byte pair: `0x04` marker + a code byte. The code byte
values collide with **Windows-1252** codepoints (e.g. alif is `0x04 0x81`, and
`0x81` is an undefined slot in cp1252 — undefined slots fall back to raw Latin-1).
This is why InPage text pasted into other programs historically looked like
"ÿ۷ÿ¢ÿ≠ÿßÿ≠" garbage.

Selected entries of the mapping (full ordered table lives in
`inpage2pdf/mapping.py`, ~110 rules):

| InPage bytes | Unicode | Character |
|---|---|---|
| `04 81` | U+0627 | ا alif |
| `04 9A` | U+0628 | ب be |
| `04 81 04 B3` | U+0622 | آ alif-madda (combination rule — must run before single alif) |
| `04 9A 04 BF` | U+0624 | و waw-hamza (combination) |
| `04 A4` | U+06CC | ی farsi yeh |
| `04 A0` | U+0646 | ن noon |
| `04 9D` | U+06AF | گ gaf |
| `04 90` | U+0632 | ز ze |
| `04 8F` | U+0691 | ڑ rreh |
| `04 92` | U+06BE | ھ heh-doachashmee |
| `04 D1–04 D9` | U+06F1–U+06F9 | ۱…۹ extended (Urdu) digits |
| `04 ED` | U+060C | ، Urdu comma |
| `04 F3` | U+06D4 | ۔ Urdu full stop |
| `04 F6` | U+FDFA | ﷺ (Sallallahu-alayhi-wasallam ligature) |

Rules that make conversion correct:

1. **Order matters.** Combination rules (alif-madda `04 81 04 B3`, hamza
   carriers like `04 A4 04 BF`) must be applied before their component singles,
   otherwise آ decodes as ا + ٓ.
2. A lone `0x04` marker that survives all rules is an InPage **word-space** → space.
3. Some code pairs map to nothing (`04 3A`, `04 3B`) — InPage internal zero-widths.
4. The mapping covers Urdu + shared Perso-Arabic; Arabic-only and Persian-only
   letters (ة ي ك) are absent from classic InPage Urdu documents but flow through
   unchanged when present.

## 5. Why Nastaliq is uniquely hard to render

Nastaliq (نستعلیق) is not just "Arabic script" — it's a **diagonal hanging**
calligraphic style:

- Letters **stack downward** and the word **slopes left-to-right-downward** like
  a hanging chain — unlike horizontal Naskh (the style of standard Arabic fonts).
- Correct rendering requires the font's full OpenType machinery: **GSUB**
  (contextual ligature substitution — Nastaliq words are mostly giant ligature
  glyphs) and **GPOS** (cursive attachment — each glyph is *positioned* relative
  to the previous one, not placed on a fixed baseline).
- Simple renderers (fpdf2 without shaping, jsPDF, etc.) output isolated
  disconnected letterforms stretched along a line — recognizable as broken.

This converter therefore routes every line through **HarfBuzz** (via fpdf2's
`set_text_shaping`, script `arab`, language `urd`, direction `rtl`), the same
shaping engine used by Linux, Android, Chrome and Firefox. Measured on a test
paragraph: shaped output carries **10× the ink density** of unshaped output at the
same width — the visual signature of correctly connected Nastaliq.

The embedded font is **Noto Nastaliq Urdu** (Google, SIL OFL 1.1) — an
open-licensed Nastaliq face with full Urdu + Latin coverage, instantiated at
weights 400 (regular) and 700 (bold) from the variable font.

Bidirectionality: fpdf2 applies the Unicode Bidirectional Algorithm before
shaping, so English words and numbers embedded in Urdu lines order correctly
within the RTL flow.

## 6. Why "perfect" .inp conversion is impossible without InPage

Concept Software has never published the format. Everything above is
community reverse-engineering. Concretely lost in any third-party conversion:

- exact page geometry (text-box coordinates, columns, master pages)
- images and vector graphics (separate binary records, format unknown)
- tables, borders, ornaments, color fills
- Noori-Nastaliq-specific glyph-level positioning choices

What survives — and what this tool preserves: **the complete text, reading order,
paragraph structure, font sizes, bold, and heading hierarchy**, re-typeset
beautifully rather than pixel-copied.

For a pixel-perfect PDF of an existing file, the only guaranteed method remains
InPage itself: *File → Print → Microsoft Print to PDF* (InPage ≥ 3.06 can also
export PDF directly). This converter is for everyone who *doesn't* have InPage.

## 7. Landscape of existing solutions (researched)

| Solution | Verdict |
|---|---|
| InPage print-to-PDF | Perfect fidelity, requires paid Windows software |
| "Online converters" (coolutils, convert.guru, …) | Marketed for InPage; quality on Nastaliq/RTL is inconsistent, files leave your machine |
| KamalAbdali/InpageToUnicode (C) | Text-only, own intermediate encoding, no PDF, scans raw file bytes (pre-OLE-aware) |
| ltrc/inPageToUnicode (JS, GPL-2.0) | The reference clipboard-text mapping (IIIT-H LTRC) |
| umer0586, zanysoft converters (C++/PHP) | Clipboard-text converters — same mapping family |
| Bridgeconn InPageToAutographa (C#) | Bible-translation pipeline; heavy, format-specific to scripture documents |
| herf (Python) | Best-in-class direct `.inp` reader + mapping (basis of this tool's extractor); no PDF output, desktop GUI wrapper |
| **This tool** | Direct `.inp` → shaped-Nastaliq **PDF**, offline, web UI + CLI + API |

## 7b. What "document formatting" consists of — and what this tool recovers

Research inventory of formatting elements in a word-processor/DTP document,
mapped against what InPage stores (per this project's reverse engineering)
and what the converter currently does:

| Formatting element | Stored in .inp? | Recovered? | How |
|---|---|---|---|
| Text content & order | yes (InPage100 story) | ✅ full | structure walker + ITU mapping |
| Paragraph structure | yes (0x0D + link records) | ✅ full | walker |
| Font size | yes (style table, 1/2000 pt) | ✅ | opcode 0x7f00 |
| Bold | yes (style table) | ✅ | opcode 0x7f08 |
| Font family | yes (font index) | ⚠️ partial | index decoded (0x7f80) but the font *table* itself is not located; rendered with Jameel Noori Nastaleeq (the dominant InPage Urdu font) |
| Alignment (center/right/justify) | per-paragraph, location not decoded | ⚠️ heuristic | headings + document-opening block + section dividers centered (matches school documents); body right-aligned |
| Number order inside multi-digit numbers | stored **reversed** (verified: 2026 stored as 6-2-0-2) | ✅ | digit-run reversal after decode |
| Line spacing | yes (metrics) | ⚠️ approx | Nastaliq-appropriate line boxes; user-adjustable |
| Paragraph spacing (before/after) | likely (attribute records) | ⚠️ approx | blank-paragraph preservation |
| Indentation / first-line indent | likely in paragraph records | ❌ not yet | — |
| Tabs | yes (0x09 bytes) | ✅ basic | preserved as spacing |
| Tables (grid) | yes (cell structure + per-cell records) | ⚠️ rebuilt | row-period detection → bordered RTL table, header repeat; exact cell coordinates not yet decoded |
| Text-frame positions (x/y) | yes (0x7E records, 1/2000 pt) | ❌ research | coordinates proven present; frame reconstruction not implemented |
| Page size | yes (document settings) | ⚠️ A4 default | not yet located in records; UI override available |
| Margins / columns | yes | ❌ not yet | fixed sane margins |
| Images / graphics | yes (separate binary records) | ❌ | not decoded |
| Headers / footers / page numbers | yes | ⚠️ re-added | optional page numbers in output |
| Bidi (Urdu/English mixing) | — | ✅ | Unicode bidi + HarfBuzz RTL |
| Color / underline / italic | likely in style opcodes | ❌ not yet | — |

**Key discovery (number order):** InPage stores multi-digit numbers with their
digits reversed in the stream (evidence: date stored ۶۲۰۲ for ۲۰۲۶; question
numbers stored ۲۱/۳۱/… for ۱۲/۱۳/…; marks stored ۰۱ for ۱۰ — verified on raw
bytes). The converter reverses each Urdu-digit run after decoding, which
restores correct numbers AND their correct RTL placement.

**Key discovery (horizontal word spacing):** typists align MCQ options etc.
with real space-bar runs (measured: 14–36 consecutive spaces, 795 runs in one
10k-char file). Three independent mechanisms were swallowing them, each now
fixed:

1. *Stream decode* — the old pipeline collapsed `[ \t]+` to one space
   (inherited from clipboard-cleanup code). Removed; runs now survive decode
   (tabs render as 4 spaces; the text-fix layer's "double_space" rule was
   likewise removed).
2. *Font internals* — in Jameel Noori Nastaleeq, HarfBuzz's Arabic shaping
   substitutes U+0020 to a glyph with an advance of only **5/2048 em**
   (≈0.06 pt at 24 pt), even though the font's cmap/hmtx declares U+0020 at
   512/2048 em. InPage therefore does not rely on the shaped space at all —
   it applies its own word spacing per typed space. Measured evidence:
   'ناول' + N spaces + 'افسانہ' renders at ~21 mm total ink width for every
   N from 1 to 30 when shaped normally.
3. *Renderer* — fpdf2's `get_string_width()` also collapses space runs, so
   width-based fit checks under-counted.

Fix: lines containing space runs are rendered by `_cell_spaced()` — every
word is drawn as its own shaped cell and each typed space contributes the
font's true U+0020 advance (read live from hmtx: Jameel 0.250 em, Noto
0.132 em). Over-wide lines scale the gaps (never the words) to fit; table
cells wrap via a run-preserving greedy wrapper (`_spaced_wrap`). Verified
geometrically: 36 spaces → 76.4 mm ink gap (expected 76.2), 30 spaces →
64.2 mm (expected 63.5); 30 wide-gap MCQ lines in the class-10 paper,
29 in paper 7, table titles at 37/42 mm.

## 8. Verification performed on this converter

- Real 92 KB Urdu short-story `.inp` (from KamalAbdali's repo) → extracted 40
  paragraphs / 27,547 characters with **zero unmapped codes**; output text reads
  as clean literary Urdu.
- Style table recovered: 37/40 paragraphs with font-size/bold metadata; sizes
  match InPage's own PDF export of equivalent files (per herf's original
  verification: 22/24/30 pt).
- Shaping verified: HarfBuzz-active PDF shows 10× ink density vs unshaped control
  at identical width; shaped PDF text extraction returns real Urdu words
  ("کام", "اور", "وہ کہ" …) in correct order.
- Negative tests: Abaqus `.inp` correctly rejected with an explanatory error;
  missing `InPage100` stream → clean error, no crash.

**Key discoveries (round 10 — bold / italic / fonts / alignment research):**

External research: the community repo `ImadC9/inpage-format` documents
InPage's tag-value property format (`[propId u8][group u8][value u16]`,
group 0x01 = character, 0x7E = paragraph) including alignment ids
(0x04/0x0F: 1=center, 2=left, 3=justify; right is the RTL default),
bold (0x7E:0x0E / 0x01:0x0E) and italic (0x7E:0x10). Their V1/V2
paragraph pattern (`0D xx xx 00 00 09 04`) does not occur in our files —
our paragraphs are `0D [byte-count u32] 04…` with no inline properties,
so alignment must live in a still-unlocated per-paragraph record.

Binary findings on the user's files (verified, not guessed):

- **Run-blob opcodes extended:** 0x7FE0 carries TWO u32 values, 0x7FE1 one
  u16, 0x7FE2 one u32. With these widths all 190 blobs of a projection
  file's run table parse cleanly — 71→176 paragraphs now recover real
  styles (projection 7 now renders its true 24 pt body instead of the
  12 pt fallback; verified no cell overflow).
- **Bold** 0x7F08=1 confirmed present in a real run (1-byte span).
- **Italic** 0x7F09 is a CANDIDATE (bold's neighbour) — applied only when
  value==1 and reported in flags; awaiting ground-truth files to verify.
- **Font table** is ASCII (not UTF-16): 'Noori Nastaliq', 'Noori
  Character', 'Simplified Arabic', 'ZoharSindhi', 'Arial', and in one file
  'Jameel Noori Nastaleeq' + 'Urdu Naskh Asiatype'. Extracted and reported
  in stats; the index↔name mapping is not yet decoded (0x7F80 values seen:
  2 and 187 — not simple table indices). All user files' Urdu text is
  Nastaliq → rendered with Jameel Noori Nastaleeq (the closest legally
  bundleable descendant of Noori Nastaliq).
- **Rendering:** Jameel has no bold/italic cuts, so bold = synthetic
  double-print with a hair offset (+55% ink measured) and italic = a
  synthetically sheared variant (JameelNooriNastaleeq-Italic.ttf, slant
  0.338→0.393 measured), both applied per paragraph AND per inline style
  run (guarded segment conversion).
- **Leading-space placement fix:** space-run lines now walk tokens in
  logical order, so a LEADING space run indents the visual RIGHT edge
  (RTL) instead of landing between words.

**Still open (needs the user's two new test documents):** per-paragraph
alignment record (their new doc has a genuinely centered top line —
differential analysis against class 10's right-aligned one will locate
the flag), italic opcode ground truth, and the font-index mapping.
**Key discovery (round 11 — BOLD encoding, fully verified):**

The user uploaded differential test files (`diff.inp`: the SAME line stored
twice — once regular, once bold; `sab.inp`: one paragraph with four inline
style runs). Binary comparison of their run tables proves:

- **Bold is encoded as font WEIGHT in opcode `0x7F02`: value 700 = bold**
  (the CSS/OTS weight scale; regular text carries no 0x7F02 or 400).
  The earlier `0x7F08=1` flag is a *different* (rarer) encoding of the
  same thing — both are now honoured (0x7F02 >= 600 bolds; a 0x7F08 flag
  is never un-bolded by a later weight <= 500).
- Two renderer bugs this uncovered:
  1. fpdf2 recomputes `w=0` cell width from the CURRENT x — so the
     synthetic-bold second print (shifted x) re-anchored to the same right
     edge and landed exactly on the first print (measured bold/regular
     ink ratio only 1.10x). Fix: resolve the width explicitly BEFORE the
     first print (ratio now 1.45x, matching the isolated-render value).
  2. `engine.convert()` strips each converted string's edges — converting
     style-run slices separately ate the spaces BETWEEN runs, so the
     slice-concatenation guard always failed and inline styles were
     dropped. Fix: `keep_edges=True` conversion for inner slices (outer
     edges still stripped like the whole paragraph) + cut-nudge repair
     (±1..3, marker-pair aligned) when a multi-char rule spans a cut.
- Verified end-to-end on the server output: diff.pdf regular line ink
  3223 px vs bold line 4681 px (1.45x); sab.pdf renders its four inline
  runs (24pt bold → 24pt regular → 14pt regular → 14pt bold) on one line.
- Still unverified: italic (0x7F09 candidate — no italic ground-truth
  file yet; the sheared Jameel-Italic font is ready and wired, it will
  activate the moment a real italic opcode is confirmed).

