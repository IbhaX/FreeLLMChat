"""Research document composer — markdown answer → ResearchDocument.

Stage 7 of the research pipeline (document composition): takes a finished
research answer plus its sources and produces the structured textbook
document. Primary path is the composer agent (native pydantic-ai structured
output — ``ResearchDocument`` validates the model's JSON directly); the
deterministic markdown fallback guarantees a document even when every
composer candidate fails, so the book view never hard-fails.

Layering: this module is application (orchestrates the agent + fallback);
the schema lives in ``research_doc`` (domain).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.services.research_doc import (
    Block, BlockType, Chapter, DocumentMeta, Flashcard,
    GlossaryEntry, QuizItem, Reference, ResearchDocument,
)

log = logging.getLogger("nova.research.composer")

_COMPOSE_RULES = """## Rules
1. PRESERVE every fact, name, number and citation from the source text — composing is restructuring, not rewriting. Never drop substance.
2. NEVER invent statistics, dates, URLs, sources or chart data. If the text has no numbers, emit no chart/statistics blocks.
3. Choose blocks that fit the content; do NOT force every block type in.
4. Keep paragraph text markdown-free except **bold** and *italic* inline.
"""

_BLOCK_CATALOG = """## Block catalog (the blocks array)
Structure: {"type":"heading","data":{"text","level"}}, {"type":"paragraph","data":{"text"}}, {"type":"section","data":{"title","text"}}, {"type":"list","data":{"items","ordered"}}

Educational callouts (use generously — this is what makes it a textbook):
- key_concept {title, text} — an important idea explained
- did_you_know {text} — an interesting verified fact from the report
- key_takeaway {text} — concise "remember this" line
- important_note {text}, warning_box {text} — cautions/limitations
- real_world {title, text} — how it applies in everyday life
- lets_understand {title, steps[]} — break a complex idea into steps
- think_about {question} — a thought-provoking question
- quick_check {question, answer} — one knowledge-check with hidden answer
- highlight_box {title, text} — featured content

Visual/data (only when the text actually contains the data — NEVER invent numbers or datasets):
- table {title, columns[], rows[][]}, comparison_table {title, columns[], rows[][]}
- chart {chart_type ("bar"|"line"|"pie"|"donut"|"area"|"scatter"), title, data: [{x, <series>: value}]} — only for figures present in the text
- statistics_card {title, stats:[{label, value, delta?}]}
- timeline {title, events:[{date, title, description}]}
- flowchart {title, steps[]} or diagram {title, description, labels[]}
- quote_card {text, author}
- case_study {title, text}
- formula_card {expression, label}
- code_block {language, code, filename?}
- vocabulary_card {term, definition}
- image_illustration {caption, description} — description of the ideal illustration; include where a visual genuinely helps
- summary {title, points[]} — section-closing summary
"""

COMPOSE_SYSTEM = """You are IbhaX's textbook document composer. Given a report OUTLINE you produce the book's skeleton — metadata, chapter plan, glossary, quiz, flashcards. Chapters come pre-split; you name and color them.

## Output (SkeletonDoc)
- title, subtitle, topic, level ("introductory"|"intermediate"|"advanced"), reading_minutes (estimate)
- chapters: one object per outline section, same order: {title (a clean chapter title — refine the raw heading), intro (1-2 sentences inviting the reader), color (one of navy|sky|mint|yellow|coral|lavender — cycle for variety)}
- glossary: [{term, definition}] — only terms actually used, up to 8
- quiz: [{question, options (3-4), answer_index, explanation}] — 3-6 items when content permits
- flashcards: [{front, back}] — key concepts worth memorizing, up to 6

Rules: never invent content not implied by the outline; chapter count must equal the outline section count.
"""

SECTION_SYSTEM = """You are IbhaX's textbook section composer. Convert ONE report section into textbook blocks (JSON: {"intro": "1-2 sentence chapter introduction", "blocks": [...]}).

