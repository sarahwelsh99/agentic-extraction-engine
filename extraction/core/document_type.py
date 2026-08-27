"""Document-type pre-check: one cheap, enum-constrained LLM call classifying
a document's TYPE from a short leading sample, run once per whole document
before split_sheets() or any Looker call.

Ported from mosaic-glean-extraction's document-type skip check
(extraction/llm_service.py's _classify_document_type, PR #67, 2026-08-19,
github.com/sarahwelsh99/mosaic-glean-extraction) - the motivating case there
was a 3.8M-char textbook that would have cost 11+ chunk-level extraction
calls. The equivalent failure mode here is worse: a flattened PDF book whose
text happens to contain SHEET_MARKER sequences fans out
(extraction/core/records.py's split_sheets()) into thousands of fake
"sheets," each paying its own structural_inspector call before being
correctly rejected as prose - see the 2026-08-18 vLLM timeout storm on guid
2d73b98d-f30c-5a4c-515a-6267df791dc9 (162k+ timeouts, zero bins committed)
for the incident this exists to prevent. Classifying the whole document
BEFORE split_sheets() ever runs catches that case with one cheap call
instead of thousands of expensive ones.

SKIP_DOCUMENT_TYPES here is NOT copied from mosaic's drive profile: their
pipeline extracts named-person PII from prose, so categories like "meeting
notes" or "hr or personnel document" stay unskipped there (that's exactly
where person-attached PII lives). This pipeline extracts tabular/structured
data instead, and none of the 9 mosaic-original non-"other" genres are
plausibly a real table - this is specifically a defense against prose that
leaked into a supposedly-structured population (structured_pending/
error_dense), not a PII-relevance filter.

**"tabular data export or report" was added on 2026-08-19, not in mosaic's
original taxonomy.** A 50-doc validation batch against this pipeline's real
population showed every one of mosaic's original categories was a false
positive when the actual content was a clean CSV export: a case-management
CSV was called "corporate policy document," an HR/Workday export was called
"hr or personnel document," a survey-response CSV was called "published
book or manual." The taxonomy the guided decoding was forced to choose from
had no correct answer for "this is a table" - forced to pick SOMETHING, the
model latched onto the column names' subject matter (case verification,
HR, audits) rather than the document's actual form (delimited rows with a
header), exactly the content-vs-form confusion the prompt already warned
against. Giving the model an accurate category to select fixed this at the
root rather than bolting on a regex override in front of the LLM call - see
the prompt's explicit delimited-data instruction below, and this category's
permanent exclusion from SKIP_DOCUMENT_TYPES.
"""

import asyncio
import json
import logging
from typing import Optional

from extraction.core import config
from extraction.core.llm_service import LocalLLMClient

logger = logging.getLogger(__name__)

# Fixed, closed taxonomy - see mosaic's own comment for why a closed enum
# (guided decoding) beats freeform labels: exact matching against a skip
# set, one auditable taxonomy shared by every caller, no typo/synonym drift
# to worry about at the call site. "tabular data export or report" is this
# repo's own addition (see module docstring) - every other category is
# mosaic's original nine.
DOCUMENT_TYPE_CATEGORIES = (
    "tabular data export or report",
    "corporate policy document",
    "published book or manual",
    "presentation slide deck",
    "legal or compliance document",
    "marketing or newsletter content",
    "automated system notification",
    "meeting notes or agenda",
    "personal correspondence",
    "hr or personnel document",
    "other",
)

# Every category except "other" (the catch-all for whatever the taxonomy
# hasn't accounted for - treating it as safe-to-skip would silently swallow
# that gap) and "tabular data export or report" (the one genre this
# pipeline's whole job is to keep, regardless of what the export's columns
# are actually about).
SKIP_DOCUMENT_TYPES = frozenset(DOCUMENT_TYPE_CATEGORIES) - {"other", "tabular data export or report"}

_SAMPLE_CHARS = 2000
_MAX_TOKENS = 20

_CATEGORIES_JSON = json.dumps(list(DOCUMENT_TYPE_CATEGORIES))
_PROMPT_TEMPLATE = """Classify the document below by its TYPE. Judge only the document's FORM/genre, not its content or subject matter - a table about HR, legal, or policy topics is still "tabular data export or report", not "hr or personnel document" or "corporate policy document". If the text consists of delimited rows (comma/tab/pipe-separated) with a header line - a spreadsheet, CSV, or database export - always choose "tabular data export or report" regardless of what the column names or values are about. Choose EXACTLY ONE label from this list:
{categories_json}
If none fit well, choose "other". Return strictly raw, minified JSON on a single line: {{"document_type": "..."}}

TEXT:
{text}"""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"document_type": {"type": "string", "enum": list(DOCUMENT_TYPE_CATEGORIES)}},
    "required": ["document_type"],
    "additionalProperties": False,
}


def _retry_delay(attempt: int) -> float:
    backoff = config.VLLM_RETRY_BACKOFF_SEC
    return backoff[min(attempt, len(backoff) - 1)]


async def classify_document_type(client: LocalLLMClient, body_text: str) -> Optional[str]:
    """One cheap LLM call classifying body_text's document TYPE (its
    form/genre, not its content) into exactly one of
    DOCUMENT_TYPE_CATEGORIES, from a leading sample only (_SAMPLE_CHARS
    chars) - a document's form is evident from its opening, so this stays a
    fixed, cheap cost regardless of document length.

    Returns one of DOCUMENT_TYPE_CATEGORIES, or None if every attempt
    failed. Callers MUST fail OPEN on None (treat it as "don't skip") - a
    classification failure is not evidence the document is skippable.
    """
    if not body_text:
        return None
    sample = body_text[:_SAMPLE_CHARS]
    prompt = _PROMPT_TEMPLATE.format(text=sample, categories_json=_CATEGORIES_JSON)

    max_retries = config.VLLM_MAX_RETRIES
    for attempt in range(max_retries):
        try:
            reply = await client.achat(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=_MAX_TOKENS,
                json_schema=_RESPONSE_SCHEMA,
            )
        except (TimeoutError, RuntimeError) as e:
            logger.warning(f"Document-type classification failed (attempt {attempt+1}/{max_retries}): {e}")
            if attempt >= max_retries - 1:
                return None
            await asyncio.sleep(_retry_delay(attempt))
            continue

        try:
            data = json.loads(reply)
            doc_type = data.get("document_type")
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            logger.warning(f"Document-type classification reply was not usable: {e}")
            return None
        return doc_type if doc_type in DOCUMENT_TYPE_CATEGORIES else None
    return None
