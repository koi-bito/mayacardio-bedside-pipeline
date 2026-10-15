#!/bin/bash
set -e

# Remove any stale shared-memory or lock files from abrupt shutdowns
rm -f /app/*.db-shm

echo "========================================================"
echo " MAYACARDIO EDGE CONTAINER INITIALIZING"
echo "========================================================"

echo "[start.sh] Starting biosignal-mock service (ramp mode, duration 3600s)..."
biosignal-mock --mode ramp --seed 7 --duration 3600 --port 7100 &
sleep 1

echo "[start.sh] Starting edge pipeline supervisor loop..."
while true; do
    if ! pgrep -f "pipeline.*run" > /dev/null; then
        echo "[start.sh] Launching pipeline daemon..."
        pipeline run --cloud "${MAYA_CLOUD_URL:-http://cloud:8000}" --mock-port 7100 &
        PIPE_PID=$!
        wait $PIPE_PID 2>/dev/null || true
        echo "[start.sh] Pipeline daemon exited. Standing by..."
    fi
    sleep 2
done
