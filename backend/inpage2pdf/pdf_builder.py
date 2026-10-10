"""PDF rendering engine for the InPage → PDF converter.

Renders extracted Urdu/Arabic/Persian Unicode text into a paginated PDF
using the Noto Nastaliq Urdu font (SIL Open Font License 1.1) with real
OpenType shaping via HarfBuzz (through fpdf2's text-shaping support).

Why Nastaliq is hard: Nastaliq is a *diagonal* script. Glyphs stack
downward and slide left as a word progresses, so it needs full GPOS/GSUB
OpenType shaping (cursive attachment, contextual alternates) — simple
"draw each character" renderers produce disconnected, broken text.
HarfBuzz (the same shaper used by Linux, Android, Chrome and Firefox)
handles this correctly, so we route all text through it.

Line height: Nastaliq stacks are very tall; we use a generous default
line spacing (2.0 x font size) which matches how InPage itself lays out
Noori Nastaliq text.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from fpdf import FPDF
from fpdf.enums import XPos, YPos

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
REGULAR_TTF = os.path.join(FONT_DIR, "NotoNastaliqUrdu-Regular.ttf")
BOLD_TTF = os.path.join(FONT_DIR, "NotoNastaliqUrdu-Bold.ttf")
JAMEEL_TTF = os.path.join(FONT_DIR, "JameelNooriNastaleeq.ttf")

# Paragraphs whose InPage font size is at least this much larger than the
# document's dominant body size are treated as headings.
HEADING_THRESHOLD_PT = 4.0

_LINE_BREAK = re.compile(r"[\r\n]+")

_BIDI_CONTROL = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


@dataclass
class RenderOptions:
    page_size: str = "A4"            # A4 | Letter | Legal
    margin_mm: float = 18.0
    body_size_pt: float = 12.0       # fallback when no style info recovered (InPage default)
    line_spacing: float = 1.7        # Nastaliq stack height (font natural = 2.5em)
    heading_spacing: float = 2.2
    scale: float = 1.0               # multiply all InPage font sizes by this
    detect_headings: bool = True
    center_headings: bool = True
    page_numbers: bool = False
    strip_kashida: bool = True
    justify: bool = False           # justify body paragraphs (InPage look)
    table_mode: str = "auto"        # auto | flow | table
    orientation: str = "P"          # P | L
    urdu_font: str = "jameel"       # jameel | noto
    exact_lines: bool = True        # keep original line breaks (no re-flow)
    doc_title: str | None = None


@dataclass
class Paragraph:
    text: str
    size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    align: str | None = None     # "C" | "R" (decoded/heuristic), None = auto
    # optional run-level styling: [(text, size_pt|None, bold, italic), ...]
    # — present only when the segment-wise conversion provably matches the
    # whole-paragraph conversion (guarded; see converter.py)
    segments: list | None = None


@dataclass
class Document:
    """Structured document ready for rendering."""
    title: str | None = None
    paragraphs: list[Paragraph] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _clean(text: str, strip_kashida: bool, collapse: bool = False) -> str:
    """collapse=True merges space runs (used for rebuilt TABLE cells, where
    runs are column padding); the default preserves the original spacing —
    InPage typists aligned MCQ options etc. with real space-bar runs."""
    text = _BIDI_CONTROL.sub("", text)
    text = text.replace("\u00ad", "")            # soft hyphen
    text = _LINE_BREAK.sub(" ", text)
    if collapse:
        text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()
    if strip_kashida:
        text = text.replace("\u0640", "")        # tatweel/kashida filler
    return text


_SECTION_DIVIDER = re.compile(r"^\(?\s*حصہ")          # ( حصہ اول) etc.
_DOTTED_LINE = re.compile(r"۔{3,}")                     # fill-in lines: نام ....
_SERIAL = re.compile(r"^[\u06F0-\u06F9]{1,3}[.\u06D4]")


def _infer_opening_centers(paragraphs: list[Paragraph]) -> None:
    """ROUND 9 FIX: the old rule centered the document's opening block
    (school name / exam title) as a guess — but the stream evidence shows
    these paragraphs were typed like every other line: default
    right-aligned InPage RTL paragraphs, with the typist's leading spaces
    INVISIBLE against the right margin (measured: school name carry 4–41
    leading space units that have no visual effect in a right-aligned RTL
    line). The user confirmed the originals were NOT centered, so opening
    paragraphs now render right-aligned like the rest of the document.
    Only explicit section dividers like ( حصہ دوم) stay centered."""
    for p in paragraphs:
        t = p.text.strip()
        if t and _SECTION_DIVIDER.match(t):
            p.align = "C"


def _dominant_size(paragraphs: list[Paragraph]) -> float | None:
    """Most common font size weighted by text length (the body size).

    Modified by inpage2pdf: the style table often covers only a few
    paragraphs (e.g. just the heading row of a table document). Using the
    dominant size of ONLY those paragraphs inflates the whole document to
    heading size (observed: a 183-line document rendered at 24pt across
    24 sparse pages). So the recovered dominant size is trusted only when
    styled paragraphs cover >= 40% of the document's characters; otherwise
    we fall back to InPage's default body size (12 pt)."""
    styled_chars = sum(len(p.text) for p in paragraphs if p.size_pt)
    total_chars = sum(len(p.text) for p in paragraphs)
    if not total_chars or styled_chars / total_chars < 0.4:
        return None
    tally: dict[float, int] = {}
    for p in paragraphs:
        if p.size_pt and p.text:
            tally[p.size_pt] = tally.get(p.size_pt, 0) + len(p.text)
    if not tally:
        return None
    return max(tally.items(), key=lambda kv: kv[1])[0]


