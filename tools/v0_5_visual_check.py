# tools/v0_5_visual_check.py
# =============================================================================
# v0.5.1 — visual verification harness. Produces 4 synthetic woven cases that
# exercise shapes the 4-turn smoke test missed:
#
#   case 1 — conversation-heavy (12-turn production-cap, mostly prose)
#   case 2 — tool-call-heavy (8-turn, >50% tool/result segments)
#   case 3 — parallel-instance (3 concurrent CLAUDE-CODE conversations)
#   case 4 — multi-part split (13 turns forcing GL-001 + GL-002 split)
#
# For each case: write the woven .jsonl, render to HTML via skool_renderer,
# then take two screenshots (1920×1080 and 600px viewport — the latter
# emulates Skool's feed downscale). Final output is a REPORT.md with a
# checklist + observations.
#
# This is a HARNESS — zero changes to render logic. If the harness uncovers
# a bug we file v0.5.2.
# =============================================================================

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

# Ensure the repo is importable when run via `python tools/v0_5_visual_check.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcript_pipeline.skool_renderer import RenderRequest, render_woven_to_html_parts
from transcript_pipeline.woven_jsonl import WovenFile, WovenHeader, WovenTurn, write_woven


OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "v0_5_check"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Synthetic case builders — realistic-shaped, deterministic
# ---------------------------------------------------------------------------


def _t(turn, agent, role, body, *, stage="Build", chapter=1, chapter_outcome="",
       conversation_id="anchor", instance=1, model=None, requires_human=False,
       confidence=0.92, ts=None):
    return WovenTurn(
        turn=turn, agent=agent, role=role, body=body, timestamp=ts,
        model=model, conversation_id=conversation_id, instance=instance,
        stage=stage, outcome=chapter_outcome,
        confidence=confidence, requires_human=requires_human,
        chapter=chapter, chapter_outcome=chapter_outcome,
    )


def case_1_conversation_heavy() -> WovenFile:
    """12-turn production cap, varied stages, mostly prose, light tool calls."""
    h = WovenHeader(session_id="case1", anchor_id="anchor-1",
                    started_at=1000.0, ended_at=1900.0, n_turns=12)
    turns = [
        _t(1, "ADAM", "HUMAN",
           "Auth flow is broken on the desktop client — users see a login loop on token refresh. Need to ship a fix this week.",
           stage="Context", chapter=1, chapter_outcome="Goal stated", ts=1000),
        _t(2, "GPT", "STRATEGY",
           "Root issue is stale key reuse. Three clients each ship their own auth code path; there's no single source of truth.",
           stage="Problem", chapter=2, chapter_outcome="Root cause framed", model="gpt-5.2", ts=1100),
        _t(3, "CLAUDE", "REASONING",
           "I'd treat unification as a parsing problem, not a normalization problem. Each client gets a typed adapter; the unified type is a discriminated union, not a flattened row.",
           stage="Audit", chapter=3, chapter_outcome="Approach picked", model="opus-4.7", ts=1200),
        _t(4, "ADAM", "HUMAN",
           "Yeah, do it. One canonical key, all clients read/write through the same adapter.",
           stage="Decision", chapter=4, chapter_outcome="Adapter contract locked", ts=1300),
        _t(5, "CLAUDE-CODE", "IMPLEMENTATION",
           "Scaffolding the storage adapter now. Three primitives: read, write, clear.\n[tool: Write src/auth/adapter.ts]",
           stage="Build", chapter=5, chapter_outcome="Adapter scaffolded", model="opus-4.7", ts=1400),
        _t(6, "CLAUDE-CODE", "IMPLEMENTATION",
           "Adding the migration helper that copies legacy keys onto the canonical key on first read.\n[tool: Write src/auth/migrate.ts]",
           stage="Build", chapter=5, chapter_outcome="Adapter scaffolded", model="opus-4.7", ts=1450),
        _t(7, "CLAUDE-CODE", "IMPLEMENTATION",
           "Wired the redux middleware to dispatch on token refresh.\n[tool: Bash npm test -- auth.test.ts]\n[result: 24 passed, 1 failed (TZ offset on Linux runner)]",
           stage="Build", chapter=5, chapter_outcome="Adapter scaffolded", model="opus-4.7", ts=1500),
        _t(8, "CLAUDE-CODE", "IMPLEMENTATION",
           "Patched the timezone handling in the test fixture. Re-running.\n[tool: Bash npm test -- auth.test.ts]\n[result: 25 passed, 0 failed]",
           stage="Fix", chapter=6, chapter_outcome="TZ regression patched", model="opus-4.7", ts=1550),
        _t(9, "CODEX", "REVIEW",
           "Both shapes work. Pick `(ts, source_id, ingest_seq)` as the tiebreak — deterministic across replays in a way `hash` isn't. Otherwise GPT's record + your scaffold compose cleanly.",
           stage="Review", chapter=7, chapter_outcome="Approved with nit", model="gpt-5.2-codex", ts=1600),
        _t(10, "ADAM", "HUMAN",
           "Apply the nit, then ship.",
           stage="Review", chapter=7, chapter_outcome="Approved with nit", ts=1700),
        _t(11, "CLAUDE-CODE", "IMPLEMENTATION",
           "Renamed `_authKey` → `_authToken` everywhere, ran the full test suite, deploying to staging.\n[tool: Bash git commit -m 'auth: canonical adapter']\n[tool: Bash git push origin main]",
           stage="Ship", chapter=8, chapter_outcome="Deployed to staging", model="opus-4.7", ts=1800),
        _t(12, "ADAM", "HUMAN",
           "Next up: instrument the link-open events for analytics, deprecate the legacy env name in v3.1.",
           stage="Next", chapter=9, chapter_outcome="Follow-on queued", ts=1900),
    ]
    return WovenFile(header=h, turns=turns)


