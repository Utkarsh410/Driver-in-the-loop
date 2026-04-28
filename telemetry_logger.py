"""
telemetry_logger.py
-------------------
Writes ACC telemetry to disk in two formats:
  • CSV  – one file per lap, easy to open in Excel / pandas
  • SQLite – one database per session, fast for queries

Usage:
    logger = TelemetryLogger(output_dir="sessions")
    logger.start_session(static_snap)
    logger.log(physics_snap, graphics_snap)     # call at ~60 Hz
    logger.lap_complete(graphics_snap)
    logger.end_session()
"""

import csv
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from queue import Queue, Empty

from acc_shared_memory import PhysicsSnapshot, GraphicsSnapshot, StaticSnapshot

# ── Column definitions ────────────────────────────────────────────────────────

PHYSICS_COLS = [
    "timestamp", "speed_kmh", "gear", "rpms",
    "throttle", "brake", "clutch", "steer_angle",
    "g_lat", "g_lon", "g_vert",
    "tyre_temp_core_fl", "tyre_temp_core_fr", "tyre_temp_core_rl", "tyre_temp_core_rr",
    "tyre_temp_inner_fl", "tyre_temp_inner_fr", "tyre_temp_inner_rl", "tyre_temp_inner_rr",
    "tyre_temp_middle_fl", "tyre_temp_middle_fr", "tyre_temp_middle_rl", "tyre_temp_middle_rr",
    "tyre_temp_outer_fl", "tyre_temp_outer_fr", "tyre_temp_outer_rl", "tyre_temp_outer_rr",
    "tyre_pressure_fl", "tyre_pressure_fr", "tyre_pressure_rl", "tyre_pressure_rr",
    "tyre_wear_fl", "tyre_wear_fr", "tyre_wear_rl", "tyre_wear_rr",
    "wheel_slip_fl", "wheel_slip_fr", "wheel_slip_rl", "wheel_slip_rr",
    "suspension_fl", "suspension_fr", "suspension_rl", "suspension_rr",
    "brake_temp_fl", "brake_temp_fr", "brake_temp_rl", "brake_temp_rr",
    "brake_bias", "fuel", "turbo_boost",
    "tc_intervention", "abs_intervention", "drs",
    "air_temp", "road_temp",
    "heading", "pitch", "roll",
]

GRAPHICS_COLS = [
    "current_lap_ms", "sector_index", "normalized_pos",
    "is_in_pit", "is_in_pit_lane", "is_valid_lap",
    "delta_lap_ms", "is_delta_positive",
]

ALL_COLS = PHYSICS_COLS + GRAPHICS_COLS

LAP_SUMMARY_COLS = [
    "lap_number", "lap_time_ms", "is_valid",
    "s1_ms", "s2_ms", "s3_ms",
    "avg_speed_kmh", "max_speed_kmh",
    "tyre_compound",
    "avg_tyre_temp_fl", "avg_tyre_temp_fr", "avg_tyre_temp_rl", "avg_tyre_temp_rr",
    "avg_tyre_pressure_fl", "avg_tyre_pressure_fr", "avg_tyre_pressure_rl", "avg_tyre_pressure_rr",
    "avg_brake_bias", "max_brake_temp",
    "avg_tc_intervention", "avg_abs_intervention",
    "fuel_used",
]


def _phy_row(p: PhysicsSnapshot) -> list:
    return [
        p.timestamp, p.speed_kmh, p.gear, p.rpms,
        p.throttle, p.brake, p.clutch, p.steer_angle,
        p.g_lat, p.g_lon, p.g_vert,
        *p.tyre_temp_core, *p.tyre_temp_inner,
        *p.tyre_temp_middle, *p.tyre_temp_outer,
        *p.tyre_pressure, *p.tyre_wear,
        *p.wheel_slip, *p.suspension_travel,
        *p.brake_temp, p.brake_bias, p.fuel, p.turbo_boost,
        p.tc_intervention, p.abs_intervention, p.drs,
        p.air_temp, p.road_temp, p.heading, p.pitch, p.roll,
    ]


def _grp_row(g: GraphicsSnapshot) -> list:
    return [
        g.current_lap_ms, g.sector_index, g.normalized_pos,
        int(g.is_in_pit), int(g.is_in_pit_lane), int(g.is_valid_lap),
        g.delta_lap_ms, int(g.is_delta_positive),
    ]


# ── Lap accumulator ───────────────────────────────────────────────────────────

