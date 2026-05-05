# Transcript Format v1.0

The canonical spec this pipeline implements. If a rule conflicts between code and this
doc, code wins; open a PR to update the doc.

## 1. Title

`PROJECT-CODE — STATUS — OUTCOME`

- Project codes: GL, MS, POAW, etc.
- Status (closed set): Shipped | Building | Incomplete | Blocked | Fixed | Audit | Reset | Field Notes
- Outcome: specific, concrete, **6 words max**
- One line, no jokes, no filler

Example: `GL-004 — Fixed — Auth Key Flow`

## 2. Chapters

`[CHAPTER ##] Stage — Outcome`

- 3–8 chapters per video
- Sequential numbering only
- Allowed stages: Context | Problem | Audit | Decision | Build | Fix | Review | Ship | Next
- No emotional/narrative language, no renaming stages mid-series

## 3. Speakers

```
[AGENT | ROLE]
```

Canonical agents:

| Agent | Role |
|---|---|
| ADAM | Human / Operator |
| GPT | Strategy / Architecture |
| CLAUDE | Reasoning / Analysis |
| CLAUDE-CODE | Implementation |
| CLAUDE-BROWSER | Research / Retrieval |
| CODEX | Code Generation / Repo Work |
| SYSTEM | Meta / Session Events |

- Every message has a speaker tag.
- One tag per message — no mode switching mid-message.
- No new agents without a system update.
- CODEX = code/repo only, not general reasoning.
- SYSTEM = session-level events only.

## 4. Visual

- **Every agent is a bubble.** One shape, no exceptions. The only thing that
  changes between agents is color.
- **CODEX inverts color** — white background, black text, graphite border. Same
  bubble shape as every other agent.
- **No avatars.** Bubble color + speaker label identifies the speaker.
- **No side rails.** Chapter rail and metadata strip are removed; chapter
  changes render inline as a thin centered divider between bubbles.
- High contrast required.
- Colors locked — no drift.

### 4.1 Color Code (live)

Each agent gets a fixed `bg / fg / border / glow` quartet. The renderer reads
this table at render time — do not duplicate values in code.

| AGENT          | BG       | FG       | BORDER   | GLOW                          |
|----------------|----------|----------|----------|-------------------------------|
| ADAM           | #007AFF  | #FFFFFF  | #1E8BFF  | rgba(0,122,255,0.35)          |
| GPT            | #0E8A8A  | #EFFBFB  | #14B8B8  | rgba(20,184,184,0.30)         |
| CLAUDE         | #C0392B  | #FFFFFF  | #E35345  | rgba(192,57,43,0.32)          |
| CLAUDE-CODE    | #D9651F  | #FFFFFF  | #F08A3C  | rgba(240,138,60,0.32)         |
| CLAUDE-BROWSER | #F5C518  | #1A1300  | #FFD84A  | rgba(245,197,24,0.30)         |
| CODEX          | #FFFFFF  | #0B0D11  | #2A3140  | rgba(11,13,17,0.18)           |
| GROK           | #6D28D9  | #FFFFFF  | #8B5CF6  | rgba(139,92,246,0.32)         |
| GEMINI         | #DB2777  | #FFFFFF  | #F472B6  | rgba(244,114,182,0.30)        |
| SYSTEM         | #2B313C  | #D6DDE6  | #3A4250  | rgba(120,130,150,0.18)        |

SYSTEM border style: `dashed`. All others: `solid`.

### 4.2 Surface chrome

| TOKEN          | VALUE    |
|----------------|----------|
| page.bg        | #0B0D11  |
| container.bg   | #11141A  |
| rule           | #1F2530  |
| ink.primary    | #E6EDF3  |
| ink.dim        | #98A2B3  |
| ink.muted      | #6B7280  |

### 4.3 Tool-call terminal chrome

Tool calls are interjections, not voice. They render with terminal chrome on
top of the recess palette below. Per-status hue is forbidden — ✓ and ✗ stay
the same recess gray.

**Recess palette** (background of the cell):

| CONTEXT                    | BG       | FG       | BORDER   |
|----------------------------|----------|----------|----------|
| inside colored bubbles     | #000000  | #5A626F  | #1A1A1A  |
| inside CODEX (white) bubble| #EBEBEB  | #B5B5B5  | #D4D4D4  |

**Chrome rules** (applied to each tool-call cell):

- Prompt character `$ ` (recess.fg dim) prefixes the command line.
- Tool-type badge (`Bash`, `Write`, `Edit`, …) in tiny uppercase, top-right
  corner of the cell, color `#3A4250` (dimmer than recess.fg).
- Command text in `#E6EDF3` weight 500 — bright, the user's voice.
- Output text in recess.fg weight 400, `white-space: pre-wrap`.
- Cell margin: 10px top + bottom.
- Multi-line commands hang-indent so wrapped lines align under the `$`.
- **No traffic-light dot.** **No inset shadow.** Color split + prompt char
  carry the chrome.

Inside CODEX (white) bubbles the recess palette inverts (light gray on
near-white) — the same chrome rules apply with the inverted colors.

### 4.4 Instance outlines

When a woven view contains multiple parallel conversations of the same agent
class, distinguish them visually:

