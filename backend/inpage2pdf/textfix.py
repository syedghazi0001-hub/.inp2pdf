#
# Conversion-artifact fixes — adapted from the Herf project
# (https://github.com/talhabinmunir/herf); rules originate from the
# ltrc reference converter (GPL-2.0). Included under GPL-2.0 with attribution.
"""Rule-based text cleanup for converted InPage text.

Every rule here comes from the ltrc reference converter's own
post-processing options (its noon-guna, hamza, and spacing fixes).
These target known InPage conversion artifacts, not general grammar.
This module does NOT attempt grammar correction — that cannot be
done reliably with offline rules, and Herf does not pretend to.
"""

import re

_LETTERS = "ابپتٹثجچحخدڈذرڑزژسشصضطظعغفقکكگلمنوئیےؤهۀةأـآيھإہۃں"
_DIACRITICS = "ًٌٍَُِّٰٖٗ"

# (name, description, pattern, replacement)
RULES = [
    ("hamza_join",
     "Standalone hamza before a joined letter becomes hamza-on-yeh "
     "(ہوءی becomes ہوئی, جاءے becomes جائے, کوءی becomes کوئی)",
     re.compile("ء([" + _LETTERS + "])"), r"ئ\1"),
    ("hamza_join_diacritic",
     "Same fix when a diacritic sits between hamza and the next letter",
     re.compile("ء([" + _DIACRITICS + "])([" + _LETTERS + "])"), r"ئ\1\2"),
    ("noon_guna_space",
     "Missing space after noon-ghunna (میںبھی becomes میں بھی)",
     re.compile("ں([" + _LETTERS + "])"), r"ں \1"),
    ("taa_marbuta_space",
     "Missing space after taa marbuta (سورۃکہلاتی becomes سورۃ کہلاتی)",
     re.compile("ۃ([" + _LETTERS + "])"), r"ۃ \1"),
    ("arabic_yeh_space",
     "Missing space after Arabic word-final yeh",
     re.compile("ي([" + _LETTERS + "])"), r"ي \1"),
    # double_space rule REMOVED by inpage2pdf: collapsing repeated spaces
    # destroyed the original document's space-bar alignment (wide MCQ
    # option gaps etc.). Space runs are original formatting and must
    # survive all the way to the PDF.

    ("space_before_full_stop",
     "Space before the Urdu full stop removed",
     re.compile(" +۔"), "۔"),
]


def fix_spelling(text: str):
    """Apply the artifact-fix rules. Returns (fixed_text, change_list)
    where change_list is [{rule, description, count}] for every rule
    that changed anything. Rules that match nothing are omitted."""
    changes = []
    for name, desc, pattern, repl in RULES:
        new_text, n = pattern.subn(repl, text)
        if n:
            changes.append({"rule": name, "description": desc, "count": n})
            text = new_text
    return text, changes
