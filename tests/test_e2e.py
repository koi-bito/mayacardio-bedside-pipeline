import asyncio
import os
import tempfile
import pytest
from mock.biosignal_mock import MockServer
from mock.check_ground_truth import verify_export
from pipeline.collector import CollectorManager
from pipeline.storage import Storage


def test_end_to_end_collector_with_mock_ramp():
    async def _run_test():
        port = 7199
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f_db:
            db_file = f_db.name
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f_exp:
            export_file = f_exp.name

        mock = MockServer(port=port, mode="ramp", seed=7, duration=30)
        server_task = asyncio.create_task(mock.run())

        # Wait for mock to start
        await asyncio.sleep(0.3)

        storage = Storage(db_file)
        collector = CollectorManager(storage=storage, host="127.0.0.1", port=port)
        collector_task = asyncio.create_task(collector.start())

        # Collect for ~2.5 seconds (~2000 samples)
        await asyncio.sleep(2.5)

        collector.stop()
        server_task.cancel()
        collector_task.cancel()

        # Let cleanup happen
        await asyncio.sleep(0.2)

        session_id = storage.get_latest_session_id()
        assert session_id is not None

        total_samples = storage.get_total_samples_saved(session_id)
        assert total_samples > 800  # verified collection throughput

        # Export to jsonl
        with open(export_file, "w", encoding="utf-8") as f:
            for s in storage.get_all_samples_for_export(session_id):
                f.write(f'{{"sensor":{s["sensor"]},"index":{s["index"]},"values":{s["values"]}}}\n')
            for l in storage.get_all_losses_for_export(session_id):
                f.write(
                    f'{{"sensor":{l["sensor"]},"from_index":{l["from_index"]},"to_index":{l["to_index"]},"reason":"{l["reason"]}"}}\n'
                )

        # Verify against ground truth!
        is_valid, errors = verify_export(export_file, mode="ramp", seed=7)
        assert is_valid, f"Ground truth validation failed: {errors}"

        # Cleanup temp files
        try:
            os.remove(db_file)
            os.remove(export_file)
        except Exception:
            pass

    asyncio.run(_run_test())
