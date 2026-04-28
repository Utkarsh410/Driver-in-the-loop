"""
realtime_monitor.py
-------------------
Rich-powered live terminal dashboard showing key ACC telemetry channels.

Panels:
  ┌─ Car status ──┬─ Tyres ────────┐
  │ Speed / Gear  │ Temp  Pressure │
  │ RPM           │ Wear  Slip     │
  ├─ Pedals ──────┼─ Timing ───────┤
  │ Throttle bar  │ Lap / Sector   │
  │ Brake bar     │ Delta / Best   │
  │ Steer         │ Position / Gap │
  └───────────────┴────────────────┘

Usage:
    monitor = RealtimeMonitor()
    monitor.start()
    monitor.update(physics_snap, graphics_snap, static_snap)
    # ... in your loop
    monitor.stop()
"""

import threading
import time
from typing import Optional

from acc_shared_memory import PhysicsSnapshot, GraphicsSnapshot, StaticSnapshot

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import BarColumn, Progress, TextColumn
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("[Monitor] 'rich' not installed. Run: pip install rich")


# ── helpers ───────────────────────────────────────────────────────────────────

_TYRE_LABELS = ["FL", "FR", "RL", "RR"]
_GEAR_NAMES  = {-1: "R", 0: "N", 1: "1", 2: "2", 3: "3", 4: "4",
                5: "5", 6: "6", 7: "7", 8: "8"}

def _ms_to_laptime(ms: int) -> str:
    if ms <= 0:
        return "--:--.---"
    m = ms // 60_000
    s = (ms % 60_000) // 1000
    c = ms % 1000
    return f"{m:02d}:{s:02d}.{c:03d}"

def _delta_str(ms: int, positive: bool) -> str:
    sign = "+" if positive else "-"
    return f"{sign}{abs(ms) / 1000.0:.3f}s"

def _temp_color(t: float) -> str:
    if t < 70:  return "blue"
    if t < 85:  return "green"
    if t < 100: return "yellow"
    return "red"

def _pbar(value: float, width: int = 20, color: str = "green") -> Text:
    """Render a simple progress bar as Rich Text."""
    filled = max(0, min(width, int(value * width)))
    bar = "█" * filled + "░" * (width - filled)
    return Text(bar, style=color)

def _slip_color(s: float) -> str:
    if s < 0.05: return "green"
    if s < 0.15: return "yellow"
    return "red"


# ── Dashboard builder ─────────────────────────────────────────────────────────

