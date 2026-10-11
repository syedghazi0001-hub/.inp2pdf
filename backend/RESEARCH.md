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


## Round 15 — Real InPage grid tables (Table tool)

**Problem:** files made with InPage's Table tool converted as plain text — no
borders, and the table's empty-cell paragraphs rendered as many blank lines
("kafi lines skip"). Tab/serial detection is useless for these files (they
contain no tabs at all).

**Structure (reverse-engineered from "table 1.inp", confirmed by its author):**
- The story of a grid table has ONE PARAGRAPH PER CELL. Each paragraph's
  run-style blob carries `0x7FE0 (chainId, cellId)` — chainId constant
  (0x6B here), cellId strictly +1 per paragraph (0x6C..0x8E = 35 cells).
- Cell line-counter records live in the object table: paired records
  `32 00 00 00 e7 9a 00 00` (50-record) + 0-record at a **124-byte stride**;
  the cell's counter is the u32 at 50-record+44 (0-record class+32).
  Verified file: 35 counters 574..715 — **5 groups of 7 = 5 rows × 7 cols**
  (row-major, cell 1 of each row = RIGHTMOST column in RTL). Within-row
  counter step is modal +4; row boundaries jump +5 (one +6 anomaly tolerated
  inside the last row).
- **No grid geometry is stored** — InPage computes cell layout itself, so we
  render a uniform grid (equal columns, content-driven row heights).
- Table text uses the SAME standard code table as everything else: the test
  file's odd-looking decode was the author literally typing random keys
  (author-confirmed); 6 codes never seen in other files (AE/B0/B9/BE/E7/F6)
  are just rare diacritic/religious-mark keys.
- Distinct from text-box chains (projection files also have 0x7FE0 links —
  one link per BOX, not per paragraph): the counter-record chain appears
  ONLY in grid-table files (0 occurrences in all 7 reference files, 35 in
  the table file). Detection additionally requires: ≥6 sequential cell
  links, rectangular counter grouping, and one-to-one byte-length match of
  the story's non-empty lines to the linked text runs.

**Implementation:** `inp_reader.extract_document_ex()` returns
`(blocks, styles, grid)`; `_table_counters`, `_linked_cells`,
`_grid_rows_cols`, `_detect_grid_table`. `converter` renders the grid via
`render_table_pdf` with `uniform` widths + `body_size`; empty cells produce
NO output lines (phantom-line fix); blocks after the table story flow below
the table (`post`). Non-table documents byte-identical to the previous
build (ink-level verified on all 7 reference files).

### Round 15b — Grid-table horizontal geometry (exact, no side gap)

**Problem:** the first grid-table render used the default 18mm render
margins, so the table came out 174mm wide with visible gaps beside it.
The user: original has NO gap between table and page margins, wants
<1mm difference.

**Found in the file (all values in InPage units = 1/2000 pt, same unit
as 0x7F00 font sizes; mm = units/2000 × 25.4/72):**
- Header property list `[propId u8][0x01][value u32]`: prop 6 = page
  width (1190550 = 210mm), 7 = height (1683779 = 297mm), 8-11 = L/R/T/B
  margins — all 12.70mm in this document → text area = 184.52mm.
- Table object record (first 0x2E-class record with page-sized A field
  at sig+28): A = 1046552 = 184.60mm = the table's recorded width ≈ the
  text area → the grid fills the page margin-to-margin, exactly as the
  user described. B (sig+40) = A − 1.84mm (inner width after borders).
- Cell records all carry the same A = 142650 = 25.16mm (default column
  width, stale) — no per-column widths stored; columns render equal
  (184.60/7 = 26.37mm).
- The 7-point diagonal polyline replicated 5× (one per row) is the
  user's drawn line, not grid data. Three 0x4C records carry ambiguous
  mm-triples (~(11.0, 12.9, 10.6)) — not confidently row heights, so
  row heights remain content-driven (user reported no vertical issue).

**Implementation:** `inp_reader._page_setup()` (props 6-11 → mm),
`_table_width_mm()` (table record A), grid dict gains
`geom = {x0_mm, y0_mm, width_mm}`. `render_table_pdf` applies geom when
present: pdf.l_margin = x0, pdf.r_margin = page−(x0+width), equal
columns over width_mm, table starts at the document's top margin
(set_y(y0_mm)). Serial-table path (projection files) untouched — ink
regression on all 7 reference files identical to deployed code.
**Verified (150dpi ink geometry):** v-lines 12.53→197.27mm (margin to
margin, no side gap), 7 equal columns 26.4mm, h-lines 12.53→60.79mm,
5 rows, text in the right column of rows 1-3.

### Round 15c — Real grid-table files: multi-format cells, per-cell sizes

**Problem:** the user's real test file ("table test.inp") produced NO table at
all — grid detection failed on it, so it fell to the flow layout (no borders,
wrong positions, wrong sizes). Three format facts the first implementation
did not cover:

