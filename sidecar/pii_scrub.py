"""
OpenEye PII scrubber.

Vision models writing scene descriptions don't know what's PII. A
description like "Dr. Sarah Chen at Memorial Hospital placing the
implant..." carries identifying information about both the operator and
the facility. Before any text leaves the device for Context, it should
be scrubbed.

This module is regex-based and intentionally conservative: it errs
toward over-redaction. False positives (e.g. a tool name that looks like
a person's name) become `[REDACTED:NAME]`, which is recoverable; missing
real PII is not.

For production deployments processing high-volume sensitive data,
opt into Presidio:
    pip install presidio-analyzer presidio-anonymizer
    export OPENEYE_PII_BACKEND=presidio

The default `regex` backend has zero extra dependencies.
"""

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default: ON for Context sync. The whole point of the consent attestation
# is that data leaves the device — scrubbing PII before that's the floor.
PII_SCRUB_ENABLED = os.getenv("OPENEYE_PII_SCRUB", "true").strip().lower() in (
    "true", "1", "yes", "on")
PII_BACKEND = os.getenv("OPENEYE_PII_BACKEND", "regex").strip().lower()

# Regex patterns. Order matters: more specific patterns first so we don't
# eat parts of a longer match.
_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # Email — well-defined, low false-positive risk
    ("EMAIL", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),

    # Phone numbers — covers US formats with/without country code, parens,
    # dots, dashes, spaces. Also matches +44 / +33 / etc. international.
    ("PHONE", re.compile(
        r"\b(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")),

    # US Social Security Number — strict format
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),

    # Credit card numbers — 13-19 digit Luhn-ish; we don't validate Luhn
    # because we'd rather over-redact than miss
    ("CREDIT_CARD", re.compile(
        r"\b(?:\d[ -]*?){13,19}\b")),

    # IP addresses (v4) — sometimes appear in network ops procedures
    ("IP", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b")),

    # Dates of birth: YYYY-MM-DD / MM/DD/YYYY / DD-MM-YYYY. We do NOT redact
    # all dates (procedure logs need timestamps); just isolated DOB-shaped strings
    # preceded by DOB/d.o.b/born/age cues. This is intentionally narrow.
    ("DOB", re.compile(
        r"\b(?:DOB|d\.o\.b\.|date of birth|born(?: on)?)\b[:\s]*"
        r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b", re.IGNORECASE)),

    # Honorific + name: Dr./Mr./Ms./Mrs./Prof. followed by 1-3 capitalized words.
    # High-precision pattern; misses bare first names (intentional — too many false positives)
    ("NAME", re.compile(
        r"\b(?:Dr|Mr|Mrs|Ms|Prof|Sir|Madam|Madame|Nurse|Officer|Capt|Capt\.|Captain|Lt|Lt\.|Sgt|Sgt\.|Surgeon)\.?\s+"
        r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b")),

    # Two consecutive capitalized words preceded by "operator/patient/subject/employee/wearer ID/name".
    # Prefix words allow capital first letter (start of sentence) but the
    # name part must be properly Title-Cased — IGNORECASE would void the
    # capital-letter requirement and match "operator placed bolt".
    ("NAMED_ROLE", re.compile(
        r"\b(?:[Oo]perator|[Pp]atient|[Ss]ubject|[Ee]mployee|[Ww]earer|"
        r"[Tt]echnician|[Nn]urse|[Ss]urgeon|[Uu]ser)\s+(?:name\s+)?"
        r"[A-Z][a-z]+\s+[A-Z][a-z]+\b")),

    # Street addresses (numbered street + suffix)
    ("ADDRESS", re.compile(
        r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,3}"
        r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|Way|Plaza|Pl\.?)\b")),
]


def _redact_regex(text: str) -> str:
    """Apply all built-in patterns. Replaces each match with [REDACTED:LABEL]."""
    if not text:
        return text
    for label, pat in _PATTERNS:
        text = pat.sub(f"[REDACTED:{label}]", text)
    return text


_presidio_analyzer = None
_presidio_anonymizer = None


def _redact_presidio(text: str) -> str:
    """Use Presidio analyzers if available. Falls back to regex on import error."""
    global _presidio_analyzer, _presidio_anonymizer
    try:
        if _presidio_analyzer is None:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
            _presidio_analyzer = AnalyzerEngine()
            _presidio_anonymizer = AnonymizerEngine()
    except ImportError:
        logger.warning(
            "OPENEYE_PII_BACKEND=presidio but presidio is not installed. "
            "Falling back to regex. Install with: pip install presidio-analyzer presidio-anonymizer")
        return _redact_regex(text)

    if not text:
        return text
    results = _presidio_analyzer.analyze(text=text, language="en")
    if not results:
        # Still run regex as a backstop — Presidio's English NER misses some
        # of our procedure-specific patterns (honorifics, etc.)
        return _redact_regex(text)
    anonymized = _presidio_anonymizer.anonymize(text=text, analyzer_results=results)
    return _redact_regex(anonymized.text)


def scrub(text: Optional[str]) -> Optional[str]:
    """Redact PII from a single string. None / empty pass through unchanged."""
    if not text:
        return text
    if not PII_SCRUB_ENABLED:
        return text
    if PII_BACKEND == "presidio":
        return _redact_presidio(text)
    return _redact_regex(text)


def scrub_conversations(conversations: List[Dict]) -> List[Dict]:
    """Apply scrub() to every message's `value` field in a ShareGPT conversation.
    Returns a NEW list — does not mutate the input."""
    if not PII_SCRUB_ENABLED:
        return conversations
    out: List[Dict] = []
    for msg in conversations:
        new_msg = dict(msg)
        if "value" in new_msg:
            new_msg["value"] = scrub(new_msg["value"])
        out.append(new_msg)
    return out
