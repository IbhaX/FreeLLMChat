"""Structured research-document schema — the illustrated-textbook format.

A ``ResearchDocument`` is the presentation contract between the document
composer and the textbook renderer (both the on-screen book view and the
PDF exporter consume the same JSON). Blocks carry typed, structured data —
the model never emits raw HTML; the trusted frontend maps each ``type`` to
a component, mirroring the ``nova-response`` component philosophy.

Layering: this module is pure domain — no I/O, no gateway imports.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    """Content blocks the textbook renderer understands."""

    # structure
    CHAPTER_INTRO = "chapter_intro"
    SECTION = "section"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    # educational callouts
    KEY_CONCEPT = "key_concept"
    HIGHLIGHT = "highlight_box"
    KEY_TAKEAWAY = "key_takeaway"
    DID_YOU_KNOW = "did_you_know"
    IMPORTANT = "important_note"
    WARNING = "warning_box"
    REMEMBER = "remember_this"
    REAL_WORLD = "real_world"
    LETS_UNDERSTAND = "lets_understand"
    THINK_ABOUT = "think_about"
    # visuals
    ILLUSTRATION = "image_illustration"
    GALLERY = "image_gallery"
    INFOGRAPHIC = "infographic"
    CHART = "chart"
    DIAGRAM = "diagram"
    FLOWCHART = "flowchart"
    TIMELINE = "timeline"
    COMPARISON = "comparison_table"
    TABLE = "table"
    STATS = "statistics_card"
    QUOTE = "quote_card"
    CASE_STUDY = "case_study"
    FORMULA = "formula_card"
    CODE = "code_block"
    VOCAB = "vocabulary_card"
    # interactive / learning
    QUIZ = "quiz"
    FLASHCARDS = "flashcards"
    QUICK_CHECK = "quick_check"
    # closing
    SUMMARY = "summary"
    REFERENCES = "references"


class Block(BaseModel):
    """One typed content block. ``data`` is block-type-specific structured
    content; ``blocks`` allows nested components (e.g. a case study holding
    a chart). ``id`` anchors TOC/deep links."""

    type: BlockType
    id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    blocks: list["Block"] = Field(default_factory=list)


class Chapter(BaseModel):
    id: str
    title: str
    number: int = 0
    intro: str = ""
    color: str = ""                       # per-chapter accent (hex or token)
    illustration: str = ""                # optional illustration URL/prompt
    blocks: list[Block] = Field(default_factory=list)


class TocEntry(BaseModel):
    id: str
    title: str
    number: int = 0
    summary: str = ""


class Cover(BaseModel):
    title: str
    subtitle: str = ""
    topic: str = ""
    level: str = ""                       # e.g. "introductory" | "intermediate" | "advanced"
    reading_minutes: int = 0
    illustration: str = ""                # topic illustration (URL or description)


class GlossaryEntry(BaseModel):
    term: str
    definition: str


class QuizItem(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)
    answer_index: int = -1                # -1 = free-response; reveal shows explanation
    explanation: str = ""


class Reference(BaseModel):
    title: str
    url: str = ""
    source: str = ""                      # publisher/site name


class Flashcard(BaseModel):
    front: str
    back: str


class DocumentMeta(BaseModel):
    run_id: int = 0
    depth: str = ""
    model: str = ""
    generated_by: str = "composer"        # composer | fallback
    sources_count: int = 0


class ResearchDocument(BaseModel):
    """The full textbook document. ``toc`` is derived from chapters when the
    composer leaves it empty; the frontend renders it regardless."""

    title: str
    subtitle: str = ""
    topic: str = ""
    level: str = ""
    reading_minutes: int = 0
    cover: Optional[Cover] = None
    toc: list[TocEntry] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    quiz: list[QuizItem] = Field(default_factory=list)
    flashcards: list[Flashcard] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    meta: DocumentMeta = Field(default_factory=DocumentMeta)

    def ensure_structure(self) -> "ResearchDocument":
        """Fill derived fields the composer commonly leaves blank."""
        for ch in self.chapters:
            ch.blocks = _clean_headings(ch.blocks)
            if ch.title:
                t = _GROUP_MARK.sub("", str(ch.title))
                t = _EMPTY_PAREN.sub("", t)
                ch.title = re.sub(r"\s{2,}", " ", t).strip(" \t-–—:,.") or ch.title
        for i, ch in enumerate(self.chapters, 1):
            if not ch.number:
                ch.number = i
            if not ch.id:
                ch.id = f"ch-{i}"
        if not self.toc:
            self.toc = [
                TocEntry(id=ch.id, title=ch.title, number=ch.number,
                         summary=ch.intro[:140])
                for ch in self.chapters
            ]
        if self.cover is None:
            self.cover = Cover(
                title=self.title, subtitle=self.subtitle, topic=self.topic,
                level=self.level, reading_minutes=self.reading_minutes)
        if not self.level:
            self.level = "introductory"
        return self


Block.model_rebuild()

_GROUP_MARK = re.compile(r"\bg\d+\s*\d*\b")
_EMPTY_PAREN = re.compile(r"\(\s*(?:Aspect|Part|Section|Group|Appendix)?\s*\)")
_META_TAIL = re.compile(
    r"`{0,3}\s*nova-response[\s\S]*$", re.IGNORECASE)


def _scrub_text(value: Any) -> Any:
    """Truncate leaked `nova-response` metadata payloads that trail real
    prose inside a block's text fields."""
    if isinstance(value, str):
        return _META_TAIL.sub("", value).rstrip(" `\n")
    if isinstance(value, list):
        return [_scrub_text(v) for v in value]
    return value


def _clean_headings(blocks: list[Block]) -> list[Block]:
    """Remove 'g1 2'-style section markers the writer models leak into
    headings. A heading reduced to nothing is dropped outright."""
    out: list[Block] = []
    for b in blocks:
        if b.type == BlockType.HEADING:
            text = _GROUP_MARK.sub("", str(b.data.get("text", "")))
            text = _EMPTY_PAREN.sub("", text)
            text = re.sub(r"\s{2,}", " ", text).strip(" \t-–—:,.")
            if not text:
                continue
            b.data["text"] = text
        for k, v in b.data.items():
            if isinstance(v, (str, list)):
                b.data[k] = _scrub_text(v)
        b.blocks = _clean_headings(b.blocks)
        if _is_empty(b):
            continue
        out.append(b)
    return out


_CONTENT_KEYS = ("text", "code", "items", "points", "rows", "events",
                 "steps", "expression", "definition", "sections", "data")


def _is_empty(b: Block) -> bool:
    """A block is empty when its *content* fields are all blank — e.g. a
    code_block stripped to nothing by metadata scrubbing. Metadata fields
    like `language`/`title` don't count as content."""
    if b.blocks:
        return False
    vals = [b.data[k] for k in _CONTENT_KEYS
            if isinstance(b.data.get(k), (str, list))]
    if not vals:
        return b.type in (BlockType.CODE_BLOCK, BlockType.PARAGRAPH,
                          BlockType.HEADING, BlockType.SECTION)

    def blank(v: Any) -> bool:
        if isinstance(v, str):
            return not v.strip()
        if isinstance(v, list):
            return all(blank(x) for x in v)
        return False
    return all(blank(v) for v in vals)


def validate_document(obj: dict[str, Any]) -> ResearchDocument:
    """Parse + structurally complete a composer payload."""
    return ResearchDocument.model_validate(obj).ensure_structure()