1. **Cells with explicit formatting get a different object class.** The cell
   counter chain is id-50 records at a 124-byte stride, but cells whose text
   carries explicit formatting (font size) use class 79312/118968 instead of
   39655 (e7 9a). The chain walk must accept any class (≥60% majority 39655)
   and trim the chain at the first non-increasing counter (the next object in
   the file can also start with an id-50 record at +124; its counter is 0).
2. **One cell can span several style runs** (the cell's text bytes and its CR
   byte land in different runs of the SAME cell id; a cell may also hold more
   than one paragraph). Cell links are therefore non-decreasing (step +1 next
   cell, step 0 same cell), and a paragraph's cell is decided by the run
   holding its **CR byte**, while its font size comes from the run holding its
   **first text byte**.
3. **Per-cell font sizes are stored in the cell's own run style** (0x7F00):
   the test file carries 24pt / default / 36pt in its three text cells. Cells
   without an explicit size use InPage's document default — Noori Nastaliq
   13pt — NOT the most common explicit size (that silently resized
   default-sized text).

Verified on "table test.inp": 25 cells = counters 758..857 in 5 groups of 5 →
**5 rows × 5 cols**; texts سید محمد غازی (24pt), سید محمد شاہ (13pt),
علی مولا (36pt) all in the rightmost column, rows 1-3; margins 12.7mm,
table 184.6mm margin-to-margin; rendered PDF contains exactly font sizes
13/24/36 and row heights 16.6 / 10.2 / 23.4 / 10.2 / 10.3mm.

**Renderer:** `draw_row` now measures and draws each cell at its own size
(grid tables), serial tables (projection files) keep the uniform body path —
including the `_spaced_wrap` line-wrapping loop that was briefly lost and
restored (its absence shifted one wrapped spaced cell in projection 7).
Ink-level regression: all 8 non-grid reference files identical to the
deployed code.

---

## Round 15d — Full stabilization (new real table + word-space fix everywhere)

### Bug 1: "table 1 NEW.inp" (user's real 5×7) → "No text found"
- Cause 1: first story run = 8 text bytes (3-4 marker pairs) → old text-start
  needed ≥8 pairs. Thresholds now progressive: (20, 8, 4, 2).
- Cause 2: THREE cells' paragraphs have length-prefix 0x09 = TAB code — the
  walk read "9" as a typed tab, prepending '\t' to cells (also shifts the
  CR-byte cell mapping by +1 per affected para). Fix: a 0x09 at paragraph
  start followed by 00 00 00 is the LENGTH PREFIX, not a tab.
- Cause 3: that file's line-counters step +2 uniformly — NO row-boundary
  signal (unlike old table 1: +4/+5, table test: +4/+3-4). Grid shape now
  comes primarily from the TABLE OBJECT RECORD: sig+82 = u16 column count
  (verified 7 / 5 / 7 on the three grid files; +98 is a constant 8, not
  cols — offset corrected after miscount). rows = ids / cols.
- NEW INVARIANT (blocks false positives): a real grid has exactly ONE
  line-counter record per cell → len(counters) == len(distinct cell ids).
  projectiion test: 96 counters vs 128 text-box ids → correctly rejected.
  All 3 grid files: 35/35, 25/25, 35/35 ✓.
- Result: 5×7 grid, real names سید/علی/محمد/غازی in column 0, per-cell
  sizes straight from the file: علی 60pt, محمد 18pt, others 13pt default.
  Render: 6 h-lines, 8 v-lines, 26.4mm columns, row heights
  10.2/37.1/13.0/10.3/10.2mm.

### Bug 2: fused single-space words ("haroof alag alag" glitch)
- Jameel Noori's shaped space is ~zero-width → plain cell() glues
  single-spaced words into one blob (user's projectiion test title was ONE
  482px cluster). Old code only routed "  " (double-space) lines to the
  spaced renderer.
- Fix: EVERY line containing a typed space (single or run) is wrapped with
  _spaced_wrap and drawn via _cell_spaced in: flow body, _multiline, title
  block, draw_row cells (measurement + draw), post-table flow.
- Spaceless lines: never split per-glyph. multi_cell's breaker splits a
  too-wide word letter-by-letter (destroyed Nastaliq joining on the 60pt
  cell) → a spaceless line is ONE line and overflows like InPage.
- Wrap width: files store their own page setup (A4, 12.7mm margins →
  184.6mm usable); our tuned 18mm margin (174mm) would re-flow lines
  InPage itself fit. RenderOptions.file_usable_mm (converter passes
  page_w − l − r) → wrap decisions at max(usable, file_usable); the drawn
  line still fits our margin via gap compression (words never shrink).
- Result: projectiion title = 6 word clusters (was 1); MCQ double-space
  gaps unchanged; 3 genuinely-too-long lines in paper 7 (195-205mm at real
  space widths — they auto-wrapped in InPage too) now wrap → paper 7 = 8pp.
  class 10 = 5pp (matches reference).

### Regression state (title_from_filename=False, i.e. app defaults)
- All 12 user files convert ok, 0 blank pages, no overflow (flow xmax
  ≤194mm, grid 197.4 = table edge).