def case_2_tool_call_heavy() -> WovenFile:
    """8 turns, >50% segments are tool-call/code-output."""
    h = WovenHeader(session_id="case2", anchor_id="anchor-2",
                    started_at=2000.0, ended_at=2700.0, n_turns=8)
    big_log = (
        "Compiling release artifact for x86_64-pc-windows-msvc...\n"
        "Linking 124 modules...\n"
        "Generated lib.rlib (3.4 MB)\n"
        "  warning: function `unused_helper` is never used\n"
        "  warning: variable does not need to be mutable\n"
        "Build completed in 47.3s\n"
        "Test suite: 312 passed, 0 failed, 4 ignored\n"
        "Coverage: 87.4%"
    )
    turns = [
        _t(1, "ADAM", "HUMAN",
           "Ship the release build for the agent crate. Verify size, run tests, push tag.",
           stage="Context", chapter=1, chapter_outcome="Release goal", ts=2000),
        _t(2, "CLAUDE-CODE", "IMPLEMENTATION",
           "Building.\n[tool: Bash cargo build --release]\n[result: " + big_log + "]",
           stage="Build", chapter=2, chapter_outcome="Compiled", model="opus-4.7", ts=2100),
        _t(3, "CLAUDE-CODE", "IMPLEMENTATION",
           "Verifying binary size and running test suite.\n[tool: Bash ls -la target/release/agent.exe]\n[result: -rwxr-xr-x 1 adam 197121 8138240 May 4 11:30 agent.exe (8.1 MB)]\n[tool: Bash cargo test --release]\n[result: running 312 tests ... test result: ok. 312 passed; 0 failed; 4 ignored; 0 measured; 0 filtered out; finished in 12.45s]",
           stage="Review", chapter=3, chapter_outcome="Tests green", model="opus-4.7", ts=2200),
        _t(4, "CODEX", "REVIEW",
           "Binary size 8.1MB looks fine. Suggest stripping debug symbols for the user-facing build:\n[tool: Codex review Cargo.toml]\n[result: profile.release.strip = false → recommend strip = \"symbols\"]",
           stage="Review", chapter=3, chapter_outcome="Tests green", model="gpt-5.2-codex", ts=2300),
        _t(5, "CLAUDE-CODE", "IMPLEMENTATION",
           "Applying the strip recommendation and rebuilding.\n[tool: Edit Cargo.toml profile.release.strip=symbols]\n[tool: Bash cargo build --release]\n[result: Build completed in 51.1s, agent.exe 5.9 MB (was 8.1 MB)]",
           stage="Fix", chapter=4, chapter_outcome="Stripped symbols", model="opus-4.7", ts=2400),
        _t(6, "CLAUDE-CODE", "IMPLEMENTATION",
           "Cutting the tag and pushing.\n[tool: Bash git tag -a v1.4.0 -m 'release: 1.4.0']\n[tool: Bash git push origin v1.4.0]\n[result: To github.com/adam-scott-thomas/agent.git\n * [new tag] v1.4.0 -> v1.4.0]",
           stage="Ship", chapter=5, chapter_outcome="Tagged v1.4.0", model="opus-4.7", ts=2500),
        _t(7, "GPT", "STRATEGY",
           "Confirm the github release notes match the tag — auto-generated changelog often misses scope of refactors.",
           stage="Review", chapter=3, chapter_outcome="Tests green", model="gpt-5.2", ts=2600),
        _t(8, "ADAM", "HUMAN",
           "Done. On to the next one.",
           stage="Next", chapter=6, chapter_outcome="Closed loop", ts=2700),
    ]
    return WovenFile(header=h, turns=turns)


