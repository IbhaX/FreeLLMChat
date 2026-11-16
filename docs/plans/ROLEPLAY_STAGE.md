# Roleplay "The Stage" — UI/UX Rethink (Mega-plan)

## Vision

Replace the admin-style roleplay page with **The Stage** — a standalone,
cinematic scene runner where chatting with characters feels like watching
a play you direct. Three pillars:

1. **Immersion** — the scene IS the screen: backdrop, cast portraits,
   story-book transcript (dialogue vs narration styled differently).
2. **Aliveness** — streamed turns, typing presence, visible turn order,
   entrance/exit animations.
3. **Play** — the user is the director: narrator lines, OOC, whispers,
   regenerate ("swipe"), undo, pause/skip characters, scene beats.

## Current gaps (evidence)

- `prompt()` dialogs for room setup and character picking.
- `room_turn` is sequential + non-streaming: every character's reply
  lands at once after a long silent wait; no typing indicator.
- Messages have no IDs/kinds — no edit, undo, regenerate, or narration.
- Emoji-only avatars; no mood, no presence, no backdrop.
- Transcript capped at 60 turns to the model with no compaction —
  long scenes silently lose the plot.
- `*{name} remains quiet, considering.*` is the only failure voice.

## Design: The Stage (new `roleplay.html`/`roleplay.js`/`stage.css`)

### Layout — three acts

```
┌────────────────────────────────────────────────────┐
│ scene title · cast chips · ⚙ controls (pause/mute) │
├──────────┬──────────────────────────┬──────────────┤
│  CAST    │        STAGE             │   DIRECTOR   │
│ rail     │  backdrop + transcript   │   panel      │
│ portraits│  narrator / dialogue /   │ scene beats, │
│ + moods  │  ooc styles, streaming   │ scenario,    │
│          │  presence "…is writing"  │ memory,undo  │
├──────────┴──────────────────────────┴──────────────┤
│ 🎭 you-as | narrator pen | input | ▸ run/skip next │
└────────────────────────────────────────────────────┘
```

- **Cast rail (left):** large portrait per character (emoji → generated
  avatar gradient + initial fallback), mood tint ring, "writing…" pulse,
  mute/pause toggle, drag to reorder turn order.
- **Stage (center):** themed backdrop (per-scene CSS gradient/scene
  emoji banner), transcript as a storybook: narration italic-centered,
  character turns as portrait-left blocks with name + mood tag, user
  turns right-aligned, OOC `((…))` dimmed monospace.
- **Director panel (right, collapsible):** scenario editor, scene beats
  ("*the lights go out*" injected as narration), turn-order list
  (drag), per-character pause, regenerate/edit/undo on last turn,
  scene summary + pinned facts, clear/restart.
- **Composer (bottom):** mode switch — **Talk** (as yourself),
  **Narrate** (italic scene text), **OOC** (out-of-character aside that
  steers characters without entering canon), **Whisper** (target one
  character only). `Enter` runs the turn; `Shift+Enter` newline.
  Turn-queue indicator shows who's up next.
- **Mobile:** cast rail becomes a horizontal avatar strip; director
  panel becomes a bottom sheet. Tap character → whisper mode.

### Interactions that make it fun

- **Presence:** character portrait pulses + "…is writing" while their
  SSE turn streams word-by-word; skipped/muted characters sit dimmed.
- **Swipe/regenerate:** hover a character turn → ⟳ rerolls that reply
  (keeps both variants, arrows to flip — SillyTavern-style swipe).
- **Entrances:** joining a room animates the cast sliding onto stage;
  the character's `greeting` field auto-plays as an opener on empty
  scenes ("Begin scene" button).
- **Reactions:** quick emoji react on any turn (stored, shown as chip).
- **Scene moods:** director sets a mood (cozy/tense/mysterious…) that
  tints the backdrop AND injects a tone line into every persona prompt.
- **Impulse:** "Surprise me" button — narrator AI advances the scene
  with an unexpected beat.

### Cast picker & character creation (no prompt())

- Visual cast sheet: character cards with portrait, tagline, "add to
  cast" toggle — reorder by drag; 6 max (existing cap).
- Character creator becomes a side sheet with live persona-preview
  ("sample line" button renders a one-off reply preview).

## Backend changes (`app/services/roleplay.py`, `roleplay_routes.py`, models)

1. **Streaming turn endpoint** — `POST /v1/roleplay/rooms/{id}/turn`
   (SSE). Emits per-character events:
   `turn_queue{order:[ids]}` → per char `char_start{id}` → `delta{text}`
   → `char_end{id,text}` → `turn_done{messages}`.
   Sequential (preserves "react to what they said") but each reply
   streams token-by-token via `gateway.chat_stream`.
