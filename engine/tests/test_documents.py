"""Document CRUD must not interpret identifiers or symlinks as file access."""

import pytest
from fastapi.testclient import TestClient

from compass import server
from compass.documents import (
    InvalidDocumentPath,
    create_document,
    delete_document,
    get_document,
    list_documents,
    save_document,
)
from compass.models.documents import StoredDocument


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_engine_token", "fictional-engine-session")
    return TestClient(server.app, headers={"Authorization": "Bearer fictional-engine-session"})


def test_document_lifecycle(tmp_path):
    doc = create_document(tmp_path, "Fictional brief", content_markdown="Original")
    doc.content_markdown = "Reviewed"
    save_document(tmp_path, doc)
    assert get_document(tmp_path, doc.id).content_markdown == "Reviewed"
    assert [d.id for d in list_documents(tmp_path)] == [doc.id]
    assert delete_document(tmp_path, doc.id)
    assert get_document(tmp_path, doc.id) is None


@pytest.mark.parametrize("doc_id", ["../outside", "../../outside", "/outside", r"..\outside", "", ".", "a" * 129])
def test_invalid_ids_cannot_read_write_or_delete(tmp_path, doc_id):
    sentinel = tmp_path / "outside.json"
    sentinel.write_text("preserved")
    for operation in (
        lambda: get_document(tmp_path, doc_id),
        lambda: delete_document(tmp_path, doc_id),
        lambda: save_document(tmp_path, StoredDocument(id=doc_id, title="Rejected")),
    ):
        with pytest.raises(InvalidDocumentPath):
            operation()
    assert sentinel.read_text() == "preserved"


def test_symlinked_document_cannot_read_write_or_delete(tmp_path):
    workspace = tmp_path / "workspace"
    doc = create_document(workspace, "Original")
    original = workspace / ".compass" / "documents" / f"{doc.id}.json"
    sentinel = tmp_path / "outside.json"
    original.replace(sentinel)
    original.symlink_to(sentinel)
    before = sentinel.read_bytes()

    for operation in (
        lambda: get_document(workspace, doc.id),
        lambda: delete_document(workspace, doc.id),
        lambda: save_document(workspace, doc),
    ):
        with pytest.raises(InvalidDocumentPath):
            operation()
    assert list_documents(workspace) == []
    assert sentinel.read_bytes() == before


def test_symlinked_storage_cannot_escape_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".compass").symlink_to(outside, target_is_directory=True)

    with pytest.raises(InvalidDocumentPath):
        create_document(workspace, "Rejected")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("endpoint", ["get", "delete", "save"])
def test_api_rejects_traversal_without_touching_sentinel(tmp_path, endpoint, client):
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text("preserved")
    body = {"workspace_path": str(tmp_path), "id": "../../sentinel"}
    if endpoint == "save":
        body["title"] = "Rejected"
    response = client.post(f"/documents/{endpoint}", json=body)
    assert response.status_code == 400
    assert sentinel.read_text() == "preserved"


def test_api_document_roundtrip_and_missing_id(tmp_path, client):
    created = client.post("/documents/save", json={"workspace_path": str(tmp_path), "title": "Fictional"})
    assert created.status_code == 200
    doc_id = created.json()["document"]["id"]
    request = {"workspace_path": str(tmp_path), "id": doc_id}
    assert client.post("/documents/get", json=request).status_code == 200
    assert client.post("/documents/delete", json=request).status_code == 200
    assert client.post("/documents/get", json=request).status_code == 404
