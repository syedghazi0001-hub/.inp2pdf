"""High-level InPage (.inp) → PDF conversion pipeline.

Stages:
  1. Container:  .inp is an OLE2 compound document; open it with olefile
                 and read the `InPage100` stream (the story data).
  2. Structure:  walk the stream — 0x04-prefixed byte pairs are Urdu
                 glyphs, 0x0D + 4 bytes is a paragraph link, bare ASCII
                 is literal text, long non-text regions are layout
                 structure. Also recovers the per-run style table
                 (font size, bold) that follows the story text.
  3. Encoding:   convert the proprietary InPage byte pairs to Unicode
                 (ordered rules; anything unmapped is reported, never
                 guessed). Optionally fix well-known conversion
                 artifacts (hamza joins, missing spaces).
  4. Render:     shape and paginate the Unicode text with HarfBuzz +
                 Noto Nastaliq Urdu into the output PDF.

Everything runs locally — no file ever leaves the machine it runs on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import engine, textfix
import re as _re

from .inp_reader import (extract_document, extract_document_ex,
                        extract_font_names, is_inp, _page_setup)
from .pdf_builder import (Document, Paragraph, RenderOptions, detect_table,
                          render_pdf, render_table_pdf)


# InPage stores multi-digit numbers in REVERSED digit order (verified on
# raw stream bytes: date stored 6,2,0,2 for 2026; question numbers stored
# 2,1 for 12; marks stored 0,1 for 10). Unicode rendering needs logical
# order, so every contiguous Urdu-digit run is reversed after decoding.
_URDU_DIGITS = "\u06F0-\u06F9\u0660-\u0669"
_DIGIT_RUN = _re.compile("[" + _URDU_DIGITS + "]{2,}")


def _fix_digit_order(text: str) -> str:
    return _DIGIT_RUN.sub(lambda m: m.group(0)[::-1], text)


@dataclass
class ConversionResult:
    ok: bool
    pdf_path: str | None = None
    error: str | None = None
    stats: dict = field(default_factory=dict)
    unmapped: list = field(default_factory=list)
    fixes: list = field(default_factory=list)
    preview_text: str = ""
    preview_paras: int = 0          # paragraphs included in preview_text


def _segments_from_runs(raw_para, st):
    """Convert run-level style segments into styled text segments.

    Guarded: the reader returns [(char_len, size, bold, italic), ...] over
    the INTERMEDIATE text; converting each slice separately is only valid
    when the concatenation equals the whole-paragraph conversion. Where it
    does not (multi-byte rules spanning a boundary), we return None and
    the paragraph renders with its dominant style — never a wrong guess."""
    if not st or not st.get("runs") or len(st["runs"]) < 2:
        return None
    runs = st["runs"]
    if len({r[1:] for r in runs}) < 2:          # all segments same style
        return None
    try:
        from . import engine as _eng
        from .inp_reader import MARKER as _M
        s = raw_para
        # cuts in intermediate char space (same logic as the reader)
        cuts, acc = [], 0
        for ln, *_ in runs[:-1]:
            acc += ln
            cuts.append(min(acc, len(s)))
        cuts = [c - 1 if 0 < c < len(s) and s[c - 1] == _M else c for c in cuts]
        segs = []
        prev = 0
        for c in cuts:
            segs.append(s[prev:c]); prev = c
        segs.append(s[prev:])
        # Convert slices WITHOUT edge-stripping (the whole paragraph only
        # strips its outer edges); then strip exactly those outer edges so
        # the concatenation mirrors the whole-paragraph conversion.
        try:
            converted = [_eng.convert(x, keep_edges=True)[0] for x in segs]
        except TypeError:
            converted = [_eng.convert(x)[0] for x in segs]
        whole = _eng.convert(s)[0]
        if converted:
            converted[0] = converted[0].lstrip()
            converted[-1] = converted[-1].rstrip()
        if "".join(converted) != whole:
            # a multi-char rule spans a cut — try nudging cuts ±1..3
            # (marker-pair aligned, monotone) before giving up
            fixed = False
            for delta in (1, -1, 2, -2, 3, -3):
                c2 = sorted(set(min(max(c + delta, 1), len(s) - 1) for c in cuts))
                c2 = [c - 1 if 0 < c < len(s) and s[c - 1] == _M else c for c in c2]
                segs2, prev2 = [], 0
                for c in c2:
                    segs2.append(s[prev2:c]); prev2 = c
                segs2.append(s[prev2:])
                try:
                    conv2 = [_eng.convert(x, keep_edges=True)[0] for x in segs2]
                except TypeError:
                    continue
                conv2[0] = conv2[0].lstrip()
                conv2[-1] = conv2[-1].rstrip()
                if "".join(conv2) == whole and len(conv2) == len(runs):
                    converted, fixed = conv2, True
                    break
            if not fixed:
                return None
        out, i = [], 0
        for (ln, size, bold, italic), txt in zip(runs, converted):
            if txt:
                out.append((txt, size, bold, italic))
        return out or None
    except Exception:
        return None


def convert_file(inp_path: str, pdf_path: str | None = None,
                 opts: RenderOptions | None = None,
                 apply_fixes: bool = True,
                 latin_digits: bool = False,
                 title_from_filename: bool = True) -> ConversionResult:
    """Convert one .inp file to PDF. Raises nothing user-hostile."""
    try:
        if not os.path.exists(inp_path):
            return ConversionResult(False, error=f"File not found: {inp_path}")
        if not is_inp(inp_path):
            return ConversionResult(
                False,
                error=("Not an InPage document (missing OLE2 signature "
                       "D0 CF 11 E0). The .inp extension is also used by "
                       "engineering software (Abaqus/EPANET); this tool "
                       "only reads InPage files."))

        blocks, styles, grid = extract_document_ex(inp_path)

        paragraphs: list[Paragraph] = []
        unmapped: list = []
        fixes: list = []
        preview: list[str] = []
        grid_table = None
        grid_cells = 0

        for bi, block in enumerate(blocks):
            # A detected grid table (InPage Table tool) replaces this
            # block's flow entirely: bordered RTL grid, one cell per
            # linked paragraph, and NO phantom blank lines for the
            # empty cells.
            if grid is not None and bi == 0:
                trows, tsizes, tbolds = [], [], []
                for r in range(grid["rows"]):
                    trow, srow, brow = [], [], []
                    for c in range(grid["cols"]):
                        cell = grid["cells"][r][c]
                        if cell is None:
                            trow.append(""); srow.append(None)
                            brow.append(False)
                            continue
                        raw, st = cell
                        uni, report = engine.convert(raw,
                                                     latin_digits=latin_digits)
                        uni = _fix_digit_order(uni)
                        if report:
                            unmapped.extend(report)
                        if apply_fixes and uni:
                            uni, changed = textfix.fix_spelling(uni)
                            for ch in changed:
                                ch["block"], ch["para"] = bi, r
                                fixes.append(ch)
                        sz = (st or {}).get("size")
                        grid_cells += 1
                        if uni:
                            preview.append(uni)
                        trow.append(uni)
                        srow.append(sz)
                        brow.append(bool((st or {}).get("bold")))
                    trows.append(trow)
                    tsizes.append(srow)
                    tbolds.append(brow)
                # cells without an explicit size use InPage's document
                # default (Noori Nastaliq 13pt in a new document) — NOT
                # the most common explicit size, which would silently
                # resize the typist's default-sized text
                dom = 13.0
                grid_table = {"cols": grid["cols"], "header": None,
                              "rows": trows, "title_end": len(paragraphs),
                              "uniform": True, "body_size": dom,
                              "cell_sizes": tsizes, "cell_bolds": tbolds,
                              "geom": grid.get("geom")}
                continue
            block_styles = styles[bi] if bi < len(styles) else []
            for pi, raw_para in enumerate(block.split("\n")):
                uni, report = engine.convert(raw_para, latin_digits=latin_digits)
                uni = _fix_digit_order(uni)
                if report:
                    unmapped.extend(report)
                if apply_fixes and uni:
                    uni, changed = textfix.fix_spelling(uni)
                    for c in changed:
                        c["block"], c["para"] = bi, pi
                        fixes.append(c)
                st = block_styles[pi] if pi < len(block_styles) else None
                paragraphs.append(Paragraph(
                    text=uni,
                    size_pt=(st or {}).get("size"),
                    bold=bool((st or {}).get("bold")),
                    italic=bool((st or {}).get("italic")),
                    segments=_segments_from_runs(raw_para, st),
                ))
            # Between InPage text frames (blocks) the original had separate
            # boxes — keep a visible one-line separation in the output.
            if bi < len(blocks) - 1:
                paragraphs.append(Paragraph(text=""))
                if uni:
                    preview.append(uni)

        # Document title: first non-empty paragraph if it is much larger
        # than the body, else the filename.
        title = None
        if title_from_filename:
            title = os.path.splitext(os.path.basename(inp_path))[0]

        doc = Document(title=title, paragraphs=paragraphs, stats={
            "blocks": len(blocks),
            "paragraphs": len(paragraphs) + grid_cells,
            "grid_table_cells": grid_cells,
            "characters": sum(len(p.text) for p in paragraphs),
            "with_style": sum(1 for p in paragraphs if p.size_pt),
            "fonts": extract_font_names(inp_path),
            "styled_segments": sum(1 for p in paragraphs if p.segments),
        })

        if pdf_path is None:
            pdf_path = os.path.splitext(inp_path)[0] + ".pdf"
        os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
        opts = opts or RenderOptions()
        # ROUND 15d: give the renderer the file's own text-area width
        # (page minus its stored margins) so stored lines that InPage
        # itself fit never re-flow in our (narrower) tuned margin.
        def _read_stream(path: str) -> bytes:
            import olefile
            ole = olefile.OleFileIO(path)
            try:
                return ole.openstream("InPage100").read()
            finally:
                ole.close()

        try:
            _ps = _page_setup(_read_stream(inp_path))
            if _ps:
                opts.file_usable_mm = (_ps["page_w_mm"]
                                       - _ps["left_mm"] - _ps["right_mm"])
        except Exception:
            pass

        layout_used = "flow"
        if grid_table is not None:
            # paragraphs gathered so far render as the leading block,
            # later blocks flow after the table
            grid_table["post"] = paragraphs[grid_table["title_end"]:]
            render_table_pdf(doc, grid_table, pdf_path, opts)
            layout_used = "grid-table"
        elif opts.table_mode != "flow":
            table = detect_table(paragraphs)
            if table and (opts.table_mode == "table" or len(table["rows"]) >= 4):
                render_table_pdf(doc, table, pdf_path, opts)
                layout_used = "table"
            elif opts.table_mode == "table":
                render_pdf(doc, pdf_path, opts)
            else:
                render_pdf(doc, pdf_path, opts)
        else:
            render_pdf(doc, pdf_path, opts)

        # dedupe unmapped report
        seen, uniq = set(), []
        for e in unmapped:
            if e["code"] not in seen:
                seen.add(e["code"])
                uniq.append(e)

        doc.stats["layout"] = layout_used
        return ConversionResult(
            True, pdf_path=pdf_path,
            stats=doc.stats, unmapped=uniq, fixes=fixes,
            preview_text="\n\n".join(preview),
            preview_paras=len(preview))
    except ValueError as exc:      # controlled extraction failures
        return ConversionResult(False, error=str(exc))
    except Exception as exc:       # noqa: BLE001 — surface anything else
        return ConversionResult(False, error=f"{type(exc).__name__}: {exc}")