class RealtimeMonitor:
    """Renders a Live rich dashboard. Thread-safe via a snapshot lock."""

    def __init__(self, refresh_hz: float = 15.0):
        self._refresh_hz = refresh_hz
        self._lock = threading.Lock()
        self._phy: Optional[PhysicsSnapshot]  = None
        self._grp: Optional[GraphicsSnapshot] = None
        self._sta: Optional[StaticSnapshot]   = None
        self._live: Optional[object] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._best_lap_ms: int = 0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not RICH_AVAILABLE:
            print("[Monitor] rich not available – monitor disabled.")
            return
        self._running = True
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def update(self, p: Optional[PhysicsSnapshot],
               g: Optional[GraphicsSnapshot],
               s: Optional[StaticSnapshot]) -> None:
        with self._lock:
            self._phy = p
            self._grp = g
            if s:
                self._sta = s
            if g and g.best_lap_ms > 0:
                if self._best_lap_ms == 0 or g.best_lap_ms < self._best_lap_ms:
                    self._best_lap_ms = g.best_lap_ms

    # ── render loop ───────────────────────────────────────────────────────────

    def _render_loop(self) -> None:
        console = Console()
        with Live(self._build_layout(), console=console,
                  refresh_per_second=self._refresh_hz,
                  screen=True) as live:
            self._live = live
            while self._running:
                live.update(self._build_layout())
                time.sleep(1.0 / self._refresh_hz)

    def _build_layout(self) -> Layout:
        with self._lock:
            p = self._phy
            g = self._grp
            s = self._sta

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=3),
        )
        layout["main"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )
        layout["left"].split_column(
            Layout(name="car"),
            Layout(name="pedals"),
        )
        layout["right"].split_column(
            Layout(name="tyres"),
            Layout(name="timing"),
        )

        layout["header"].update(self._header_panel(p, g, s))
        layout["car"].update(self._car_panel(p))
        layout["pedals"].update(self._pedals_panel(p))
        layout["tyres"].update(self._tyre_panel(p, g))
        layout["timing"].update(self._timing_panel(g))
        layout["footer"].update(self._footer_panel(p, g))
        return layout

    # ── panels ────────────────────────────────────────────────────────────────

    def _header_panel(self, p, g, s) -> Panel:
        if not s:
            return Panel("[dim]Waiting for ACC...[/dim]", style="bold")
        track  = s.track.upper()
        car    = s.car_model.replace("_", " ").title()
        player = s.player_name
        session_map = {0: "UNKNOWN", 1: "PRACTICE", 2: "QUALIFYING", 3: "RACE"}
        sess = session_map.get(g.session_type, "?") if g else "?"
        status = f"[bold cyan]{track}[/bold cyan]  ·  {car}  ·  {player}  ·  [yellow]{sess}[/yellow]"
        return Panel(Text.from_markup(status), box=box.HORIZONTALS)

    def _car_panel(self, p: Optional[PhysicsSnapshot]) -> Panel:
        t = Table.grid(padding=(0, 2))
        t.add_column(justify="right", style="dim")
        t.add_column(justify="left")
        if not p:
            t.add_row("Status", "[dim]No data[/dim]")
        else:
            gear_name = _GEAR_NAMES.get(p.gear, str(p.gear))
            rpm_pct   = min(1.0, p.rpms / 9000.0)
            rpm_color = "red" if rpm_pct > 0.90 else "yellow" if rpm_pct > 0.75 else "green"
            t.add_row("Speed",    f"[bold white]{p.speed_kmh:>6.1f}[/bold white] km/h")
            t.add_row("Gear",     f"[bold {'yellow' if gear_name == 'N' else 'white'}]{gear_name}[/bold {'yellow' if gear_name == 'N' else 'white'}]")
            t.add_row("RPM",      f"[{rpm_color}]{p.rpms:>5d}[/{rpm_color}]  {_pbar(rpm_pct, 16, rpm_color)}")
            t.add_row("G-Lat",   f"[{'red' if abs(p.g_lat)>2.5 else 'white'}]{p.g_lat:+.2f}[/] g")
            t.add_row("G-Lon",   f"[{'red' if abs(p.g_lon)>2 else 'white'}]{p.g_lon:+.2f}[/] g")
            t.add_row("Fuel",    f"[{'red' if p.fuel < 5 else 'yellow' if p.fuel < 15 else 'green'}]{p.fuel:.1f}[/] L")
            t.add_row("Boost",   f"{p.turbo_boost:.2f} bar")
            t.add_row("Road T",  f"{p.road_temp:.0f} °C  Air {p.air_temp:.0f} °C")
        return Panel(t, title="[bold]Car Status[/bold]", box=box.ROUNDED)

    def _pedals_panel(self, p: Optional[PhysicsSnapshot]) -> Panel:
        t = Table.grid(padding=(0, 1))
        t.add_column(width=10, justify="right", style="dim")
        t.add_column()
        t.add_column(width=6, justify="right")
        if not p:
            t.add_row("", "[dim]No data[/dim]", "")
        else:
            t.add_row("Throttle", _pbar(p.throttle, 22, "green"),  f"{p.throttle*100:.0f}%")
            t.add_row("Brake",    _pbar(p.brake,    22, "red"),    f"{p.brake*100:.0f}%")
            t.add_row("Clutch",   _pbar(p.clutch,   22, "yellow"), f"{p.clutch*100:.0f}%")

            steer_norm = (p.steer_angle + 1.0) / 2.0
            t.add_row("Steer",   _pbar(steer_norm, 22, "cyan"),   f"{p.steer_angle*100:+.0f}")

            tc_color   = "red" if p.tc_intervention > 0.01 else "dim"
            abs_color  = "red" if p.abs_intervention > 0.01 else "dim"
            t.add_row(f"[{tc_color}]TC[/{tc_color}]",
                      f"[{tc_color}]{p.tc_intervention:.3f}[/{tc_color}]", "")
            t.add_row(f"[{abs_color}]ABS[/{abs_color}]",
                      f"[{abs_color}]{p.abs_intervention:.3f}[/{abs_color}]", "")
            t.add_row("BrakeBias", f"{p.brake_bias*100:.1f}%", "")
        return Panel(t, title="[bold]Inputs[/bold]", box=box.ROUNDED)

    def _tyre_panel(self, p: Optional[PhysicsSnapshot],
                    g: Optional[GraphicsSnapshot]) -> Panel:
        t = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        t.add_column("",   style="dim", width=6)
        for lbl in _TYRE_LABELS:
            t.add_column(lbl, justify="right", width=8)
        if not p:
            t.add_row("–", *["[dim]–[/dim]"] * 4)
        else:
            def _row(label, values, fmt, color_fn=None):
                cells = []
                for v in values:
                    color = color_fn(v) if color_fn else "white"
                    cells.append(f"[{color}]{fmt.format(v)}[/{color}]")
                t.add_row(label, *cells)

            _row("TCore", p.tyre_temp_core,   "{:.0f}°",  _temp_color)
            _row("TInner",p.tyre_temp_inner,  "{:.0f}°",  _temp_color)
            _row("TOuter",p.tyre_temp_outer,  "{:.0f}°",  _temp_color)
            _row("Press", p.tyre_pressure,    "{:.1f}",   lambda v: "green" if 26<v<29 else "red")
            _row("Wear",  p.tyre_wear,        "{:.3f}",   lambda v: "red" if v < 0.3 else "yellow" if v < 0.6 else "green")
            _row("Slip",  p.wheel_slip,       "{:.3f}",   _slip_color)
            _row("BTemp", p.brake_temp,       "{:.0f}°",  lambda v: "red" if v>600 else "yellow" if v>400 else "green")
        compound = g.tyre_compound if g else "–"
        return Panel(t, title=f"[bold]Tyres[/bold]  [{compound}]", box=box.ROUNDED)

    def _timing_panel(self, g: Optional[GraphicsSnapshot]) -> Panel:
        t = Table.grid(padding=(0, 2))
        t.add_column(justify="right", style="dim")
        t.add_column(justify="left")
        if not g:
            t.add_row("Status", "[dim]No data[/dim]")
        else:
            valid_col = "green" if g.is_valid_lap else "red"
            delta_col = "red" if g.is_delta_positive else "green"
            t.add_row("Lap",     f"[bold white]{g.completed_laps + 1}[/bold white]")
            t.add_row("Current", f"[bold white]{_ms_to_laptime(g.current_lap_ms)}[/bold white]  [{valid_col}]{'VALID' if g.is_valid_lap else 'INVALID'}[/{valid_col}]")
            t.add_row("Last",    _ms_to_laptime(g.last_lap_ms))
            t.add_row("Best",    f"[bold cyan]{_ms_to_laptime(g.best_lap_ms)}[/bold cyan]")
            t.add_row("Delta",   f"[{delta_col}]{_delta_str(g.delta_lap_ms, g.is_delta_positive)}[/{delta_col}]")
            t.add_row("Sector",  f"S{g.sector_index + 1}   last {_ms_to_laptime(g.last_sector_ms)}")
            t.add_row("Pos",     f"P{g.position}")

            time_left = g.session_time_left
            h = int(time_left // 3600)
            m = int((time_left % 3600) // 60)
            sec = int(time_left % 60)
            t.add_row("Time left", f"{h:02d}:{m:02d}:{sec:02d}")
            t.add_row("Fuel/Lap",  f"{g.fuel_x_lap:.2f} L  ({g.fuel_est_laps:.1f} laps)")

            if g.gap_ahead_ms != 0:
                t.add_row("Gap Ahd",  f"{g.gap_ahead_ms / 1000.0:.3f}s")
            if g.gap_behind_ms != 0:
                t.add_row("Gap Bhd",  f"{g.gap_behind_ms / 1000.0:.3f}s")
        return Panel(t, title="[bold]Timing[/bold]", box=box.ROUNDED)

    def _footer_panel(self, p, g) -> Panel:
        if not p or not g:
            return Panel("[dim]Starting...[/dim]")
        pit = "[yellow]IN PIT[/yellow]" if g.is_in_pit else ("[dim]pit lane[/dim]" if g.is_in_pit_lane else "")
        drs = "[green]DRS[/green]" if p.drs > 0.5 else ""
        track_pct = f"Pos {g.normalized_pos * 100:.1f}%"
        parts = [s for s in [pit, drs, track_pct] if s]
        return Panel("  ·  ".join(parts) if parts else "[dim]On track[/dim]",
                     box=box.HORIZONTALS)