class LapAccumulator:
    """Collects per-frame data for a single lap, produces a summary row."""

    def __init__(self, lap_number: int, tyre_compound: str):
        self.lap_number    = lap_number
        self.tyre_compound = tyre_compound
        self._frames: List[tuple] = []
        self._sector_times: List[int] = []
        self._start_fuel: Optional[float] = None

    def add_frame(self, p: PhysicsSnapshot, g: GraphicsSnapshot) -> None:
        if self._start_fuel is None:
            self._start_fuel = p.fuel
        self._frames.append((p, g))

    def add_sector(self, sector_ms: int) -> None:
        self._sector_times.append(sector_ms)

    def summarise(self, lap_time_ms: int, is_valid: bool) -> Optional[dict]:
        if not self._frames:
            return None
        speeds   = [f[0].speed_kmh for f in self._frames]
        tt       = [[f[0].tyre_temp_core[i] for f in self._frames] for i in range(4)]
        tp       = [[f[0].tyre_pressure[i]  for f in self._frames] for i in range(4)]
        bt       = [max(f[0].brake_temp)    for f in self._frames]
        bb       = [f[0].brake_bias         for f in self._frames]
        tc_int   = [f[0].tc_intervention    for f in self._frames]
        abs_int  = [f[0].abs_intervention   for f in self._frames]
        _avg     = lambda xs: sum(xs) / len(xs) if xs else 0.0
        s = self._sector_times
        return {
            "lap_number":          self.lap_number,
            "lap_time_ms":         lap_time_ms,
            "is_valid":            int(is_valid),
            "s1_ms":               s[0] if len(s) > 0 else 0,
            "s2_ms":               s[1] if len(s) > 1 else 0,
            "s3_ms":               s[2] if len(s) > 2 else 0,
            "avg_speed_kmh":       round(_avg(speeds), 2),
            "max_speed_kmh":       round(max(speeds), 2),
            "tyre_compound":       self.tyre_compound,
            "avg_tyre_temp_fl":    round(_avg(tt[0]), 2),
            "avg_tyre_temp_fr":    round(_avg(tt[1]), 2),
            "avg_tyre_temp_rl":    round(_avg(tt[2]), 2),
            "avg_tyre_temp_rr":    round(_avg(tt[3]), 2),
            "avg_tyre_pressure_fl": round(_avg(tp[0]), 3),
            "avg_tyre_pressure_fr": round(_avg(tp[1]), 3),
            "avg_tyre_pressure_rl": round(_avg(tp[2]), 3),
            "avg_tyre_pressure_rr": round(_avg(tp[3]), 3),
            "avg_brake_bias":       round(_avg(bb), 4),
            "max_brake_temp":       round(max(bt), 1),
            "avg_tc_intervention":  round(_avg(tc_int), 4),
            "avg_abs_intervention": round(_avg(abs_int), 4),
            "fuel_used":            round((self._start_fuel or 0) - self._frames[-1][0].fuel, 3),
        }


# ── Main logger ───────────────────────────────────────────────────────────────

