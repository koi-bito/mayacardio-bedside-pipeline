import os
import tempfile
import pytest
from pipeline.storage import Storage


@pytest.fixture
def temp_storage():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_path = f.name
    storage = Storage(temp_path)
    yield storage
    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_insert_samples_idempotency(temp_storage):
    session_id = "test_sess"
    data = [[10, 20, 30], [11, 21, 31]]

    inserted_first = temp_storage.insert_samples_batch(session_id, 1, 0, data)
    assert inserted_first == 2
    assert temp_storage.get_total_samples_saved(session_id) == 2

    # Insert same samples again -> should be ignored without error
    inserted_second = temp_storage.insert_samples_batch(session_id, 1, 0, data)
    assert inserted_second == 0
    assert temp_storage.get_total_samples_saved(session_id) == 2


def test_insert_loss_with_null_to_index(temp_storage):
    session_id = "sess_restart"
    temp_storage.insert_loss(session_id, 1, 5000, None, "session_ended_unread")

    losses = list(temp_storage.get_all_losses_for_export(session_id))
    assert len(losses) == 1
    assert losses[0]["sensor"] == 1
    assert losses[0]["from_index"] == 5000
    assert losses[0]["to_index"] is None
    assert losses[0]["reason"] == "session_ended_unread"


def test_sync_state_tracking(temp_storage):
    session_id = "sync_sess"
    assert temp_storage.get_last_synced_index(session_id, 1) == -1

    temp_storage.update_last_synced_index(session_id, 1, 399)
    assert temp_storage.get_last_synced_index(session_id, 1) == 399

    # Advancing sync index
    temp_storage.update_last_synced_index(session_id, 1, 799)
    assert temp_storage.get_last_synced_index(session_id, 1) == 799


def test_write_error_tracking(temp_storage):
    assert temp_storage.get_write_error_count() == 0
    temp_storage.record_write_error("disk I/O error simulated")
    assert temp_storage.get_write_error_count() == 1
    temp_storage.record_write_error("database or disk is full")
    assert temp_storage.get_write_error_count() == 2


def test_max_processed_window(temp_storage):
    session_id = "window_sess"
    assert temp_storage.get_max_processed_window(session_id, "online") == -1
    temp_storage.save_result(session_id, 0, "online", {"complete": True})
    assert temp_storage.get_max_processed_window(session_id, "online") == 0
    temp_storage.save_result(session_id, 5, "online", {"complete": True})
    assert temp_storage.get_max_processed_window(session_id, "online") == 5
    # Different mode should not interfere
    assert temp_storage.get_max_processed_window(session_id, "offline") == -1
