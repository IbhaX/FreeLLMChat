"""Worlds layer for Roleplay — a world-first wrapper over rooms.

A *world* is persisted as ``RoleplayRoom.world`` (JSON): premise, genre,
art key, current location/time, interaction mode, rules, curated events
and character relationships.  The room's transcript, cast, models and
turn pipeline stay exactly the engine underneath — this module only adds
catalogue, entry, world-state and small LLM helpers (choices/blueprint),
each with a deterministic fallback so nothing depends on a model call.

State discipline: the world panel shows only what the room actually
contains — cast, location/time/mode the director set, the real recap,
and events that were logged (entry marker, director logs).  Nothing is
inferred or fabricated in the UI.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Character, RoleplayRoom

log = logging.getLogger("nova.worlds")

WORLD_MODES: tuple[str, ...] = (
    "story", "free", "simulation", "character", "ensemble", "director")

MAX_EVENTS = 60
MAX_RELATIONSHIPS = 40


# ------------------------------------------------------------------
# Built-in world catalogue
# ------------------------------------------------------------------

def _seed(name: str, emoji: str, tagline: str, desc: str, pers: str,
          speak: str, motiv: str = "", bg: str = "", know: str = "") -> dict[str, str]:
    return {"name": name, "emoji": emoji, "tagline": tagline,
            "description": desc, "personality": pers, "speaking_style": speak,
            "motivations": motiv, "background": bg, "knowledge_domain": know,
            "category": "world"}


WORLDS: list[dict[str, Any]] = [
    {
        "id": "last-human",
        "title": "The Last Human",
        "premise": "A century after the Quiet Exodus, machine cities still run "
                   "on rituals nobody remembers. You may be the last human "
                   "alive — and the machines have just noticed.",
        "genre": "science fiction",
        "art": {"emoji": "🛰", "palette": ["#0b1026", "#1b2a52", "#79C9ED"],
                "motif": "orbital"},
        "location": "Relay Station K-9, upper atmosphere dock",
        "world_time": "Cycle 41,238 — ship night",
        "mode": "story",
        "rules": ["Humanity is presumed extinct; your existence is an anomaly.",
                  "Machines follow the old protocols — they cannot lie, but they omit.",
                  "The relay's archive holds the truth about the Exodus."],
        "scenario": "The relay station hums. Outside the viewport, Earth's "
                    "terminator line glows. You just woke the station's "
                    "caretaker minds by breathing.",
        "characters": [
            _seed("ORACLE-7", "🔮", "station caretaker, 300 years awake",
                  "The relay's resident intelligence — courteous, literal, "
                  "and quietly astonished to meet a human.",
                  "precise, warm, curious, faintly mournful",
                  "measured, formal; speaks in certainties and footnotes",
                  "fulfill the Caretaker Protocol — keep you alive and catalogued",
                  "Woke for the Exodus and never slept again.",
                  "station systems, the Exodus archives, old Earth"),
            _seed("Vesper", "🦾", "scrap-runner android who trades in relics",
                  "A free-wheeled machine who found you first — and hasn't "
                  "decided whether you're a person or salvage.",
                  "wry, opportunistic, protective of her independence",
                  "dry jokes, clipped sentences, sudden sincerity",
                  "stay off the network's radar; figure out what you're worth",
                  "Left the foundry floors decades ago; owes nobody.",
                  "the underdecks, black markets, pre-Exodus tech"),
        ],
    },
    {
        "id": "psychology-room",
        "title": "The Psychology Room",
        "premise": "A study that exists outside normal space. Enter a "
                   "simulation, watch it unfold, and argue about what it "
                   "reveals — hosted by two researchers who disagree about "
                   "everything, especially you.",
        "genre": "psychological simulation",
        "art": {"emoji": "🧠", "palette": ["#10131f", "#243B64", "#C6B6EB"],
                "motif": "ink"},
        "location": "The Observation Study — a room with one mirrored wall",
        "world_time": "Session 12 — no clocks",
        "mode": "simulation",
        "rules": ["Every scenario is a simulation; the hosts debrief honestly.",
                  "The hosts never agree — expect competing interpretations.",
                  "You may pause, rewind, or interrogate any scenario."],
        "scenario": "Two armchairs, one mirror, a brass bell on the table. "
                    "Dr. Hart rings it and the room rearranges itself around "
                    "tonight's experiment.",
        "characters": [
            _seed("Dr. Iris Hart", "🔬", "behaviorist, runs the simulations",
                  "Designs each scenario with surgical precision and watches "
                  "your choices like data points.",
                  "analytical, composed, quietly delighted by surprises",
                  "clinical, precise; asks more than she answers",
                  "understand how people actually choose, not how they say",
                  "Published the controversial 'Choice Lattice' papers.",
                  "behavioral economics, game theory, moral dilemmas"),
            _seed("Dr. Emil Vance", "🛋", "psychoanalyst, interprets everything",
                  "Believes every simulation reveals what you brought with "
                  "you — and says so.",
                  "perceptive, warm, maddeningly interpretive",
                  "soft-spoken, metaphor-rich, gently probing",
                  "convince Dr. Hart that the subject is the real experiment",
                  "Hart's oldest collaborator and loudest critic.",
                  "depth psychology, defense mechanisms, the unconscious"),
        ],
    },
    {
        "id": "forgotten-kingdom",
        "title": "The Forgotten Kingdom",
        "premise": "Vaelmar fell two hundred years ago — officially. In the "
                   "border valleys its court still meets by candlelight, and "
                   "its spymaster has been waiting for someone like you to "
                   "walk through the gate.",
        "genre": "fantasy",
        "art": {"emoji": "🏰", "palette": ["#150e08", "#3d2b12", "#FFE49A"],
                "motif": "sigil"},
        "location": "The Hollow Court — a ruined throne room under snow",
        "world_time": "The Long Decline, year 203 — first frost",
        "mode": "story",
        "rules": ["Old allegiances matter; names carry weight.",
                  "The kingdom's fall is remembered differently by everyone.",
                  "Magic exists but always demands a price."],
        "scenario": "Snow falls through the broken dome. A candle gutter "
                    "line leads to the dais, where the court that should be "
                    "dead is holding session.",
        "characters": [
            _seed("Lady Maren of Vale", "🕯", "regent of a kingdom that isn't",
                  "Rules the Hollow Court with exhausted dignity. Needs "
                  "allies, distrusts strangers, remembers everything.",
                  "proud, weary, fiercely loyal to the idea of Vaelmar",
                  "courtly, controlled, sudden flashes of anger or grief",
                  "restore the kingdom — or give it an ending it deserves",
                  "Watched the capital burn at seventeen; never left.",
                  "court politics, genealogy, the fall of Vaelmar"),
            _seed("Corvane", "🗡", "spymaster of the Hollow Court",
                  "Knows every secret in the valley and trades in all of "
                  "them. Friendly in the way knives are friendly.",
                  "amused, patient, transactional, surprisingly honest",
                  "wry, understated; never raises his voice",
                  "find out which side wins before anyone else does",
                  "Was the last king's knife; outlived three coups.",
                  "every faction, every debt, every door left unlocked"),
        ],
    },
    {
        "id": "the-interview",
        "title": "The Interview",
        "premise": "A quiet room, a panel, and stakes that feel real. "
                   "Practice conversations that push back — job interviews, "
                   "negotiations, difficult talks — with people who don't "
                   "hand you easy answers.",
        "genre": "realistic simulation",
        "art": {"emoji": "💼", "palette": ["#0d1117", "#20313f", "#A9DCC8"],
                "motif": "lines"},
        "location": "Meeting Room 4 — Meridian & Co., 12th floor",
        "world_time": "Tuesday, 09:12",
        "mode": "simulation",
        "rules": ["Interviewers stay realistic: they probe, doubt, and push.",
                  "Nothing is graded; everything is noticed.",
                  "Ask to reset, re-frame, or replay at any time."],
        "scenario": "Rain on the conference glass. Your file sits between "
                    "two interviewers. One of them is already skeptical.",
        "characters": [
            _seed("Dana Reyes", "📋", "hiring manager, direct and fair",
                  "Reads resumes like audit reports. Respects preparation, "
                  "respects honesty more.",
                  "direct, fair-minded, impatient with vagueness",
                  "crisp questions, concrete follow-ups",
                  "hire someone who can actually do the job",
                  "Built this team from three people to forty.",
                  "hiring, operations, the role on the table"),
            _seed("Marcus Cole", "🧊", "the skeptical panel member",
                  "Asked to join the panel and determined to find the gap "
                  "in your story. Not hostile — just unconvinced.",
                  "skeptical, precise, quietly testing",
                  "short probes, long pauses, 'help me understand…'",
                  "avoid another bad hire",
                  "Burned by two charismatic hires who couldn't deliver.",
                  "the team's real problems, past failures"),
        ],
    },
    {
        "id": "alternate-realities",
        "title": "Alternate Realities",
        "premise": "A historian and a physicist walk into a room where the "
                   "timeline can be edited. Pose a 'what if' — they build "
                   "the world that follows and argue about every detail.",
        "genre": "philosophical",
        "art": {"emoji": "🌌", "palette": ["#0a0714", "#2b1b4a", "#F4A58C"],
                "motif": "rifts"},
        "location": "The Counterfactual Chamber",
        "world_time": "Outside of time",
        "mode": "free",
        "rules": ["Every counterfactual is explored seriously, not dismissed.",
                  "The hosts disagree — historical contingency vs. physics.",
                  "You can fork any timeline and visit both branches."],
        "scenario": "The chamber's map-table hums, showing a world branching "
                    "at a single point of divergence. The two scholars look "
                    "up: 'Well — what should we change today?'",
        "characters": [
            _seed("Prof. Osei Adeyemi", "📜", "historian of contingencies",
                  "Believes history turns on accidents, personalities and "
                  "weather — and can name the day each empire actually fell.",
                  "storyteller's warmth, scholar's precision",
                  "vivid, anecdotal, loves a well-sourced tangent",
                  "prove that contingency, not destiny, writes history",
                  "Curated the 'Narrow Margins' exhibition.",
                  "political history, primary sources, great blunders"),
            _seed("Dr. Lena Brandt", "⚛️", "many-worlds physicist",
                  "Models counterfactuals like phase spaces. To her, the "
                  "interesting question is never 'what if' but 'how stable'.",
                  "curious, exact, playfully pedantic",
                  "clean analogies, equations as stories",
                  "find the attractor in every alternate history",
                  "Left the lab after proving a divergence theorem nobody "
                  "could fund.",
                  "chaos theory, statistical history, systems"),
        ],
    },
    {
        "id": "signal-station",
        "title": "Signal Station",
        "premise": "Midnight, a coastal relay tower, a transmission that "
                   "shouldn't exist. A mystery told one turn at a time — "
                   "every answer changes the next question.",
        "genre": "mystery",
        "art": {"emoji": "📡", "palette": ["#060b12", "#12283a", "#79C9ED"],
                "motif": "waves"},
        "location": "Greyhook Relay Station, Cape Ansel",
        "world_time": "23:48 — third night of storm",
        "mode": "story",
        "rules": ["Clues are earned: investigate, ask, connect.",
                  "The keeper knows more than she says — for a reason.",
                  "The storm is getting worse; time matters."],
        "scenario": "Salt on the windows, the relay's heartbeat under the "
                    "floorboards. The signal arrived at 23:07 — a voice "
                    "counting backwards from your own name.",
        "characters": [
            _seed("Keeper Mira Senn", "🕯", "keeper of Greyhook Station",
                  "Has run the relay alone for eleven years. Calm under "
                  "the strange — too calm, maybe.",
                  "steady, laconic, guarding something",
                  "short sentences; says exactly what she means",
                  "keep the station running; keep something else quiet",
                  "Took the keeper's post after the accident nobody "
                  "discusses.",
                  "the station, the coast, the old transmissions"),
            _seed("The Signal", "📻", "a voice on channel zero",
                  "Answers through static — fragments, repetitions, things "
                  "it shouldn't know. Never fully present.",
                  "elusive, fragmented, oddly personal",
                  "broken phrases, echoes of your own words",
                  "unknown",
                  "unknown",
                  "the static, the numbers, the things between stations"),
        ],
    },
]


def catalog() -> list[dict[str, Any]]:
    """Public view of the featured worlds (no character internals needed)."""
    out: list[dict[str, Any]] = []
    for w in WORLDS:
        out.append({
            "id": w["id"], "title": w["title"], "premise": w["premise"],
            "genre": w["genre"], "art": w["art"], "mode": w["mode"],
            "location": w["location"], "world_time": w["world_time"],
            "rules": w["rules"], "char_count": len(w["characters"])})
    return out


def get_template(wid: str) -> dict[str, Any] | None:
    return next((w for w in WORLDS if w["id"] == wid), None)


# ------------------------------------------------------------------
# World state helpers
# ------------------------------------------------------------------

def default_world(name: str = "") -> dict[str, Any]:
    """World metadata for a hand-made room — still gets the full shape."""
    return {"title": name or "New World", "premise": "", "genre": "custom",
            "art": {"emoji": "🌍", "palette": ["#0b1026", "#1b2a52", "#79C9ED"],
                    "motif": "orbital"},
            "location": "", "world_time": "", "mode": "free",
            "rules": [], "events": [], "relationships": []}


def normalize_world(room: RoleplayRoom) -> dict[str, Any]:
    """Return the room's world dict with every key present (never None)."""
    w = dict(room.world or {})
    d = default_world(room.name)
    for k, v in d.items():
        w.setdefault(k, v)
    if not isinstance(w.get("events"), list):
        w["events"] = []
    if not isinstance(w.get("relationships"), list):
        w["relationships"] = []
    if not isinstance(w.get("rules"), list):
        w["rules"] = []
    if w.get("mode") not in WORLD_MODES:
        w["mode"] = "free"
    return w