2. **Message identity** — every stored message gets `id` (uuid), `kind`
   (`dialogue|narration|ooc|whisper`), `variants[]` for swipes.
   Backfill: existing rows get `id`+`dialogue` on read.
3. **New endpoints:**
   - `POST /rooms/{id}/narrate` — insert narration beat (no char reply
     unless `advance=1`).
   - `POST /rooms/{id}/retry` — regenerate last (or message-id) char
     turn → new variant.
   - `PATCH /rooms/{id}/messages/{mid}` — edit user/narration text.
   - `DELETE /rooms/{id}/messages/{mid}` — undo/remove a turn.
   - `PATCH /rooms/{id}` — scenario, mood, paused cast, turn order.
   - `POST /rooms/{id}/summary` — compacts transcript into a scene
     summary (stored `room.summary`), auto-invoked when transcript
     approaches `MAX_TURNS`, injected into persona prompts.
4. **`room.messages` kinds respected in `transcript_messages`:** ooc
   lines become a steering system note (`[OOC from the player: …]`),
   narration stays neutral context, whispers visible only to their
   target's history.
5. **Persona prompt upgrades:** scene mood line, cast turn order,
   "respond to the narration/whisper" awareness, per-room `director
   notes` field injected as stage direction.
6. **Greeting autoplay** — on first turn of an empty room, characters
   open with their `greeting` (still via model for coherence).
7. Keep SQLite; `RoleplayRoom` gains `summary`, `mood`, `paused_ids`,
   `director_notes` columns (additive migration).

## NOT in scope

- No voice/TTS, no image-gen avatars, no multiplayer/human rooms,
  no NSFW toggle changes, no character marketplace — v2 candidates.
- No changes to the existing `/v1/characters` CRUD semantics beyond the
  new creator UI.

## Failure modes & guards

| Mode | Guard |
|---|---|
| Provider dead mid-turn | Per-char `chat_stream` wrapped; failed char emits `char_end{text:"*…goes quiet*"}` and the queue continues; `turn_done` reports partial. |
| SSE disconnect mid-turn | Turns are detached `chat_jobs` jobs (same registry as group/research) → `/live/stream` reattach replays. |
| Character emits OOC/breaks persona | Persona prompt already forbids; add post-filter stripping `As an AI…`/`[Name]:` self-prefixes before storing. |
| Degenerate/blob output | Reuse `_looks_like_tool_blob` guard + fallback from `agents.py`. |
| 400-turn cap corruption | Bounded: `room.messages` still trims; undo only touches stored ids. |

## Test plan

- `tests/test_roleplay_turn.py` (new): turn order & sequential
  visibility, speaker_filter/whisper, narration/ooc transcript mapping,
  empty-reply fallback, retry variant append, edit/undo invariants,
  summary injection past cap, SSE event sequence
  (`char_start`→`delta*`→`char_end`), detached-job reattach.
- Extend `test_agent_config_shapes.py` pattern for persona-prompt
  assembly (mood/director notes injection).
- Playwright smoke: room → stage loads, cast strip renders, stream
  shows presence, swipe flips variants, refresh reattaches.

## Phases

| # | Task | Files |
|---|------|-------|
| 1 | Message ids/kinds/variants + migration-free backfill; transcript mapping for narration/ooc/whisper | `roleplay.py`, `models.py` |
| 2 | Streaming turn endpoint + detached job + narrator/retry/edit/undo/patch endpoints | `roleplay_routes.py`, `chat_jobs` |
| 3 | Stage shell: cast rail, backdrop, storybook transcript, composer modes | `roleplay.html`, `stage.css`, `roleplay.js` rewrite |
| 4 | Presence + streaming renderer + turn queue + swipe UI | `roleplay.js` |
| 5 | Director panel: scenario/mood/beats/order/pause, summary display | `roleplay.js`, routes patch |
| 6 | Cast sheet picker + creator side-sheet + greeting autoplay | `roleplay.js` |
| 7 | Mobile layout + a11y (aria-live on stage, reduced-motion) | `stage.css` |
| 8 | Tests + smoke + wire nav entry "🎭 Stage" | tests, `chat.html` nav |

## Decision audit (auto-decided)

| # | Decision | Choice | Rejected |
|---|----------|--------|----------|
| 1 | Own page vs inside chat | Own page `/roleplay` — immersion needs full chrome | chat-mode toggle |
| 2 | Sequential vs parallel turns | Sequential + streamed (reactions need context) | parallel |
| 3 | Avatars | Emoji/gradient now, image-gen later | blocking on assets |
| 4 | Narrator | Separate message kind + optional AI narrator beat | extra "Narrator" character hack |
| 5 | Variants | `variants[]` on the message, not sibling rows | rewrite history |