- Pages: 1g 1, diff 1, sab 1, projections ×3 3pp, tables ×4 1pp,
  class 10 5pp, paper 7 8pp, PAPEgggggR 4pp.
- table test sizes 13/24/36 preserved; table 1 NEW sizes 13/60/18/13/13.
- NOTE: single-spaced lines intentionally DIFFER from deployed code (real
  word gaps now) — that IS the fix. Old reference PDFs show fused words.

### App behavior notes
- title_from_filename only when form field cover_title == "1" (default
  off). Test batteries must pass title_from_filename=False to match.

---

## UI Round 1 — "Ink & Paper" redesign (backend untouched, locked)

User feedback: boring one-pattern UI → main page sirf converter, guide/FAQ/privacy
alag pages, graphic-designer look, animations including icons (not just text).

### Structure (4 pages, all extend templates/base.html)
- `/` — tiny hero (animated InPage→PDF arrow, 3 trust chips) + ONE converter
  card (dropzone, options in details, convert button, results). No SEO blob —
  that moved to /guide + /faq.
- `/guide` — 3 numbered steps (ink-drop numbers, icons DRAW on scroll),
  settings explained (icon rows), chat-preview download troubleshooting,
  self-hosting.
- `/faq` — JS accordion, plus/minus morph, FAQPage JSON-LD (moved from home).
- `/privacy` — 5 honest one-liners with icons.
- sitemap.xml lists all 4; robots.txt unchanged; WebApplication JSON-LD kept
  on home; TEMPLATES_AUTO_RELOAD=True added (template edits live w/o restart).

### Design system (static/style.css, ~700 lines)
- Palette: warm paper #f8f4ea, card #fffdf7, ink #221b12, saffron accent
  #b4530a/#d97706 (Urdu manuscript feel — NOT the generic blue/purple).
- Fonts: Fraunces (display serif) + Inter (body) via Google Fonts, solid
  system fallbacks.
- Animated icons (all inline SVG + CSS, zero libraries):
  logo page draws itself + ink drop drips (4.5s loop, faster on hover);
  dropzone cloud floats + arrow bobs + border dashes MARCH when dragging
  (SVG rect, pathLength=1000, dashoffset loop); convert button shows a PEN
  WRITING (nib wiggle + line drawing loop) while converting; file pills pop
  in with spring, remove ✕ rotates on hover; result card rises, ✓ check
  DRAWS itself (dashoffset), stat chips stagger in; PDF pages fade in;
  FAQ plus→minus morph (two bars rotate/fade); guide icons stroke-draw when
  scrolled into view (IntersectionObserver adds .in); nav underline grows
  from center; background blobs drift slowly (46s); hero arrow draws once
  then nudges on hover.
- Accessibility: prefers-reduced-motion kills all loops; .nojs fallback keeps
  reveal-sections visible without JS; focus-visible rings; dropzone is
  keyboard-operable (Enter/Space).

### Front-end behavior — CONTRACT PRESERVED
- static/app.js (was inline in index.html): same IDs (drop, fileInput,
  fileList, go, pageSize, urduFont, layoutMode, scale, lineSpacing,
  landscape, results), same form fields to /api/convert, ?key= API-key
  support, pdf.js preview with page-image fallback + 20s timeout, "Show
  remaining N pages", blob download with in-card fallback hint (now
  per-card class instead of duplicate #dlfallback id), copy-link dialog,
  extracted-text details. Improvements: download/copy via event delegation
  (data-attrs) instead of inline onclick, esc() for all injected names.
- Verified: all 4 pages 200 + balanced HTML (parser check), CSS braces
  275/275, node --check app.js OK, E2E conversion via exact UI form fields
  (table 1 NEW + projectiion test) OK, all static assets 200.
- Safari safety: dz-border rect has width/height="100%" attributes (CSS
  geometry alone is not enough on older Safari).

### Pending after user's UI approval
- Rebuild inpage2pdf-deploy.zip (Netlify site/ must point at the same 4
  routes) + inpage2pdf-windows.zip.
- og-banner.png still shows the old green design — regenerate if wanted.

---

## UI Round 2 — dark Gen Z redesign (user request: black, heavy animations, fun, no fazool text)

### Removed
- "Files 24h me delete" chip + footer note + FAQ answer + FAQ JSON-LD
  mention — all gone (privacy page keeps one soft "auto-cleanup" line, no
  24h number). Hero copy shortened to one punchy line.

### Design system rewrite (static/style.css, dark "neon" theme)
- Base: #08080d near-black + dotted grid + SVG-noise grain overlay
  (data-URI) + 3 blurred neon blobs (lime/violet/cyan) drifting.
- Fonts: Unbounded (display, wide Gen Z) + Space Grotesk (body).
- Neon palette: lime #c8ff1f primary, violet #a78bfa, pink #ff5d8f,
  cyan #3ee6ff on dark cards #10101a.
- Marquee ticker under header (infinite scroll, pauses on hover, spinning
  ✦ separators, duplicated track for seamless loop).
