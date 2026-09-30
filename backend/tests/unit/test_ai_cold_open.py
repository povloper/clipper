from types import SimpleNamespace

import pytest

from src import ai


TRANSCRIPT = "\n".join(
    [
        "[00:00 - 00:15] This is the setup for a complete standalone clip.",
        "[00:15 - 00:30] This is the payoff with a strong surprising conclusion.",
    ]
)
SEGMENT_TEXT = (
    "This is the setup for a complete standalone clip. "
    "This is the payoff with a strong surprising conclusion."
)


@pytest.mark.parametrize(
    ("cold_open", "expected"),
    [
        ({"start_time": "00:10", "end_time": "00:11"}, ("00:10", "00:11")),
        ({"start_time": "00:10", "end_time": "00:12"}, ("00:10", "00:12")),
        ({"start_time": "00:10", "end_time": "00:13"}, None),
        ({"start_time": "00:10", "end_time": "00:10"}, None),
        ({"start_time": "00:12", "end_time": "00:10"}, None),
        ({"start_time": "00:29", "end_time": "00:31"}, None),
        ({"start_time": "bad", "end_time": "00:12"}, None),
        (None, None),
    ],
)
@pytest.mark.asyncio
async def test_cold_open_validation_keeps_only_one_to_two_second_in_segment_ranges(
    monkeypatch, cold_open, expected
):
    analysis = ai.TranscriptAnalysis.model_validate(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:30",
                    "text": SEGMENT_TEXT,
                    "cold_open": cold_open,
                }
            ],
            "summary": "A short summary.",
            "key_topics": ["testing"],
        }
    )

    async def fake_run(*_args, **_kwargs):
        return SimpleNamespace(output=analysis)

    monkeypatch.setattr(ai, "get_transcript_agent", lambda: object())
    monkeypatch.setattr(ai, "_run_transcript_analysis", fake_run)

    result = await ai.get_most_relevant_parts_by_transcript(TRANSCRIPT)
    proposal = result.most_relevant_segments[0].cold_open

    if expected is None:
        assert proposal is None
    else:
        assert proposal is not None
        assert (proposal.start_time, proposal.end_time) == expected
