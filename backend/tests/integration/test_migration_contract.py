import pytest
from sqlalchemy import text

from src.database import init_db


@pytest.mark.asyncio
async def test_runtime_migrations_are_idempotent_and_critical_schema_exists(
    db_session, initialized_database
):
    before = {
        row.version
        for row in (
            await db_session.execute(
                text("SELECT version FROM schema_migrations")
            )
        ).all()
    }

    await db_session.rollback()
    await init_db()
    await init_db()

    after = {
        row.version
        for row in (
            await db_session.execute(
                text("SELECT version FROM schema_migrations")
            )
        ).all()
    }
    assert after == before

    required_columns = {
        "generated_clips": {
            "hook_title",
            "cold_open_start",
            "cold_open_end",
        },
        "tasks": {
            "share_token",
            "share_enabled",
        },
        "api_keys": {
            "key_hash",
            "revoked_at",
        },
        "app_settings": {
            "setting_key",
            "encrypted_value",
            "prefer_admin_value",
        },
    }

    rows = (
        await db_session.execute(
            text(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(CAST(:tables AS text[]))
                """
            ),
            {"tables": list(required_columns)},
        )
    ).all()
    actual: dict[str, set[str]] = {}
    for row in rows:
        actual.setdefault(row.table_name, set()).add(row.column_name)

    for table, columns in required_columns.items():
        assert columns <= actual.get(table, set()), (
            table,
            columns - actual.get(table, set()),
        )
