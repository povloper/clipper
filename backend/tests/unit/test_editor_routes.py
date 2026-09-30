import json
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import editor
from src.database import get_db
from src.editor_document import EditDocument, atomic_json, editor_dir


@pytest.fixture
async def client(tmp_path, monkeypatch):
    task = {"id": "task", "user_id": "owner", "status": "completed"}
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4"}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        config=SimpleNamespace(temp_dir=str(tmp_path)),
    )
    monkeypatch.setattr(editor, "TaskService", lambda db: service)
    monkeypatch.setattr(
        "src.api.routes.tasks._get_user_id_from_headers",
        AsyncMock(return_value="owner"),
    )
    monkeypatch.setattr(editor.JobQueue, "enqueue_job", AsyncMock(return_value="job"))

    @asynccontextmanager
    async def transaction(*args):
        yield

    monkeypatch.setattr(editor, "task_edit_transaction", transaction)
    db = SimpleNamespace(close=AsyncMock())
    app = FastAPI()
    app.include_router(editor.router)
    app.dependency_overrides[get_db] = lambda: db
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    doc = EditDocument.model_validate(
        {"segments": [{"id": "one", "start": 0, "end": 2}]}
    ).model_dump()
    atomic_json(directory / "draft.json", {"revision": 0, "document": doc})
    atomic_json(directory / "original.json", doc)
    atomic_json(directory / "asset.json", {"status": "ready", "duration": 2})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, service, doc, directory


async def test_drafts_require_task_and_clip_ownership(client, monkeypatch):
    http, service, doc, directory = client
    monkeypatch.setattr(
        "src.api.routes.tasks._get_user_id_from_headers",
        AsyncMock(return_value="outsider"),
    )
    assert (await http.get("/tasks/task/clips/clip/editor")).status_code == 403
    assert (
        await http.patch(
            "/tasks/task/clips/clip/editor",
            json={"basis": "clip.mp4", "revision": 0, "document": doc},
        )
    ).status_code == 403
    monkeypatch.setattr(
        "src.api.routes.tasks._get_user_id_from_headers",
        AsyncMock(return_value="owner"),
    )
    service.clip_repo.get_clip_by_id.return_value = {
        "id": "clip",
        "task_id": "other",
        "filename": "clip.mp4",
    }
    assert (await http.get("/tasks/task/clips/clip/editor")).status_code == 404


async def test_revision_and_basis_prevent_stale_writes(client):
    http, service, doc, directory = client
    payload = {"basis": "clip.mp4", "revision": 0, "document": doc}
    assert (await http.patch("/tasks/task/clips/clip/editor", json=payload)).json()[
        "revision"
    ] == 1
    assert (
        await http.patch("/tasks/task/clips/clip/editor", json=payload)
    ).status_code == 409
    payload["basis"] = "previous.mp4"
    assert (
        await http.patch("/tasks/task/clips/clip/editor", json=payload)
    ).status_code == 409
    payload.update(basis="clip.mp4", revision=1, document={**doc, "volume": -1})
    assert (
        await http.patch("/tasks/task/clips/clip/editor", json=payload)
    ).status_code == 400


async def test_export_snapshot_cancel_and_private_download(client):
    http, service, doc, directory = client
    response = await http.post(
        "/tasks/task/clips/clip/editor/exports",
        json={"basis": "clip.mp4", "document": doc, "preset": "reels"},
    )
    assert response.status_code == 202
    job = response.json()["id"]
    assert (directory / f"request-{job}.json").exists()
    assert (
        await http.get(f"/tasks/task/clips/clip/editor/exports/{job}/file")
    ).status_code == 409
    assert (
        await http.delete(f"/tasks/task/clips/clip/editor/exports/{job}")
    ).status_code == 200
    assert (directory / f"cancel-{job}").exists()
    assert (
        await http.get("/tasks/task/clips/clip/editor/media/draft.json")
    ).status_code == 404
    assert (
        await http.get("/tasks/task/clips/clip/editor/exports/not-a-job")
    ).status_code == 404


async def test_failed_enqueue_is_retryable(client, monkeypatch):
    http, service, doc, directory = client
    monkeypatch.setattr(
        editor.JobQueue, "enqueue_job", AsyncMock(side_effect=ConnectionError())
    )
    response = await http.post(
        "/tasks/task/clips/clip/editor/exports",
        json={"basis": "clip.mp4", "document": doc},
    )
    assert response.status_code == 503
    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    assert state["jobs"][0]["status"] == "failed"



async def test_combine_uses_cold_open_editor_source_ranges(client):
    http, service, _doc, _directory = client
    clips = [
        {
            "id": "clip-1",
            "task_id": "task",
            "filename": "clip-1.mp4",
            "cold_open_start": 12.0,
            "cold_open_end": 13.5,
        },
        {
            "id": "clip-2",
            "task_id": "task",
            "filename": "clip-2.mp4",
            "cold_open_start": None,
            "cold_open_end": None,
        },
    ]
    service.clip_repo.get_clip_by_id.side_effect = clips
    service._get_clip_source_ranges = lambda clip: (
        [(10.0, 15.0)] if clip["id"] == "clip-1" else [(20.0, 25.0)]
    )

    documents = [
        EditDocument.model_validate(
            {
                "segments": [
                    {"id": "cold-open", "start": 0, "end": 1.5},
                    {"id": "original", "start": 1.5, "end": 6.5},
                ]
            }
        ).model_dump(),
        EditDocument.model_validate(
            {"segments": [{"id": "original", "start": 0, "end": 5}]}
        ).model_dump(),
    ]

    for clip, document, duration in zip(clips, documents, (6.5, 5.0)):
        directory = editor_dir(
            service.config.temp_dir,
            "task",
            clip["id"],
            clip["filename"],
        )
        atomic_json(directory / "draft.json", {"revision": 0, "document": document})
        atomic_json(directory / "original.json", document)
        atomic_json(
            directory / "asset.json",
            {
                "status": "ready",
                "duration": duration,
                "width": 1080,
                "height": 1920,
                "hasAudio": True,
            },
        )

    response = await http.post(
        "/tasks/task/editor/combine",
        json={"clip_ids": ["clip-1", "clip-2"]},
    )

    assert response.status_code == 202
    anchor = editor_dir(
        service.config.temp_dir,
        "task",
        "clip-1",
        "clip-1.mp4",
    )
    requests = list(anchor.glob("request-*.json"))
    assert len(requests) == 1
    payload = json.loads(requests[0].read_text())
    assert payload["inputs"][0]["ranges"] == [
        [12.0, 13.5],
        [10.0, 15.0],
    ]
    assert payload["inputs"][1]["ranges"] == [[20.0, 25.0]]
