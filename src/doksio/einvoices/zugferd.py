from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import BinaryIO
from xml.etree import ElementTree

ZUGFERD_FILENAMES = {
    "factur-x.xml",
    "zugferd-invoice.xml",
    "zugferd.xml",
    "xrechnung.xml",
}


@dataclass(frozen=True)
class ExtractedEInvoice:
    source_filename: str
    raw_xml: str
    data: dict


def extract_einvoice_from_pdf(file_obj: BinaryIO) -> ExtractedEInvoice | None:
    content = file_obj.read()
    if not content:
        return None

    attachment = (
        _extract_with_facturx(content)
        or _extract_with_pypdf(content)
        or _extract_plain_xml(content)
    )
    if attachment is None:
        return None

    source_filename, xml_bytes = attachment
    raw_xml = _decode_xml(xml_bytes)
    if not raw_xml:
        return None

    parsed_data = parse_invoice_xml(raw_xml)
    if not parsed_data:
        return None

    return ExtractedEInvoice(
        source_filename=source_filename,
        raw_xml=raw_xml,
        data={
            "source": "zugferd",
            "source_filename": source_filename,
            **parsed_data,
        },
    )


def _extract_with_facturx(content: bytes) -> tuple[str, bytes] | None:
    try:
        import facturx
    except ImportError:
        return None

    pdf_file = BytesIO(content)
    extract = getattr(facturx, "get_xml_from_pdf", None)
    if extract is None:
        return None

    for kwargs in (
        {"check_xsd": False},
        {"check_xsd": False, "check_schematron": False},
        {},
    ):
        pdf_file.seek(0)
        try:
            xml_content = extract(pdf_file, **kwargs)
        except (TypeError, ValueError):
            continue
        except Exception:
            return None
        attachment = _attachment_from_facturx_value(xml_content)
        if attachment:
            return attachment
    return None


def _attachment_from_facturx_value(value) -> tuple[str, bytes] | None:
    if isinstance(value, tuple) and len(value) == 2:
        filename, xml_content = value
        xml_bytes = _first_attachment_value(xml_content)
        if isinstance(filename, str) and xml_bytes:
            return filename, xml_bytes

    xml_bytes = _first_attachment_value(value)
    if xml_bytes:
        return "factur-x.xml", xml_bytes
    return None


def parse_invoice_xml(raw_xml: str) -> dict:
    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError:
        return {}

    root_name = _local_name(root.tag)
    if root_name == "CrossIndustryInvoice":
        return _parse_cross_industry_invoice(root)
    if root_name == "Invoice":
        return _parse_ubl_invoice(root)
    return {}


def _extract_with_pypdf(content: bytes) -> tuple[str, bytes] | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    try:
        reader = PdfReader(BytesIO(content))
    except Exception:
        return None

    try:
        attachments = getattr(reader, "attachments", {}) or {}
    except Exception:
        return None

    for filename, values in attachments.items():
        normalized_filename = filename.lower()
        if not (
            normalized_filename.endswith(".xml")
            or normalized_filename in ZUGFERD_FILENAMES
        ):
            continue
        xml_bytes = _first_attachment_value(values)
        if xml_bytes:
            return filename, xml_bytes
    return None


def _first_attachment_value(value) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    if isinstance(value, list | tuple):
        for item in value:
            extracted = _first_attachment_value(item)
            if extracted:
                return extracted
    return None


def _extract_plain_xml(content: bytes) -> tuple[str, bytes] | None:
    start = content.find(b"<?xml")
    if start < 0:
        return None

    candidates = [
        b"</rsm:CrossIndustryInvoice>",
        b"</CrossIndustryInvoice>",
        b"</Invoice>",
    ]
    end_positions = []
    for candidate in candidates:
        position = content.find(candidate, start)
        if position >= 0:
            end_positions.append(position + len(candidate))
    if not end_positions:
        return None

    end = min(end_positions)
    return "embedded-invoice.xml", content[start:end]


def _decode_xml(xml_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "iso-8859-1"):
        try:
            return xml_bytes.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return ""


def _parse_cross_industry_invoice(root: ElementTree.Element) -> dict:
    data = {
        "syntax": "CII",
        "profile": _text(
            root,
            "ExchangedDocumentContext",
            "GuidelineSpecifiedDocumentContextParameter",
            "ID",
        ),
        "invoice_number": _text(root, "ExchangedDocument", "ID"),
        "invoice_date": _text(
            root,
            "ExchangedDocument",
            "IssueDateTime",
            "DateTimeString",
        ),
        "seller_name": _text(
            root,
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeAgreement",
            "SellerTradeParty",
            "Name",
        ),
        "buyer_name": _text(
            root,
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeAgreement",
            "BuyerTradeParty",
            "Name",
        ),
        "currency": _text(
            root,
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "InvoiceCurrencyCode",
        ),
        "line_total_amount": _text(
            root,
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementHeaderMonetarySummation",
            "LineTotalAmount",
        ),
        "tax_basis_total_amount": _text(
            root,
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementHeaderMonetarySummation",
            "TaxBasisTotalAmount",
        ),
        "tax_total_amount": _text(
            root,
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementHeaderMonetarySummation",
            "TaxTotalAmount",
        ),
        "grand_total_amount": _text(
            root,
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementHeaderMonetarySummation",
            "GrandTotalAmount",
        ),
        "due_payable_amount": _text(
            root,
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementHeaderMonetarySummation",
            "DuePayableAmount",
        ),
        "tax_breakdown": _parse_cii_tax_breakdown(root),
    }
    return _compact(data)


