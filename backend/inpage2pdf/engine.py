#
# Conversion engine — adapted from the Herf project
# (https://github.com/talhabinmunir/herf), based on the ltrc reference
# converter (GPL-2.0). Included under GPL-2.0 with attribution.
"""Herf conversion engine.

Converts InPage proprietary byte-pair encoded text to standard Unicode,
and reports every byte code it could not map instead of guessing.
"""

import re
from .mapping import ITU_RULES

MARKER = "\u0004"

# cp1252 leaves these byte positions undefined; fall back to raw Latin-1
# codepoints so mapping keys like \x04\x81 (alif) resolve correctly.
_CP1252_UNDEFINED = {0x81, 0x8D, 0x8F, 0x90, 0x9D}

_BYTE_TABLE = {}
for _b in range(256):
    if _b in _CP1252_UNDEFINED:
        _BYTE_TABLE[_b] = chr(_b)
    else:
        _BYTE_TABLE[_b] = bytes([_b]).decode("cp1252")


def decode_bytes(raw: bytes) -> str:
    """Decode raw file bytes into the intermediate cp1252-based text form."""
    return "".join(_BYTE_TABLE[b] for b in raw)


def convert(text: str, user_mapping: dict | None = None,
            latin_digits: bool = False, strip_kashida: bool = False,
            keep_edges: bool = False):
    """Convert intermediate text to Unicode.

    Rules are applied IN ORDER (combination sequences before single
    codes), matching the ltrc reference converter. User-supplied
    mappings are applied first so they take precedence.

    Returns (unicode_text, report) where report lists every marker+byte
    pair not covered by any rule: {code, char, count, contexts}.
    Unmapped pairs stay in the output untouched — nothing is guessed.
    """
    if user_mapping:
        for k in sorted(user_mapping.keys(), key=len, reverse=True):
            if k in text:
                text = text.replace(k, user_mapping[k])

    for k, v in ITU_RULES:
        if k in text:
            text = text.replace(k, v)

    # Collect unmapped marker pairs BEFORE cleanup, with context.
    report = {}
    for m in re.finditer(MARKER + r"(?![ \r\n])(.)", text):
        ch = m.group(1)
        code = f"0x04 0x{ord(ch):02X}"
        entry = report.setdefault(code, {"code": code, "char": ch,
                                         "count": 0, "contexts": []})
        entry["count"] += 1
        if len(entry["contexts"]) < 5:
            s = max(0, m.start() - 25)
            ctx = text[s:m.end() + 25].replace(MARKER, " ")
            ctx = re.sub(r"\s+", " ", ctx).strip()
            entry["contexts"].append(ctx)

    # Remaining lone markers are InPage word-space markers.
    text = text.replace(MARKER, " ")
    # Modified by inpage2pdf: the original herf/ltrc cleanup collapsed all
    # space runs to a single space — destroying the extra spacing the InPage
    # typist entered with the space bar (e.g. the wide gaps between MCQ
    # options: measured 14 consecutive spaces in a real file). Those runs
    # are original formatting and are now PRESERVED. Tabs (InPage 0x09)
    # become 4 spaces.
    text = text.replace("\t", "    ")
    text = re.sub(r" *\r?\n *", "\n", text)
    if keep_edges:
        # style-segment conversion: keep leading/trailing spaces so the
        # concatenation of segment conversions equals the whole-paragraph
        # conversion (the whole only strips the paragraph's outer edges)
        pass
    else:
        text = text.strip()

    if latin_digits:
        text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if strip_kashida:
        text = text.replace("\u0640", "")

    return text, sorted(report.values(), key=lambda e: -e["count"])


def convert_bytes(raw: bytes, **kwargs):
    return convert(decode_bytes(raw), **kwargs)


def convert_segments(segments: list[str], user_mapping: dict | None = None,
                     latin_digits: bool = False, strip_kashida: bool = False,
            keep_edges: bool = False):
    """Convert one paragraph that is split into style segments, keeping
    the segment boundaries through conversion.

    Returns (pieces, text, report). text and report are exactly what
    convert("".join(segments)) produces — that whole-paragraph result
    stays authoritative. pieces is a list of strings, one per input
    segment, whose concatenation equals text; it is None when applying
    the rules segment-by-segment did not reproduce the whole-paragraph
    result (a mapping rule spanned a segment boundary), in which case
    the caller should fall back to unsegmented styling rather than
    risk changing the converted text.
    """
    joined = "".join(segments)
    whole, report = convert(joined, user_mapping=user_mapping,
                            latin_digits=latin_digits,
                            strip_kashida=strip_kashida)

    digit_map = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    parts = []
    for seg in segments:
        t = seg
        if user_mapping:
            for k in sorted(user_mapping.keys(), key=len, reverse=True):
                if k in t:
                    t = t.replace(k, user_mapping[k])
        for k, v in ITU_RULES:
            if k in t:
                t = t.replace(k, v)
        t = t.replace(MARKER, " ")
        if latin_digits:
            t = t.translate(digit_map)
        if strip_kashida:
            t = t.replace("ـ", "")
        parts.append(t)

    # Re-apply convert()'s whitespace normalization (collapse runs of
    # space/tab, strip the paragraph edges) across the concatenation
    # while tracking where each segment ends.
    out: list[str] = []
    bounds: list[int] = []
    prev_space = False
    for t in parts:
        for ch in t:
            if ch in " \t":
                if prev_space or not out:
                    continue
                out.append(" ")
                prev_space = True
            else:
                out.append(ch)
                prev_space = False
        bounds.append(len(out))
    while out and out[-1] == " ":
        out.pop()
    bounds = [min(b, len(out)) for b in bounds]
    final = "".join(out)
    if final != whole:
        return None, whole, report
    pieces = []
    start = 0
    for b in bounds:
        pieces.append(final[start:b])
        start = b
    return pieces, whole, report