class NastaliqPDF(FPDF):
    # Jameel Noori Nastaleeq has no separate Bold cut; its regular file is
    # registered for both styles so bold requests never crash. Noto keeps
    # a real bold. Jameel's stacks are shallower than Noto's, so its line
    # boxes are slightly tightened via _LINE_FACTOR.
    _LINE_FACTOR = {"jameel": 0.80, "noto": 1.0}

    def __init__(self, opts: RenderOptions, **kw):
        super().__init__(format=opts.page_size, orientation=opts.orientation, **kw)
        self.opts = opts
        custom = os.path.join(FONT_DIR, "Custom.ttf")
        # ---- italic & fake-bold support (round 10) ------------------------
        # Nastaliq fonts ship no italic/bold cuts. Italic = synthetically
        # sheared variant generated beside the font. Bold = fake bold:
        # when the style maps to the same regular file we double-print
        # with a hair offset (the same trick InPage itself uses).
        self._fake_bold = False
        italic_src = None
        if opts.urdu_font == "custom" and os.path.exists(custom):
            font_file = custom
            self.add_font("Nastaliq", "", custom)
            self.add_font("Nastaliq", "B", custom)
            self._fake_bold = True
            self.line_factor = self._LINE_FACTOR["jameel"]
        elif opts.urdu_font == "jameel" and os.path.exists(JAMEEL_TTF):
            font_file = JAMEEL_TTF
            self.add_font("Nastaliq", "", JAMEEL_TTF)
            self.add_font("Nastaliq", "B", JAMEEL_TTF)
            self._fake_bold = True
            italic_src = os.path.join(FONT_DIR, "JameelNooriNastaleeq-Italic.ttf")
            self.line_factor = self._LINE_FACTOR["jameel"]
        else:
            font_file = REGULAR_TTF
            self.add_font("Nastaliq", "", REGULAR_TTF)
            self.add_font("Nastaliq", "B", BOLD_TTF)
            self._fake_bold = False      # Noto has a real bold cut
            italic_src = os.path.join(FONT_DIR, "NotoNastaliqUrdu-Italic.ttf")
            self.line_factor = self._LINE_FACTOR["noto"]
        if italic_src and os.path.exists(italic_src):
            self.add_font("Nastaliq", "I", italic_src)
            self.add_font("Nastaliq", "BI", italic_src)
        else:                             # no italic available — degrade
            self.add_font("Nastaliq", "I", font_file)
            self.add_font("Nastaliq", "BI", font_file)
        # True advance of ONE typed space in the active font (Jameel 0.25em,
        # Noto 0.132em). Crucial: HarfBuzz's Arabic shaping substitutes the
        # space glyph to a near-zero-width variant (5/2048 em in Jameel), so
        # typed space runs are swallowed inside shaped cells. InPage renders
        # each typed space at the font's full advance — we must do the same,
        # which is why _cell_spaced() below positions words manually.
        self.space_em = 0.25
        try:
            from fontTools.ttLib import TTFont
            _tf = TTFont(font_file, lazy=True)
            _g = _tf.getBestCmap().get(0x20)
            if _g:
                self.space_em = _tf["hmtx"][_g][0] / _tf["head"].unitsPerEm
            _tf.close()
        except Exception:
            pass
        self.set_margins(opts.margin_mm, opts.margin_mm, opts.margin_mm)
        self.set_auto_page_break(True, margin=opts.margin_mm)
        # Full OpenType shaping through HarfBuzz, with RTL base direction
        # (fpdf2 also runs the Unicode bidirectional algorithm on the
        # text, so embedded English/numbers order correctly inside Urdu).
        self.set_text_shaping(
            use_shaping_engine=True,
            direction="rtl",
            script="arab",
            language="urd",
        )

    # ---------- typed space-run rendering (InPage word spacing) ----------

    def _space_mm(self) -> float:
        """Width of one typed space at the current font size, in mm."""
        return max(self.space_em * self.font_size_pt * 25.4 / 72.0, 0.1)

    # ---------- fake bold / styled drawing (round 10) ----------

    def _is_fake_bold(self) -> bool:
        return (self._fake_bold and "B" in (self.font_style or ""))

    def cell(self, w=None, h=None, text="", border=0, ln="DEPRECATED",
             align="", fill=False, link="", center=False, markdown=False,
             new_x=None, new_y=None, **kw):
        """cell() with synthetic bold: when the active style is bold but the
        font has no bold cut (Jameel), the glyphs are printed a second time
        with a hair offset — visibly bolder, exactly how InPage renders
        bold Noori Nastaliq."""
        kw2 = dict(h=h, text=text, border=border, align=align,
                   fill=fill, link=link, center=center, markdown=markdown, **kw)
        if new_x is not None:
            kw2["new_x"] = new_x
        if new_y is not None:
            kw2["new_y"] = new_y
        if not (text and self._is_fake_bold()):
            return super().cell(w, **kw2)
        # Resolve w EXPLICITLY before printing: fpdf2 recomputes w=0 from
        # the CURRENT x to the right margin, so a shifted second cell with
        # w=0 would anchor to the very same right edge and the shift would
        # be lost (verified: bold ratio was 1.10x with w=0, 1.45x fixed).
        x, y = self.get_x(), self.get_y()
        w_eff = (self.w - self.r_margin - x) if (w is None or w == 0) else w
        kw2["w"] = w_eff
        res = super().cell(**kw2)
        nx, ny = self.get_x(), self.get_y()
        off = max(self.font_size_pt / 72 * 25.4 * 0.045, 0.06)   # ~4.5% of size
        self.set_xy(x + off, y)
        super().cell(w=w_eff, h=h, text=text, border=0, align=align,
                     fill=False, link="", center=False, markdown=False)
        self.set_xy(nx, ny)
        return res

    def _multiline(self, w: float, h: float, text: str, align: str = "R",
                   x_left: float | None = None) -> None:
        """multi_cell replacement that routes every visual line through
        cell() — so synthetic bold and future run-level styling apply to
        wrapped text too. Line breaking is fpdf2's own (split_only)."""
        try:
            lines = self.multi_cell(w, h, text, align=align, split_only=True)
        except Exception:
            lines = [text]
        if not lines:
            lines = [""]
        y = self.get_y()
        for ln in lines:
            self.set_x(self.l_margin if x_left is None else x_left)
            self.cell(w, h, ln, align=align)
            y += h
            self.set_y(y)

    def _spaced_plan(self, w: float, text: str):
        """Split `text` into words and inter-word gap widths (mm).

        Returns (words, word_widths, gap_widths) or None when the words
        alone already exceed `w` (caller should fall back to wrapping)."""
        sp = self._space_mm()
        words, gaps = [], []
        for tok in re.split(r"( +)", text):
            if not tok:
                continue
            if tok[0] == " ":
                gaps.append(len(tok) * sp)
            else:
                words.append(tok)
        if not words:
            return None
        ws = [self.get_string_width(t) for t in words]
        if sum(ws) > w:
            return None
        if not gaps and len(words) > 1:           # only single spaces present
            gaps = [sp] * (len(words) - 1)
        return words, ws, gaps

    def _spaced_fits(self, w: float, text: str) -> bool:
        return self._spaced_plan(w, text) is not None

    def _cell_spaced(self, w: float, h: float, text: str, align: str = "R",
                     advance: bool = True) -> bool:
        """Render one visual line that contains typed space runs.

        HarfBuzz shaping collapses space runs to almost nothing (the font's
        shaped Arabic space is ~zero-width), so a plain cell() would glue
        MCQ options together. Instead every word is drawn as its own shaped
        cell and each typed space contributes its full font advance — the
        same word-spacing model InPage itself uses. If the whole line is
        wider than `w`, the gaps (never the words) are scaled down to fit.
        RTL: the logical first word is placed right-most."""
        plan = self._spaced_plan(w, text)
        if plan is None:
            return False
        words, ws, gaps = plan
        word_total, gap_total = sum(ws), sum(gaps)
        if word_total + gap_total > w and gap_total > 0:
            f = max(w - word_total, 0.0) / gap_total
            gaps = [g * f for g in gaps]
        total = word_total + sum(gaps)
        x0, y = self.get_x(), self.get_y()
        if align == "R":
            x_pen = x0 + w
        elif align == "C":
            x_pen = x0 + (w + total) / 2
        else:                                      # L: block flush left
            x_pen = x0 + total
        # Ordered-token walk: tokens are consumed in logical order so a
        # LEADING space run indents the visual RIGHT edge (RTL) instead of
        # landing between words, and a trailing run sits at the visual left.
        wi = gi = 0
        for tok in re.split(r"( +)", text):
            if not tok:
                continue
            if tok[0] == " ":
                if gi < len(gaps):
                    x_pen -= gaps[gi]
                    gi += 1
            else:
                if wi < len(ws):
                    x_pen -= ws[wi]
                    self.set_xy(x_pen, y)
                    self.cell(ws[wi], h, words[wi], align="L")
                    wi += 1
        if advance:
            self.set_y(y + h)
        return True

    def _cell_styled(self, w: float, h: float, segments, align: str = "R",
                     scale: float = 1.0, base_size: float = 12.0) -> bool:
        """Render one visual line whose words carry RUN-LEVEL styles
        (inline bold/italic/size from the InPage style runs).

        Every word is drawn as its own shaped cell with its own font style
        and size; typed space runs keep their full width (same model as
        _cell_spaced). Returns False when the words cannot fit (caller
        falls back to the dominant-style path)."""
        sp = self._space_mm()
        words = []            # (word, size, style)
        seg_of = []           # parallel: segment style per word
        # split all segment texts into words, tracking styles
        for txt, size_pt, bold, italic in segments:
            style = ("B" if bold else "") + ("I" if italic else "")
            sz = min(max((size_pt or base_size), 7.0), 48.0)
            for tok in re.split(r"( +)", txt):
                if not tok:
                    continue
                if tok[0] == " ":
                    words.append((" " * len(tok), sz, style))
                else:
                    words.append((tok, sz, style))
        if not words:
            return False
        # measure every word with its own font style/size
        sp_by_key = {}
        ws, gaps = [], []
        for word, sz, style in words:
            key = (style, round(sz, 2))
            if key not in sp_by_key:
                self.set_font("Nastaliq", style, sz)
                sp_by_key[key] = self._space_mm()
            if word[0] == " ":
                gaps.append(len(word) * sp_by_key[key])
            else:
                self.set_font("Nastaliq", style, sz)
                ws.append(self.get_string_width(word))
        if not ws:
            return False
        word_total, gap_total = sum(ws), sum(gaps)
        if word_total > w:
            return False
        if word_total + gap_total > w and gap_total > 0:
            f = max(w - word_total, 0.0) / gap_total
            gaps = [g * f for g in gaps]
        total = word_total + sum(gaps)
        x0, y = self.get_x(), self.get_y()
        if align == "R":
            x_pen = x0 + w
        elif align == "C":
            x_pen = x0 + (w + total) / 2
        else:
            x_pen = x0 + total
        # ordered-token walk (leading gaps indent the visual RIGHT edge)
        wi = gi = 0
        for word, sz, style in words:
            if word[0] == " ":
                if wi == 0 and not ws:
                    continue
                x_pen -= gaps[gi] if gi < len(gaps) else 0.0
                gi += 1
            else:
                x_pen -= ws[wi]
                self.set_font("Nastaliq", style, sz)
                self.set_xy(x_pen, y)
                self.cell(ws[wi], h, word, align="L")
                wi += 1
        self.set_y(y + h)
        return True

    def _spaced_wrap(self, w: float, text: str) -> list[str]:
        """Greedy word-wrap that PRESERVES typed space runs within a line.

        Breaks happen at word boundaries (the gap that crosses the break is
        consumed by the wrap, exactly like InPage); every gap kept on a
        line keeps its original space count. Used for table cells, where
        long content genuinely must wrap."""
        sp = self._space_mm()
        seq: list[list] = []                       # [word, following_gap_mm]
        for tok in re.split(r"( +)", text):
            if not tok:
                continue
            if tok[0] == " ":
                if seq:
                    seq[-1][1] = len(tok) * sp
            else:
                seq.append([tok, 0.0])
        lines, cur, cur_w = [], [], 0.0
        for word, gap in seq:
            ww = self.get_string_width(word)
            need = ww + (sp if cur else 0.0)       # 1 space joins words on a line
            if cur and cur_w + need > w:
                lines.append(cur)
                cur, cur_w = [[word, gap]], ww
            else:
                cur_w += need if cur else ww
                cur.append([word, gap])
        if cur:
            lines.append(cur)
        out = []
        for ln in lines:
            s = ""
            for i, (word, gap) in enumerate(ln):
                s += word
                if i < len(ln) - 1:
                    s += " " * max(1, int(round(gap / sp)) if sp else 1)
            out.append(s)
        return out

    def footer(self):
        if not self.opts.page_numbers:
            return
        self.set_y(-12)
        self.set_font("Nastaliq", "", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"{self.page_no()}", align="C")


