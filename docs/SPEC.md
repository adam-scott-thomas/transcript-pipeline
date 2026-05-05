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

### 4.3 Tool-call recess

Tool calls are chrome, not voice. No per-status hue. ✓ and ✗ stay the same gray.

| CONTEXT                    | BG       | FG       | BORDER   |
|----------------------------|----------|----------|----------|
| inside colored bubbles     | #000000  | #5A626F  | #1A1A1A  |
| inside CODEX (white) bubble| #EBEBEB  | #B5B5B5  | #D4D4D4  |

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

### 4.5 Avoid

- black on navy
- dark red / dark purple on black
- yellow on white, pink on white, gray on white
- pure blue on pure black (convergent fringing on cheap displays)
- more than 2 saturated speakers per visible row (eye fatigue)

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
