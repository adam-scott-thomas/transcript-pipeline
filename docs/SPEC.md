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

- All bubbles **black bg / white text** until per-agent colors are decided.
- CODEX always renders as **white card**, never a bubble.
- High contrast required.
- Colors locked once chosen — no drift.

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
- Max **12 messages per video**
- Overflow → split into Part 1 / Part 2 with sequential codes (e.g. GL-04, GL-05)
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