- Hero: gradient animated title (white→lime→cyan→violet, 8s shift),
  glowing arrow that DRAWS + a glowing dot that flies across it on loop,
  5 floating shapes (stars/rings/squiggles) around hero, pulsing ✦ chips.
- Converter card: cursor spotlight (JS sets --mx/--my, radial lime glow),
  hover lift + neon border glow, dropzone "breathes" (inset glow pulse),
  marching dashes on dragover, cloud floats + arrow bobs.
- Convert button: lime gradient + shine SWEEP loop + glow, spring hover,
  bouncing 3-dot loader while converting.
- Results: card rises, READY sticker (lime, rotated, glow, hover wobble),
  CONFETTI BURST on success (18 JS-generated particles, random --tx/--ty/
  --r, 5 neon colors), staggered stat chips, check draws itself, pages
  fade in, neon scrollbar.
- fpills pop with spring, remove ✕ spins 120°; guide cards tilt on hover;
  FAQ +/− morph neon; privacy cards lift; footer heart BEATS.
- prefers-reduced-motion still kills everything; keyboard + focus states
  kept; nojs fallback.

### Files touched
- static/style.css (full rewrite ~900 lines), templates/base.html (fonts,
  3rd blob, ticker, footer without 24h), templates/index.html (Gen Z hero
  + fx shapes, chips minus 24h), static/app.js (spotlight, confetti, dots
  loader, compact save-help copy — API/pdf.js/fallback logic IDENTICAL),
  templates/guide.html (lead), templates/faq.html (24h removed + JSON-LD),
  templates/privacy.html (rewritten, no 24h emphasis).
- Verified: CSS 335/335 braces, node --check OK, all 4 pages 200 +
  balanced HTML, no '24 hour' text anywhere (incl. JSON-LD), all
  functional IDs intact, E2E conversions (table 1 NEW 1pp grid, urdu
  paper 7 8pp) OK via exact UI form fields.

---

## UI Round 3 — "Obsidian / Ink Lab" (master prompt; replaced Gen Z round)

User verdict on round 2: big floating symbols + scrolling before the tool = junk.
Master prompt direction: obsidian black, restrained emerald, editorial, precise,
converter immediately reachable, purposeful motion only.

### Design system (static/style.css, full rewrite #3)
- Palette exactly per spec: bg #08090B, graphite #101216, charcoal #171A20,
  borders #292D35, emerald #18D99A, mint #8BF0C7, text #F3F5F7, gray #9299A5,
  muted red #E5484D. Depth via tonal contrast + fine borders, NOT gradients.
- Background: faint 44px technical grid (body::before), drifting at 90s —
  barely perceptible. No blobs, no neon, no ticker, no confetti, no fx shapes.
- Fonts: Inter (UI) + Noto Nastaliq Urdu (Urdu signature + text preview);
  system fallbacks for sandboxed preview.
- Brand name PRESERVED ("InPage → PDF") per the prompt's own rule (name
  established in codebase/SEO) — INKFORM not used; "URDU DOCUMENT ENGINE"
  status label carries the lab identity.

### Home (templates/index.html)
- Asymmetric editorial hero: left copy (status pill w/ pulsing dot, headline
  "From InPage to PDF. / Without losing the craft.", honest sub, real Urdu
  line «اصل نستعلیق · اصل ترتیب»); right = ORIGINAL SVG illustration:
  source .inp doc (Nastaliq-inspired flowing strokes, plain table, page
  fold) → transformation trail (dashed emerald path + 2 SMIL animateMotion
  dots) → output PDF doc (neat typeset lines, emerald table header, PDF
  tag, verified-seal check that draws itself). Lab crosshair marks.
  Illustration HIDDEN below 880px — tool first, no scrolling.
