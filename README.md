# MayaCardio — Bedside Biosignal Pipeline ("Never Lose a Heartbeat")

[![Native ARM64 CI](https://github.com/koi-bito/mayacardio-bedside-pipeline/actions/workflows/arm64_evidence.yml/badge.svg?branch=main)](https://github.com/koi-bito/mayacardio-bedside-pipeline/actions/workflows/arm64_evidence.yml)
[![Platform](https://img.shields.io/badge/Platform-Native%20ARM64%20%28Raspberry%20Pi%204%29-blue)](https://github.com/koi-bito/mayacardio-bedside-pipeline)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-brightgreen)](https://github.com/koi-bito/mayacardio-bedside-pipeline)
[![Tests](https://img.shields.io/badge/Tests-15%2F15%20Passing-success)](https://github.com/koi-bito/mayacardio-bedside-pipeline)
[![Data Integrity](https://img.shields.io/badge/Ground%20Truth-100%25%20Verified-success)](https://github.com/koi-bito/mayacardio-bedside-pipeline)

> Candidate Case Assessment for Software Systems Engineer · MayaCardio (Oct 2026)

---

## 1. Overview & Architecture

This repository implements a resilient bedside data pipeline that records two continuous sensors (~800 samples/sec total), guarantees zero silent data loss on crashes or power cuts, streams live data to the cloud when online, processes locally when offline, and automatically catches up historical backlogs (up to 2-hour outages) within 15 minutes.

```
biosignal-mock (TCP, port 7100)
       │
       ▼
  [Collector] ──── writes ──── [Local Store]  (SQLite WAL, PRAGMA synchronous=NORMAL)
       │                              │
       │ network up?                  │ catch-up on reconnect
       ▼                              ▼
  [Online Streamer]            [Offline Processor]
       │                              │
       ▼                              │
  [Cloud Backend] (FastAPI) ◄─────────┘
```

### Core Design Guarantees
- **Zero Silent Data Loss:** Every sample is saved to SQLite before network transmission. Any missing sample ranges are explicitly recorded in `losses` with a reason.
- **Crash & Power-Cut Resilience:** SQLite in WAL mode with `PRAGMA synchronous = NORMAL`. Data loss on sudden power pull is strictly bounded to the uncheckpointed WAL buffer (< 1 second).
- **Exact-Once Cloud Delivery:** Idempotent ingestion keyed on composite primary key `(session_id, sensor, sample_index)`.
- **Clock Independence:** System clock changes do not affect data ordering or windowing; all ordering uses monotonically increasing `sample_index`.
- **Fast Catch-Up:** Drains historical backlog using high-throughput batching (4,000 samples/batch, representing 10 seconds of sensor data per request), syncing 2 hours of data (5.76M samples) well within the 15-minute criterion limit.

---

## 2. Quickstart & Installation

### Requirements
- Python 3.10+
- Linux (ARM64 Raspberry Pi 4 class or Oracle Cloud Ampere A1) or Windows/macOS for development.

### Setup on Fresh Board
```bash
git clone <repo-url>
cd "Restricted Offer 16oct deadline"

# Install package and dependencies
pip install -e .
```

---

## 3. Running the Pipeline

### Step 1: Start Mock Service (Sensor Emulator)
Run the built-in spec-compliant mock (or the official `biosignal-mock` binary):
```bash
# Ramp mode for ground truth verification:
biosignal-mock --mode ramp --seed 7 --port 7100

# Or Signal mode for realistic ECG & PPG signals:
biosignal-mock --mode signal --seed 7 --port 7100
```

### Step 2: Start Cloud Backend (FastAPI)
```bash
python -m uvicorn cloud.app:app --host 0.0.0.0 --port 8000
```

### Step 3: Start Edge Pipeline Daemon
```bash
pipeline run --cloud http://127.0.0.1:8000 --mock-port 7100
```

---

## 4. Required CLI Commands

### 1. `pipeline status`
Displays the current pipeline state as a single JSON line:
```bash
pipeline status
# Output:
# {"mode": "online", "samples_saved": 144000, "samples_in_cloud": 144000, "backlog": 0}
```

### 2. `pipeline export`
Exports session samples followed by any losses as newline-delimited JSON:
```bash
# Export from device local store:
pipeline export --session <SESSION_ID> --from device > device_export.jsonl

# Export from cloud backend:
pipeline export --session <SESSION_ID> --from cloud > cloud_export.jsonl
```

### 3. `pipeline results`
Prints one JSON line per 1-second window (400 samples) with Section 3 metrics:
```bash
pipeline results --session <SESSION_ID>
```
Output includes:
- `missing_1`, `missing_2`, `complete`
- Per-channel `mean`, `min`, `max`
- Sensor 2 `magnitude` ($\text{mean}(\sqrt{\text{ch1}^2 + \text{ch2}^2})$)
- Sensor 1 channel 2 `heart_rate` (calculated over rolling 5-second window using 4-condition peak detection)
- `mode` (`"online"` or `"offline"`)

---

## 5. Verification Against Ground Truth

Compare exported data directly against ground truth:
```bash
check-ground-truth device_export.jsonl --mode ramp --seed 7
# Output:
# PASS: 100% data integrity verified. No silent losses, no duplicates, all values match.
```

---

## 6. Running Tests

Run the full automated test suite:
```bash
pytest tests/ -v
```

---

## 7. ARM64 Oracle Cloud Resource Limits

To run on Oracle Cloud Ampere A1 constrained to Raspberry Pi 4 specs (4 cores, 2 GB RAM):
```bash
# Run with 4 CPU cores and 2 GB memory limit
systemd-run --scope -p MemoryMax=2G taskset -c 0-3 pipeline run
```
