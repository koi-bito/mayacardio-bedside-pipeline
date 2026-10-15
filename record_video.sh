#!/bin/bash
set -e

# MayaCardio Bedside Pipeline — Video Recording Orchestration Script
# Guides the candidate through recording the <= 5 minute video demonstration.
# Demonstrates:
#   Scene 1: Network cut & automatic restoration
#   Scene 2: Process kill (kill -9) & crash recovery
#   Scene 3: Sudden power cut (docker restart --timeout 0) & ground truth verification

AUTO_MODE=false
if [ "$1" == "--auto" ]; then
    AUTO_MODE=true
fi

pause() {
    if [ "$AUTO_MODE" = "true" ]; then
        sleep 3
    else
        echo ""
        read -p ">>> Press ENTER to proceed to the next step... <<<" _
        echo ""
    fi
}

echo "========================================================================"
echo " MAYACARDIO SSE ASSESSMENT — 5-MINUTE DEMO VIDEO ORCHESTRATOR"
echo "========================================================================"
echo "Ensure Docker containers are running before starting:"
echo "  docker compose -f docker-compose.video.yml up -d --build"
echo "========================================================================"

# Pre-flight check
if ! docker ps | grep -q "mayacardio-edge-1"; then
    echo "ERROR: mayacardio-edge-1 container is not running!"
    echo "Please start the containers first: docker compose -f docker-compose.video.yml up -d"
    exit 1
fi

mkdir -p evidence

echo ""
echo "Starting video demonstration sequence..."
sleep 2

# ------------------------------------------------------------------------------
# SCENE 1: NETWORK CUT AND RESTORATION
# ------------------------------------------------------------------------------
echo ""
echo "========================================================================"
echo "=== SCENE 1: NETWORK CUT AND RESTORATION ==="
echo "========================================================================"
echo "1. Checking initial pipeline state (expect mode: online, backlog: 0):"
docker exec mayacardio-edge-1 pipeline status
pause

echo "2. Cutting network between edge device and cloud backend..."
docker network disconnect mayanet mayacardio-edge-1
echo "Network disconnected. Waiting 12 seconds for Mode Controller detection (<=10s criterion)..."
sleep 12

echo "3. Querying pipeline status during network cut (expect mode: offline):"
docker exec mayacardio-edge-1 pipeline status
pause

echo "4. Restoring network connection between edge and cloud backend..."
docker network connect mayanet mayacardio-edge-1
echo "Network restored. Waiting 15 seconds for Catch-Up Agent to synchronize backlog..."
sleep 15

echo "5. Querying pipeline status after restoration (mode: online, backlog draining):"
docker exec mayacardio-edge-1 pipeline status
sleep 5

echo "6. Confirming catch-up completion (expect mode: online, backlog: 0):"
docker exec mayacardio-edge-1 pipeline status
pause

# ------------------------------------------------------------------------------
# SCENE 2: PROCESS KILL (SIGKILL) & WAL RECOVERY
# ------------------------------------------------------------------------------
echo ""
echo "========================================================================"
echo "=== SCENE 2: PROCESS KILL (kill -9) & CRASH RECOVERY ==="
echo "========================================================================"
echo "1. Recording current sample count before kill:"
docker exec mayacardio-edge-1 pipeline status
pause

echo "2. Killing edge pipeline daemon with SIGKILL (kill -9)..."
docker exec mayacardio-edge-1 pkill -9 -f "pipeline.*run" || true
echo "Process terminated abruptly."
sleep 3

echo "3. Restarting pipeline daemon inside edge container..."
docker exec -d mayacardio-edge-1 pipeline run --cloud http://cloud:8000 --mock-port 7100
echo "Waiting 5 seconds for recovery & continued streaming..."
sleep 5

echo "4. Exporting session data from device disk:"
docker exec mayacardio-edge-1 sh -c "pipeline export --from device > /evidence/video_kill_export.jsonl"
echo "Exported dataset to /evidence/video_kill_export.jsonl"

echo "5. Verifying data against ground truth (expect PASS):"
docker exec mayacardio-edge-1 check-ground-truth /evidence/video_kill_export.jsonl --mode ramp --seed 7
pause

# ------------------------------------------------------------------------------
# SCENE 3: ABRUPT POWER CUT & INTEGRITY VERIFICATION
# ------------------------------------------------------------------------------
echo ""
echo "========================================================================"
echo "=== SCENE 3: ABRUPT POWER CUT (docker restart --timeout 0) ==="
echo "========================================================================"
echo "1. Simulating sudden power pull (instant kill with zero graceful shutdown)..."
docker restart --timeout 0 mayacardio-edge-1
echo "Power cut simulated. Container rebooting..."
sleep 6

echo "2. Querying pipeline status after reboot (expect clean resumption):"
docker exec mayacardio-edge-1 pipeline status
sleep 5

echo "3. Exporting post-reboot dataset from edge device:"
docker exec mayacardio-edge-1 sh -c "pipeline export --from device > /evidence/video_power_export.jsonl"
echo "Exported dataset to /evidence/video_power_export.jsonl"

echo "4. Running ground truth verification over recovered database:"
docker exec mayacardio-edge-1 check-ground-truth /evidence/video_power_export.jsonl --mode ramp --seed 7
pause

# ------------------------------------------------------------------------------
# FINAL SUMMARY
# ------------------------------------------------------------------------------
echo ""
echo "========================================================================"
echo "=== FINAL PIPELINE INTEGRITY & STATUS ==="
echo "========================================================================"
docker exec mayacardio-edge-1 pipeline status
echo ""
echo "=== Demonstration Completed Successfully ==="
echo "All 3 failure scenarios (Network cut, Process kill, Power cut) demonstrated."
echo "Zero data loss, zero corrupted samples, 100% ground truth verified."
echo "========================================================================"
