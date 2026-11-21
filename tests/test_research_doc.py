"""Research-document schema + deterministic composer fallback tests."""

import asyncio

import pytest

from app.services.research_composer import (
    compose_document, markdown_to_document)
from app.services.research_doc import (
    BlockType, ResearchDocument, validate_document)

MD = """## Overview

The speed of light is exactly 299,792,458 m/s [1].

## Measurement history

Early estimates came from astronomy.

- Rømer: first quantitative estimate (1676)
- Fizeau: first terrestrial measurement (1849)
- Michelson: precision rotating-mirror work

| Method | Era | Result (km/s) |
|---|---|---|
| Rømer | 1676 | ~220,000 |
| Michelson | 1926 | ~299,796 |

> The light speed is a universal constant. — common physics adage

```python
c = 299_792_458  # m/s, exact by SI definition
```

## Sources
- [BIPM SI Brochure](https://www.bipm.org/si)
"""


def test_fallback_splits_chapters_and_blocks() -> None:
    doc = markdown_to_document("What is the speed of light?", MD, run_id=3)
    assert doc is not None
    assert len(doc.chapters) == 2
    types = [b.type for ch in doc.chapters for b in ch.blocks]
    assert BlockType.LIST in types
    assert BlockType.TABLE in types
    assert BlockType.QUOTE in types
    assert BlockType.CODE in types
    assert doc.meta.generated_by == "fallback"


def test_fallback_collects_sources_and_dedupes_tail() -> None:
    doc = markdown_to_document(
        "q", MD, sources=[{"title": "NIST", "url": "https://nist.gov"}])
    assert doc is not None
    urls = {r.url for r in doc.references}
    assert "https://nist.gov" in urls
    assert "https://www.bipm.org/si" in urls
    # Sources tail is stripped from chapter content.
    body = " ".join(
        b.data.get("text", "") for ch in doc.chapters for b in ch.blocks)
    assert "bipm.org" not in body


def test_fallback_no_headings_single_chapter() -> None:
    doc = markdown_to_document("q", "just a paragraph, no headings")
    assert doc is not None
    assert len(doc.chapters) == 1
    assert doc.chapters[0].blocks[0].type == BlockType.PARAGRAPH


def test_fallback_empty_returns_none() -> None:
    assert markdown_to_document("q", "") is None
    assert markdown_to_document("q", "   ") is None


def test_ensure_structure_fills_toc_numbers_and_cover() -> None:
    doc = validate_document({
        "title": "T",
        "chapters": [{"id": "", "title": "A"}, {"id": "b", "title": "B"}],
    })
    assert [c.number for c in doc.chapters] == [1, 2]
    assert doc.chapters[0].id == "ch-1"
    assert [(t.id, t.title) for t in doc.toc] == [
        ("ch-1", "A"), ("b", "B")]
    assert doc.cover is not None and doc.cover.title == "T"
    assert doc.level == "introductory"


def test_validate_document_rejects_bad_payload() -> None:
    with pytest.raises(Exception):
        validate_document({"chapters": [{"title": 42}]})  # title must be str
    with pytest.raises(Exception):
        validate_document({"title": 5})


def test_round_trip_serialization() -> None:
    doc = markdown_to_document("q", MD, run_id=9)
    assert doc is not None
    again = validate_document(doc.model_dump(mode="json"))
    assert again.model_dump(mode="json") == doc.model_dump(mode="json")


def test_document_json_is_block_typed() -> None:
    doc = markdown_to_document("q", MD)
    assert doc is not None
    raw = doc.model_dump(mode="json")
    for ch in raw["chapters"]:
        for b in ch["blocks"]:
            assert b["type"] in {t.value for t in BlockType}


def test_compose_degrades_when_agent_unavailable() -> None:
    """No gateway at all → every agent call fails internally, each section
    falls back to the deterministic parser — a fallback-tagged document is
    still produced rather than hard-failing."""
    doc = asyncio.run(compose_document(None, "q", MD, run_id=1))
    assert doc is not None
    assert doc.meta.generated_by == "fallback"
    assert len(doc.chapters) == 2
    types = [b.type for ch in doc.chapters for b in ch.blocks]
    assert BlockType.TABLE in types


def test_compose_empty_input_returns_none() -> None:
    assert asyncio.run(compose_document(None, "q", "")) is None
