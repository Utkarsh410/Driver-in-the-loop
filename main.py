"""
main.py
-------
CLI entry point for the ACC Driver-in-the-Loop simulation toolkit.

Modes
─────
  log      – Start a telemetry logging session (real ACC or mock).
  monitor  – Show real-time Rich dashboard only (no file output).
  analyze  – Post-session analysis on an existing session directory.
  demo     – Combined: mock ACC data → live dashboard + file logging.

Usage examples
──────────────
  python main.py demo
  python main.py log --hz 60 --output sessions/
  python main.py monitor
  python main.py analyze sessions/20240101_120000_monza_ferrari_296_gt3/
  python main.py analyze sessions/20240101_120000_monza_ferrari_296_gt3/ --plots
"""

import argparse
import signal
import sys
import time
import threading
from pathlib import Path

from acc_shared_memory import ACCSharedMemory


# ── graceful shutdown ─────────────────────────────────────────────────────────

_stop_event = threading.Event()

def _handle_sigint(sig, frame):
    print("\n[main] SIGINT – shutting down...")
    _stop_event.set()

signal.signal(signal.SIGINT, _handle_sigint)


# ── modes ─────────────────────────────────────────────────────────────────────

def run_log(args):
    """Log telemetry to disk with optional real-time console output."""
    from telemetry_logger import TelemetryLogger

    sm = ACCSharedMemory(mock=args.mock)
    logger = TelemetryLogger(output_dir=args.output, use_sqlite=True)

    sm.start(poll_hz=args.hz)
    time.sleep(0.5)   # let first snapshot arrive

    # Wait for static data
    print("[main] Waiting for ACC session info...")
    for _ in range(40):
        _, _, sta = sm.snapshot()
        if sta:
            break
        time.sleep(0.1)
    else:
        print("[main] Timed out waiting for static data. Using placeholder.")
        from acc_shared_memory import StaticSnapshot
        sta = StaticSnapshot("unknown_car","unknown_track","Driver","DRV",3,9000,70.0,5000.0,"DH","WH")

    logger.start_session(sta)

    last_lap   = 0
    last_sector = 0
    frame_count = 0
    hz          = args.hz
    interval    = 1.0 / hz
    print(f"[main] Logging at {hz} Hz. Press Ctrl+C to stop.\n")

    while not _stop_event.is_set():
        t0 = time.perf_counter()
        phy, grp, _sta = sm.snapshot()
        if phy and grp:
            logger.log(phy, grp)
            frame_count += 1

            # Lap detection
            if grp.completed_laps > last_lap:
                logger.lap_complete(grp, grp.last_lap_ms)
                last_lap = grp.completed_laps

            # Sector detection
            if grp.sector_index != last_sector:
                logger.sector_complete(grp.last_sector_ms)
                last_sector = grp.sector_index

            if frame_count % (hz * 5) == 0:
                from telemetry_logger import _ms_to_laptime
                speed = phy.speed_kmh
                lap   = grp.completed_laps
                laptime = _ms_to_laptime(grp.current_lap_ms)
                print(f"  Lap {lap+1:>3}  {laptime}  {speed:>6.1f} km/h  "
                      f"Throttle {phy.throttle*100:.0f}%  Brake {phy.brake*100:.0f}%  "
                      f"Frames {logger.frames_written:,}")

        elapsed = time.perf_counter() - t0
        time.sleep(max(0.0, interval - elapsed))

    sm.stop()
    logger.end_session()
    print(f"[main] Done. Session saved to: {logger.session_dir}")


def run_monitor(args):
    """Live dashboard only – no file output."""
    from realtime_monitor import RealtimeMonitor

    sm      = ACCSharedMemory(mock=args.mock)
    monitor = RealtimeMonitor(refresh_hz=15)

    sm.start(poll_hz=60)
    monitor.start()

    print("[main] Monitor running. Press Ctrl+C to stop.")
    while not _stop_event.is_set():
        phy, grp, sta = sm.snapshot()
        monitor.update(phy, grp, sta)
        time.sleep(1.0 / 30.0)

    sm.stop()
    monitor.stop()


