from __future__ import annotations

import re
from string import Formatter

from doksio.documents.invoice_titles import extract_invoice_title_data
from doksio.documents.models import DocumentSpace, DocumentTitleRule

DEFAULT_EINVOICE_TITLE_FORMAT = (
    "{seller_name:.12}: {invoice_number}{invoice_date_suffix}"
)
DEFAULT_INVOICE_OCR_TITLE_FORMAT = DEFAULT_EINVOICE_TITLE_FORMAT
EINVOICE_TITLE_PLACEHOLDERS = {
    "invoice_number": "Rechnungsnummer",
    "invoice_date": "Rechnungsdatum (TT.MM.JJJJ)",
    "invoice_date_suffix": 'Rechnungsdatum mit vorangestelltem " vom "',
    "invoice_date_raw": "Rechnungsdatum im Ursprungsformat",
    "seller_name": "Verkäufer/Lieferant",
    "buyer_name": "Käufer/Empfänger",
    "currency": "Währung",
    "line_total_amount": "Summe der Rechnungspositionen",
    "tax_basis_total_amount": "Steuerbasis",
    "tax_total_amount": "Steuerbetrag",
    "grand_total_amount": "Bruttobetrag",
    "due_payable_amount": "Zahlbetrag",
    "syntax": "eRechnungs-Syntax",
    "profile": "eRechnungs-Profil",
}
INVOICE_OCR_TITLE_PLACEHOLDERS = {
    "invoice_number": "Rechnungsnummer",
    "invoice_date": "Rechnungsdatum (TT.MM.JJJJ)",
    "invoice_date_suffix": 'Rechnungsdatum mit vorangestelltem " vom "',
    "invoice_date_raw": "Rechnungsdatum im erkannten Ursprungsformat",
    "seller_name": "Lieferant aus dem Briefkopf",
}
_SAFE_STRING_FORMAT_SPEC = re.compile(r"(?:\.\d+)?")
_OPTIONAL_EINVOICE_TITLE_PLACEHOLDERS = {"invoice_date_suffix"}
_OPTIONAL_INVOICE_OCR_TITLE_PLACEHOLDERS = {
    "invoice_date_suffix",
    "seller_name",
}

DEFAULT_TITLE_POLICY = {
    "strategy": DocumentTitleRule.Strategy.AUTOMATIC,
    "regex_search": "",
    "regex_replace": "",
    "einvoice_format": DEFAULT_EINVOICE_TITLE_FORMAT,
    "fallback_strategy": DocumentTitleRule.FallbackStrategy.AUTOMATIC,
    "invoice_ocr_format": DEFAULT_INVOICE_OCR_TITLE_FORMAT,
    "invoice_ocr_fallback_strategy": (
        DocumentTitleRule.InvoiceOcrFallbackStrategy.AUTOMATIC
    ),
}


def resolve_document_title_policy(document_space: DocumentSpace) -> dict[str, str]:
    """Resolve the exact box override and then the tenant-wide default."""

    rules = DocumentTitleRule.objects.filter(
        tenant_id=document_space.tenant_id,
    )
    rule = rules.filter(document_space_id=document_space.id).first()
    if rule is None:
        rule = rules.filter(document_space__isnull=True).first()
    return rule.as_policy() if rule is not None else dict(DEFAULT_TITLE_POLICY)


def _validate_title_format(
    format_string: str,
    placeholders: dict[str, str],
) -> tuple[str, ...]:
    if not format_string.strip():
        raise ValueError("Bitte einen Format-String angeben.")

    fields = []
    try:
        parsed_fields = Formatter().parse(format_string)
        for _literal, field_name, format_spec, conversion in parsed_fields:
            if field_name is None:
                continue
            if field_name not in placeholders:
                raise ValueError(f"Unbekannter Platzhalter: {{{field_name}}}.")
            if conversion:
                raise ValueError("Konvertierungen mit ! sind nicht erlaubt.")
            if not _SAFE_STRING_FORMAT_SPEC.fullmatch(format_spec):
                raise ValueError(
                    "Als Formatangabe ist nur eine Längenbegrenzung wie "
                    "{seller_name:.12} erlaubt."
                )
            fields.append(field_name)
    except (ValueError, KeyError, IndexError) as error:
        if isinstance(error, ValueError) and str(error).startswith(
            ("Unbekannter", "Konvertierungen", "Als Formatangabe")
        ):
            raise
        raise ValueError(f"Ungültiger Format-String: {error}") from error

    if not fields:
        raise ValueError(
            "Der Format-String muss mindestens einen Platzhalter enthalten."
        )
    return tuple(fields)


def validate_einvoice_title_format(format_string: str) -> tuple[str, ...]:
    return _validate_title_format(format_string, EINVOICE_TITLE_PLACEHOLDERS)


def validate_invoice_ocr_title_format(format_string: str) -> tuple[str, ...]:
    return _validate_title_format(format_string, INVOICE_OCR_TITLE_PLACEHOLDERS)


def _format_einvoice_date(raw_date: str) -> str:
    if len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[6:]}.{raw_date[4:6]}.{raw_date[:4]}"
    if len(raw_date) == 10 and raw_date[4] == "-" and raw_date[7] == "-":
        return f"{raw_date[8:]}.{raw_date[5:7]}.{raw_date[:4]}"
    return raw_date


def title_from_einvoice_data(
    einvoice_data: dict,
    format_string: str,
) -> str | None:
    required_fields = validate_einvoice_title_format(format_string)
    raw_date = str(einvoice_data.get("invoice_date", "")).strip()
    values = {
        field_name: str(einvoice_data.get(field_name, "")).strip()
        for field_name in EINVOICE_TITLE_PLACEHOLDERS
    }
    values["invoice_date_raw"] = raw_date
    values["invoice_date"] = _format_einvoice_date(raw_date)
    values["invoice_date_suffix"] = (
        f" vom {values['invoice_date']}" if values["invoice_date"] else ""
    )
    if any(
        not values[field_name]
        for field_name in required_fields
        if field_name not in _OPTIONAL_EINVOICE_TITLE_PLACEHOLDERS
    ):
        return None

    title = " ".join(format_string.format_map(values).split())
    return title[:255] if title else None


def title_from_invoice_ocr_text(
    text: str,
    format_string: str,
) -> str | None:
    required_fields = validate_invoice_ocr_title_format(format_string)
    invoice_data = extract_invoice_title_data(text)
    raw_date = invoice_data.get("invoice_date", "").strip()
    values = {
        field_name: str(invoice_data.get(field_name, "")).strip()
        for field_name in INVOICE_OCR_TITLE_PLACEHOLDERS
    }
    values["invoice_date_raw"] = raw_date
    values["invoice_date"] = _format_einvoice_date(raw_date)
    values["invoice_date_suffix"] = (
        f" vom {values['invoice_date']}" if values["invoice_date"] else ""
    )
    if any(
        not values[field_name]
        for field_name in required_fields
        if field_name not in _OPTIONAL_INVOICE_OCR_TITLE_PLACEHOLDERS
    ):
        return None

    title = " ".join(format_string.format_map(values).split())
    title = re.sub(r"^[\s:;,/|-]+", "", title)
    return title[:255] if title else None


def ocr_policy_with_einvoice_fallback(policy: dict | None) -> dict[str, str]:
    policy = policy or {}
    if policy.get("strategy") != DocumentTitleRule.Strategy.EINVOICE:
        return dict(policy)
    fallback_policy = dict(policy)
    fallback_policy["strategy"] = policy.get(
        "fallback_strategy",
        DocumentTitleRule.FallbackStrategy.AUTOMATIC,
    )
    return fallback_policy