def case_3_parallel_instances() -> WovenFile:
    """12 turns, 3 concurrent CLAUDE-CODE conversations interleaved with ADAM
    + GPT. instance values 1, 2, 3 distributed."""
    h = WovenHeader(session_id="case3", anchor_id="anchor-3",
                    started_at=3000.0, ended_at=3900.0, n_turns=12)
    turns = [
        _t(1, "ADAM", "HUMAN",
           "Three things to land today: auth fix, dashboard perf, email rename.",
           stage="Context", chapter=1, chapter_outcome="Three-track plan", ts=3000),
        _t(2, "CLAUDE-CODE", "IMPLEMENTATION",
           "Auth track: starting on the storage adapter.\n[tool: Write src/auth/adapter.ts]",
           stage="Build", chapter=2, chapter_outcome="Auth track started",
           conversation_id="cc-auth", instance=1, model="opus-4.7", ts=3100),
        _t(3, "CLAUDE-CODE", "IMPLEMENTATION",
           "Dashboard track: implementing the SWR cache layer for shared metrics.\n[tool: Write src/dashboard/metrics.ts]",
           stage="Build", chapter=3, chapter_outcome="Dashboard track started",
           conversation_id="cc-dash", instance=2, model="opus-4.7", ts=3200),
        _t(4, "CLAUDE-CODE", "IMPLEMENTATION",
           "Email rename track: updating templates from APP_URL → APP_PUBLIC_URL.\n[tool: Bash grep -r APP_URL src/email/]",
           stage="Build", chapter=4, chapter_outcome="Email track started",
           conversation_id="cc-email", instance=3, model="opus-4.7", ts=3300),
        _t(5, "ADAM", "HUMAN",
           "Auth first — what's the status?",
           stage="Build", chapter=2, chapter_outcome="Auth track started", ts=3400),
        _t(6, "CLAUDE-CODE", "IMPLEMENTATION",
           "Auth: adapter scaffolded, tests green. Migration helper next.\n[tool: Bash npm test -- auth.test.ts]\n[result: 24 passed]",
           stage="Build", chapter=2, chapter_outcome="Auth track started",
           conversation_id="cc-auth", instance=1, model="opus-4.7", ts=3500),
        _t(7, "CLAUDE-CODE", "IMPLEMENTATION",
           "Dashboard: metrics provider wraps the fetcher; throttled to 30s globally.",
           stage="Build", chapter=3, chapter_outcome="Dashboard track started",
           conversation_id="cc-dash", instance=2, model="opus-4.7", ts=3550),
        _t(8, "CLAUDE-CODE", "IMPLEMENTATION",
           "Email: 6 templates updated, env var rename committed.",
           stage="Build", chapter=4, chapter_outcome="Email track started",
           conversation_id="cc-email", instance=3, model="opus-4.7", ts=3600),
        _t(9, "GPT", "STRATEGY",
           "Three parallel tracks is the right call — they're independent surfaces. Watch the merge order so the email rename doesn't land before staging soak.",
           stage="Review", chapter=5, chapter_outcome="Cross-track review",
           model="gpt-5.2", ts=3700),
        _t(10, "CLAUDE-CODE", "IMPLEMENTATION",
           "Auth: shipped to staging.\n[tool: Bash git push origin main]",
           stage="Ship", chapter=6, chapter_outcome="Auth shipped",
           conversation_id="cc-auth", instance=1, model="opus-4.7", ts=3750),
        _t(11, "CLAUDE-CODE", "IMPLEMENTATION",
           "Dashboard: shipped, observed 3s paint (was 5.2s).",
           stage="Ship", chapter=7, chapter_outcome="Dashboard shipped",
           conversation_id="cc-dash", instance=2, model="opus-4.7", ts=3800,
           confidence=0.62, requires_human=True),  # boundary — exercise low-conf
        _t(12, "CLAUDE-CODE", "IMPLEMENTATION",
           "Email: links live. Old key deprecation queued for v3.1.",
           stage="Ship", chapter=8, chapter_outcome="Email shipped",
           conversation_id="cc-email", instance=3, model="opus-4.7", ts=3850),
    ]
    return WovenFile(header=h, turns=turns)