def render_pdf(doc: Document, out_path: str, opts: RenderOptions | None = None) -> None:
    opts = opts or RenderOptions()
    pdf = NastaliqPDF(opts)
    body = _dominant_size(doc.paragraphs) or opts.body_size_pt
    body = min(max(body * opts.scale, 7.0), 36.0)
    usable = pdf.w - pdf.l_margin - pdf.r_margin

    # --- optional cover title (the document/file name) -------------------
    if doc.title:
        pdf.add_page()
        pdf.set_font("Nastaliq", "B", body + 6)
        pdf.set_text_color(20, 20, 20)
        pdf._multiline(usable, (body + 6) * opts.heading_spacing * 0.72,
                       doc.title, align="C")

    _infer_opening_centers(doc.paragraphs)

    # GAP PRESERVATION: in the InPage stream every empty paragraph is a
    # blank line the original typist entered — i.e. the document's real
    # vertical spacing (after the title block, between questions/rows,
    # between sections). Each one is rendered as a FULL blank line at the
    # body line height, never collapsed, so the PDF's vertical rhythm
    # matches the original document.
    last_content = -1
    for _i, _p in enumerate(doc.paragraphs):
        if _clean(_p.text, opts.strip_kashida):
            last_content = _i

    first_para = not bool(doc.title)
    blank_h = None
    for para_i, para in enumerate(doc.paragraphs):
        text = _clean(para.text, opts.strip_kashida)
        if not text:
            if para_i > last_content:      # trailing blanks: no phantom pages
                break
            if not first_para:
                if blank_h is None:
                    _b = body * opts.scale
                    blank_h = _b * opts.line_spacing * 0.42 * pdf.line_factor
                if pdf.get_y() + blank_h > pdf.h - pdf.b_margin:
                    pdf.add_page()
                else:
                    pdf.ln(blank_h)
            continue

        size = (para.size_pt or body) * opts.scale
        size = min(max(size, 7.0), 48.0)
        is_heading = (opts.detect_headings
                      and para.size_pt is not None
                      and para.size_pt >= body + HEADING_THRESHOLD_PT)
        if is_heading:
            size = max(size, body + 2)
            pdf.set_font("Nastaliq", "B", size)
            spacing = opts.heading_spacing
        else:
            size = min(size, body + HEADING_THRESHOLD_PT - 0.5)  # keep hierarchy sane
            style = ("B" if para.bold else "") + ("I" if para.italic else "")
            pdf.set_font("Nastaliq", style, size)
            spacing = opts.line_spacing
        pdf.set_text_color(20, 20, 20)

        line_h = size * spacing * 0.42 * pdf.line_factor  # pt -> mm-ish scaled line box
        if first_para:
            pdf.add_page()
            first_para = False
        elif is_heading:
            pdf.ln(max(4.0, line_h * 0.35))

        # NOTE: fpdf2's "J" alignment parks each paragraph's LAST line on
        # the LEFT (LTR convention) — wrong for Urdu. Verified by measuring
        # glyph positions: last line ended 51pt short of the right margin.
        # Right alignment is the correct RTL default.
        if is_heading and opts.center_headings:
            align = "C"
        elif para.align == "C":
            align = "C"
        else:
            align = "R"

        # Line-faithful rendering: InPage typists broke every line with
        # Enter, so each stored paragraph IS a visual line of the original
        # page. Re-flowing it at our page width moves text away from where
        # it was in the original document. So: render each stored line as
        # one visual line; wrap ONLY if it cannot fit on the page at all.
        # fpdf2's get_string_width collapses space runs, so estimate the
        # true width by adding the extra spaces' width back in (each typed
        # space measures at the font's real U+0020 advance).
        try:
            sp_w = pdf._space_mm()
        except Exception:
            sp_w = pdf.font_size_pt * 0.25
        def _est_w(t: str) -> float:
            collapsed = re.sub(r" {2,}", " ", t)
            extra = len(t) - len(collapsed)
            try:
                base = pdf.get_string_width(collapsed)
            except Exception:
                return usable + 1
            return base + extra * sp_w
        pieces = text.split("\n") if not opts.exact_lines else [text]
        for piece in pieces:
            if para.segments:
                if pdf.get_y() + line_h > pdf.h - pdf.b_margin:
                    pdf.add_page()
                if pdf._cell_styled(usable, line_h, para.segments, align,
                                    base_size=min(size, 48.0)):
                    pdf.set_x(pdf.l_margin)      # inline-styled line drawn
                    continue
                # segments did not fit one line — fall through to the
                # dominant-style paths below (never re-flow styled words)
            if "  " in piece and pdf._spaced_fits(usable, piece):
                # Typed space-run line (MCQ options etc.): shaped cells
                # swallow space runs, so render it with manual word
                # placement — every typed space keeps its full width.
                if pdf.get_y() + line_h > pdf.h - pdf.b_margin:
                    pdf.add_page()
                pdf.set_x(pdf.l_margin)
                pdf._cell_spaced(usable, line_h, piece, align)
                pdf.set_x(pdf.l_margin)
            elif opts.exact_lines and _est_w(piece) <= usable:
                if pdf.get_y() + line_h > pdf.h - pdf.b_margin:
                    pdf.add_page()
                pdf.set_x(pdf.l_margin)
                pdf.cell(0, line_h, piece, align=align, new_x=XPos.LMARGIN,
                         new_y=YPos.NEXT)
            else:
                pdf._multiline(usable, line_h, piece, align=align)

    if first_para:  # document was empty
        pdf.add_page()
        pdf.set_font("Nastaliq", "", body)
        pdf._multiline(usable, body * opts.line_spacing * 0.42, " ", align="R")

    pdf.output(out_path)