class TelemetryLogger:
    """
    Thread-safe, non-blocking telemetry writer.
    log() enqueues data; a background thread drains the queue to disk.
    """

    def __init__(self, output_dir: str = "sessions", use_sqlite: bool = True):
        self._output_dir = Path(output_dir)
        self._use_sqlite = use_sqlite
        self._queue: Queue = Queue(maxsize=10_000)
        self._session_dir: Optional[Path] = None
        self._db_conn: Optional[sqlite3.Connection] = None
        self._csv_file = None
        self._csv_writer = None
        self._lap_csv_file = None
        self._lap_csv_writer = None
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._static: Optional[StaticSnapshot] = None
        self._current_lap = 0
        self._lap_accumulator: Optional[LapAccumulator] = None
        self._frames_written = 0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start_session(self, static: StaticSnapshot) -> None:
        self._static = static
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{ts}_{static.track}_{static.car_model}"
        self._session_dir = self._output_dir / name
        self._session_dir.mkdir(parents=True, exist_ok=True)
        print(f"[Logger] Session directory: {self._session_dir}")

        # CSV setup
        csv_path = self._session_dir / "telemetry.csv"
        self._csv_file   = open(csv_path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(ALL_COLS)

        lap_csv_path      = self._session_dir / "lap_summary.csv"
        self._lap_csv_file   = open(lap_csv_path, "w", newline="", encoding="utf-8")
        self._lap_csv_writer = csv.DictWriter(self._lap_csv_file, fieldnames=LAP_SUMMARY_COLS)
        self._lap_csv_writer.writeheader()

        # SQLite setup
        if self._use_sqlite:
            db_path = self._session_dir / "session.db"
            self._db_conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._init_db()

        self._running = True
        self._thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._thread.start()
        print("[Logger] Started.")

    def end_session(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._csv_file:
            self._csv_file.close()
        if self._lap_csv_file:
            self._lap_csv_file.close()
        if self._db_conn:
            self._db_conn.close()
        print(f"[Logger] Session ended. {self._frames_written} frames written.")

    # ── public API ────────────────────────────────────────────────────────────

    def log(self, p: PhysicsSnapshot, g: GraphicsSnapshot) -> None:
        """Non-blocking: enqueue a telemetry frame."""
        if not self._running:
            return
        try:
            self._queue.put_nowait(("frame", p, g))
        except Exception:
            pass  # queue full – drop frame rather than block game loop

    def lap_complete(self, g: GraphicsSnapshot, prev_lap_ms: int) -> None:
        """Call when ACC signals a new lap."""
        self._queue.put(("lap", g, prev_lap_ms))

    def sector_complete(self, sector_ms: int) -> None:
        if self._lap_accumulator:
            self._lap_accumulator.add_sector(sector_ms)

    # ── drain loop ────────────────────────────────────────────────────────────

    def _drain_loop(self) -> None:
        BATCH = 200
        buf: list = []
        while self._running or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.1)
            except Empty:
                if buf:
                    self._flush(buf)
                    buf.clear()
                continue

            if item[0] == "frame":
                _, p, g = item
                row = _phy_row(p) + _grp_row(g)
                buf.append(row)
                if self._lap_accumulator:
                    self._lap_accumulator.add_frame(p, g)
                if len(buf) >= BATCH:
                    self._flush(buf)
                    buf.clear()

            elif item[0] == "lap":
                _, g, lap_ms = item
                if buf:
                    self._flush(buf)
                    buf.clear()
                self._close_lap(g, lap_ms)
                self._current_lap = g.completed_laps
                self._lap_accumulator = LapAccumulator(self._current_lap, g.tyre_compound)

    def _flush(self, rows: list) -> None:
        if not rows:
            return
        self._csv_writer.writerows(rows)
        self._csv_file.flush()
        if self._db_conn:
            self._db_conn.executemany(
                f"INSERT INTO telemetry VALUES ({','.join(['?']*len(ALL_COLS))})", rows
            )
            self._db_conn.commit()
        self._frames_written += len(rows)

    def _close_lap(self, g: GraphicsSnapshot, lap_ms: int) -> None:
        if not self._lap_accumulator:
            return
        summary = self._lap_accumulator.summarise(lap_ms, g.is_valid_lap)
        if summary:
            self._lap_csv_writer.writerow(summary)
            self._lap_csv_file.flush()
            if self._db_conn:
                cols = ", ".join(summary.keys())
                vals = ", ".join(["?"] * len(summary))
                self._db_conn.execute(f"INSERT INTO lap_summary ({cols}) VALUES ({vals})",
                                      list(summary.values()))
                self._db_conn.commit()
            valid = "VALID" if g.is_valid_lap else "INVALID"
            print(f"[Logger] Lap {self._current_lap + 1}  {_ms_to_laptime(lap_ms)}  [{valid}]")

    # ── SQLite schema ─────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        col_defs = ", ".join(f"{c} REAL" for c in ALL_COLS)
        self._db_conn.execute(f"CREATE TABLE IF NOT EXISTS telemetry ({col_defs})")
        lap_defs = ", ".join(
            f"{c} {'INTEGER' if 'ms' in c or c in ('lap_number','is_valid') else 'REAL' if 'avg' in c or 'max' in c or 'fuel' in c else 'TEXT'}"
            for c in LAP_SUMMARY_COLS
        )
        self._db_conn.execute(f"CREATE TABLE IF NOT EXISTS lap_summary ({lap_defs})")
        self._db_conn.execute(
            "CREATE TABLE IF NOT EXISTS session_info "
            "(key TEXT PRIMARY KEY, value TEXT)"
        )
        if self._static:
            info = [
                ("car_model",   self._static.car_model),
                ("track",       self._static.track),
                ("player",      self._static.player_name),
                ("max_fuel",    str(self._static.max_fuel)),
                ("track_len",   str(self._static.track_length)),
                ("started_at",  datetime.now().isoformat()),
            ]
            self._db_conn.executemany("INSERT OR REPLACE INTO session_info VALUES (?,?)", info)
        self._db_conn.commit()

    # ── helpers ───────────────────────────────────────────────────────────────

    @property
    def session_dir(self) -> Optional[Path]:
        return self._session_dir

    @property
    def frames_written(self) -> int:
        return self._frames_written


def _ms_to_laptime(ms: int) -> str:
    if ms <= 0:
        return "--:--.---"
    minutes  = ms // 60_000
    seconds  = (ms % 60_000) // 1000
    millis   = ms % 1000
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"