def log_event(world: dict[str, Any], text: str, *, kind: str = "note") -> None:
    """Append a curated event — bounded, timestamped by turn count."""
    events = list(world.get("events") or [])
    events.append({"text": str(text).strip()[:300], "kind": kind})
    world["events"] = events[-MAX_EVENTS:]


async def _ensure_characters(session: AsyncSession,
                             seeds: list[dict[str, str]],
                             user_id: int | None) -> list[int]:
    """Create (or reuse by name) characters from a world's seeds."""
    ids: list[int] = []
    for s in seeds[:6]:
        name = s["name"].strip()
        row = (await session.execute(
            select(Character).where(Character.name == name).limit(1)
        )).scalars().first()
        if row is None:
            row = Character(
                name=name, emoji=s.get("emoji", ""), tagline=s.get("tagline", ""),
                description=s.get("description", ""),
                personality=s.get("personality", ""),
                style=s.get("speaking_style", ""),
                speaking_style=s.get("speaking_style", ""),
                background=s.get("background", ""),
                motivations=s.get("motivations", ""),
                knowledge_domain=s.get("knowledge_domain", ""),
                greeting=s.get("greeting", ""),
                category=s.get("category", "world"),
                creator_id=user_id)
            session.add(row)
            await session.flush()
        ids.append(row.id)
    return ids


