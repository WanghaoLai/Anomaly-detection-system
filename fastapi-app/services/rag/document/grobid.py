"""GROBID TEI 学术元数据补充适配器。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Mapping


TEI_NAMESPACE = "http://www.tei-c.org/ns/1.0"
NS = {"tei": TEI_NAMESPACE}


@dataclass(frozen=True)
class GrobidEnrichmentResult:
    metadata: Mapping[str, object]
    diagnostics: Mapping[str, object]


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join("".join(element.itertext()).split())
    return value or None


def parse_grobid_tei(xml_text: str) -> dict[str, object]:
    root = ET.fromstring(xml_text)
    title = _text(root.find(".//tei:titleStmt/tei:title", NS))
    authors = []
    affiliations = []
    for author in root.findall(".//tei:titleStmt/tei:author", NS):
        name = " ".join(filter(None, [
            _text(author.find(".//tei:forename[@type='first']", NS)),
            _text(author.find(".//tei:forename[@type='middle']", NS)),
            _text(author.find(".//tei:surname", NS)),
        ])).strip()
        if name and name not in authors:
            authors.append(name)
        for affiliation in author.findall(".//tei:affiliation", NS):
            value = _text(affiliation)
            if value and value not in affiliations:
                affiliations.append(value)
    abstract = _text(root.find(".//tei:profileDesc/tei:abstract", NS))
    keywords = [
        value for term in root.findall(".//tei:keywords/tei:term", NS)
        if (value := _text(term))
    ]
    date = root.find(".//tei:publicationStmt/tei:date", NS)
    date_value = (date.get("when") if date is not None else None) or _text(date)
    year_match = re.search(r"(?:19|20)\d{2}", date_value or "")
    doi = None
    for identifier in root.findall(".//tei:idno", NS):
        if str(identifier.get("type") or "").casefold() == "doi":
            doi = _text(identifier)
            break
    venue = _text(root.find(".//tei:monogr/tei:title", NS))
    references = []
    for item in root.findall(".//tei:listBibl/tei:biblStruct", NS):
        value = _text(item)
        if value:
            references.append(value)
    sections = [
        value for head in root.findall(".//tei:body//tei:head", NS)
        if (value := _text(head))
    ]
    return {
        "title": title,
        "authors": authors,
        "affiliations": affiliations,
        "publication_year": int(year_match.group(0)) if year_match else None,
        "venue": venue,
        "keywords": keywords,
        "abstract": abstract,
        "doi": doi,
        "references": references,
        "sections": sections,
    }


class GrobidMetadataEnricher:
    def __init__(
        self,
        *,
        base_url: str = "",
        enabled: bool = True,
        timeout_seconds: float = 30.0,
        client_provider: Callable[[], object] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.enabled = bool(enabled)
        self.timeout_seconds = float(timeout_seconds)
        self._client_provider = client_provider

    def enrich(self, file_bytes: bytes, filename: str) -> GrobidEnrichmentResult:
        if not self.enabled:
            return GrobidEnrichmentResult({}, {
                "metadata_enricher": "disabled",
                "grobid_status": "disabled",
            })
        if not self.base_url and self._client_provider is None:
            return GrobidEnrichmentResult({}, {
                "metadata_enricher": "grobid",
                "grobid_status": "not_configured",
                "warnings": ["GROBID URL 未配置，已跳过学术元数据补充"],
            })
        close_client = self._client_provider is None
        if close_client:
            import httpx
            client = httpx.Client(timeout=self.timeout_seconds)
        else:
            client = self._client_provider()
        try:
            response = client.post(
                f"{self.base_url}/api/processFulltextDocument",
                files={"input": (filename, file_bytes, "application/pdf")},
                data={
                    "consolidateHeader": "0",
                    "consolidateCitations": "0",
                    "includeRawCitations": "1",
                    "generateIDs": "1",
                },
                headers={"Accept": "application/xml"},
            )
            status_code = int(getattr(response, "status_code", 0))
            if status_code == 204:
                return GrobidEnrichmentResult({}, {
                    "metadata_enricher": "grobid",
                    "grobid_status": "empty",
                    "warnings": ["GROBID 未抽取到元数据"],
                })
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            metadata = parse_grobid_tei(str(response.text))
            return GrobidEnrichmentResult(metadata, {
                "metadata_enricher": "grobid",
                "grobid_status": "completed",
                "reference_count": len(metadata.get("references") or []),
            })
        except Exception as exc:
            return GrobidEnrichmentResult({}, {
                "metadata_enricher": "grobid",
                "grobid_status": "failed",
                "warnings": [f"GROBID 补充失败: {type(exc).__name__}"],
            })
        finally:
            if close_client:
                client.close()


__all__ = [
    "GrobidEnrichmentResult",
    "GrobidMetadataEnricher",
    "parse_grobid_tei",
]