| INSTANCE | OUTLINE                              |
|----------|--------------------------------------|
| 1        | none                                 |
| 2        | 1px white                            |
| 3        | 1px white / 4px gap / 1px white      |
| 4+       | 1px / 4px gap / 1px / 4px gap / 1px  |

ADAM is always instance 1 (one human, one chair).

### 4.5 Inline chapter divider

Chapter changes render INLINE between bubbles, not in a side rail.

- 1px rule, color `#1F2530` (surface.rule)
- Centered marker text: `[CHAPTER NN] Stage — Outcome`
- Text size 14px, color `#98A2B3` (surface.ink.dim)
- Margin 20px top, 10px bottom
- The first chapter does NOT emit a divider (the title bar already frames it)

### 4.6 Layout

16:9, 1920×1080.

| ZONE       | DIM            | NOTES                                       |
|------------|----------------|---------------------------------------------|
| title bar  | full × 56px    | slim top strip — code/status/outcome/parttag |
| main col   | full × rest    | 80px padding either side, ~1760px usable    |
| bubble max | 1400px         | bubble doesn't span the full main column    |
| margin V   | 10px each      | top/bottom on each bubble                   |
| padding    | 14px / 18px    | bubble internal vertical / horizontal       |

ADAM right-aligned. All other agents left-aligned.

### 4.7 Dwell timing (fast pace, default)

Per-segment `data-dwell-ms` emitted by the renderer for v0.6 capture.

| STAGE                                  | PROSE  | TOOL-CALL    | CODE-OUTPUT   |
|----------------------------------------|--------|--------------|---------------|
| Decision, Ship                         | 1500   | 750          | 525           |
| Audit, Review                          | 1000   | 500          | 400 (floor)   |
| Context, Problem, Build, Fix, Next     |  700   | 400 (floor)  | 400 (floor)   |

`requires_human=True` adds **+400ms** to its prose segment.
Tool-call dwell = max(400, prose × 50%). Code-output dwell = max(400, prose × 35%).
Absolute floor for any segment: **400ms**.

### 4.8 Avoid

- black on navy
- dark red / dark purple on black
- yellow on white, pink on white, gray on white
- pure blue on pure black (convergent fringing on cheap displays)
- more than 2 saturated speakers per visible row (eye fatigue)

## 4.9 Copy-paste carry annotation

When ADAM pastes a prior AI bubble's content into another agent's chat, the
weaver detects the carry and the renderer suppresses the duplicate.

**Detection** (post-weave, before JSONL write):

- For each ADAM turn in chronological order, embed its body and compute
  cosine similarity vs the previous 10 AI turns' content vectors (sliding
  window).
- If `max_similarity ≥ carry_threshold` (default 0.85, CLI flag
  `--carry-threshold`):
  - Mark this ADAM turn `is_carry=true`, record `carry_source=<turn>` and
    `carry_similarity=<float>`.
  - Append target agent to source bubble's `carried_to` list.
- 0.30 < similarity < 0.85 = partial carry, render normally with no
  annotation. Future revisit candidate.

**Schema** (turn additions in `.woven.jsonl`):

| FIELD            | TYPE        | DEFAULT |
|------------------|-------------|---------|
| `is_carry`       | bool        | false   |
| `carry_source`   | int or null | null    |
| `carry_similarity` | float or null | null  |
| `carried_to`     | list[str]   | []      |

**Render rules:**

- ADAM turns with `is_carry=true` are SKIPPED — not rendered at all.
- Source bubbles with non-empty `carried_to` show a thumbs-up indicator at
  bottom-right: `👍 → CL` for CLAUDE, `→ CC` for CLAUDE-CODE, `→ GP` for
  GPT, `→ CX` for CODEX, `→ CB` for CLAUDE-BROWSER, `→ GR` for GROK,
  `→ GE` for GEMINI.
- Multiple carries from the same source stack horizontally:
  `👍 → CL  👍 → GP`.

## 5. Status tags (optional)

`[STATUS]` appended to message or chapter.

Allowed: `SHIPPED | BUILDING | INCOMPLETE | BLOCKED | FIXED`. Use sparingly, never
contradicting title status.

## 6. Structure

```
TITLE

[CHAPTER 01]
messages...

[CHAPTER 02]
messages...
```

- Chronological order only
- Turn caps per **lane**:

| LANE       | CAP   | NOTES                                    |
|------------|-------|------------------------------------------|
| production | 12    | hand-edited fresh content, video pacing  |
| archive    | 1000  | woven historical chats, read-friendly    |
| uncapped   | None  | research / debug, no enforcement         |

- Overflow (when over the lane's cap) → split into Part 1 / Part 2 with sequential
  zero-padded codes (e.g. `GL-004`, `GL-005`). Each part renders to its own HTML
  file: `<stem>-part-01.html`, `<stem>-part-02.html`.
- Chapter count 3–8 is a **warning band**, not a hard cap. Renderer logs and
  proceeds.
- No restructuring after the fact

## 7. Cross-video references

Inline only, no special tag. Format: `PROJECT-NUMBER` (e.g., `GL-002`, `POAW-007`).

## 8. Resumed sessions

- Resumed = new file
- New title, new chapter numbering from 01
- No "Session resumed" SYSTEM messages

## 9. Consistency

Same project → same format. Same agent → same role. Same agent → same color (when set).
Same stages → same naming. **No drift. No improvisation.**