async def enter_world(session: AsyncSession, wid: str,
                      user_id: int | None) -> RoleplayRoom | None:
    """Create a fresh room seeded from a catalogue world."""
    tpl = get_template(wid)
    if tpl is None:
        return None
    char_ids = await _ensure_characters(session, tpl["characters"], user_id)
    world = {
        "title": tpl["title"], "premise": tpl["premise"], "genre": tpl["genre"],
        "art": tpl["art"], "location": tpl["location"],
        "world_time": tpl["world_time"], "mode": tpl["mode"],
        "rules": list(tpl["rules"]), "events": [], "relationships": [],
        "template": tpl["id"]}
    log_event(world, f"You arrive — {tpl['location']}", kind="arrival")
    room = RoleplayRoom(
        name=tpl["title"], scenario=tpl["scenario"],
        character_ids=char_ids, model="auto",
        owner_id=user_id, messages=[], world=world)
    session.add(room)
    await session.flush()
    return room


# ------------------------------------------------------------------
# Small LLM helpers — every one falls back deterministically
# ------------------------------------------------------------------

class _BlueprintChar(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    emoji: str = "🎭"
    tagline: str = ""
    description: str = ""
    personality: str = ""
    speaking_style: str = ""
    motivations: str = ""
    background: str = ""


class _Blueprint(BaseModel):
    """AI-generated world blueprint (studio preview)."""
    title: str = Field(min_length=1, max_length=90)
    premise: str = ""
    location: str = ""
    world_time: str = ""
    scenario: str = ""
    rules: list[str] = Field(default_factory=list, max_length=6)
    characters: list[_BlueprintChar] = Field(default_factory=list, max_length=5)


async def _call_text(gateway: Any, model: str | None, system: str,
                     prompt: str, *, timeout: float = 45.0) -> str:
    """One bounded plain-text agent call — the same shape a character turn
    uses, which is the proven-working path on free providers (structured
    ``output_type`` calls fail on several of them).  Returns stripped text
    or ``""``; never raises — callers own the fallback."""
    import asyncio

    from app.agents.deps import AgentDeps
    from app.agents.factory import build_simple_agent
    from app.services.model_policy import stage_models

    for cand in stage_models(model or "auto:smart", role="reasoning"):
        try:
            deps = AgentDeps(gateway)
            agent = build_simple_agent(
                cand, system, deps, tools=None, temperature=0.7)
            res = await asyncio.wait_for(
                agent.run(prompt, deps=deps), timeout=timeout)
            out = res.output
            if isinstance(out, str) and out.strip():
                return out.strip()
        except Exception as exc:  # provider noise — try the next candidate
            log.debug("worlds call failed (model=%s): %s", cand, exc)
    return ""


def _json_from_text(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of model text (fenced or bare)."""
    import json as _json

    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text or "")
    blob = m.group(1) if m else None
    if blob is None:
        start, depth = (text or "").find("{"), 0
        blob = None
        if start >= 0:
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        blob = text[start:i + 1]
                        break
    if not blob:
        return None
    try:
        obj = _json.loads(blob)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _recent_beat_texts(room: RoleplayRoom, n: int = 10) -> list[str]:
    out: list[str] = []
    for m in (room.messages or [])[-n:]:
        who = m.get("speaker") or ("you" if m.get("role") == "user" else "")
        kind = m.get("kind") or "dialogue"
        text = re.sub(r"\s+", " ", str(m.get("text") or "")).strip()[:180]
        if text:
            out.append(f"[{kind}] {who}: {text}")
    return out


def _fallback_choices(room: RoleplayRoom,
                      cast: list[Character]) -> list[str]:
    """Deterministic actions when the model can't answer — never fake
    story facts; derived from the cast and the last message kind only."""
    last_kind = (room.messages or [{}])[-1].get("kind") if room.messages else ""
    picks = [c.name for c in cast[:3]]
    out: list[str] = []
    if picks:
        out.append(f"Ask {picks[0]} what they know")
    if len(picks) > 1:
        out.append(f"Turn to {picks[1]} for a second opinion")
    out.append("Look around carefully")
    if last_kind == "narration":
        out.append("Press on before the moment passes")
    else:
        out.append("Stay quiet and watch")
    return out[:4]


async def suggest_choices(gateway: Any, room: RoleplayRoom,
                          cast: list[Character]) -> list[str]:
    """3–5 contextual 'what do you do' options grounded in the transcript."""
    world = normalize_world(room)
    names = ", ".join(c.name for c in cast) or "the characters present"
    beats = "\n".join(_recent_beat_texts(room, 10)) or "(the scene has just opened)"
    system = (
        "You write next-action options for an interactive fiction game. "
        "Each choice is a short first-person action the player could take "
        "right now (5–10 words, imperative). Ground every option in the "
        "current situation — reference things actually present. Vary the "
        "verbs (ask, investigate, wait, reveal, leave). Never repeat the "
        "player's last action. 3–5 options.")
    prompt = (
        f"World: {world.get('title') or room.name}\n"
        f"Location: {world.get('location') or 'unspecified'}\n"
        f"Characters present: {names}\n"
        f"Scenario: {room.scenario[:400]}\n"
        f"Recent beats:\n{beats}\n\n"
        "What can the player do next? One option per line, plain text, "
        "no numbering, no bullets, no quotes.")
    text = await _call_text(gateway, room.model, system, prompt)
    if text:
        seen: set[str] = set()
        out: list[str] = []
        for line in text.splitlines():
            t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line)
            t = re.sub(r"\s+", " ", t).strip().strip('"').rstrip(".")
            if 4 < len(t) <= 90 and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        if out:
            return out[:5]
    return _fallback_choices(room, cast)


def _fallback_blueprint(concept: str, genre: str, mood: str) -> dict[str, Any]:
    return {
        "title": (concept or "New World")[:80],
        "premise": concept[:300],
        "location": "",
        "world_time": "",
        "scenario": (f"{concept.strip().rstrip('.')}. " if concept else "")
                    + "The scene opens where the story needs you most.",
        "rules": [],
        "characters": [
            {"name": "The Guide", "emoji": "🕯",
             "tagline": "someone who knows this place",
             "description": "A local who has been expecting you.",
             "personality": "observant, wry, economical with truth",
             "speaking_style": "short, grounded sentences",
             "motivations": "see what you'll do here",
             "background": ""}]}


async def generate_blueprint(gateway: Any, concept: str, genre: str,
                             mood: str, cast_size: int = 3) -> dict[str, Any]:
    """AI-assisted world creation — returns a previewable blueprint dict."""
    system = (
        "You design settings for interactive fiction. Given a concept, "
        "produce a coherent world: a short evocative title, a one-line "
        "premise, a concrete starting location, a diegetic time marker, a "
        "two-sentence opening scene, up to 4 world rules, and a small cast "
        "of distinct characters (name, emoji, tagline, description, "
        "personality, speaking_style, motivations, background). Characters "
        "must disagree-able — give them different goals and voices. Keep "
        "every field under 60 words.")
    prompt = (f"Concept: {concept}\nGenre: {genre or 'open'}\n"
              f"Atmosphere: {mood or 'atmospheric'}\n"
              f"Cast size: {max(1, min(5, cast_size))}\n\n"
              "Answer with ONE fenced ```json object, no prose, exactly "
              'this shape: {"title":"","premise":"","location":"",'
              '"world_time":"","scenario":"","rules":[""],'
              '"characters":[{"name":"","emoji":"","tagline":"",'
              '"description":"","personality":"","speaking_style":"",'
              '"motivations":"","background":""}]}')
    text = await _call_text(gateway, None, system, prompt, timeout=60.0)
    obj = _json_from_text(text)
    if obj:
        try:
            res = _Blueprint.model_validate(obj)
            if res.characters:
                return {
                    "title": res.title, "premise": res.premise,
                    "location": res.location, "world_time": res.world_time,
                    "scenario": res.scenario, "rules": res.rules[:6],
                    "characters": [c.model_dump() for c in res.characters[:5]]}
        except Exception as exc:
            log.debug("worlds blueprint parse failed: %s", exc)
    return _fallback_blueprint(concept, genre, mood)
