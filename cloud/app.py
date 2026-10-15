import json
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pipeline.config import CLOUD_DB_PATH, WINDOW_SIZE
from pipeline.processing import process_window
from pipeline.storage import Storage

app = FastAPI(title="MayaCardio Cloud Ingest API")
cloud_storage = Storage(CLOUD_DB_PATH)
cloud_last_processed: Dict[str, int] = {}


class IngestPayload(BaseModel):
    session_id: str
    sensor: int
    first_index: int
    data: List[List[int]]
    losses: Optional[List[Dict[str, Any]]] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ingest")
def ingest(payload: IngestPayload):
    session_id = payload.session_id
    sensor = payload.sensor
    first_index = payload.first_index
    data = payload.data

    # 1. Insert losses if present
    if payload.losses:
        cloud_storage.insert_losses_batch(session_id, sensor, payload.losses)

    # 2. Insert samples idempotently
    inserted = cloud_storage.insert_samples_batch(session_id, sensor, first_index, data)

    # 3. Process completed windows in online mode
    last_s1 = cloud_storage.get_last_sample_index(session_id, 1)
    last_s2 = cloud_storage.get_last_sample_index(session_id, 2)
    min_last = min(last_s1, last_s2)

    if min_last >= 0:
        max_complete_window = min_last // WINDOW_SIZE
        if session_id not in cloud_last_processed:
            cloud_last_processed[session_id] = cloud_storage.get_max_processed_window(
                session_id, mode="online"
            )

        start_w = cloud_last_processed[session_id] + 1
        for w in range(start_w, max_complete_window + 1):
            w_start = w * WINDOW_SIZE
            w_end = w_start + WINDOW_SIZE - 1

            s1_samples = cloud_storage.get_samples_range(session_id, 1, w_start, w_end)
            s2_samples = cloud_storage.get_samples_range(session_id, 2, w_start, w_end)

            # For HR, fetch 5 seconds of sensor 1 history (400*(w-4) to 400*(w+1)-1)
            history_dict = {}
            if w >= 4:
                hr_start = 400 * (w - 4)
                hr_end = 400 * (w + 1) - 1
                hr_samples = cloud_storage.get_samples_range(session_id, 1, hr_start, hr_end)
                history_dict = {idx: vals[1] for idx, vals in hr_samples.items()}

            res = process_window(
                w, s1_samples, s2_samples, mode="online", s1_history_ch2=history_dict
            )
            cloud_storage.save_result(session_id, w, "online", res)
            cloud_last_processed[session_id] = w

    return {"status": "ok", "inserted": inserted}


@app.get("/status")
def status(session_id: Optional[str] = Query(None)):
    count = cloud_storage.get_total_samples_saved(session_id)
    return {"samples_in_cloud": count}


@app.get("/results")
def get_results(session_id: str = Query(...)):
    results = cloud_storage.get_results(session_id)
    return {"session_id": session_id, "results": results}


@app.get("/export")
def export(session_id: str = Query(...)):
    def line_generator():
        try:
            # First, all samples
            for s in cloud_storage.get_all_samples_for_export(session_id):
                yield json.dumps(s, separators=(",", ":")) + "\n"
            # Then, all losses
            for l in cloud_storage.get_all_losses_for_export(session_id):
                yield json.dumps(l, separators=(",", ":")) + "\n"
        except Exception as e:
            logger.error(f"Error in export line_generator: {e}")

    return StreamingResponse(line_generator(), media_type="application/x-ndjson")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
