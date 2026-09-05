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

Two more measured against a 300-guid sample apiece, real body_text pulled
from the live structured_pending/structured_pending_2 backlog (not this one
guid) - see FINANCIAL_ACCOUNT/GOVERNMENT_ID below:

  FINANCIAL_ACCOUNT: the bare 13-19 digit-run branch
  ("\\b(?:\\d[ -]?){13,19}\\b") allows a separator after every single digit,
  so it does not require those digits to be one number - it happily
  stitches together several shorter, unrelated numbers (three separate
  8-digit case IDs, two separate phone numbers) into a fake long "account
  number". Measured: 56.3% (169/300) of matches had no other financial
  signal anywhere in the document and were not a genuinely contiguous digit
  run either - e.g. "Case, 19663844 19663057 19663614" (three case numbers)
  and "1-888-978-0332 450-978-0332" (two phone numbers). Separately,
  swift's optional suffix ("\\bswift\\s+(?:code|number)?\\b") let the bare
  word match - 1.0% (3/300) of matches were literally just "Swift"/"SWIFT"
  with no banking context. Both require the same shape of fix: the
  digit-run only counts as genuine when a truly unbroken 13-19 digit token
  exists (not merely the loose, separator-permitting match), and swift's
  suffix is no longer optional.

  GOVERNMENT_ID: bare "\\b\\d{12}\\b" requires no context at all - any 12-digit
  number counts. Measured: 39.0% (117/300) of matches had no other
  government-ID signal anywhere and were unambiguously something else
  ("591055516300", "258020240331" - no visible ID context surrounding
  either). Unlike the digit-run bug above, these are already single,
  unbroken 12-digit runs - concatenation isn't the problem, the total
  absence of any context requirement is. So there is no tighter regex to
  require here the way there is for FINANCIAL_ACCOUNT's digit-run: a bare,
  unqualified 12-digit number simply never counts as a government ID on its
  own, the same principle DOB already applies to a bare keyword or a bare
  date alone.