- Workspace card (#ws): step indicator 01 Source / 02 Settings / 03 Export
  (active/done/busy states driven by JS), scan line (emerald, sweeps only
  while the real fetch is in flight), slim indeterminate progress bar.
- Dropzone: compact (no giant dashed box), inner 14px grid, 4 viewfinder
  corner brackets that light emerald + shift outward on dragover, upload
  icon with drawn arrow, "Drop your InPage documents here" / "Choose .inp
  files" / Browse files secondary action. On file select → .filled compact
  "Add more files" strip + brief flash pulse; file ROWS (icon, name, size,
  Ready dot, remove) replace the big empty state.
- Options: details with grid-template-rows 0fr→1fr animation, contextual
  one-line descriptions under each control, landscape as a real toggle
  switch. All 6 controls + IDs unchanged.
- Convert button "Convert to PDF": emerald, inset highlight, hover lift,
  spinner + "Converting" while processing, duplicate-submit guard
  (processing flag), disabled without files.
- Results: PDF icon tile, output filename + size, Ready badge (check
  draws), stats chips, Download/Open/Copy link, pdf.js preview with image
  fallback, extracted-text details, download-issue details (chat preview).
  Errors: red-tinted card with the REAL backend message.
- Below: concise 3-step workflow strip (01/02/03, one line each). Minimal
  footer. No emoji icons anywhere; consistent 1.5–1.7 stroke icons.

### Other pages
- base.html: minimal nav (brand + Guide/FAQ), no ticker; footer minimal.
- guide: gnum now "01/02/03" text, lead "3 steps. Bas.", arrow char removed.
- faq/privacy: unchanged markup, restyled by new CSS.

### Files changed (this round)
static/style.css, static/app.js, templates/base.html, templates/index.html,
templates/guide.html. NO backend/app.py changes.

### Verified (actually run)
- CSS 337/337 braces; node --check app.js OK.
- All 4 pages 200 with balanced HTML (parser); functional IDs + pdf.js
  intact; old junk (ticker/fx/confetti/✦/chips) absent; no emoji; workspace
  early in DOM; no '24 hour' text.
- Live E2E: pages 200; multi-file convert (table 1 NEW + 1g) via EXACT UI
  form fields ok; view/download/pageimg URLs 200; bad-file error path
  returns real error message.
- prefers-reduced-motion kills all motion; keyboard: dropzone Enter/Space,
  focus-visible rings; overflow-x guarded.

---

## UI Round 4 — minimal cleanup (user: "bary symbols zero karo, simple UI, designer-quality")

Round-3 leftovers removed — home is now headline + converter only:
- REMOVED: hero SVG illustration (doc-transform art), status pill
  "URDU DOCUMENT ENGINE", Urdu signature line, step rail (01/02/03),
  3-step workflow section, viewfinder corner brackets, dropzone inner
  grid pattern, background technical grid (flat #08090B now), per-control
  option descriptions, result-card PDF icon tiles, 4th stat chip.
- KEPT (functional only): 2-line headline + 1-line sub; plain bordered
  dropzone with small upload icon + Browse files; compact add-more strip
  + file rows when filled; flash pulse on select; drag-over emerald;
  advanced options (6 controls, same IDs); Convert to PDF button with
  spinner + duplicate guard; slim progress bar + scan line during REAL
  processing only; result cards (name, size, Ready badge with drawn
  check, 3 stats, Download/Open/Copy link); pdf.js preview + image
  fallback; extracted-text details; real error messages.
- Files: static/style.css (rewrite #4, 262/262 braces), templates/index.html
  (8.5KB, workspace 1.1k chars into body), static/app.js (steps logic
  removed). No backend changes.
- Verified: pages 200 + balanced HTML, node --check OK, functional IDs
  intact, E2E convert/view/download 200 via exact UI form fields.

---

## UI Round 5 — classical + Apple-keynote motion (symbols back)

User: "symbols rehny doo, sab apple keynotes motion graphics mai kr doo, UI ko classical"

Restored (from round 3, classical restyle):
- Hero illustration (source .inp → trail → output PDF docs, engraved
  hairline style, serif SOURCE·.INP / OUTPUT·.PDF labels, crosshair marks,
  SMIL trail dots, self-drawing seal check) + gentle 8s float.
- Status label "The Urdu Document Engine" (pulsing dot).
- Dropzone viewfinder corner brackets (slide out on dragover, emerald).
- Step rail — now ROMAN numerals I / II / III in italic serif.
- 3-step workflow section below converter, roman I./II./III., scroll reveal.
- Result-card PDF icon tiles.

Classical language:
- Fraunces serif (display: headline, step numerals, page titles, brand,
  workflow headings — italic for numerals) + Inter body. Ivory text
  #F2EFE6 on obsidian; emerald stays the single accent.

Apple-keynote motion system:
- Easing tokens: --ease cubic-bezier(.16,1,.3,1), --spring
  cubic-bezier(.3,1.4,.4,1) applied to ALL transitions/animations.
- Headline line-mask reveal (overflow-hidden .hl spans, translateY 115%→0,
  staggered 1s). "craft" = italic serif with slow emerald gradient shimmer.
- Sequence: status fade → lines rise → sub fade → workspace springs in
  (cardIn .9s spring, delay .5s) → illustration strokes draw (d-1..d-4),
  docs slide in from sides, seal check draws at 1.7s.
- Micro: rows spring in, remove ✕ rotates 90°, button hover lift w/ spring,
  download icon dips, toggle knob springs, s3/workflow items stagger in on
  scroll (IntersectionObserver), page canvases ease up.
- prefers-reduced-motion: full static fallback (masks open, strokes drawn,
  opacity 1).

Files: style.css (rewrite #5, 343/343 braces), index.html (symbols back),
app.js (step rail + s3 reveal + pdfic restored), base.html (Fraunces).
No backend changes. Verified: pages 200 + balanced HTML, node --check OK,
functional IDs intact, multi-file E2E (table 1 NEW 1pp + projectiion test
3pp) view/download 200 via exact UI form fields.

---

## UI Round 6 — mockup-faithful rebuild (user's PNG, analyzed programmatically)

User attached "InPage to PDF Converter Dashboard.png" (1536x1024) — no vision,
so the design was extracted by pixel analysis: dominant colors, saturated
accent blobs w/ bounding boxes (scipy label), 110x55 ASCII renders of
green/white/gray masks, region zooms. Findings that drove the rebuild:

- bg near-black teal #010709; flat (no cards). Accent family: green text
  #1AD0A6, bright button #14E6AD, big deep bar #06A883, white #DDE2EE.
- Nav: green logo icon x74-105 + wordmark; empty right.
- Hero 2-col: LEFT green eyebrow label y98, big white headline y130-165,
  GREEN headline line 2 y186-223, gray subtext, 3 feature items w/ green
  icons y290-335. RIGHT: white document mockup x1005-1230 y93-335 with
  green arrow icon left of it.
- Workspace (column x253-1282): heading = green icon + text + green
  underline y390-428; dropzone LEFT w/ green up-arrow x~390 y440-490;
  selected-file rows RIGHT x~1010 y465-520 (green icon + name);
  mid bright-green button x500-660 y614; helper text; BIG full-width
  deep-green CTA bar x253-1282 y781-837 (the dominant element).
- Footer: 3 feature items w/ green icons y893-930.

Implementation (style.css rewrite #6, index.html, app.js, base.html):
- Inter only (800 weights, tight tracking) — mockup is sans; Fraunces removed.
- Hero: green eyebrow (pulsing dot), line-mask keynote reveal, line 2 in
  green #1AD0A6, subtext, 3 feature chips; right = CSS doc-mock (white
  page, growing text lines, emerald table header, PDF tag, self-drawing
  check seal, floating green arrow + sparks).
- Workspace: icon+heading+growing green underline; grid = dashed dropzone
  (drawn arrow) + Selected-files panel (rows w/ count, empty state);
  Browse files bright-emerald button + note; adv options (0fr panel);
  BIG deep-emerald "Convert to PDF" bar w/ sheen sweep (disabled until
  files); scan line during real processing; results (pdfic tile, badge,
  btn-sm actions, stats, pdf.js preview, extracted text).
- Footer features 3x (reveal on scroll) + minimal base footer.
- Verified: CSS 342/342, node --check OK, pages 200 + balanced HTML,
  functional IDs + browseBtn/fcount wired, E2E multi-file (table 1 NEW 1pp
  grid + urdu paper 7 8pp) view/download 200, error path shows real error.

---

## UI Round 7 — FULL ROLLBACK to the original UI (user request)

User: "sab hata doo, jis trah starting mai tha UI wesa hi kr doo, simple sa"

- templates/index.html → RESTORED to the original self-contained file
  (light theme #f7f8fa, white cards, green #0d9665, Segoe UI, header +
  2 step cards + results + SEO section + Madad details + foot; inline
  CSS + inline JS with pdf.js preview, image fallback, download help,
  copy-link; 29.7KB vs original 29.9KB — whitespace-only diff).
- DELETED: templates/base.html, guide.html, faq.html, privacy.html,
  static/style.css, static/app.js (all 6 redesign rounds removed).
- app.py: /guide /faq /privacy routes removed, sitemap back to single
  URL, TEMPLATES_AUTO_RELOAD removed — page layer identical to
  pre-redesign state. Backend conversion engine untouched (round 15d
  fixes intact).
- Verified: / renders 200 + balanced HTML; all original markers/IDs
  present; /guide /faq /privacy now 404; E2E multi-file conversion
  (table 1 NEW 1pp, projectiion test 3pp, 1g 1pp) view/download 200.

---

## UI Round 8 — user's own design file implemented (uploads/inpage_pdf_ui_design.html)

User provided a complete UI concept HTML (dark teal #05090b + emerald
#12e5ad, Inter). It was a static mockup ("UI concept only — connect to
your backend"). Implemented EXACTLY as templates/index.html with the real
converter wired in:

- Design preserved 1:1: header (logo + Urdu Document Engine status pill),
  ambient glow + technical grid bg, hero (kicker, "From InPage to PDF.
  Without losing the craft." with green line 2, intro, 3 feature icons),
  art panel (orbit ring w/ rotating dot, .inp source doc + PDF doc with
  Urdu text + fake tables, pulsing arrow-orb), workspace (01 SOURCE /
  02 SETTINGS / 03 EXPORT step rail w/ active underline, big dropzone
  with shine sweep + Browse button, Selected Files panel with count
  badge + empty state, Advanced Options collapsible, gradient Convert
  button, status message line), workflow section, footer. All concept
  CSS verbatim + media queries + reduced-motion.
- REAL functionality wired: file select/drag-drop/dedupe/remove →
  POST /api/convert with all 6 option fields (pageSize, urduFont,
  layoutMode, scale, lineSpacing, landscape), step rail states
  (source/ready/busy via .done class), spinner in convert button,
  status messages real (select/converting/done/failed), result cards in
  design language (badge, stat chips, Download/Open/Copy link, pdf.js
  preview + page-image fallback + show-remaining, extracted text, chat-
  preview download fallback hint), ?key= API-key support.
- Original SEO head merged (title, desc, keywords, canonical, favicon,
  OG, twitter, WebApplication + FAQPage JSON-LD).
- Concept placeholders removed (option-note, "UI preview" strings).
- Sandbox hiccup: pip wipe after restart → reinstalled fpdf2, pypdfium2,
  olefile, flask, uharfbuzz (uharfbuzz was the missing shaping dep).
- Verified: / 200 + balanced HTML, node --check inline JS OK, all IDs
  present, no placeholders; E2E full battery — table 1 NEW 1pp grid,
  projectiion test 3pp, urdu paper 7 8pp, 1g/diff/sab 1pp, table 1/
  table test 1pp, projection 7 3pp, PAPEgggggR 4pp — all ok, view/
  download/pageimg 200; bad-file error path returns real error.

## UI Round 9 — more animations, compact dropzone, new logo (2026-10-11)

User: "yar thora animations barha doo iss mai baki sab theek hai. oor is ka jo
dropyour document wala box hai woo thora chota kroo oor aik acha ya logo bhi
bnaoo inpage oor pdf ka mix kr k lakin simple sa oor dark theme mai"

Three changes, all in templates/index.html only (backend untouched, 15d lock):

- NEW LOGO (header SVG + matching favicon data-URI): document outline with
  folded corner in emerald gradient stroke (InPage/document side), Nastaliq
  "ن" flourish bowl + dot inside (Urdu identity), solid emerald pill badge
  bottom-right with knocked-out dark "PDF" text. Simple, dark-theme. Old
  text-lines logo replaced. Verified by rasterizing with cairosvg (4x) and
  numpy zone checks: all 4 doc edges, flourish, dot, pill (71% density),
  dark PDF letters all present. .logo now 35px, height:auto, logoglow
  filter pulse animation.
- DROPZONE COMPACT: min-height 250→185px (mobile 220→165px), padding
  25→16/22px, upload-icon 56→44px (mobile 36), h2 17→15px, tighter
  margins, browse padding 12/21→10/18. File-panel min-height 250→185.
- MORE ANIMATIONS (all killed by existing prefers-reduced-motion rule):
  status-dot blink, h1 line-2 gradient sheen sweep (@supports background-
  clip:text guarded), feature-icon breathe glow + staggered entrance,
  art glow blob pulse, second dashed orbit ring o2 (reverse 28s), floating
  docs (floatA/floatB ±6px), arrow-orb now travels ±16px between docs
  (orbtravel, replaces pulse), dropzone inner glow breathing (dzpulse) +
  drag scale(.985)+glow burst, upload-icon bob, step-num pop on activate +
  active underline stepglow, convert button animated gradient (gradslide)
  + hover shine sweep on browse/convert, options open rise, file count
  badge pop (retriggered via reflow trick in renderFiles), file-item hover
  border, result card hover lift, workflow stagger + hover lift/rotate.

- INCIDENT (tooling): batching multiple edit_file calls on the SAME file in
  one parallel block races — each reads pre-batch state, last write wins,
  most edits silently lost + a corrupted duplicated tail appended after
  </html>. Recovery: single atomic python patch script (23 sequential
  replaces with count==1 asserts) + truncated file at first </html>
  (removed 102 junk bytes). LESSON: never parallel-edit one file; use one
  bash python script for multi-part patches.
- Verified: HTML balance clean, CSS braces 237/237, node --check OK, all
  IDs/markers present, old pulse keyframes gone. Server restarted
  (inpage-to-pdf-converter-39703d59, pid 2809, port 8080). E2E: table 1
  NEW 1pp grid-table ok, projectiion test 3pp + 1g 1pp multi-file ok,
  view/download 200 (URL-encoded names).
- Pending user approval → then rebuild deploy/windows zips + og-banner.

## UI Round 10 — user's "final ui.html" implemented 1:1 + slightly more animations (2026-10-11)

User attached uploads/final ui.html: "iss trah ki ui kr dooo koi fazool extra
cheez na dalna bss thori animations ziada kr doo" — implement this design
exactly, no useless extras, just a bit more animations. Supersedes Round 8/9
UI (backend untouched, 15d lock).

New design (very different from Round 8): teal/mint palette (#050a0b bg,
--teal #18d9b0, --mint #75f6d8, --cyan, red #ff7668 accents), fixed .bg
layer (radial gradients + 58px grid + SVG fractal-noise texture) with two
ambient .glow orbs, 82px header (brand doc-icon + "InPage to PDF", nav
Home/How it Works/Features with teal underline sweep, theme button that
toggles alternate ambient lighting), centered hero (eyebrow pill "FAST •
SECURE • ACCURATE" w/ blinking dot, h1 with .accent spans, subline),
838px converter card (24px radius, top edge light animation): document-flow
with energy-line beam + 4 particles + .inp file-visual (اردو serif symbol,
INP label) + transfer circle (pulsing + arrow nudge) + pdf file-visual (♧,
red theme), 165px dashed dropzone (floating cloud icon, dropShine sweep,
"Choose File ▰" pill button, 50MB note), compact single selected-files bar
(▤ icon, name, size, × remove), gradient Convert button (buttonShine),
status line; 4-feature grid (Secure/Fast/Accurate/Any Device, 4 icon
colors); footer (© 2026 + ♥ heart pulse). Design already ships 13 keyframes
(ambient float blink beam particle pulse arrow shine dropShine buttonShine
edge rise heart) + 2 media queries + reduced-motion kill switch.

- Implementation: single write_file (NO parallel edits — Round-9 lesson).
  Design CSS verified 100% VERBATIM 1:1 (normalized char-exact diff after
  removing only 5 intentional micro-additions) and all 24 design body lines
  present. Design JS adapted: statusEl rename, busy guards, real messages
  (placeholder "connect this button" strings removed), click-on-dropzone
  opens picker; theme toggle + nav active JS kept verbatim.
- NO options panel (design has none): UI posts hidden defaults
  page_size=A4, urdu_font=jameel, layout_mode=auto, scale=1.00,
  line_spacing=1.7, landscape=0 — identical conversion behavior to
  Round-8 defaults, zero visual clutter.
- Functional additions (design language, invisible until used): .results
  cards inside converter (res-head, badge ok/bad, Download/Open/Copy link,
  stat chips, pdf.js preview + pageimg fallback + show-remaining, extracted
  text, dlfallback hint), busy state (converter.busy dims drop/selected/
  browse/remove, white spinner in convert), convert:disabled,
  .remove:hover, .drop:hover, status color transition, small-screen result
  tweaks in 560px query.
- Extra animations ("thori ziada", all subtle, reduced-motion safe):
  eyebrow glow breathe (eyeglow), h1 .accent gradient sheen sweep
  (@supports background-clip:text), convert gradient slow shift (gradslide
  alternate), feature cards staggered rise + hover lift, feature icons
  gentle float w/ staggered delays, browse/convert :active press.
- SEO head merged from before (title/desc/keywords/canonical/OG/twitter/
  WebApplication+FAQPage JSON-LD); theme-color #050a0b; favicon = design's
  brand doc-icon (teal on dark); pdf.js + ?key= support kept.
- Verified: HTML balanced, CSS braces 223/223, node --check OK, all design
  + functional + extra-anim markers present, no old-UI leftovers, no design
  placeholders. Server inpage-to-pdf-converter-63e098c2 (pid 3865, 8080).
  E2E with exact UI fields: table 1 NEW 1pp grid-table, projectiion test
  3pp table, 1g 1pp flow — all ok; view/download 200; pageimg 200
  (1g_p01.jpg — note: names are <stem>_pNN.jpg).
- Pending user approval → then rebuild deploy/windows zips + og-banner
  (old zips still round-13/pre-15/pre-UI).

## Round 11 — Complete updated project packages rebuilt (2026-10-11)

User: complete updated project package do — saari files/folders (backend,
templates, static, requirements.txt, wsgi.py, configs), koi required file
skip nahi karna.

- og-banner.png regenerated to match Round-10 teal design (PIL: ambient
  glows, grid, eyebrow pill, INP→PDF document-flow motif with energy line
  + Nastaliq اردو + ♧ + transfer circle, title, subtitle, feature line;
  1200x630, 73KB; zone-verified with numpy).
- inpage2pdf-deploy.zip REBUILT (16.0MB): site/ (current UI rendered with
  base_url=YOUR-SITE.netlify.app + pdfjs + new banner + robots + sitemap),
  backend/ (current full project: app.py, wsgi.py, cli.py, Dockerfile,
  README.md, RESEARCH.md, requirements.txt, templates/, static/, fonts/
  5 TTFs, inpage2pdf/ 7 modules, converted/ 11 sample PDFs), netlify.toml,
  render.yaml, README-DEPLOY.md, dev/local_netlify_sim.py, FILES-MANIFEST.txt.
- inpage2pdf-windows.zip REBUILT (41.0MB, 924+ files): embedded Python 3.11
  runtime + preinstalled deps (from round-12 package, unchanged), project
  files updated to current (app.py, cli.py, requirements.txt, README.md,
  RESEARCH.md, templates/index.html = Round-10 UI, static/og-banner.png,
  inpage2pdf/*.py, converted/ 11 PDFs), START-WEBSITE.bat, CONVERT-FILE.bat,
  PARHEN-README-PEHLE.txt kept, FILES-MANIFEST.txt added.
- CRITICAL CATCH: first deploy-zip build was 1.5MB — backend/fonts/ (5 TTF,
  23MB) had not been copied into the stage; caught by size diff vs old zip
  + full file-list comm compare vs old zip. After fix: 16.0MB, comm shows
  ZERO missing files in both zips (only __pycache__ dirs intentionally
  dropped; manifest + extra sample PDFs are the only additions).
- Verification: unzip -t clean on both; new-windows zip contains new
  templates/index.html (38,314b) + og-banner (73,090b) + current app.py
  (20,213b) + python.exe; deploy backend template byte-identical to live
  project. FILES-MANIFEST.txt in each zip lists every file + size.
- Old round-13 zips deleted; new zips at /home/user/inpage2pdf-deploy.zip
  and /home/user/inpage2pdf-windows.zip.
