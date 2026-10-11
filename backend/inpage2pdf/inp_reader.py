#
# .inp reader — adapted from the Herf project (https://github.com/talhabinmunir/herf),
# whose format findings derive from ltrc/inPageToUnicode (GPL-2.0) and from
# comparison against InPage's own exports. Included under GPL-2.0 with attribution.
"""Direct reader for InPage .INP binary files.

An .INP file is an OLE2 Compound Document with two streams:
DocumentInfo and InPage100. Text lives in InPage100 as 0x04-marker
byte pairs (the same encoding as InPage's text export), interleaved
with structure: '\\r' + 4 bytes marks a paragraph boundary, tabs and
printable ASCII are stored as bare bytes, and null-heavy regions
are layout/style structure, not text.

Verified against one real file (para1.inp, ~222k chars): output was
identical to InPage's own Unicode-text export except that the export
contained 4 stray junk bytes the binary does not — the direct read
was cleaner. The paragraph-boundary rule (\\r + exactly 4 structure
bytes) and the junk-region threshold below are derived from that one
file; other InPage versions may differ. Anything this parser is not
sure about ends up in the unmapped report, never silently guessed.

Character formatting (decoded against the same file, cross-checked
against InPage's own PDF export of it):

- The 4 bytes after each '\\r' are a little-endian uint32 equal to the
  byte length of the NEXT paragraph + 1 — a skip-ahead link, no style
  content (verified exact on all 570 paragraph pairs).
- Immediately after the story text (last '\\r' + 4 link bytes) comes a
  6-byte marker 00 00 FF FF FF FF, then a character-run style table:
  records of <run_bytes u32> <reclen u32> <0x0001 u16> <bloblen u32>
  <blob>, where reclen == 6 + bloblen and run_bytes counts text bytes
  exactly as stored (marker pair = 2, bare ASCII/tab = 1, '\\r' = 1,
  the 4 link bytes not counted).
- Each blob is a token stream of (opcode u16, value) pairs. Decoded
  opcodes: 0x7f00 value u32 = font size in 1/2000 pt (48000 = 24 pt;
  matches the PDF export exactly for 22/24/30 pt text); 0x7f08 value
  u16 = bold flag; 0x7f80 value u16 = font index (table not yet
  located). Opcodes 0x0080/0x7f06/0x7f8a (u32) and
  0x0040/0x7f02/0x0201/0x0202 (u16) are structurally understood
  (their value widths are proven by the fact that all 1300+ run blobs
  in the reference file parse cleanly) but their meanings are unknown
  and they are ignored. A blob containing an opcode outside this set
  is reported as style None — never guessed.
- The DocumentInfo stream in the reference file is all zeros; it holds
  no style table.
"""

import struct

import olefile

_UNDEFINED = {0x81, 0x8D, 0x8F, 0x90, 0x9D}

MARKER = ""                     # intermediate-form marker prefix


def _b2c(b: int) -> str:
    return chr(b) if b in _UNDEFINED else bytes([b]).decode("cp1252")


