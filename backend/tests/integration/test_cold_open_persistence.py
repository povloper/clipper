import pytest
from sqlalchemy import text

from src.repositories.clip_repository import ClipRepository
from tests.fixtures.factories import create_source, create_task, create_user


@pytest.mark.asyncio
async def test_cold_open_migration_and_repository_round_trip(db_session):
    owner = await create_user(db_session)
    source = await create_source(
        db_session,
        source_type="video_url",
        url="upload://cold-open.mp4",
    )
    task = await create_task(
        db_session,
        user_id=owner["id"],
        source_id=source["id"],
    )

    # Fails immediately if the runtime migration was not applied.
    await db_session.execute(
        text(
            "SELECT cold_open_start, cold_open_end "
            "FROM generated_clips LIMIT 0"
        )
    )

    clip_id = await ClipRepository.create_clip(
        db_session,
        task["id"],
        "cold-open.mp4",
        "/tmp/cold-open.mp4",
        "00:10",
        "00:40",
        30.0,
        "A clip with a proposed cold open",
        0.95,
        "Strong opening candidate",
        1,
        cold_open_start=12.25,
        cold_open_end=13.75,
    )

    stored = await ClipRepository.get_clip_by_id(db_session, clip_id)
    assert stored is not None
    assert stored["cold_open_start"] == pytest.approx(12.25)
    assert stored["cold_open_end"] == pytest.approx(13.75)

    row = (
        await db_session.execute(
            text(
                "SELECT cold_open_start, cold_open_end "
                "FROM generated_clips WHERE id = :id"
            ),
            {"id": clip_id},
        )
    ).one()
    assert row.cold_open_start == pytest.approx(12.25)
    assert row.cold_open_end == pytest.approx(13.75)
