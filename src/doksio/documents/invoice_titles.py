from __future__ import annotations

import re

_INVOICE_NUMBER_PATTERNS = [
    re.compile(
        r"""
        \b
        (?:rechnungs?\s*[- ]?\s*(?:nummer|nr\.?)|belegnummer|fakturanummer)
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
    re.compile(
        r"""
        \b
        lastschrift\s*(?:nummer|nr\.?)
        \s*[:.]?\s*
        (?P<value>[A-Z0-9][A-Z0-9._/-]{2,})
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""
        auftraggeber\s+fakturanummer\s+rechnungsdatum
        [^\S\n]*\n[^\S\n]*
        [^\n]*?\b\d+\s+(?P<value>[A-Z0-9][A-Z0-9._/-]{2,})\s+
        \d{1,2}[./-]\d{1,2}[./-]\d{2,4}
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
        rechnungsdatum
        \s*[:.]?\s*
        (?P<value>\d{1,2}\s+[A-Za-zÄÖÜäöüß]+\s+\d{4})
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
    re.compile(
        r"""
        zahlungsbeleg\s+datum[^\n]*
        [^\S\n]*\n[^\S\n]*
        \S+\s+(?P<value>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
]
_HEADER_DATE_PATTERN = re.compile(
    r"(?<!\d)(?P<value>\d{1,2}[./-]\d{1,2}[./-]\d{4})(?!\d)"
)
_SOLD_BY_PATTERN = re.compile(
    r"\b(?:verkauft|geliefert)\s+von\s+(?P<value>[^\n\r]{3,100})",
    re.IGNORECASE,
)
_COMPANY_SUFFIX_PATTERN = re.compile(
    r"""
    \b(
        gmbh(?:\s*&\s*co\.?\s*kg)?
        |mbh
        |ag
        |kg
        |ohg
        |eg
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
_FIELD_LABEL_VALUES = {
    "auftraggeber",
    "belegdatum",
    "datum",
    "fakturanummer",
    "kundennummer",
    "rechnungsdatum",
    "rechnungsnummer",
    "und",
}


def _first_match(text: str, patterns: list[re.Pattern]) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            value = match.group("value").strip(" \t:.,")
            if value.casefold() not in _FIELD_LABEL_VALUES:
                return value
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


def _clean_seller_name(candidate: str) -> str:
    legal_name_match = re.search(r"\bder\s+(.+)$", candidate, re.IGNORECASE)
    if (
        legal_name_match is not None
        and _COMPANY_SUFFIX_PATTERN.search(legal_name_match.group(1))
    ):
        candidate = legal_name_match.group(1)
    return candidate.strip(" \t:.,")


def _seller_name(text: str) -> str:
    sold_by_match = _SOLD_BY_PATTERN.search(text)
    if sold_by_match is not None:
        return _clean_seller_name(sold_by_match.group("value"))

    header_segments = _header_segments(text)
    for brand_index, brand in enumerate(header_segments[:4]):
        if (
            len(brand) < 3
            or _HEADER_NOISE_PATTERN.search(brand)
            or any(character.isdigit() for character in brand)
        ):
            continue
        if _COMPANY_SUFFIX_PATTERN.search(brand):
            return _clean_seller_name(brand)
        brand_word = next(
            (
                word
                for word in re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", brand)
                if word.casefold() not in {"haus", "ihre", "spezialist"}
            ),
            "",
        )
        if brand_word and any(
            brand_word.casefold() in candidate.casefold()
            and _COMPANY_SUFFIX_PATTERN.search(candidate)
            for candidate in header_segments[brand_index + 1 : 16]
        ):
            return _clean_seller_name(brand)

    for raw_line in text.splitlines()[:40]:
        line = " ".join(raw_line.split())
        if "·" in line or "•" in line:
            candidate = re.split(r"\s*[·•]\s*", line, maxsplit=1)[0].strip()
            if len(candidate) >= 3 and any(character.isalpha() for character in candidate):
                return candidate
        address_sender = re.match(
            r"(?P<value>[^,]{3,100}),\s+.*\b\d{5}\b",
            line,
        )
        if address_sender is not None:
            candidate = address_sender.group("value").strip()
            if not any(character.isdigit() for character in candidate):
                return _clean_seller_name(candidate)

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
    return _clean_seller_name(candidate) if score >= 4 else ""


def extract_invoice_title_data(text: str) -> dict[str, str]:
    """Extract conservative title fields from an invoice OCR/text layer."""

    invoice_number = _first_match(text, _INVOICE_NUMBER_PATTERNS)
    invoice_date = _first_match(text, _INVOICE_DATE_PATTERNS)
    if invoice_number and not invoice_date:
        header_text = "\n".join(text.splitlines()[:40])
        header_date = _HEADER_DATE_PATTERN.search(header_text)
        if header_date is not None:
            invoice_date = header_date.group("value")

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "seller_name": _seller_name(text),
    }
