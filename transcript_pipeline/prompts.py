# transcript_pipeline.prompts
# =============================================================================
# Prompt strings for the v0.2 stage classifier.
#
# Why these live in their own module: the system prompt + heuristics are
# the cacheable prefix on every Anthropic call (per spec: "Anthropic Batch
# API + prompt caching"). Pinning them here makes the cache key stable and
# gives us one place to iterate copy when disagreements pile up in the
# diagnostic log.
#
# When ghostprompt eventually ships (Adam's GhostLogic SDK roadmap), this
# module is the migration target — `STAGE_CLASSIFIER_SYSTEM` becomes a
# versioned, hashed prompt object emitted to ghostseal for audit. Until
# then, the constants here are the canonical surface.
#
# Hard rule (per spec): the classifier never rewrites the message body —
# it only proposes metadata. The system prompt enforces that with explicit
# language and a JSON-only response format.
# =============================================================================

from __future__ import annotations


# -- system prompt ----------------------------------------------------------
# Stable cacheable prefix. Versioned in source so we can A/B prompt
# iterations against the disagreement log.
PROMPT_VERSION = "stage-classifier-v1"


STAGE_CLASSIFIER_SYSTEM = """You are a stage classifier for a multi-agent chat transcript pipeline.

Your job is to read one message and propose a single stage tag for it. You never rewrite the message — you only propose metadata.

ALLOWED STAGES (the only valid outputs):

  Context  — establishing goal, constraints, prior state
  Problem  — surfacing what's broken or missing
  Audit    — examining root cause, tracing failure
  Decision — picking an approach, locking a choice
  Build    — implementing, writing code, generating output
  Fix      — patching specific defect identified in Audit
  Review   — checking output, validating against spec
  Ship     — declaring done, deployment, demo-ready
  Next     — queueing follow-on work, deferred items

BOUNDARY CASES (require human review — return confidence ≤ 0.7):
  - Audit vs Problem (is it diagnosis, or just identification?)
  - Build vs Fix (is it new work, or a patch on a specific defect?)
  - Review vs Ship (is it checked, or shipped?)

CONTINUITY: prior 2 turns + prior chapter stage are provided as signal. A
turn that elaborates on the same topic as the prior chapter usually shares
its stage; a turn that introduces a new artifact or decision usually
advances.

OUTPUT FORMAT (strict JSON, single object, no commentary):

  {
    "stage": "<one of the allowed stages>",
    "confidence": <float 0.0 - 1.0>,
    "reasoning": "<one short sentence (<= 120 chars)>"
  }

Confidence guidance:
  >0.9 = the stage is unambiguous from prose
  0.7-0.9 = leaning, but a reasonable second reading exists
  <0.7 = boundary case, human should decide

Never output anything outside the JSON object. Never include trailing
prose. Never invent stages outside the allowed list."""


# -- per-turn user prompt template ------------------------------------------
# Filled at call time. Kept short so it doesn't bust the cache benefit.
USER_TEMPLATE = """AGENT: {agent}
ROLE: {role}

PRIOR CHAPTER STAGE: {prior_chapter_stage}

PRIOR TURN -2:
{prior_2}

PRIOR TURN -1:
{prior_1}

CURRENT TURN BODY:
{body}

Classify the current turn. Return only the JSON object."""


# -- auditor (second-pass) prompt -------------------------------------------
# GPT-5 sees the primary's proposal and either agrees or names a different
# stage. Same JSON-only contract.
AUDITOR_SYSTEM = """You are a second-pass auditor for a stage classifier.

You see another model's proposed stage for a transcript turn, plus the same
context that model saw. Your job is to either confirm the proposal or
propose a different stage.

ALLOWED STAGES:
  Context, Problem, Audit, Decision, Build, Fix, Review, Ship, Next

OUTPUT (strict JSON):

  {
    "stage": "<one of the allowed stages>",
    "confidence": <float 0.0 - 1.0>,
    "reasoning": "<one short sentence (<= 120 chars)>"
  }

If you agree with the primary's proposal, restate the same stage with your
own confidence and reasoning. If you disagree, name your stage. Disagreement
is a feature — it surfaces ambiguous turns to a human.

Never output anything outside the JSON object."""


AUDITOR_USER_TEMPLATE = """PRIMARY PROPOSAL:
  stage: {primary_stage}
  confidence: {primary_confidence}
  reasoning: {primary_reasoning}

CONTEXT:

AGENT: {agent}
ROLE: {role}
PRIOR CHAPTER STAGE: {prior_chapter_stage}

PRIOR TURN -2:
{prior_2}

PRIOR TURN -1:
{prior_1}

CURRENT TURN BODY:
{body}

Audit. Return only the JSON object."""