def is_inp(path_or_bytes) -> bool:
    """OLE2 magic check: D0 CF 11 E0."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        head = bytes(path_or_bytes[:8])
    else:
        with open(path_or_bytes, "rb") as f:
            head = f.read(8)
    return head[:4] == b"\xd0\xcf\x11\xe0"


def _find_long_run(data: bytes, pos: int, min_chars: int = 20) -> int:
    """Next offset >= pos where >= min_chars consecutive 0x04-pairs begin."""
    n = len(data)
    i = pos
    while i < n - 2 * min_chars:
        if data[i] == 0x04:
            j, c = i, 0
            while j < n - 1 and data[j] == 0x04:
                j += 2
                c += 1
                if c >= min_chars:
                    return i
            i = j + 1
        else:
            i += 1
    return -1


# Modified by inpage2pdf: herf's original reader resumed only at runs of
# >= 20 marker pairs, which abandons the rest of a document when an
# embedded object (image/table data) interrupts the story and the
# following text has shorter runs. We progressively relax the threshold
# so extraction continues; unmapped codes are still reported, never guessed.
def _find_text_resume(data: bytes, pos: int) -> int:
    for min_chars in (20, 10, 6, 3):
        i = _find_long_run(data, pos, min_chars)
        if i != -1:
            return i
    return -1


def _find_text_start(data: bytes) -> int:
    # Progressive thresholds: normal documents open with a long text run,
    # but a grid-table story can be a handful of 3-4 pair cell texts (the
    # user's real table files) — still require >=2 pairs so structure
    # bytes are never mistaken for text.
    for min_chars in (20, 8, 4, 2):
        i = _find_long_run(data, 0, min_chars)
        if i != -1:
            return i
    return -1


# Style-blob opcodes and their value widths. Widths were proven by
# parsing every run blob in the reference file with them; meanings are
# only claimed for the opcodes actually used in _parse_style_blob.
# Round 10 additions (proven on the projection files' 190-run table):
#   0x7FE0 carries TWO u32 values, 0x7FE1 one u16, 0x7FE2 one u32 —
#   with these widths every one of the 190 blobs parses cleanly.
_OP_U32 = {0x7f00, 0x7f06, 0x7f8a, 0x0080, 0x7fe2}
_OP_U16 = {0x7f80, 0x7f08, 0x7f02, 0x0201, 0x0202, 0x0040, 0x7fe1}
_OP_U32X2 = {0x7fe0}                        # opcode + u32 + u32


def _parse_style_blob(blob: bytes) -> dict | None:
    """Decode one run's attribute blob. Returns {'size': pt|None,
    'bold': bool, 'italic': bool, 'flags': [(opcode, value), ...]} or
    None if any token is unrecognized.
    'flags' reports every opcode seen (including structurally-known but
    semantically-unknown ones like 0x7F02/0x7FE0) so new meanings can be
    verified against ground truth before they are ever applied — this
    tool never guesses a formatting meaning."""
    size = None
    bold = False
    italic = False
    cell = None                          # 0x7FE0 second id (table cell)
    flags: list[tuple[int, int]] = []
    i = 0
    n = len(blob)
    while i + 2 <= n:
        op = struct.unpack_from("<H", blob, i)[0]
        i += 2
        if op in _OP_U32X2:
            if i + 8 > n:
                return None
            v1, v2 = struct.unpack_from("<II", blob, i)
            i += 8
            flags.append((op, v1))
            flags.append((op, v2))
            if op == 0x7fe0:
                cell = v2
        elif op in _OP_U32:
            if i + 4 > n:
                return None
            val = struct.unpack_from("<I", blob, i)[0]
            i += 4
            flags.append((op, val))
        elif op in _OP_U16:
            if i + 2 > n:
                return None
            val = struct.unpack_from("<H", blob, i)[0]
            i += 2
            flags.append((op, val))
        else:
            return None
        if op == 0x7f00:
            size = val / 2000
        elif op == 0x7f08:
            bold = bool(val)
        elif op == 0x7f02:
            # Font WEIGHT (CSS-style): 700 = bold, 400 = regular.
            # VERIFIED on the user's differential test file (same line
            # stored twice — regular run carries no 0x7F02, bold run
            # carries 0x7F02=700) and confirmed in a second file with
            # inline bold spans at two different sizes.
            if val >= 600:
                bold = True
            # values <= 500 are regular weight — never UN-bold a run
            # that a 0x7F08 flag already marked (both encodings exist).
        elif op == 0x7f09:
            # CANDIDATE italic slot (neighbour of bold) — applied only
            # when value==1; flagged in the report for verification.
            italic = bool(val)
    if i != n:
        return None
    return {"size": size, "bold": bold, "italic": italic, "flags": flags,
            "cell": cell}


def _parse_style_runs(data: bytes, pos: int) -> list[tuple[int, dict | None]]:
    """Parse the character-run style table that follows the story text.
    pos points just past the last paragraph's CR + 4 link bytes.
    Returns [(run_byte_count, style_or_None), ...]; empty list if the
    table marker is not where this format version puts it."""
    marker = data.find(b"\xff\xff\xff\xff", pos, pos + 32)
    if marker == -1:
        return []
    pos = marker + 4
    runs: list[tuple[int, dict | None]] = []
    n = len(data)
    while pos + 14 <= n:
        count, reclen = struct.unpack_from("<II", data, pos)
        if count == 0 or count > 0x100000 or reclen < 8 or reclen > 512:
            break
        flag = struct.unpack_from("<H", data, pos + 8)[0]
        bloblen = struct.unpack_from("<I", data, pos + 10)[0]
        if flag != 1 or reclen != 6 + bloblen or pos + 14 + bloblen > n:
            break
        runs.append((count, _parse_style_blob(data[pos + 14:pos + 14 + bloblen])))
        pos += 8 + reclen
    return runs


def _table_counters(data: bytes) -> list[int]:
    """Line-slot counters for InPage grid-table cells.

    A grid table (InPage Table tool) stores, in the object-table region,
    one paired record per cell: a 50-record ('32 00 00 00 e7 9a 00 00')
    followed by a 0-record whose u32 at offset +32 of its class marker
    holds the cell's line-slot counter. Pairs sit at a fixed 124-byte
    stride. Verified on the user's grid-table file: 35 pairs, counters
    574..715. No non-table file in the reference set contains this
    pattern at all (0 occurrences), which is what makes it a safe
    table discriminator."""
    offs: list[int] = []
    p = data.find(b"\x32\x00\x00\x00")     # id-50 object records
    while p != -1:
        offs.append(p)
        p = data.find(b"\x32\x00\x00\x00", p + 1)
    off_set = set(offs)
    best: list[int] = []
    for start in offs:
        chain = [start]
        while chain[-1] + 124 in off_set:
            chain.append(chain[-1] + 124)
        # the next object in the file may also start with an id-50
        # record at +124: trim the chain at the first counter that does
        # not strictly increase (cell line numbers always increase)
        cs = [struct.unpack_from("<I", data, o + 44)[0] for o in chain]
        end = 1
        while end < len(cs) and cs[end] > cs[end - 1]:
            end += 1
        chain = chain[:end]
        if len(chain) < 6 or len(chain) <= len(best):
            continue
        classes = [struct.unpack_from("<I", data, o + 4)[0] for o in chain]
        if classes.count(39655) < len(chain) * 0.6:
            continue        # not the cell-counter structure
        best = chain
    return [struct.unpack_from("<I", data, o + 44)[0] for o in best]


def _linked_cells(runs: list[tuple[int, dict | None]]) -> list[tuple[int, dict | None]]:
    """Extract the cell-link run sequence from parsed style runs.

    In a grid-table story every paragraph is its own cell and the run's
    style blob carries 0x7FE0 (chainId, cellId). Returns the maximal
    run of style runs whose cell ids are strictly consecutive, or []
    when fewer than 6 (layout text-box chains link whole multi-
    paragraph boxes, not one run per paragraph)."""
    best: list[tuple[int, dict | None]] = []
    cur: list[tuple[int, dict | None]] = []
    last_id = None
    for nbytes, st in runs:
        cid = (st or {}).get("cell")
        # step +1 = next cell; step 0 = same cell (a second run for the
        # same cell — its CR byte, or a second paragraph line)
        if cid is not None and last_id is not None \
                and cid in (last_id, last_id + 1):
            cur.append((nbytes, st))
        elif cid is not None:
            if len(cur) > len(best):
                best = cur
            cur = [(nbytes, st)]
        else:
            if len(cur) > len(best):
                best = cur
            cur = []
        last_id = cid
    if len(cur) > len(best):
        best = cur
    return best if len(best) >= 6 else []


def _grid_rows_cols(counters: list[int]) -> tuple[int, int] | None:
    """Group cell line-counters into a rectangular rows x cols grid.

    Within a row the counter advances by a small modal step (4 on the
    verified file); across a row boundary it jumps one higher (5). One
    larger anomaly (a +6 inside the last row of the verified file) is
    tolerated: a candidate row length L is accepted when EVERY cross-
    row delta exceeds the modal step and >= 80% of within-row deltas
    do not. Returns (rows, cols) or None."""
    n = len(counters)
    if n < 6:
        return None
    deltas = [b - a for a, b in zip(counters, counters[1:])]
    from collections import Counter
    modal = Counter(deltas).most_common(1)[0][0]
    if modal <= 0:
        return None
    best = None
    for L in range(2, n // 2 + 1):
        if n % L:
            continue
        bset = {k * L - 1 for k in range(1, n // L)}
        bounds = [deltas[k] for k in sorted(bset)]
        withins = [d for i, d in enumerate(deltas) if i not in bset]
        if not bounds or not all(d > modal for d in bounds):
            continue
        if sum(1 for d in withins if d <= modal) / len(withins) < 0.8:
            continue
        ratio = sum(1 for d in withins if d <= modal) / len(withins)
        if best is None or ratio > best[0]:
            best = (ratio, n // L, L)
    if best is None:
        return (1, n) if n <= 64 else None    # no boundaries: single row
    return best[1], best[2]


# InPage internal length unit: 1/2000 pt (same unit as the 0x7F00 font
# sizes). Verified on the grid-table file: page 210x297mm is stored as
# 1190550 x 1683779 and the table width 184.60mm as 1046552.
_UNIT_TO_MM = 25.4 / 72 / 2000


def _page_setup(data: bytes) -> dict | None:
    """Page size and margins from the header property list.

    The header carries [propId u8][0x01][value u32] records; prop 6 is
    the page width (~1190550 units for A4), 7 the height, and 8-11 the
    left/right/top/bottom margins. All values are in 1/2000 pt. Returns
    {'page_w_mm', 'page_h_mm', 'left_mm', 'right_mm', 'top_mm',
    'bottom_mm'} or None when the list is not found / implausible."""
    n = len(data)
    for off in range(0, min(n, 0x3000) - 6):
        if (data[off] == 6 and data[off + 1] == 0x01
                and 1000000 < struct.unpack_from("<I", data, off + 2)[0]
                < 1400000):
            # walk the whole property list around this record
            props: dict[int, int] = {}
            p = off
            while p - 6 >= 0 and data[p - 1] == 0x01 and data[p - 6] < 40:
                props[data[p - 6]] = struct.unpack_from("<I", data, p - 4)[0]
                p -= 6
            p = off
            while p + 6 <= n and data[p + 1] == 0x01 and data[p] < 40:
                props[data[p]] = struct.unpack_from("<I", data, p + 2)[0]
                p += 6
            try:
                pw = props[6] * _UNIT_TO_MM
                ph = props[7] * _UNIT_TO_MM
                l, r, t, b = (props[k] * _UNIT_TO_MM for k in (8, 9, 10, 11))
            except KeyError:
                return None
            if not (100 <= pw <= 420 and 100 <= ph <= 600
                    and all(0 <= m < 60 for m in (l, r, t, b))
                    and pw - l - r > 50):
                return None
            return {"page_w_mm": pw, "page_h_mm": ph, "left_mm": l,
                    "right_mm": r, "top_mm": t, "bottom_mm": b}
    return None


def _table_width_mm(data: bytes) -> float | None:
    """Total width of the grid-table object, in mm.

    The table object is the first 0x2E-class record whose A field
    (u32 at sig+28) is page-sized; per-cell records carry only the
    small default column width (142650 units = 25.16mm). Returns None
    when no such record exists."""
    sig = b"\x01\x00\x00\x00\xe7\x9a\x00\x00\x01\x00\x00\x00\x2e"
    pos = 0
    while True:
        i = data.find(sig, pos)
        if i == -1:
            return None
        a = struct.unpack_from("<I", data, i + 28)[0]
        if 700000 <= a <= 1400000:          # ~123..247mm — a real table
            return a * _UNIT_TO_MM
        pos = i + 1


def _table_cols(data: bytes) -> int | None:
    """Column count of the grid table, straight from the table object.

    The table object record (the 0x2E-class record with a page-sized A
    field) carries the owning chain id (0x6B in every file so far)
    followed by the column count as a u16 at sig+82. Verified on three
    grid files: 7 / 5 / 7 columns — matching each table exactly where
    the line-counter deltas alone cannot (one real file's counters step
    +2 uniformly with no row-boundary signal at all). Non-table files
    can also carry such a record (text-box chains), so this value is
    only used together with the per-cell counter invariant."""
    sig = b"\x01\x00\x00\x00\xe7\x9a\x00\x00\x01\x00\x00\x00\x2e"
    pos = 0
    while True:
        i = data.find(sig, pos)
        if i == -1:
            return None
        a = struct.unpack_from("<I", data, i + 28)[0]
        if 700000 <= a <= 1400000 and i + 84 <= len(data):
            cols = struct.unpack_from("<H", data, i + 82)[0]
            if 2 <= cols <= 20:
                return cols
        pos = i + 1


def _detect_grid_table(data, runs, blocks, counters) -> dict | None:
    """Detect an InPage grid table (Table tool) in the first story.

    All evidence must hold — anything less and the caller renders the
    legacy flow, never a wrong guess:
      1. >= 6 style runs whose 0x7FE0 cell ids are strictly consecutive
         (one run per paragraph = one cell),
      2. cell line-counter records in the object table, one per cell,
         grouping into a rectangular rows x cols grid,
      3. the story's non-empty paragraphs match the linked text runs
         one-to-one in counted bytes (the cell texts, in order).
    Returns {'rows', 'cols', 'cells'} with cells[r][c] = (text, style)
    or None. Column 0 is the first cell in reading order — the
    RIGHTMOST column of an RTL table."""
    cells_runs = _linked_cells(runs)
    if not cells_runs or not blocks:
        return None
    # distinct cell ids in file order — must be strictly consecutive
    ids: list[int] = []
    for _nb, st in cells_runs:
        cid = (st or {}).get("cell")
        if not ids or ids[-1] != cid:
            ids.append(cid)
    if not ids or ids != list(range(ids[0], ids[0] + len(ids))):
        return None
    # INVARIANT: a grid table has exactly one line-counter record per
    # cell, so the counter chain and the linked cell ids must match 1:1.
    # (A text-box document can also carry consecutive 0x7FE0 links and a
    # table-like object record, but its counters never match its links —
    # verified on the projection file: 96 counters vs 128 box ids.)
    n = len(ids)
    if not counters or len(counters) != n:
        return None
    # Grid shape: the table object's own column count is authoritative
    # (one real file's line-counters carry NO row-boundary signal);
    # the counter-delta grouping is only a fallback.
    cols = _table_cols(data)
    if cols is None or n % cols:
        shape = _grid_rows_cols(counters)
        if shape is None:
            return None
        rows, cols = shape
        if rows * cols != n:
            return None
    else:
        rows = n // cols
    id_index = {cid: k for k, cid in enumerate(ids)}
    # run byte spans
    cum: list[tuple[int, int, dict | None]] = []
    s = 0
    for nb, st in cells_runs:
        cum.append((s, s + nb, st))
        s += nb
    total = s
    # A paragraph's CR byte (its last counted byte) decides its cell:
    # InPage may split one cell's text and CR across two runs of the
    # SAME cell (verified on both grid-table files).
    cell_lines: dict[int, list[str]] = {}
    cell_style: dict[int, dict] = {}
    pos = 0
    for ln in blocks[0].split("\n"):
        end = pos + len(ln)          # CR byte position
        if end >= total:
            break                    # past the linked story
        st = None                 # run holding the CR byte -> the cell
        st0 = None                # run holding the first text byte -> size
        for a, z, sst in cum:
            if st0 is None and a <= pos < z:
                st0 = sst
            if a <= end < z:
                st = sst
                break
        cid = (st or {}).get("cell")
        k = id_index.get(cid)
        if k is None:
            return None              # paragraph outside the cell chain
        if ln.strip():
            cell_lines.setdefault(k, []).append(ln)
        # cell style: prefer a run that actually carries the size
        for sst in (cell_style.get(k), st0, st):
            if sst is not None and sst.get("size") is not None:
                cell_style[k] = sst
                break
        if k not in cell_style:
            cell_style[k] = st0 if st0 is not None else st
        pos = end + 1
    if not cell_lines:
        return None
    cells = [[None] * cols for _ in range(rows)]
    for k, lines in cell_lines.items():
        r, c = divmod(k, cols)
        cells[r][c] = ("\n".join(lines), cell_style.get(k))
    out = {"rows": rows, "cols": cols, "cells": cells}
    # geometry: the grid must fill the text area exactly (margin to
    # margin) and start at the top margin, exactly like InPage drew it
    setup = _page_setup(data)
    if setup:
        tw = _table_width_mm(data)
        text_w = setup["page_w_mm"] - setup["left_mm"] - setup["right_mm"]
        if tw and abs(tw - text_w) < 3.0:       # table == text area
            out["geom"] = {"x0_mm": setup["left_mm"],
                           "y0_mm": setup["top_mm"],
                           "width_mm": min(tw, text_w)}
        elif tw:                                 # table narrower than area
            out["geom"] = {"x0_mm": setup["left_mm"],
                           "y0_mm": setup["top_mm"],
                           "width_mm": tw}
    return out


def extract_document_ex(path: str, junk_limit: int = 16,
                     min_block_chars: int = 10):
    """Extract text, per-paragraph character styling, and grid tables.

    Returns (blocks, styles, grid_table):

    Returns (blocks, styles):
      blocks — list of intermediate-form text blocks, exactly as
        extract_intermediate() returns them (paragraphs joined by \\n).
      styles — parallel list: styles[b][p] belongs to paragraph p of
        blocks[b] (i.e. blocks[b].split("\\n")[p]) and is either None
        (no style recovered — never guessed) or
        {'size': pt|None, 'bold': bool, 'coverage': 0.0-1.0,
         'runs': [(char_len, size, bold), ...] | None} where
        coverage is the fraction of the paragraph's bytes the dominant
        (size, bold) run combination actually spans, and runs carves
        the paragraph's intermediate text into consecutive style
        segments (inline bold spans, inline size changes). runs is
        None when the byte-to-character mapping for that paragraph is
        not exact (stray structure bytes) or a covering run failed to
        parse — the paragraph-level dominant style still applies.
    """
    ole = olefile.OleFileIO(path)
    try:
        if not ole.exists("InPage100"):
            raise ValueError("No InPage100 stream — not a recognized InPage file.")
        data = ole.openstream("InPage100").read()
    finally:
        ole.close()

    n = len(data)
    blocks: list[str] = []
    # per block, per paragraph: (counted_bytes, intermediate_chars, has_cr)
    para_info: list[list[tuple[int, int, bool]]] = []
    buf: list[str] = []
    cur_paras: list[tuple[int, int, bool]] = []
    cur_bytes = 0                       # counted bytes of paragraph in progress
    cur_chars = 0                       # intermediate chars of same paragraph
    text_end = -1                       # just past last CR + 4 link bytes
    i = _find_text_start(data)
    if i == -1:
        raise ValueError("No text found in this InPage file.")
    junk = 0

    def close_block():
        nonlocal buf, cur_paras, cur_bytes, cur_chars
        if len(buf) >= min_block_chars:
            blocks.append("".join(buf))
            para_info.append(cur_paras + [(cur_bytes, cur_chars, False)])
        buf = []
        cur_paras = []
        cur_bytes = 0
        cur_chars = 0

    while i < n:
        b = data[i]
        if b == 0x04 and i + 1 < n:
            buf.append("" + _b2c(data[i + 1]))
            i += 2
            cur_bytes += 2
            cur_chars += 2
            junk = 0
        elif b == 0x0D:                     # paragraph mark + 4 link bytes
            buf.append("\n")
            # +1: the CR itself is counted in run byte space
            cur_paras.append((cur_bytes + 1, cur_chars, True))
            cur_bytes = 0
            cur_chars = 0
            i += 5
            text_end = i
            junk = 0
        elif b == 0x09:
            # A tab at the very start of a paragraph whose next three
            # bytes are zero is the paragraph's LENGTH PREFIX (small
            # paragraphs: 0x09 = 8 text bytes + CR), not a typed tab —
            # verified on the user's real grid-table file where three
            # cells gained phantom tabs this way.
            at_start = (not buf) or buf[-1] == "\n"
            if at_start and data[i + 1:i + 4] == b"\x00\x00\x00":
                i += 1
                cur_bytes += 1
                junk = 0
                continue
            buf.append("\t")
            i += 1
            cur_bytes += 1
            cur_chars += 1
            junk = 0
        elif 0x20 <= b <= 0x7E:             # bare ASCII is literal text
            # Modified by inpage2pdf: an ISOLATED printable byte at a
            # paragraph boundary is a structural code (verified to be a
            # length prefix of the following text: code x 1.48 = bytes),
            # not text. Real Latin text is a run of >=2 printable bytes.
            at_par_start = (not buf) or buf[-1] == "\n"
            nxt_ascii = i + 1 < n and 0x20 <= data[i + 1] <= 0x7E
            if at_par_start and not nxt_ascii:
                i += 1                   # structural code — skip it
                cur_bytes += 1
            else:
                buf.append(chr(b))
                i += 1
                cur_bytes += 1
                cur_chars += 1
            junk = 0
        else:
            junk += 1
            i += 1
            cur_bytes += 1                  # stray bytes occupy run space
            if junk >= junk_limit:          # structure region: block over
                cur_bytes -= junk           # ...but a block gap does not
                close_block()
                nxt = _find_text_resume(data, i)
                if nxt == -1:
                    break
                i = nxt
                junk = 0
    close_block()
    if not blocks:
        raise ValueError("No text blocks recovered from this InPage file.")

    runs = _parse_style_runs(data, text_end) if text_end != -1 else []

    # Assign each paragraph the dominant style among the runs covering
    # its byte span, plus the exact within-paragraph style segments
    # where the byte-to-character mapping is provably 1:1. Style stream
    # position advances block by block in file order; paragraphs past
    # the end of the run table get None.
    styles: list[list[dict | None]] = []
    ri = 0                                  # current run index
    consumed = 0                            # bytes consumed of runs[ri]
    for bi in range(len(para_info)):
        block_styles: list[dict | None] = []
        block_paras = blocks[bi].split("\n")
        for pi, (pb, pchars, has_cr) in enumerate(para_info[bi]):
            agg: dict[tuple, int] = {}
            pieces: list[tuple[int, dict | None]] = []  # (bytes, style)
            remaining = pb
            while remaining > 0 and ri < len(runs):
                avail = runs[ri][0] - consumed
                take = min(avail, remaining)
                st = runs[ri][1]
                if st is not None:
                    key = (st["size"], st["bold"], st.get("italic", False))
                    agg[key] = agg.get(key, 0) + take
                pieces.append((take, st))
                consumed += take
                remaining -= take
                if consumed >= runs[ri][0]:
                    ri += 1
                    consumed = 0
            if not (agg and pb > 0):
                block_styles.append(None)
                continue
            (size, bold, italic), covered = max(agg.items(), key=lambda kv: kv[1])
            entry = {"size": size, "bold": bold, "italic": italic,
                     "coverage": covered / pb, "runs": None}
            # Byte offsets equal character offsets only when every
            # counted byte produced exactly one intermediate char
            # (marker pair: 2 bytes -> 2 chars, ASCII/tab: 1 -> 1, the
            # CR is the +1); stray bytes break that, so no run detail.
            exact = (remaining == 0
                     and pb == pchars + (1 if has_cr else 0)
                     and all(st is not None for _, st in pieces))
            if exact and pi < len(block_paras):
                s = block_paras[pi]
                # piece boundaries in char space; last piece absorbs
                # the CR byte, so clamp the total to the char count
                cuts = []
                acc = 0
                for blen, _ in pieces[:-1]:
                    acc += blen
                    cuts.append(min(acc, pchars))
                # never split a marker pair: a boundary that lands on
                # the pair's second byte moves back onto the marker
                cuts = [c - 1 if 0 < c < len(s) and s[c - 1] == MARKER
                        else c for c in cuts]
                segs = []
                prev = 0
                for c in cuts + [pchars]:
                    c = max(prev, c)
                    segs.append((c - prev, pieces[len(segs)][1]))
                    prev = c
                merged: list[tuple[int, float | None, bool, bool]] = []
                for ln, st in segs:
                    if ln == 0:
                        continue
                    key2 = (st["size"], st["bold"], st.get("italic", False))
                    if merged and merged[-1][1:] == key2:
                        merged[-1] = (merged[-1][0] + ln,) + key2
                    else:
                        merged.append((ln,) + key2)
                if merged:
                    entry["runs"] = merged
            block_styles.append(entry)
        styles.append(block_styles)
    grid = _detect_grid_table(data, runs, blocks, _table_counters(data))
    return blocks, styles, grid


def extract_document(path: str, junk_limit: int = 16,
                     min_block_chars: int = 10):
    """Backward-compatible wrapper: returns (blocks, styles)."""
    blocks, _styles, _grid = extract_document_ex(path, junk_limit,
                                                 min_block_chars)
    return blocks, _styles


def extract_intermediate(path: str, junk_limit: int = 16,
                         min_block_chars: int = 10) -> list[str]:
    """Return text blocks in the intermediate (0x04-marker) form that
    herf.engine.convert() accepts. Block 0 is the main story; any
    further blocks are additional text regions found after structure
    gaps (text frames, footnotes, and similar)."""
    blocks, _ = extract_document(path, junk_limit, min_block_chars)
    return blocks

# Known InPage font-name words (font tables are stored as ASCII strings in
# the stream header — verified in the round-10 research on the user's
# files: 'Noori Nastaliq', 'Noori Character', 'Simplified Arabic',
# 'ZoharSindhi', 'Arial' all appear as plain ASCII before the story).
_FONT_WORDS = (
    b"Nastaliq", b"Nastaleeq", b"Jameel", b"Noori", b"Nafees", b"Alvi",
    b"Faiz", b"Gulzar", b"Mehr", b"InPage", b"Arial", b"Times",
    b"Tahoma", b"Verdana", b"Courier", b"Symbol", b"ZoharSindhi",
    b"Simplified Arabic", b"Traditional Arabic", b"Urdu",
)
_STYLE_WORDS = (b"Normal", b"Heading", b"Body",)


def extract_font_names(path: str) -> list[str]:
    """Return the document's font names (best effort, reporting only).

    The font table itself is a length-prefixed ASCII structure we have not
    fully located; the names are recoverable as readable ASCII strings in
    the stream header. Order of first appearance is preserved because the
    0x7F80 font-index values refer to this (unlocated) table — names alone
    are reported, never used to guess rendering decisions."""
    ole = olefile.OleFileIO(path)
    try:
        data = ole.openstream("InPage100").read()
    finally:
        ole.close()
    # the story text is 0x04-marker pairs; the header is before it. Font
    # names live in the header, so only scan up to the story start.
    ts = _find_text_start(data)
    header = data[:ts] if ts != -1 else data[:8192]
    found: list[str] = []
    i = 0
    while i < len(header):
        # candidate printable-ASCII run
        j = i
        while j < len(header) and 0x20 <= header[j] <= 0x7E:
            j += 1
        if j - i >= 4:
            s = header[i:j]
            if any(w in s for w in _FONT_WORDS) and s not in found:
                # keep the font-ish part of the string
                found.append(s.decode("ascii", "replace"))
        i = j + 1
    return found