def run_demo(args):
    """Mock data → live dashboard + file logging simultaneously."""
    from telemetry_logger import TelemetryLogger
    from realtime_monitor import RealtimeMonitor

    sm      = ACCSharedMemory(mock=True)
    monitor = RealtimeMonitor(refresh_hz=15)
    logger  = TelemetryLogger(output_dir=args.output, use_sqlite=True)

    sm.start(poll_hz=60)
    time.sleep(0.5)

    _, _, sta = sm.snapshot()
    if sta:
        logger.start_session(sta)
    else:
        print("[main] Static snapshot not ready – retrying...")
        time.sleep(1)
        _, _, sta = sm.snapshot()
        if sta:
            logger.start_session(sta)

    monitor.start()

    last_lap    = 0
    last_sector = 0
    interval    = 1.0 / 60
    print("[main] DEMO running (mock ACC data). Press Ctrl+C to stop.")

    while not _stop_event.is_set():
        t0 = time.perf_counter()
        phy, grp, _sta = sm.snapshot()
        if phy and grp:
            logger.log(phy, grp)
            monitor.update(phy, grp, _sta)
            if grp.completed_laps > last_lap:
                logger.lap_complete(grp, grp.last_lap_ms)
                last_lap = grp.completed_laps
            if grp.sector_index != last_sector:
                logger.sector_complete(grp.last_sector_ms)
                last_sector = grp.sector_index
        elapsed = time.perf_counter() - t0
        time.sleep(max(0.0, interval - elapsed))

    sm.stop()
    monitor.stop()
    logger.end_session()
    print(f"[main] Demo ended. Session: {logger.session_dir}")


def run_analyze(args):
    """Post-session analysis on an existing session directory."""
    from lap_analyzer import load_session

    session_dir = Path(args.session_dir)
    if not session_dir.exists():
        print(f"[main] Session directory not found: {session_dir}")
        sys.exit(1)

    session = load_session(str(session_dir))
    session.print_summary()

    if args.plots:
        saved = session.save_all_plots()
        print(f"\n[main] {len(saved)} plot(s) saved.")
        for p in saved:
            print(f"  {p}")

    if args.compare and len(args.compare) == 2:
        from lap_analyzer import load_session
        import matplotlib.pyplot as plt
        a, b = int(args.compare[0]), int(args.compare[1])
        fig = session.compare_laps(a, b)
        if fig:
            out_path = session_dir / f"compare_L{a}_L{b}.png"
            fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
            plt.close(fig)
            print(f"[main] Lap comparison saved: {out_path}")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ACC Driver-in-the-Loop Simulation Toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # log
    p_log = sub.add_parser("log", help="Log telemetry to disk")
    p_log.add_argument("--hz",     type=int, default=60, help="Polling rate (default 60)")
    p_log.add_argument("--output", type=str, default="sessions", help="Output directory")
    p_log.add_argument("--mock",   action="store_true", help="Use mock data (no ACC required)")

    # monitor
    p_mon = sub.add_parser("monitor", help="Real-time Rich dashboard")
    p_mon.add_argument("--mock", action="store_true")

    # demo
    p_demo = sub.add_parser("demo", help="Mock data + live dashboard + logging")
    p_demo.add_argument("--output", type=str, default="sessions")

    # analyze
    p_ana = sub.add_parser("analyze", help="Post-session analysis")
    p_ana.add_argument("session_dir", help="Path to session directory")
    p_ana.add_argument("--plots",   action="store_true", help="Generate and save all analysis plots")
    p_ana.add_argument("--compare", nargs=2, metavar=("LAP_A", "LAP_B"),
                       help="Overlay two lap traces")

    args = parser.parse_args()

    dispatch = {
        "log":     run_log,
        "monitor": run_monitor,
        "demo":    run_demo,
        "analyze": run_analyze,
    }
    dispatch[args.mode](args)


if __name__ == "__main__":
    main()