def _parse_ubl_invoice(root: ElementTree.Element) -> dict:
    data = {
        "syntax": "UBL",
        "profile": _text(root, "ProfileID"),
        "invoice_number": _text(root, "ID"),
        "invoice_date": _text(root, "IssueDate"),
        "seller_name": _text(
            root,
            "AccountingSupplierParty",
            "Party",
            "PartyName",
            "Name",
        ),
        "buyer_name": _text(
            root,
            "AccountingCustomerParty",
            "Party",
            "PartyName",
            "Name",
        ),
        "currency": _text(root, "DocumentCurrencyCode"),
        "line_total_amount": _text(root, "LegalMonetaryTotal", "LineExtensionAmount"),
        "tax_total_amount": _text(root, "TaxTotal", "TaxAmount"),
        "grand_total_amount": _text(root, "LegalMonetaryTotal", "TaxInclusiveAmount"),
        "due_payable_amount": _text(root, "LegalMonetaryTotal", "PayableAmount"),
        "tax_breakdown": _parse_ubl_tax_breakdown(root),
    }
    return _compact(data)


def _parse_cii_tax_breakdown(root: ElementTree.Element) -> list[dict[str, str]]:
    settlement = _find_descendant(root, "ApplicableHeaderTradeSettlement")
    if settlement is None:
        return []

    rows = []
    for tax in _iter_descendants(settlement, "ApplicableTradeTax"):
        net_amount = _text(tax, "BasisAmount")
        if not net_amount:
            continue
        rows.append(
            {
                "category": _text(tax, "CategoryCode"),
                "rate": _text(tax, "RateApplicablePercent"),
                "net_amount": net_amount,
                "tax_amount": _text(tax, "CalculatedAmount"),
            }
        )
    return _compact_tax_breakdown(rows)


def _parse_ubl_tax_breakdown(root: ElementTree.Element) -> list[dict[str, str]]:
    rows = []
    for subtotal in _iter_descendants(root, "TaxSubtotal"):
        net_amount = _text(subtotal, "TaxableAmount")
        if not net_amount:
            continue
        category = _find_child(subtotal, "TaxCategory")
        rows.append(
            {
                "category": _text(category, "ID") if category is not None else "",
                "rate": _text(category, "Percent") if category is not None else "",
                "net_amount": net_amount,
                "tax_amount": _text(subtotal, "TaxAmount"),
            }
        )
    return _compact_tax_breakdown(rows)


def _compact_tax_breakdown(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row.get("category", ""), row.get("rate", ""))
        current = grouped.setdefault(
            key,
            {
                "category": row.get("category", ""),
                "rate": row.get("rate", ""),
                "net_amount": "0",
                "tax_amount": "0",
            },
        )
        current["net_amount"] = _sum_amounts(
            current.get("net_amount", ""),
            row.get("net_amount", ""),
        )
        current["tax_amount"] = _sum_amounts(
            current.get("tax_amount", ""),
            row.get("tax_amount", ""),
        )

    return [_compact(row) for row in grouped.values()]


def _sum_amounts(first: str, second: str) -> str:
    try:
        value = Decimal(first or "0") + Decimal(second or "0")
    except InvalidOperation:
        return second or first
    return format(value, "f")


def _text(root: ElementTree.Element, *path: str) -> str:
    element = root
    for name in path:
        element = _find_child(element, name)
        if element is None:
            return ""
    return (element.text or "").strip()


def _find_child(
    element: ElementTree.Element,
    local_name: str,
) -> ElementTree.Element | None:
    for child in element:
        if _local_name(child.tag) == local_name:
            return child
    return None


def _find_descendant(
    element: ElementTree.Element,
    local_name: str,
) -> ElementTree.Element | None:
    for descendant in element.iter():
        if _local_name(descendant.tag) == local_name:
            return descendant
    return None


def _iter_descendants(
    element: ElementTree.Element,
    local_name: str,
):
    for descendant in element.iter():
        if _local_name(descendant.tag) == local_name:
            yield descendant


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _compact(data: dict) -> dict:
    return {key: value for key, value in data.items() if value not in ("", None)}