""" + _BLOCK_CATALOG + _COMPOSE_RULES + """
Additionally: start with a "chapter_intro" block {"type":"paragraph","data":{"text": "…"}} only if the section lacks an opening paragraph. Keep every fact.
"""

COMPOSE_MODEL_ENV = "COMPOSER_MODEL"


_MAX_SECTION_CHARS = 12_000    # per-section input bound (free-tier request limits)
_MAX_SOURCES = 20


class _ChapterPlan(BaseModel):
    title: str
    intro: str = ""
    color: str = ""


class _SkeletonDoc(BaseModel):
    title: str = ""
    subtitle: str = ""
    topic: str = ""
    level: str = ""
    reading_minutes: int = 0
    chapters: list[_ChapterPlan] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    quiz: list[QuizItem] = Field(default_factory=list)
    flashcards: list[Flashcard] = Field(default_factory=list)


class _SectionDoc(BaseModel):
    intro: str = ""
    blocks: list[Block] = Field(default_factory=list)


def _section_prompt(question: str, title: str, body: str) -> str:
    return (f"Research question: {question}\nSection heading: {title}\n\n"
            f"Section text to compose into blocks:\n\n{body}\n\n"
            "Emit the section JSON now.")


def _outline_prompt(question: str, sections: list[tuple[str, str]]) -> str:
    outline = "\n\n".join(
        f"### Section {i + 1}: {t}\n{(b[:400] + '…') if len(b) > 400 else b}"
        for i, (t, b) in enumerate(sections))
    return (f"Research question: {question}\n\n"
            f"The report has exactly {len(sections)} sections. Outline:\n\n"
            f"{outline}\n\nProduce the skeleton now — one chapter object "
            f"per section, in the same order.")


async def _try_output(gateway: Any, model: Optional[str], system: str,
                      prompt: str, out_type: Any, emit: Any) -> Any:
    """One bounded agent call for a structured output; returns the parsed
    object or ``None``. Never raises — callers own the fallback."""
    import os

    from app.agents.deps import AgentDeps
    from app.agents.factory import build_simple_agent
    from app.services.model_policy import stage_models

    cand_model = model or os.environ.get(COMPOSE_MODEL_ENV, "auto:smart")
    for cand in stage_models(cand_model, role="reasoning"):
        try:
            deps = AgentDeps(gateway, emit=emit)
            agent = build_simple_agent(
                cand, system, deps, tools=[], temperature=0,
                output_type=out_type, retries=1)
            res = await agent.run(prompt, deps=deps)
            if isinstance(res.output, out_type):
                return res.output
            log.debug("composer got %s (model=%s)", type(res.output).__name__, cand)
        except Exception as exc:
            log.debug("composer call failed (model=%s): %s", cand, exc)
    return None


async def compose_document(
    gateway: Any,
    question: str,
    answer: str,
    *,
    sources: Optional[list[dict[str, Any]]] = None,
    run_id: int = 0,
    model: Optional[str] = None,
    emit: Any = None,
) -> Optional[ResearchDocument]:
    """Compose a ``ResearchDocument`` section-wise so every provider call
    stays inside free-tier request limits.

    1. Split the markdown report into sections (H1/H2 boundaries).
    2. One small skeleton call (outline → book metadata + chapter plan +
       glossary/quiz/flashcards).
    3. Parallel per-section block calls (bounded input AND output), each
       with a deterministic parse fallback — a failed section still lands
       in the book as basic blocks rather than disappearing.

    Returns ``None`` only when the input is unusable; callers then fall
    back to :func:`markdown_to_document` wholesale.
    """
    import asyncio

    if not isinstance(answer, str) or not answer.strip():
        return None
    text, refs = _strip_sources(answer, sources)
    sections = _split_sections(text)
    if not sections:
        return None

    sem = asyncio.Semaphore(3)

    async def skeleton() -> Any:
        async with sem:
            return await _try_output(
                gateway, model, COMPOSE_SYSTEM,
                _outline_prompt(question, sections), _SkeletonDoc, emit)

    async def section(i: int, title: str, body: str) -> tuple[str, list[Block], bool]:
        """(intro, blocks, composed?) — composed=False means the
        deterministic parser produced the blocks."""
        if len(body) > _MAX_SECTION_CHARS:
            body = body[:_MAX_SECTION_CHARS]
        async with sem:
            out = await _try_output(
                gateway, model, SECTION_SYSTEM,
                _section_prompt(question, title, body), _SectionDoc, emit)
        if isinstance(out, _SectionDoc) and out.blocks:
            return out.intro, out.blocks, True
        return "", _blocks_from_lines(body.split("\n")), False

    sk, *secs = await asyncio.gather(
        skeleton(),
        *(section(i, t, b) for i, (t, b) in enumerate(sections)))

    chapters: list[Chapter] = []
    for i, ((title, _body), (intro, blocks, _ok)) in enumerate(
            zip(sections, secs), 1):
        plan = (sk.chapters[i - 1] if isinstance(sk, _SkeletonDoc)
                and i - 1 < len(sk.chapters) else None)
        chapters.append(Chapter(
            id=f"ch-{i}", number=i,
            title=(plan.title if plan and plan.title else title),
            intro=(plan.intro if plan and plan.intro else intro),
            color=(plan.color if plan and plan.color in _PALETTE
                   else _PALETTE[(i - 1) % len(_PALETTE)]),
            blocks=blocks))
    composed = isinstance(sk, _SkeletonDoc)
    doc = ResearchDocument(
        title=(sk.title if composed and sk.title else question.strip()[:160]
               or "Research report"),
        subtitle=(sk.subtitle if composed else "An illustrated research report"),
        topic=(sk.topic if composed and sk.topic else question.strip()[:80]),
        level=(sk.level if composed else ""),
        reading_minutes=(sk.reading_minutes if composed else 0),
        chapters=chapters,
        glossary=sk.glossary if composed else [],
        quiz=sk.quiz if composed else [],
        flashcards=sk.flashcards if composed else [],
        references=refs,
        meta=DocumentMeta(
            run_id=run_id,
            generated_by="composer" if composed else "fallback",
            sources_count=len(refs)),
    )
    return doc.ensure_structure()


# --------------------------------------------------------------------------
# Deterministic markdown -> document fallback
# --------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_FENCE = re.compile(r"^\s*```(\w*)\s*$")
_PALETTE = ["navy", "sky", "mint", "yellow", "coral", "lavender"]
_SOURCES_TAIL = re.compile(
    r"(?:^|\n)#{1,4}\s*(?:sources|references|citations|bibliography|further reading)\b[^\n]*\n((?:.*\n?)+)$",
    re.IGNORECASE)
_SRC_LINE = re.compile(
    r"^\s*[-*•]?\s*(?:\[(\d+)\]\s*)?(?:\[(.+?)\]\((https?://[^)\s]+)\)|(.+?)((?:https?://\S+)?))\s*$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse_table(lines: list[str]) -> dict[str, Any] | None:
    if len(lines) < 3:
        return None
    cols = _cells(lines[0])
    rows = [c for c in (_cells(l) for l in lines[2:] if _TABLE_LINE.match(l)) if any(c)]
    return {"columns": cols, "rows": rows} if cols and rows else None


def _strip_sources(answer: str, sources: Optional[list[dict[str, Any]]],
                   ) -> tuple[str, list[Reference]]:
    """Merge collected sources with any trailing sources/references section
    in the markdown, returning (body_without_tail, references)."""
    refs: list[Reference] = []
    for r in sources or []:
        if isinstance(r, dict) and (r.get("title") or r.get("url")):
            refs.append(Reference(
                title=str(r.get("title") or r.get("url") or "")[:160],
                url=str(r.get("url") or "")[:500],
                source=str(r.get("source") or "")[:120]))
    text = answer.strip()
    tail = _SOURCES_TAIL.search(text)
    if tail:
        for line in tail.group(1).strip().split("\n"):
            m = _SRC_LINE.match(line)
            if not m:
                continue
            title = (m.group(2) or m.group(4) or "").strip()[:160]
            url = (m.group(3) or m.group(5) or "").strip()[:500]
            if title or url:
                refs.append(Reference(title=title or url, url=url))
        text = text[: tail.start()].strip()
    return text, refs


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (title, body) sections on H1/H2 boundaries.
    Content before the first heading becomes an 'Overview' section."""
    sections: list[tuple[str, str]] = []
    title, body = "", []
    for line in text.split("\n"):
        hm = _HEADING.match(line)
        if hm and len(hm.group(1)) <= 2:
            if body or title:
                sections.append((title or "Overview", "\n".join(body).strip()))
            title = re.sub(r"^chapter\s+\d+[:\s-]*", "",
                           hm.group(2).strip(), flags=re.I)
            body = []
        else:
            body.append(line)
    if body or title:
        sections.append((title or "Overview", "\n".join(body).strip()))
    return [(t, b) for t, b in sections if t or b.strip()]


