import argparse
import asyncio
import json
import logging
import sys
from typing import Optional
import httpx
from pipeline.collector import CollectorManager
from pipeline.config import (
    CLOUD_URL,
    DEFAULT_DB_PATH,
    MOCK_HOST,
    MOCK_PORT,
)
from pipeline.mode_controller import ModeController
from pipeline.storage import Storage
from pipeline.streamer import StreamerManager


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_status(args):
    storage = Storage(args.db)
    session_id = args.session or storage.get_latest_session_id()

    samples_saved = storage.get_total_samples_saved(session_id)

    # Check cloud status
    mode = "offline"
    samples_in_cloud = 0
    try:
        resp = httpx.get(f"{args.cloud.rstrip('/')}/health", timeout=2.0)
        if resp.status_code == 200:
            mode = "online"
            status_resp = httpx.get(
                f"{args.cloud.rstrip('/')}/status",
                params={"session_id": session_id} if session_id else {},
                timeout=2.0,
            )
            if status_resp.status_code == 200:
                samples_in_cloud = status_resp.json().get("samples_in_cloud", 0)
    except Exception:
        mode = "offline"

    write_errors = storage.get_write_error_count()
    backlog = max(0, samples_saved - samples_in_cloud)

    status_obj = {
        "mode": mode,
        "samples_saved": samples_saved,
        "samples_in_cloud": samples_in_cloud,
        "backlog": backlog,
    }
    if write_errors > 0:
        status_obj["disk_errors"] = write_errors

    sys.stdout.write(json.dumps(status_obj) + "\n")
    sys.stdout.flush()


def cmd_export(args):
    session_id = args.session
    if not session_id:
        storage = Storage(args.db)
        session_id = storage.get_latest_session_id()
        if not session_id:
            sys.stderr.write("Error: No session found to export.\n")
            sys.exit(1)

    source = getattr(args, "from", "device")
    if source == "cloud":
        try:
            with httpx.stream(
                "GET",
                f"{args.cloud.rstrip('/')}/export",
                params={"session_id": session_id},
                timeout=60.0,
            ) as response:
                if response.status_code != 200:
                    sys.stderr.write(f"Error fetching cloud export: status {response.status_code}\n")
                    sys.exit(1)
                for line in response.iter_lines():
                    if line:
                        sys.stdout.write(line + "\n")
        except Exception as e:
            sys.stderr.write(f"Cloud export failed: {e}\n")
            sys.exit(1)
    else:
        # Export from device SQLite
        storage = Storage(args.db)
        # Samples first
        for sample in storage.get_all_samples_for_export(session_id):
            sys.stdout.write(json.dumps(sample, separators=(",", ":")) + "\n")
        # Losses next
        for loss in storage.get_all_losses_for_export(session_id):
            sys.stdout.write(json.dumps(loss, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def cmd_results(args):
    session_id = args.session
    storage = Storage(args.db)
    if not session_id:
        session_id = storage.get_latest_session_id()
        if not session_id:
            sys.stderr.write("Error: No session found.\n")
            sys.exit(1)

    results = storage.get_results(session_id)
    if not results:
        # If device DB has 0 results (e.g. session was processed online in cloud),
        # query cloud backend so results are always accessible from CLI
        try:
            cloud_resp = httpx.get(
                f"{args.cloud.rstrip('/')}/results",
                params={"session_id": session_id},
                timeout=5.0,
            )
            if cloud_resp.status_code == 200:
                results = cloud_resp.json().get("results", [])
        except Exception:
            pass

    for r in results:
        sys.stdout.write(json.dumps(r, separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def run_pipeline(args):
    setup_logging(args.verbose)
    storage = Storage(args.db)
    mode_controller = ModeController(cloud_url=args.cloud)
    collector = CollectorManager(storage=storage, host=args.mock_host, port=args.mock_port)
    streamer = StreamerManager(storage=storage, mode_controller=mode_controller, cloud_url=args.cloud)

    tasks = [
        asyncio.create_task(collector.start()),
        asyncio.create_task(mode_controller.run()),
        asyncio.create_task(streamer.run()),
    ]

    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        collector.stop()
        mode_controller.stop()
        streamer.stop()


def main():
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")
    common_parser.add_argument("--cloud", default=CLOUD_URL, help="Cloud backend URL")
    common_parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    parser = argparse.ArgumentParser(
        prog="pipeline", parents=[common_parser], description="MayaCardio Edge Data Pipeline"
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to run")

    # pipeline run
    run_p = subparsers.add_parser("run", parents=[common_parser], help="Start the full pipeline daemon")
    run_p.add_argument("--mock-host", default=MOCK_HOST, help="Mock server host")
    run_p.add_argument("--mock-port", type=int, default=MOCK_PORT, help="Mock server TCP port")

    # pipeline status
    status_p = subparsers.add_parser("status", parents=[common_parser], help="Get pipeline status JSON")
    status_p.add_argument("--session", help="Session ID (default: latest)")

    # pipeline export --session ID --from [device|cloud]
    export_p = subparsers.add_parser("export", parents=[common_parser], help="Export session data as JSON lines")
    export_p.add_argument("--session", help="Session ID (default: latest)")
    export_p.add_argument(
        "--from",
        dest="from",
        choices=["device", "cloud"],
        default="device",
        help="Export from device or cloud",
    )

    # pipeline results --session ID
    results_p = subparsers.add_parser("results", parents=[common_parser], help="Export window results as JSON lines")
    results_p.add_argument("--session", help="Session ID (default: latest)")

    args = parser.parse_args()

    if args.subcommand == "run":
        try:
            asyncio.run(run_pipeline(args))
        except KeyboardInterrupt:
            print("\nPipeline stopped.")
    elif args.subcommand == "status":
        cmd_status(args)
    elif args.subcommand == "export":
        cmd_export(args)
    elif args.subcommand == "results":
        cmd_results(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
