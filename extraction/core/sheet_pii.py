"""Sheet-level PII classification: population_selection's categories, with two
targeted precision fixes layered on top - not edited into the shared regex.

extraction/core/pipeline_agent.py's run_document() scores PII per sheet
(rather than once per whole document, which is all population_selection
itself does), and at that finer granularity two real noise sources in
PII_CATEGORY_PATTERNS became visible on real documents:

  ADDRESS: one alternative (the German/Dutch street-suffix branch, matching
  e.g. "Koenigsring 5") relies on an [A-ZAEOEUE] class to require a
  capitalized, proper-noun-looking word. The whole ADDRESS pattern carries a
  single leading (?i), which silently neuters that capital-letter check -
  "offering" (ends in "ring") matched case-insensitively, and the following
  "[^\\n.]{0,40}\\d{1,6}" is loose enough that almost any digit within 40
  characters closed the match. Confirmed on guid
  ae09945d-bb1b-c996-06a8-6a87b3dcc1ac: 'offering a lead","09/30' flagged as
  an address.

  DOB: neither of its two alternatives requires the other half of a real
  DOB field. The keyword alternative (date of birth / dob / birthdate / ...)
  matches the bare word with no date value anywhere near it - confirmed on
  the same guid, "she jumped from dob to address avoiding ID" is a
  verification-*procedure* note, not a stored date of birth. And the
  bullet-point alternative (e.g. "*09/30/2020") matches a bare date with no
  birth-related keyword anywhere near it - also confirmed on that guid: this
  dataset is call-center coaching notes, where "*10/05/2020 shows the
  willingness to help..." is a log-entry timestamp, not a birthdate. Either
  half alone is exactly the same problem the user flagged twice on this
  guid ("not any date is DOB") - so classify_sheet_text() requires both a
  keyword and a date value, in proximity, before counting DOB at all.

This module does NOT edit population_selection/selector.py's
PII_CATEGORY_PATTERNS or classify_text(): that's the corpus-level rule,
already validated at scale (see docs/CLAUDE.md's backtest numbers), and
changing its regex - even to fix a real bug - changes established behavior
without a backtest to confirm the new false-positive/false-negative balance
is still acceptable. classify_sheet_text() reuses the same patterns for
every other category verbatim, and only tightens ADDRESS/DOB with an
additional check before trusting a match.
"""

import re
from typing import Dict

from population_selection.selector import PII_CATEGORY_PATTERNS

# The exact same German/Dutch street-suffix shape ADDRESS uses, kept in sync
# deliberately - but without the (?i) flag, so the capital-letter check it
# was written to enforce actually does something.
_GERMAN_STREET_SUFFIX_CASE_SENSITIVE = re.compile(
    r"\b[A-ZÄÖÜ][\wäöüßÀ-ÿ-]*(?:weg|gasse|platz|ring|allee)\b[^\n.]{0,40}\d{1,6}"
)
# An explicit address phrase, or a real street-number/postal-code shape, is
# unambiguous regardless of case - only the bare street-suffix branch above
# needs the extra check.
_ADDRESS_UNAMBIGUOUS = re.compile(
    r"(?i)\b(?:mailing|home|resident(?:ial)?)\s+address\b|\baddress\s*:|"
    r"\bcalea\b|\bplz\s*:|\bdirecci[oó]n\b|\badresse\b|\bindirizzo\b|"
    r"\b\d{1,6}\s+\w+(?:\s\w+){0,3}\s+(?:street|st\.?|avenue|ave\.?|road|rd\.?|"
    r"boulevard|blvd\.?|drive|dr\.?|lane|ln\.?|way|court|ct\.?|place|pl\.?|"
    r"suite|ste\.?|parkway|pkwy\.?|circle|cir\.?|highway|hwy\.?|terrace|"
    r"terr\.?|square|sq\.?|crescent|cres\.?)\b|"
    r",\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b|"
    r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b"
)

_DOB_KEYWORD = re.compile(
    r"(?i)\b(date\s+of\s+birth|d\.?\s*o\.?\s*b\.?|birth\s*date|birthdate|"
    r"born\s+on|year\s+of\s+birth|"
    r"fecha\s+de\s+nacimiento|date\s+de\s+naissance|n[eé]\(?e?\)?\s+le|"
    r"geburtsdatum|geburtstag|geboren\s+am|"
    r"data\s+di\s+nascita|data\s+de\s+nascimento|"
    r"дата\s+на\s+раждане)\b|"
    r"\bdate\b[^\n.]{0,25}\bbirth\b"
)
_DATE_VALUE_NEARBY = re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|\b(?:19|20)\d{2}\b")
_DOB_WINDOW_CHARS = 40


def _address_is_genuine(text: str) -> bool:
    if _ADDRESS_UNAMBIGUOUS.search(text):
        return True
    # What's left can only be the street-suffix branch; require it to
    # actually be capitalized before trusting it.
    return bool(_GERMAN_STREET_SUFFIX_CASE_SENSITIVE.search(text))


def _dob_is_genuine(text: str) -> bool:
    for m in _DOB_KEYWORD.finditer(text):
        start, end = max(0, m.start() - _DOB_WINDOW_CHARS), m.end() + _DOB_WINDOW_CHARS
        before, after = text[start:m.start()], text[m.end():end]

        date_after = _DATE_VALUE_NEARBY.search(after)
        date_before = _DATE_VALUE_NEARBY.search(before)
        # A genuine "<keyword>: <value>" field never crosses a sentence
        # break; a coincidental date elsewhere in the same free-text note
        # does. Confirmed on ae09945d-bb1b-c996-06a8-6a87b3dcc1ac: "weak for
        # DOB or phone #.///01-18-2021" - a coaching-log date 16 characters
        # after the keyword, separated from it by a period, not a real DOB.
        if date_after and "." not in after[:date_after.start()]:
            return True
        if date_before and "." not in before[date_before.end():]:
            return True
    return False


_EXTRA_CHECKS = {
    "ADDRESS": _address_is_genuine,
    "DOB": _dob_is_genuine,
}


def classify_sheet_text(text: str) -> Dict:
    """Same shape as population_selection.selector.classify_text():
    {"pii_score", "has_pii", "pii_signals"} - every category uses the exact
    shared pattern; ADDRESS and DOB additionally require the extra check
    above before counting as a real signal.
    """
    text = text or ""
    signals = {}
    for name, pattern in PII_CATEGORY_PATTERNS.items():
        if not re.search(pattern, text):
            signals[name] = False
            continue
        extra_check = _EXTRA_CHECKS.get(name)
        signals[name] = extra_check(text) if extra_check else True

    matched_names = [name for name, matched in signals.items() if matched]
    return {
        "pii_score": sum(signals.values()),
        "has_pii": any(signals.values()),
        "pii_signals": ",".join(matched_names),
    }