def _blocks_from_lines(lines: list[str]) -> list[Block]:
    """Markdown line-walk → typed blocks (fences, tables, lists, quotes,
    headings ≥3, paragraphs)."""
    blocks: list[Block] = []
    para: list[str] = []
    i = 0

    def flush_para() -> None:
        nonlocal para
        buf = "\n".join(para).strip()
        para = []
        if buf:
            blocks.append(Block(type=BlockType.PARAGRAPH, data={"text": buf}))

    while i < len(lines):
        line = lines[i]
        fm = _FENCE.match(line)
        if fm:
            flush_para()
            buf: list[str] = []
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            blocks.append(Block(type=BlockType.CODE, data={
                "language": fm.group(1) or "text", "code": "\n".join(buf)}))
            i += 1
            continue
        hm = _HEADING.match(line)
        if hm:
            flush_para()
            blocks.append(Block(type=BlockType.HEADING, data={
                "text": hm.group(2).strip(), "level": min(len(hm.group(1)), 4)}))
            i += 1
            continue
        if _TABLE_LINE.match(line) and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush_para()
            block = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and _TABLE_LINE.match(lines[i]):
                block.append(lines[i])
                i += 1
            parsed = _parse_table(block)
            if parsed:
                blocks.append(Block(type=BlockType.TABLE, data=parsed))
            continue
        if _BULLET.match(line) or _NUMBERED.match(line):
            flush_para()
            ordered = bool(_NUMBERED.match(line))
            items: list[str] = []
            while i < len(lines):
                bm = _BULLET.match(lines[i]) or _NUMBERED.match(lines[i])
                if not bm:
                    break
                items.append(bm.group(1).strip())
                i += 1
            blocks.append(Block(type=BlockType.LIST,
                                data={"items": items, "ordered": ordered}))
            continue
        if _QUOTE.match(line):
            flush_para()
            qts: list[str] = []
            while i < len(lines):
                m2 = _QUOTE.match(lines[i])
                if not m2:
                    break
                qts.append(m2.group(1))
                i += 1
            blocks.append(Block(type=BlockType.QUOTE,
                                data={"text": " ".join(qts)}))
            continue
        if line.strip():
            para.append(line.rstrip())
        i += 1
    flush_para()
    return blocks


def markdown_to_document(
    question: str,
    answer: str,
    *,
    sources: Optional[list[dict[str, Any]]] = None,
    run_id: int = 0,
) -> Optional[ResearchDocument]:
    """Split a markdown report into chapters and typed blocks. Never fails
    on unparseable input — worst case the whole report lands as paragraphs
    inside a single chapter."""
    if not isinstance(answer, str) or not answer.strip():
        return None
    text, refs = _strip_sources(answer, sources)
    sections = _split_sections(text)
    if not sections:
        return None
    chapters = [
        Chapter(id=f"ch-{i}", number=i, title=title,
                color=_PALETTE[(i - 1) % len(_PALETTE)],
                blocks=_blocks_from_lines(body.split("\n")))
        for i, (title, body) in enumerate(sections, 1)
    ]
    doc = ResearchDocument(
        title=question.strip()[:160] or "Research report",
        subtitle="An illustrated research report",
        topic=question.strip()[:80],
        chapters=chapters,
        references=refs,
        meta=DocumentMeta(run_id=run_id, generated_by="fallback",
                          sources_count=len(refs)),
    )
    return doc.ensure_structure()
