from __future__ import annotations

import re

_INVOICE_NUMBER_PATTERNS = [
    re.compile(
        r"""
        \b
        (?:rechnungs(?:nummer|nr\.?)|belegnummer|fakturanummer)
        \s*[:.]?\s*
        (?P<value>[A-Z0-9][A-Z0-9._/-]{2,})
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""
        \b
        invoice\s*(?:number|no\.?|\#)
        \s*[:.]?\s*
        (?P<value>[A-Z0-9][A-Z0-9._/-]{2,})
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
]
_INVOICE_DATE_PATTERNS = [
    re.compile(
        r"""
        \b
        (?:rechnungsdatum|belegdatum|fakturadatum)
        \s*[:.]?\s*
        (?P<value>
            \d{1,2}[./-]\d{1,2}[./-]\d{2,4}
            |
            \d{4}-\d{1,2}-\d{1,2}
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""
        \b
        invoice\s*date
        \s*[:.]?\s*
        (?P<value>
            \d{1,2}[./-]\d{1,2}[./-]\d{2,4}
            |
            \d{4}-\d{1,2}-\d{1,2}
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
]
_COMPANY_SUFFIX_PATTERN = re.compile(
    r"""
    \b(
        gmbh(?:\s*&\s*co\.?\s*kg)?
        |mbh
        |ag
        |kg
        |ohg
        |gbr
        |ug(?:\s*\(haftungsbeschränkt\))?
        |e\.?\s*k\.?
        |se
        |ltd\.?
        |limited
        |inc\.?
        |s\.?a\.?r\.?l\.?
        |b\.?v\.?
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_HEADER_NOISE_PATTERN = re.compile(
    r"""
    (
        rechnung
        |invoice
        |rechnungsnummer
        |rechnungsdatum
        |kundennummer
        |bestellnummer
        |auftragsnummer
        |lieferschein
        |telefon
        |phone
        |fax
        |www\.
        |https?://
        |@
        |iban
        |bic
        |ust-?id
        |steuer-?nr
        |seite\s+\d+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ADDRESS_ONLY_PATTERN = re.compile(
    r"""
    ^(?:
        [A-Z]{0,2}-?\d{4,5}\s+
        |.*\b(?:straße|str\.|weg|platz|allee)\s+\d
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _first_match(text: str, patterns: list[re.Pattern]) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            return match.group("value").strip(" \t:.,")
    return ""


def _header_segments(text: str) -> list[str]:
    segments = []
    for raw_line in text.splitlines()[:35]:
        for column in re.split(r"\s{2,}", raw_line.strip()):
            candidate = re.split(r"\s*[●•]\s*", column, maxsplit=1)[0]
            candidate = " ".join(candidate.strip(" \t|").split())
            if candidate:
                segments.append(candidate)
    return segments


def _seller_name(text: str) -> str:
    candidates = []
    for index, candidate in enumerate(_header_segments(text)):
        if len(candidate) < 3 or len(candidate) > 100:
            continue
        if _HEADER_NOISE_PATTERN.search(candidate):
            continue
        if _ADDRESS_ONLY_PATTERN.search(candidate):
            continue
        if not any(character.isalpha() for character in candidate):
            continue

        score = 0
        if _COMPANY_SUFFIX_PATTERN.search(candidate):
            score += 10
        if index < 8:
            score += 4
        elif index < 16:
            score += 2
        if any(character.isdigit() for character in candidate):
            score -= 3
        candidates.append((score, -index, candidate))

    if not candidates:
        return ""
    score, _negative_index, candidate = max(candidates)
    return candidate if score >= 4 else ""


def extract_invoice_title_data(text: str) -> dict[str, str]:
    """Extract conservative title fields from an invoice OCR/text layer."""

    return {
        "invoice_number": _first_match(text, _INVOICE_NUMBER_PATTERNS),
        "invoice_date": _first_match(text, _INVOICE_DATE_PATTERNS),
        "seller_name": _seller_name(text),
    }