def case_4_multi_part_split() -> WovenFile:
    """13 turns — production cap is 12, so split forces part-01 + part-02."""
    h = WovenHeader(session_id="case4", anchor_id="anchor-4",
                    started_at=4000.0, ended_at=5300.0, n_turns=13)
    turns = []
    stages = ["Context", "Problem", "Audit", "Decision", "Build", "Build",
              "Build", "Fix", "Review", "Review", "Ship", "Next", "Next"]
    chapters = [1, 2, 3, 4, 5, 5, 5, 6, 7, 7, 8, 9, 9]
    chapter_outcomes = {
        1: "Goal", 2: "Diagnosed", 3: "Traced", 4: "Locked approach",
        5: "Implemented", 6: "Patched TZ", 7: "Reviewed", 8: "Deployed",
        9: "Follow-on",
    }
    for i in range(13):
        agent = "ADAM" if i in (0, 3, 8, 11) else (
            "GPT" if i in (1, 9) else "CLAUDE-CODE"
        )
        role = "HUMAN" if agent == "ADAM" else (
            "STRATEGY" if agent == "GPT" else "IMPLEMENTATION"
        )
        model = None if agent == "ADAM" else (
            "gpt-5.2" if agent == "GPT" else "opus-4.7"
        )
        ts = 4000 + i * 100
        body = f"Turn {i+1}: {stages[i]} content for split test. " + (
            "This bubble has ample text so the layout fills out at 12 turns "
            "and the split point lands cleanly between chapters. "
        ) * 2
        turns.append(_t(
            i + 1, agent, role, body,
            stage=stages[i], chapter=chapters[i],
            chapter_outcome=chapter_outcomes[chapters[i]],
            model=model, ts=ts,
        ))
    return WovenFile(header=h, turns=turns)


# ---------------------------------------------------------------------------
# Render + screenshot
# ---------------------------------------------------------------------------


CASES = [
    ("case1_conversation_heavy", case_1_conversation_heavy,
     "GL-001", "Fixed", "Auth Key Flow"),
    ("case2_tool_call_heavy", case_2_tool_call_heavy,
     "GL-002", "Shipped", "Agent Release Build"),
    ("case3_parallel_instances", case_3_parallel_instances,
     "GL-003", "Building", "Three-Track Day"),
    ("case4_multi_part_split", case_4_multi_part_split,
     "GL-004", "Shipped", "Long Build Split Test"),
]


def main() -> None:
    print("=" * 60)
    print("v0.5.1 visual verification harness")
    print("=" * 60)
    rendered: list[tuple[str, list[Path]]] = []
    for slug, builder, code, status, outcome in CASES:
        woven = builder()
        jsonl_path = OUT_DIR / f"{slug}.woven.jsonl"
        write_woven(
            jsonl_path,
            session_id=woven.header.session_id,
            anchor_id=woven.header.anchor_id,
            started_at=woven.header.started_at,
            ended_at=woven.header.ended_at,
            turns=woven.turns,
        )
        request = RenderRequest(
            project_code=code, status=status, outcome=outcome, lane="production"
        )
        out_stem = OUT_DIR / slug
        paths = render_woven_to_html_parts(woven, request=request, out_stem=out_stem)
        rendered.append((slug, paths))
        print(f"  rendered {slug}: {len(paths)} part(s)")

    print(f"\nrendered {sum(len(p) for _, p in rendered)} HTML page(s) total")
    return rendered


if __name__ == "__main__":
    main()
