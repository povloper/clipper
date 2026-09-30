import hashlib
import hmac
import time

import pytest
from sqlalchemy import text

from src.services.api_key_service import ApiKeyService
from tests.fixtures.factories import create_clip, create_source, create_task, create_user


def signed_headers(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    signature = hmac.new(
        b"test-backend-auth-secret",
        payload,
        hashlib.sha256,
    ).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("get", "", None),
        ("get", "/clips", None),
        ("post", "/share", None),
        ("delete", "/share", None),
        ("patch", "", {"title": "Should not be allowed"}),
        ("delete", "", None),
        ("get", "/clips/{clip_id}/file", None),
        ("patch", "/clips/{clip_id}", {"start_offset": 0, "end_offset": 0}),
        ("post", "/clips/{clip_id}/split", {"split_time": 1}),
        (
            "patch",
            "/clips/{clip_id}/captions",
            {
                "caption_text": "Blocked",
                "position": "bottom",
                "highlight_words": [],
            },
        ),
        ("get", "/clips/{clip_id}/export", None),
    ],
)
async def test_task_and_clip_routes_reject_cross_user_access(
    client, db_session, tmp_path, method, suffix, payload
):
    attacker = await create_user(
        db_session,
        user_id="security-attacker",
        email="security-attacker@example.com",
    )
    owner = await create_user(
        db_session,
        user_id="security-owner",
        email="security-owner@example.com",
    )
    source = await create_source(db_session, title="Private source")
    task = await create_task(
        db_session,
        user_id=owner["id"],
        source_id=source["id"],
        status="completed",
    )
    clip = await create_clip(db_session, task_id=task["id"])
    clip_path = tmp_path / "private.mp4"
    clip_path.write_bytes(b"private")
    await db_session.execute(
        text(
            "UPDATE generated_clips SET file_path = :path WHERE id = :clip_id"
        ),
        {"path": str(clip_path), "clip_id": clip["id"]},
    )
    await db_session.commit()

    path = f"/tasks/{task['id']}" + suffix.format(clip_id=clip["id"])
    kwargs = {"headers": signed_headers(attacker["id"])}
    if payload is not None:
        kwargs["json"] = payload

    response = await getattr(client, method)(path, **kwargs)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_share_token_cannot_read_a_clip_from_another_task(
    client, db_session, tmp_path
):
    owner = await create_user(
        db_session,
        user_id="share-owner",
        email="share-owner@example.com",
    )
    source = await create_source(db_session, title="Shared source")
    shared_task = await create_task(
        db_session,
        user_id=owner["id"],
        source_id=source["id"],
        status="completed",
    )
    other_task = await create_task(
        db_session,
        user_id=owner["id"],
        source_id=source["id"],
        status="completed",
    )
    await create_clip(db_session, task_id=shared_task["id"])
    other_clip = await create_clip(db_session, task_id=other_task["id"])
    other_path = tmp_path / "other-task.mp4"
    other_path.write_bytes(b"must-not-leak")
    await db_session.execute(
        text(
            "UPDATE generated_clips SET file_path = :path WHERE id = :clip_id"
        ),
        {"path": str(other_path), "clip_id": other_clip["id"]},
    )
    await db_session.commit()

    share = await client.post(
        f"/tasks/{shared_task['id']}/share",
        headers=signed_headers(owner["id"]),
    )
    assert share.status_code == 200
    token = share.json()["share_token"]

    response = await client.get(
        f"/tasks/shared/{token}/clips/{other_clip['id']}/file"
    )

    assert response.status_code == 404
    assert response.content != b"must-not-leak"


@pytest.mark.asyncio
async def test_api_key_routes_are_isolated_and_never_list_secrets(
    client, db_session
):
    first = await create_user(
        db_session,
        user_id="api-owner-one",
        email="api-owner-one@example.com",
    )
    second = await create_user(
        db_session,
        user_id="api-owner-two",
        email="api-owner-two@example.com",
    )
    service = ApiKeyService(db_session)
    first_key = await service.create_api_key(first["id"], "First key")
    second_key = await service.create_api_key(second["id"], "Second key")

    listing = await client.get(
        "/api-keys/",
        headers=signed_headers(first["id"]),
    )
    assert listing.status_code == 200
    rows = listing.json()["api_keys"]
    assert [row["id"] for row in rows] == [first_key["id"]]
    assert all("key" not in row and "key_hash" not in row for row in rows)
    assert first_key["key"] not in listing.text
    assert second_key["key"] not in listing.text

    revoke_other = await client.delete(
        f"/api-keys/{second_key['id']}",
        headers=signed_headers(first["id"]),
    )
    assert revoke_other.status_code == 404
    second_listing = await service.list_api_keys(second["id"])
    assert second_listing[0]["id"] == second_key["id"]
    assert second_listing[0]["revoked"] is False


@pytest.mark.asyncio
async def test_admin_runtime_settings_never_expose_encrypted_or_plaintext_values(
    client, db_session
):
    admin = await create_user(
        db_session,
        user_id="runtime-admin",
        email="runtime-admin@example.com",
        is_admin=True,
    )
    await db_session.execute(
        text(
            """
            INSERT INTO app_settings (
                setting_key, encrypted_value, updated_by, prefer_admin_value
            )
            VALUES (
                'OPENAI_API_KEY', 'v1:opaque-ciphertext', :admin_id, true
            )
            ON CONFLICT (setting_key) DO UPDATE
            SET encrypted_value = EXCLUDED.encrypted_value,
                updated_by = EXCLUDED.updated_by,
                prefer_admin_value = EXCLUDED.prefer_admin_value
            """
        ),
        {"admin_id": admin["id"]},
    )
    await db_session.commit()

    response = await client.get(
        "/admin/runtime-settings",
        headers=signed_headers(admin["id"]),
    )
    assert response.status_code == 200
    payload = response.json()
    setting = next(
        row for row in payload["settings"] if row["key"] == "OPENAI_API_KEY"
    )
    assert setting["configured"] is True
    assert setting["has_admin_value"] is True
    assert "encrypted_value" not in setting
    assert "opaque-ciphertext" not in response.text

    invalid = await client.patch(
        "/admin/runtime-settings",
        headers=signed_headers(admin["id"]),
        json={"updates": {"NOT_A_REAL_SETTING": "secret"}},
    )
    assert invalid.status_code == 400