This module does NOT edit population_selection/selector.py's
PII_CATEGORY_PATTERNS or classify_text(): that's the corpus-level rule,
already validated at scale (see docs/CLAUDE.md's backtest numbers), and
changing its regex - even to fix a real bug - changes established behavior
without a backtest to confirm the new false-positive/false-negative balance
is still acceptable. classify_sheet_text() reuses the same patterns for
every other category verbatim, and only tightens ADDRESS/DOB/
FINANCIAL_ACCOUNT/GOVERNMENT_ID with an additional check before trusting a
match.
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


# Every FINANCIAL_ACCOUNT branch except the loose digit-run and the
# optional-suffix swift - kept in sync with PII_CATEGORY_PATTERNS['FINANCIAL_ACCOUNT']
# by construction (see this module's docstring for the swift/digit-run
# measurements). swift's suffix is made mandatory here rather than left
# optional, since a bare "swift" is exactly the same false-positive shape
# already fixed for DOB/ADDRESS.
_FINANCIAL_ACCOUNT_UNAMBIGUOUS = re.compile(
    r"(?i)\brouting\s+number\b|\bbank\s+account\b|"
    r"\baccount\s+number\b|\biban\b|\bswift\s+(?:code|number)\b|"
    r"\bcvv\b|\bcvc\b|\bexpiration\s+date\b.{0,20}\bcard\b|"
    r"\b\w*account\b[^\n]{0,20}\d{5,}|\bsubscriber\s+number\b[^\n]{0,20}\d{3,}|"
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"
)
# What's left can only be the bare digit-run branch. The loose pattern
# allows a separator after every digit, which lets it stitch several
# shorter, unrelated numbers into a fake long one - require a genuinely
# unbroken 13-19 digit token instead, exactly the shape a real unformatted
# card number takes.
_FINANCIAL_DIGIT_RUN_UNBROKEN = re.compile(r"\b\d{13,19}\b")
# Requiring an unbroken token stops the cross-token stitching, but measured
# on the same sample: 159 of 169 stitching-only false positives were still
# flagged, because the whole document (not just the originally-flagged
# snippet) usually contains some other genuinely unbroken long digit
# sequence anyway - a phone number with country code ("00492251954304445"),
# an ISBN-13 ("9783898759908"), a tracking/record ID
# ("1744773424700016000"), even the decimal digits of an ordinary float
# ("370.1946666666667"). None of those are financial account numbers, and
# an unbroken token alone cannot tell them apart from one - only 10 of 169
# actually cleared. A financial-context keyword within this many characters
# either side is required in addition, the same proximity-window shape
# DOB's keyword+date check already uses.
_FINANCIAL_CONTEXT_KEYWORD = re.compile(
    r"(?i)\bcredit\s+card\b|\bdebit\s+card\b|\bcard\s+number\b|\bcard\s*#|"
    r"\bvisa\b|\bmastercard\b|\bamex\b|american\s+express|\bdiscover\s+card\b|"
    r"\baccount\s+(?:no|number|#)\b|\bacct\.?\s*(?:no|number|#)\b|"
    r"\bbank\s+account\b|\brouting\b|\biban\b|\bswift\b"
)
_FINANCIAL_DIGIT_RUN_WINDOW_CHARS = 40

# Every GOVERNMENT_ID branch except the bare, context-free \d{12} - kept in
# sync with PII_CATEGORY_PATTERNS['GOVERNMENT_ID'] by construction.
_GOVERNMENT_ID_UNAMBIGUOUS = re.compile(
    r"(?i)\b\d{3}[-\s]\d{2}[-\s]\d{4}\b|\b\d{3}[-\s]\d{3}[-\s]\d{3}\b|"
    r"\b\d{4}[-\s]\d{4}[-\s]\d{4}\b|"
    r"\b[A-Z]{5}\d{4}[A-Z]\b|\b[A-Z]{2}\d{6}[A-Z]\b|"
    r"\bssn\b|\bsin\b|social\s+insurance\s+number|social\s+security\s+number|"
    r"passport(?:\s+(?:no|number|#))?|driver'?s?\s+licen[cs]e|"
    r"military\s+id|national\s+id(?:entity)?(?:\s+(?:no|number|card))?|"
    r"government[\s-]issued\s+id|"
    r"aadhaa?r(?:\s+(?:no|number|card))?|\buidai\b|"
    r"\bpan\s+(?:card|number|no)\b|permanent\s+account\s+number|"
    r"national\s+insurance\s+number|\bnino\b|"
    r"\bcurp\b|\brfc\b|tax\s+identification\s+number|\btin\s+(?:no|number)\b|"
    r"documento\s+de\s+identidad|documento\s+nacional\s+de\s+identidad|\bdni\b|"
    r"\bcnp\b|"
    r"social\s+security\s+system|\bsss\s+number\b|ss\s+number\s+slip"
)


def _financial_account_is_genuine(text: str) -> bool:
    if _FINANCIAL_ACCOUNT_UNAMBIGUOUS.search(text):
        return True
    # What's left can only be the digit-run branch. An unbroken token alone
    # is still common, non-financial data (phone numbers, ISBNs, tracking
    # IDs, even a float's own decimal digits - see this module's docstring),
    # so also require a financial-context keyword nearby before trusting it.
    for m in _FINANCIAL_DIGIT_RUN_UNBROKEN.finditer(text):
        start = max(0, m.start() - _FINANCIAL_DIGIT_RUN_WINDOW_CHARS)
        end = m.end() + _FINANCIAL_DIGIT_RUN_WINDOW_CHARS
        if _FINANCIAL_CONTEXT_KEYWORD.search(text[start:end]):
            return True
    return False


def _government_id_is_genuine(text: str) -> bool:
    if _GOVERNMENT_ID_UNAMBIGUOUS.search(text):
        return True
    # What's left can only be the bare \d{12} branch. Unlike the digit-run
    # bug above, this is already a single unbroken token - concatenation
    # isn't the problem, the total absence of any context requirement is.
    # There is no tighter regex to require, so a bare 12-digit number simply
    # never counts on its own, the same principle DOB already applies to a
    # bare keyword or a bare date alone.
    return False


_EXTRA_CHECKS = {
    "ADDRESS": _address_is_genuine,
    "DOB": _dob_is_genuine,
    "FINANCIAL_ACCOUNT": _financial_account_is_genuine,
    "GOVERNMENT_ID": _government_id_is_genuine,
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
