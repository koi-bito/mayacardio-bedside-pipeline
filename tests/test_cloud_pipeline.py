import asyncio
import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from cloud.app import app
from mock.biosignal_mock import MockServer
from pipeline.collector import CollectorManager
from pipeline.mode_controller import ModeController
from pipeline.storage import Storage
from pipeline.streamer import StreamerManager


def test_cloud_endpoints():
    client = TestClient(app)
    # Test health
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Test ingest
    payload = {
        "session_id": "test_sess_cloud",
        "sensor": 1,
        "first_index": 0,
        "data": [[10, 20, 30], [11, 21, 31]],
    }
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Test status
    resp = client.get("/status", params={"session_id": "test_sess_cloud"})
    assert resp.status_code == 200
    assert resp.json()["samples_in_cloud"] >= 2

    # Test export
    resp = client.get("/export", params={"session_id": "test_sess_cloud"})
    assert resp.status_code == 200
    lines = [l for l in resp.text.split("\n") if l.strip()]
    assert len(lines) >= 2