# ===================== table layout reconstruction =====================
#
# InPage table documents (scheme-of-work sheets, result cards, date
# sheets) store their cells in the text stream row by row. The visible
# signature: a repeating "serial" paragraph (Urdu digits + '.') that
# starts each row, with a fixed number of paragraphs between consecutive
# serials (= the column count). When that pattern is confidently
# detected we rebuild the document as a real bordered table with the
# same reading order, right-to-left column order, header row, and
# per-column widths derived from content — a far closer match to the
# original InPage layout than flowing prose.

_SERIAL_RE = re.compile(r"^[\u06F0-\u06F9\u0660-\u0669]{1,3}[\u06D4.]+$")


def detect_table(paragraphs: list[Paragraph]):
    """Return dict(cols, header, title_idx, rows) or None if no confident table."""
    items = []
    for idx, p in enumerate(paragraphs):
        t = _clean(p.text, True)          # space runs PRESERVED (cell content)
        if t:
            items.append((idx, t))
    if len(items) < 12:
        return None
    ser = [k for k, (_, t) in enumerate(items) if _SERIAL_RE.match(t)]
    if len(ser) < 5:
        return None
    gaps = [b - a for a, b in zip(ser, ser[1:])]
    from collections import Counter
    cols, cnt = Counter(gaps).most_common(1)[0]
    if not (3 <= cols <= 8) or cnt / len(gaps) < 0.7:
        return None

    first = ser[0]
    header = None
    header_start_idx = None
    if first >= cols:
        cand = [items[first - cols + j][1] for j in range(cols)]
        if not any(_SERIAL_RE.match(t) for t in cand):
            header = cand
            header_start_idx = items[first - cols][0]

    rows = []
    bounds = list(zip(ser, ser[1:])) + [(ser[-1], len(items))]
    for a, b in bounds:
        cells = [items[a + j][1] for j in range(min(cols, b - a))]
        if b - a > cols:                       # extra paragraphs: merge into last cell
            extra = " ".join(items[a + j][1] for j in range(cols, b - a))
            cells[-1] = cells[-1] + " " + extra
        while len(cells) < cols:
            cells.append("")
        rows.append(cells)

    title_end = header_start_idx if header is not None else items[first][0]
    return {"cols": cols, "header": header, "rows": rows, "title_end": title_end}


