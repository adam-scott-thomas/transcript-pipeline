"""Temporal weaver: instance assignment + window filtering + sort order."""

from transcript_pipeline.adapters.cc_jsonl import SourceStream
from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import Agent
from transcript_pipeline.temporal_weaver import weave


def _stream(convo_id: str, agent: Agent, role: str, ts_list: list[float]) -> SourceStream:
    """Build a stream where every turn is the named agent (no human turns).
    Tests that need humans interleave a separate ADAM stream."""
    turns = [
        ParsedTurn(
            turn=i + 1,
            agent=agent,
            role=role,
            body=f"{convo_id} #{i+1}",
            timestamp=ts_list[i],
            conversation_id=convo_id,
        )
        for i in range(len(ts_list))
    ]
    return SourceStream(
        conversation_id=convo_id,
        turns=turns,
        started_at=ts_list[0] if ts_list else None,
        ended_at=ts_list[-1] if ts_list else None,
    )


def test_anchor_only_pass_through(core):
    a = _stream("anchor", Agent.CLAUDE_CODE, "IMPLEMENTATION", [100.0, 110.0, 120.0])
    res = weave(a, others=[])
    assert len(res.merged) == 3
    assert all(t.timestamp is not None for t in res.merged)
    assert [t.timestamp for t in res.merged] == [100.0, 110.0, 120.0]


def test_overlapping_other_stream_merges_in_timestamp_order(core):
    anchor = _stream("anchor", Agent.CLAUDE_CODE, "IMPLEMENTATION", [100.0, 200.0])
    other = _stream("gpt-1", Agent.GPT, "STRATEGY", [150.0])  # falls between anchor turns
    res = weave(anchor, [other])
    assert [t.timestamp for t in res.merged] == [100.0, 150.0, 200.0]
    # the GPT turn should be the middle one
    assert res.merged[1].agent is Agent.GPT


def test_out_of_window_other_is_excluded(core):
    anchor = _stream("anchor", Agent.CLAUDE_CODE, "IMPLEMENTATION", [100.0, 200.0])
    far_away = _stream("far", Agent.GPT, "STRATEGY", [1_000_000.0])  # way outside window
    res = weave(anchor, [far_away], window_seconds=60.0)
    # only anchor turns survived
    assert len(res.merged) == 2
    assert all(t.conversation_id == "anchor" for t in res.merged)


def test_instances_assigned_per_agent_class(core):
    """Two CLAUDE_CODE conversations and one GPT in window — instances 1,2 and 1."""
    anchor = _stream("anchor-cc", Agent.CLAUDE_CODE, "IMPLEMENTATION", [100.0, 105.0])
    other_cc = _stream("other-cc", Agent.CLAUDE_CODE, "IMPLEMENTATION", [102.0, 107.0])
    gpt = _stream("g-1", Agent.GPT, "STRATEGY", [104.0])
    res = weave(anchor, [other_cc, gpt])

    cc_turns = [t for t in res.merged if t.agent is Agent.CLAUDE_CODE]
    cc_ids = {t.conversation_id: t.instance for t in cc_turns}
    # both anchor and other_cc are present
    assert "anchor-cc" in cc_ids
    assert "other-cc" in cc_ids
    # they get instances 1 and 2 in some order, but each conversation's
    # instance is consistent
    assert {cc_ids["anchor-cc"], cc_ids["other-cc"]} == {1, 2}

    gpt_turns = [t for t in res.merged if t.agent is Agent.GPT]
    assert all(t.instance == 1 for t in gpt_turns)


def test_adam_always_instance_one(core):
    """Adam is one human regardless of how many AI windows are open."""
    # Build a CC stream that includes an ADAM turn explicitly
    anchor = SourceStream(
        conversation_id="a",
        turns=[
            ParsedTurn(turn=1, agent=Agent.ADAM, role="HUMAN", body="hi",
                       timestamp=100.0, conversation_id="a"),
            ParsedTurn(turn=2, agent=Agent.CLAUDE_CODE, role="IMPLEMENTATION",
                       body="ack", timestamp=110.0, conversation_id="a"),
        ],
        started_at=100.0,
        ended_at=110.0,
    )
    other = _stream("b", Agent.GPT, "STRATEGY", [105.0])
    res = weave(anchor, [other])
    adam_turns = [t for t in res.merged if t.agent is Agent.ADAM]
    assert adam_turns, "no adam turns produced — fixture bug"
    assert all(t.instance == 1 for t in adam_turns)


def test_turn_numbers_renumbered_sequentially(core):
    anchor = _stream("a", Agent.CLAUDE_CODE, "IMPLEMENTATION", [100.0, 200.0])
    other = _stream("b", Agent.GPT, "STRATEGY", [150.0])
    res = weave(anchor, [other])
    assert [t.turn for t in res.merged] == [1, 2, 3]
