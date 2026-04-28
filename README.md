# ACC Driver-in-the-Loop Simulation Toolkit

Python telemetry integration for **Assetto Corsa Competizione** — shared memory reader, real-time dashboard, data logger, and post-session lap analysis.

---

## Architecture

```
Assetto Corsa Competizione
      │
      ├── Shared Memory (Windows, 60 Hz)          ← acc_shared_memory.py
      └── UDP Broadcast (cross-platform)           ← udp_receiver.py
                │
      ┌─────────┴──────────┐
      │                    │
TelemetryLogger       RealtimeMonitor         ← background threads
      │                    │
   CSV + SQLite       Rich terminal
   per-lap files       dashboard
      │
LapAnalyzer
  Matplotlib plots
  Pandas summaries
```

---

## Requirements

- Python 3.10+
- Windows 10/11 (for real shared memory access; mock mode works cross-platform)
- Assetto Corsa Competizione (Steam version)

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Quick Start

### 1 — Demo mode (no ACC needed)
Generates synthetic telemetry, shows the live dashboard, and writes a session to disk.

```bash
python main.py demo
```

### 2 — Log only (real ACC)
Start ACC, load into a session, then run:

```bash
python main.py log --hz 60 --output sessions/
```

### 3 — Live dashboard only
```bash
python main.py monitor
```

Mock mode (no ACC):
```bash
python main.py monitor --mock
```

### 4 — Post-session analysis
```bash
# Print lap table
python main.py analyze sessions/20240101_120000_monza_ferrari_296_gt3/

# Generate all plots as PNG files
python main.py analyze sessions/20240101_120000_monza_ferrari_296_gt3/ --plots

# Overlay two laps
python main.py analyze sessions/... --compare 5 8
```

---

## Module Reference

| File | Purpose |
|---|---|
| `acc_shared_memory.py` | ctypes structs for ACC shared memory; cross-platform mock mode |
| `udp_receiver.py` | UDP packet receiver for remote / cross-platform use |
| `telemetry_logger.py` | Non-blocking CSV + SQLite writer; per-lap summaries |
| `realtime_monitor.py` | Rich-powered terminal dashboard (60 Hz data, 15 Hz refresh) |
| `lap_analyzer.py` | Pandas + Matplotlib post-session analysis |
| `main.py` | CLI entry point (`log` / `monitor` / `demo` / `analyze`) |

---

## ACC Setup

### Shared Memory (recommended)
No ACC plugin needed — ACC exposes shared memory natively.  
Just ensure ACC is running when you run `python main.py log`.

### UDP (optional, for remote logging)
In ACC: **Options → General → Shared Memory Export → Enable UDP**  
Default port: `9996`.  
Then use `UDPReceiver` from `udp_receiver.py` in your own script.

---

## Output Files

Each session creates a directory like `sessions/20240101_120000_monza_ferrari_296_gt3/`:

```
sessions/
└── 20240101_120000_monza_ferrari_296_gt3/
    ├── telemetry.csv        # All channels, one row per frame (~60 Hz)
    ├── lap_summary.csv      # One row per lap (lap time, sector times, averages)
    ├── session.db           # SQLite with same data + session_info table
    └── plots/               # Created by --plots flag
        ├── lap_overview.png
        ├── tyre_degradation.png
        ├── gforce_scatter.png
        ├── fuel_strategy.png
        └── brake_point_analysis.png
```

---

## Telemetry Channels

**Physics (60 Hz)**
- `speed_kmh`, `gear`, `rpms`
- `throttle`, `brake`, `clutch`, `steer_angle`
- `g_lat`, `g_lon`, `g_vert`
- `tyre_temp_core/inner/middle/outer` × 4 corners (FL FR RL RR)
- `tyre_pressure`, `tyre_wear`, `wheel_slip`, `suspension_travel` × 4
- `brake_temp` × 4, `brake_bias`
- `fuel`, `turbo_boost`, `tc_intervention`, `abs_intervention`
- `air_temp`, `road_temp`, `heading`, `pitch`, `roll`

**Graphics (session)**
- `current_lap_ms`, `last_lap_ms`, `best_lap_ms`
- `sector_index`, `last_sector_ms`, `normalized_pos`
- `is_in_pit`, `is_valid_lap`, `tyre_compound`
- `delta_lap_ms`, `fuel_x_lap`, `gap_ahead_ms`

---

## Extending the Toolkit

### Add a new analysis plot
In `lap_analyzer.py`, add a method to `LapSession` that returns a `matplotlib.figure.Figure`, then register it in `save_all_plots()`.

### Add a new telemetry channel
1. Add the field to `_Physics` or `_Graphics` ctypes struct in `acc_shared_memory.py`.
2. Add it to the corresponding `PhysicsSnapshot` / `GraphicsSnapshot` dataclass.
3. Update `_parse_physics()` / `_parse_graphics()` to populate it.
4. Add the column name to `PHYSICS_COLS` or `GRAPHICS_COLS` in `telemetry_logger.py`.
5. Update `_phy_row()` / `_grp_row()` to include the value.

### Integrate with external tools
The SQLite database at `session.db` can be queried directly:

```python
import sqlite3, pandas as pd
conn = sqlite3.connect("sessions/.../session.db")
df   = pd.read_sql("SELECT speed_kmh, brake, g_lat FROM telemetry WHERE completed_laps = 5", conn)
```

---

## Tested configurations

| Car | Track | Notes |
|---|---|---|
| Porsche 992 GT3 | Spa Francorchamps | Full shared memory layout verified |
| Mercedes AMG GT3| Spa | — |
| Porsche 992 GT3 R | Nürburgring | — |

ACC shared memory layout is the same across all cars and tracks; only static
metadata (max RPM, fuel capacity, sector count) differs.