def _column_widths(rows, header, cols, usable_mm):
    """Content-proportional column widths with a hard per-column minimum
    (a Nastaliq glyph at 12pt needs ~12mm; narrower cells cannot render)."""
    MIN_W = 13.0
    max_len = []
    for c in range(cols):
        vals = [r[c] for r in rows if c < len(r)]
        if header:
            vals += [header[c]]
        m = max((len(re.sub(r" {2,}", " ", v)) for v in vals), default=4)
        max_len.append(max(3.0, min(m, 60.0)))
    serial_col = all(_SERIAL_RE.match(r[0]) for r in rows if r) and cols > 2
    if serial_col:
        weights = [7.0] + [max(1.0, m) for m in max_len[1:]]
    else:
        weights = [max(1.0, m) for m in max_len]
    total = sum(weights)
    widths = [usable_mm * w / total for w in weights]
    # enforce minimum, then take the excess proportionally from wider cols
    widths = [max(w, MIN_W) for w in widths]
    over = sum(widths) - usable_mm
    if over > 0:
        flex_total = sum(w for w in widths if w > MIN_W)
        if flex_total > 0:
            for i, w in enumerate(widths):
                if w > MIN_W:
                    widths[i] = max(MIN_W, w - over * (w / flex_total))
        s = sum(widths)
        if s > usable_mm:                      # last resort: uniform shrink
            widths = [w * usable_mm / s for w in widths]
    return widths


