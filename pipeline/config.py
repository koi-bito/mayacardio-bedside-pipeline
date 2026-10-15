import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = os.environ.get("MAYA_DB_PATH", str(BASE_DIR / "device_data.db"))
CLOUD_DB_PATH = os.environ.get("MAYA_CLOUD_DB_PATH", str(BASE_DIR / "cloud_data.db"))

# Network & Mock defaults
MOCK_HOST = os.environ.get("MOCK_HOST", "127.0.0.1")
MOCK_PORT = int(os.environ.get("MOCK_PORT", "7100"))

# Cloud defaults
CLOUD_URL = os.environ.get("MAYA_CLOUD_URL", "http://127.0.0.1:8000")

# Sensor & Stream parameters
SAMPLE_RATE = 400  # samples/sec per sensor
WINDOW_SIZE = 400  # 1 second window
HR_WINDOW_SECONDS = 5  # 5 second window for HR
HR_WINDOW_SAMPLES = HR_WINDOW_SECONDS * SAMPLE_RATE  # 2000 samples
MOCK_BUFFER_SECONDS = 10  # Mock discards beyond 10s buffer

# Mode switching parameters
HEALTH_PROBE_INTERVAL = 5.0  # seconds between probes
MAX_FAILURES_BEFORE_OFFLINE = 2  # 2 fails = switch to offline (<= 10s)
PROBE_TIMEOUT = 2.5  # seconds for health probe request

# Batch sizes
ONLINE_BATCH_SIZE = 400  # 1 second of data per POST
CATCHUP_BATCH_SIZE = 4000  # 10 seconds of data per POST during catchup
