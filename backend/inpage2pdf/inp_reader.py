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
    for min_chars in (20, 8):
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
    return {"size": size, "bold": bold, "italic": italic, "flags": flags}


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


def extract_document(path: str, junk_limit: int = 16,
                     min_block_chars: int = 10):
    """Extract text plus per-paragraph character styling.

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
    return blocks, styles


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