def render_table_pdf(doc: Document, table: dict, out_path: str,
                     opts: RenderOptions) -> None:
    """Render a detected InPage table document as a bordered RTL table."""
    pdf = NastaliqPDF(opts)
    body = _dominant_size(doc.paragraphs) or opts.body_size_pt
    body = min(max(body * opts.scale, 7.0), 20.0)

    usable = pdf.w - 2 * opts.margin_mm
    pad_h, pad_v = 1.3, 1.4
    line_h = body * opts.line_spacing * 0.42 * pdf.line_factor

    # --- title block (paragraphs before the table) ---
    title_paras = [p for p in doc.paragraphs[:table["title_end"]]]
    if title_paras:
        pdf.add_page()
        big = None
        for p in title_paras:                      # biggest recovered size wins
            if p.size_pt:
                big = max(big or 0, p.size_pt)
        size_t = min(((big or 16.0) * opts.scale), 26.0)
        first = True
        for p in title_paras:
            t = _clean(p.text, opts.strip_kashida)
            if not t:
                continue
            _st = ("B" if (first or p.bold) else "") + ("I" if p.italic else "")
            pdf.set_font("Nastaliq", _st, size_t if first else min(size_t, 14))
            pdf.set_text_color(20, 20, 20)
            th = size_t * opts.line_spacing * 0.45
            uw = pdf.w - pdf.l_margin - pdf.r_margin
            # ROUND 9 FIX: title block was force-centered; the originals are
            # right-aligned InPage paragraphs (leading spaces are invisible
            # in RTL right-aligned lines), so render like the flow layout.
            if "  " in t and pdf._spaced_fits(uw, t):
                pdf.set_x(pdf.l_margin)
                pdf._cell_spaced(uw, th, t, "R")
            else:
                pdf._multiline(uw, th, t, align="R")
            first = False
        pdf.ln(4)

    widths = _column_widths(table["rows"], table["header"], table["cols"], usable)
    # RTL: column 0 (serial) is the RIGHTMOST column
    xs = []
    x = pdf.w - opts.margin_mm
    for w in widths:
        xs.append(x - w)
        x -= w

    def draw_row(cells, bold=False, fill=False):
        # measure needed height
        n_lines = 1
        for c in range(table["cols"]):
            txt = cells[c] if c < len(cells) else ""
            if not txt:
                continue
            pdf.set_font("Nastaliq", "B" if bold else "", body)
            cw = widths[c] - 2 * pad_h
            if "  " in txt:
                n_lines = max(n_lines, len(pdf._spaced_wrap(cw, txt)))
                continue
            lines = pdf.multi_cell(cw, line_h, txt,
                                   align="R", split_only=True)
            n_lines = max(n_lines, len(lines) if lines else 1)
        row_h = n_lines * line_h + 2 * pad_v
        if pdf.get_y() + row_h > pdf.h - opts.margin_mm - 6:
            pdf.add_page()
            if table["header"] and not bold:      # repeat header after break
                draw_row(table["header"], bold=True, fill=True)
        y = pdf.get_y()
        for c in range(table["cols"]):
            txt = cells[c] if c < len(cells) else ""
            if fill:
                pdf.set_fill_color(232, 236, 244)
                pdf.rect(xs[c], y, widths[c], row_h, style="F")
            pdf.set_draw_color(90, 90, 90)
            pdf.set_line_width(0.25)
            pdf.rect(xs[c], y, widths[c], row_h)
            if txt:
                pdf.set_xy(xs[c] + pad_h, y + pad_v)
                pdf.set_font("Nastaliq", "B" if bold else "", body)
                pdf.set_text_color(20, 20, 20)
                align = "C" if (bold or _SERIAL_RE.match(txt)) else "R"
                cw = widths[c] - 2 * pad_h
                if "  " in txt:
                    y_cur = pdf.get_y()
                    for ln in pdf._spaced_wrap(cw, txt):
                        pdf.set_xy(xs[c] + pad_h, y_cur)
                        if not pdf._cell_spaced(cw, line_h, ln, align):
                            pdf.set_xy(xs[c] + pad_h, y_cur)
                            pdf._multiline(cw, line_h, ln, align=align,
                                           x_left=xs[c] + pad_h)
                        y_cur += line_h
                else:
                    pdf._multiline(cw, line_h, txt, align=align,
                                   x_left=xs[c] + pad_h)
        pdf.set_y(y + row_h)

    start_y = pdf.get_y()
    if pdf.page == 0 or pdf.get_y() < opts.margin_mm + 1:
        pdf.add_page()
    if table["header"]:
        draw_row(table["header"], bold=True, fill=True)
    for row in table["rows"]:
        draw_row(row)
    if pdf.page == 0:
        pdf.add_page()
    pdf.output(out_path)
