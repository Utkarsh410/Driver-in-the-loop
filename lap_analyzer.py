"""
lap_analyzer.py
---------------
Post-session analysis tools.  Works on the CSV / SQLite files written by
TelemetryLogger.  All plots are returned as matplotlib figures — call
fig.show() or fig.savefig() as required.

Key functions:
    load_session(session_dir)          → LapSession
    LapSession.lap_overview()          → lap-time bar chart + sector breakdown
    LapSession.compare_laps(n, m)      → overlay speed/brake/throttle traces
    LapSession.tyre_degradation()      → tyre temp/wear across laps
    LapSession.gforce_scatter()        → GG diagram (lat vs lon g-force)
    LapSession.brake_point_analysis()  → speed-vs-braking-point map
    LapSession.fuel_strategy()         → fuel consumption per lap
    LapSession.print_summary()         → formatted console table
"""

import os
from pathlib import Path
from typing import Optional, List, Dict

import pandas as pd
import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")          # headless-safe backend
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.ticker import FuncFormatter
    MPL = True
except ImportError:
    MPL = False
    print("[Analyzer] matplotlib not installed. Plots disabled.")


# ── helpers ───────────────────────────────────────────────────────────────────

def _ms_to_laptime(ms: float) -> str:
    if ms <= 0 or np.isnan(ms):
        return "--:--.---"
    ms = int(ms)
    return f"{ms//60000:02d}:{(ms%60000)//1000:02d}.{ms%1000:03d}"

def _lap_formatter(ms, pos):
    return _ms_to_laptime(ms)


# ── session loader ────────────────────────────────────────────────────────────

class LapSession:
    """
    Loaded ACC session.  Exposes raw telemetry DataFrame and lap summary DataFrame.
    """

    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)
        self.telemetry:   Optional[pd.DataFrame] = None
        self.lap_summary: Optional[pd.DataFrame] = None
        self.meta: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        telem_csv = self.session_dir / "telemetry.csv"
        lap_csv   = self.session_dir / "lap_summary.csv"

        if telem_csv.exists():
            self.telemetry = pd.read_csv(telem_csv)
            print(f"[Analyzer] Loaded {len(self.telemetry):,} telemetry frames from {telem_csv.name}")
        else:
            print(f"[Analyzer] telemetry.csv not found in {self.session_dir}")

        if lap_csv.exists():
            self.lap_summary = pd.read_csv(lap_csv)
            print(f"[Analyzer] Loaded {len(self.lap_summary)} lap summaries from {lap_csv.name}")

        db_path = self.session_dir / "session.db"
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT key, value FROM session_info").fetchall()
            self.meta = dict(rows)
            conn.close()

    # ── public API ────────────────────────────────────────────────────────────

    def print_summary(self) -> None:
        if self.lap_summary is None or self.lap_summary.empty:
            print("[Analyzer] No lap summary data available.")
            return
        print(f"\n{'─'*72}")
        print(f"  Session: {self.meta.get('track','?').upper()}  |  {self.meta.get('car_model','?')}")
        print(f"  Driver:  {self.meta.get('player','?')}")
        print(f"{'─'*72}")
        df = self.lap_summary.copy()
        header = f"{'Lap':>4} {'Time':>10} {'V':>2} {'S1':>9} {'S2':>9} {'S3':>9} {'MaxV':>6} {'Fuel':>5} {'BB%':>5} {'Comp':>5}"
        print(header)
        print("─" * 72)
        best_ms = df[df["is_valid"] == 1]["lap_time_ms"].min() if not df.empty else 0
        for _, row in df.iterrows():
            lt_ms = row["lap_time_ms"]
            tag   = "◆" if lt_ms == best_ms and row["is_valid"] else " "
            valid = "✓" if row["is_valid"] else "✗"
            s1    = _ms_to_laptime(row.get("s1_ms", 0))
            s2    = _ms_to_laptime(row.get("s2_ms", 0))
            s3    = _ms_to_laptime(row.get("s3_ms", 0))
            print(f"{tag}{int(row['lap_number']):>3} {_ms_to_laptime(lt_ms):>10} "
                  f"{valid:>2} {s1:>9} {s2:>9} {s3:>9} "
                  f"{row.get('max_speed_kmh',0):>6.1f} "
                  f"{row.get('fuel_used',0):>5.2f} "
                  f"{row.get('avg_brake_bias',0)*100:>5.1f} "
                  f"{str(row.get('tyre_compound','?')):>5}")
        print("─" * 72)

    def lap_overview(self) -> Optional[object]:
        """Bar chart of lap times with sector breakdown."""
        if not MPL or self.lap_summary is None:
            return None
        df = self.lap_summary
        fig, axes = plt.subplots(2, 1, figsize=(12, 8), facecolor="#111111")
        fig.suptitle(f"Lap Overview – {self.meta.get('track','?').upper()}  "
                     f"{self.meta.get('car_model','?')}", color="white", fontsize=13)

        ax1, ax2 = axes
        laps = df["lap_number"]
        colors = ["#2ecc71" if v else "#e74c3c" for v in df["is_valid"]]
        bars = ax1.bar(laps, df["lap_time_ms"] / 1000.0, color=colors, edgecolor="#333", linewidth=0.5)
        if not df.empty:
            best = df[df["is_valid"] == 1]["lap_time_ms"].min() / 1000.0 if df["is_valid"].any() else 0
            ax1.axhline(best, color="#3498db", linewidth=1.0, linestyle="--", label=f"Best: {_ms_to_laptime(int(best*1000))}")
            ax1.legend(framealpha=0.2, labelcolor="white")
        ax1.set_xlabel("Lap", color="white")
        ax1.set_ylabel("Lap time (s)", color="white")
        ax1.tick_params(colors="white")
        ax1.set_facecolor("#1a1a2e")
        for spine in ax1.spines.values(): spine.set_color("#333")

        if {"s1_ms","s2_ms","s3_ms"}.issubset(df.columns):
            s1 = df["s1_ms"] / 1000.0
            s2 = df["s2_ms"] / 1000.0
            s3 = df["s3_ms"] / 1000.0
            ax2.bar(laps, s1, label="S1", color="#e74c3c", edgecolor="#333", linewidth=0.5)
            ax2.bar(laps, s2, bottom=s1, label="S2", color="#f39c12", edgecolor="#333", linewidth=0.5)
            ax2.bar(laps, s3, bottom=s1+s2, label="S3", color="#2ecc71", edgecolor="#333", linewidth=0.5)
            ax2.legend(framealpha=0.2, labelcolor="white")
        ax2.set_xlabel("Lap", color="white")
        ax2.set_ylabel("Sector (s)", color="white")
        ax2.tick_params(colors="white")
        ax2.set_facecolor("#1a1a2e")
        for spine in ax2.spines.values(): spine.set_color("#333")

        plt.tight_layout()
        return fig

    def compare_laps(self, lap_a: int, lap_b: int) -> Optional[object]:
        """Overlay speed, throttle, brake, gear traces for two laps."""
        if not MPL or self.telemetry is None:
            return None
        df = self.telemetry
        a = df[df["current_lap_ms"] > 0].copy()

        # Reconstruct per-lap frames via lap counter
        if "completed_laps" not in df.columns:
            print("[Analyzer] 'completed_laps' column needed for compare_laps.")
            return None

        def _get_lap(n):
            return df[df["completed_laps"] == n].copy().reset_index(drop=True)

        la = _get_lap(lap_a)
        lb = _get_lap(lap_b)
        if la.empty or lb.empty:
            print(f"[Analyzer] Lap data not found for laps {lap_a}/{lap_b}.")
            return None

        fig, axes = plt.subplots(4, 1, figsize=(14, 10), sharex=False, facecolor="#111111")
        fig.suptitle(f"Lap comparison  L{lap_a} vs L{lap_b}  –  "
                     f"{self.meta.get('track','?').upper()}", color="white")
        channels = [
            ("speed_kmh",  "Speed (km/h)",  True),
            ("throttle",   "Throttle",      False),
            ("brake",      "Brake",         False),
            ("gear",       "Gear",          False),
        ]
        colors_a = "#3498db"
        colors_b = "#e74c3c"
        for ax, (col, label, _) in zip(axes, channels):
            if col in la.columns and col in lb.columns:
                t_a = np.linspace(0, 1, len(la))
                t_b = np.linspace(0, 1, len(lb))
                ax.plot(t_a, la[col], color=colors_a, linewidth=1.0, label=f"Lap {lap_a}", alpha=0.9)
                ax.plot(t_b, lb[col], color=colors_b, linewidth=1.0, label=f"Lap {lap_b}", alpha=0.9, linestyle="--")
                ax.set_ylabel(label, color="white", fontsize=9)
                ax.tick_params(colors="white", labelsize=8)
                ax.set_facecolor("#1a1a2e")
                for spine in ax.spines.values(): spine.set_color("#333")
            ax.legend(framealpha=0.2, labelcolor="white", fontsize=8)
        axes[-1].set_xlabel("Normalised lap position", color="white")
        plt.tight_layout()
        return fig

    def tyre_degradation(self) -> Optional[object]:
        """Tyre temperature and wear across all laps."""
        if not MPL or self.lap_summary is None:
            return None
        df = self.lap_summary
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), facecolor="#111111")
        fig.suptitle("Tyre degradation", color="white")
        corners = ["fl", "fr", "rl", "rr"]
        corner_labels = ["Front Left", "Front Right", "Rear Left", "Rear Right"]
        palette = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12"]
        for i, (ax, corner, label, color) in enumerate(zip(axes.flat, corners, corner_labels, palette)):
            temp_col = f"avg_tyre_temp_{corner}"
            pres_col = f"avg_tyre_pressure_{corner}"
            if temp_col in df.columns:
                ax2 = ax.twinx()
                ax.plot(df["lap_number"], df[temp_col], color=color, linewidth=1.5, marker="o", markersize=3)
                ax.set_ylabel("Avg temp (°C)", color=color, fontsize=9)
                ax.tick_params(axis="y", colors=color, labelsize=8)
                if pres_col in df.columns:
                    ax2.plot(df["lap_number"], df[pres_col], color="white", linewidth=1.0,
                             linestyle="--", alpha=0.5, marker="s", markersize=2)
                    ax2.set_ylabel("Pressure (PSI)", color="white", fontsize=8)
                    ax2.tick_params(axis="y", colors="white", labelsize=8)
            ax.set_title(label, color="white", fontsize=10)
            ax.set_xlabel("Lap", color="white", fontsize=8)
            ax.tick_params(axis="x", colors="white", labelsize=8)
            ax.set_facecolor("#1a1a2e")
            for spine in ax.spines.values(): spine.set_color("#333")
        plt.tight_layout()
        return fig

    def gforce_scatter(self, max_samples: int = 5000) -> Optional[object]:
        """GG diagram: lateral g (x) vs longitudinal g (y)."""
        if not MPL or self.telemetry is None:
            return None
        df = self.telemetry.dropna(subset=["g_lat", "g_lon"])
        step = max(1, len(df) // max_samples)
        df = df.iloc[::step]
        fig, ax = plt.subplots(figsize=(7, 7), facecolor="#111111")
        scatter = ax.scatter(df["g_lat"], df["g_lon"],
                             c=df["speed_kmh"], cmap="plasma",
                             s=1, alpha=0.5, linewidths=0)
        cb = plt.colorbar(scatter, ax=ax)
        cb.set_label("Speed (km/h)", color="white")
        cb.ax.yaxis.set_tick_params(color="white")
        plt.setp(cb.ax.yaxis.get_ticklabels(), color="white")
        # friction circle
        theta = np.linspace(0, 2 * np.pi, 300)
        ax.plot(np.cos(theta) * 3.5, np.sin(theta) * 3.5,
                color="#555", linewidth=0.8, linestyle="--")
        ax.axhline(0, color="#444", linewidth=0.5)
        ax.axvline(0, color="#444", linewidth=0.5)
        ax.set_xlabel("Lateral G", color="white")
        ax.set_ylabel("Longitudinal G", color="white")
        ax.set_title("GG Diagram", color="white")
        ax.set_aspect("equal")
        ax.tick_params(colors="white")
        ax.set_facecolor("#0a0a1a")
        for spine in ax.spines.values(): spine.set_color("#333")
        plt.tight_layout()
        return fig

    def fuel_strategy(self) -> Optional[object]:
        """Fuel level over time and per-lap consumption."""
        if not MPL or self.lap_summary is None:
            return None
        df = self.lap_summary
        if "fuel_used" not in df.columns:
            return None
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), facecolor="#111111")
        fig.suptitle("Fuel strategy", color="white")
        ax1.bar(df["lap_number"], df["fuel_used"], color="#f39c12", edgecolor="#333", linewidth=0.5)
        ax1.axhline(df["fuel_used"].mean(), color="white", linestyle="--", linewidth=0.8,
                    label=f"Avg {df['fuel_used'].mean():.2f} L/lap")
        ax1.set_xlabel("Lap", color="white"); ax1.set_ylabel("Fuel used (L)", color="white")
        ax1.legend(framealpha=0.2, labelcolor="white")
        ax1.tick_params(colors="white"); ax1.set_facecolor("#1a1a2e")
        for spine in ax1.spines.values(): spine.set_color("#333")

        cum_fuel = df["fuel_used"].cumsum()
        ax2.fill_between(df["lap_number"], cum_fuel, alpha=0.4, color="#f39c12")
        ax2.plot(df["lap_number"], cum_fuel, color="#f39c12", linewidth=1.5)
        ax2.set_xlabel("Lap", color="white"); ax2.set_ylabel("Cumulative fuel (L)", color="white")
        ax2.tick_params(colors="white"); ax2.set_facecolor("#1a1a2e")
        for spine in ax2.spines.values(): spine.set_color("#333")
        plt.tight_layout()
        return fig

    def brake_point_analysis(self) -> Optional[object]:
        """Speed vs normalised track position, highlighting heavy braking zones."""
        if not MPL or self.telemetry is None:
            return None
        df = self.telemetry.dropna(subset=["normalized_pos", "speed_kmh", "brake"])
        step = max(1, len(df) // 8000)
        df = df.iloc[::step]
        fig, ax = plt.subplots(figsize=(14, 5), facecolor="#111111")
        ax.scatter(df["normalized_pos"], df["speed_kmh"],
                   c=df["brake"], cmap="RdYlGn_r",
                   s=1, alpha=0.6, linewidths=0, vmin=0, vmax=1)
        heavy = df[df["brake"] > 0.7]
        if not heavy.empty:
            ax.scatter(heavy["normalized_pos"], heavy["speed_kmh"],
                       color="#e74c3c", s=3, alpha=0.4, label="Heavy braking")
            ax.legend(framealpha=0.2, labelcolor="white")
        ax.set_xlabel("Track position (normalised)", color="white")
        ax.set_ylabel("Speed (km/h)", color="white")
        ax.set_title("Speed trace with braking intensity", color="white")
        ax.tick_params(colors="white"); ax.set_facecolor("#0a0a1a")
        for spine in ax.spines.values(): spine.set_color("#333")
        plt.tight_layout()
        return fig

    def save_all_plots(self, output_dir: Optional[str] = None) -> List[Path]:
        """Generate and save all analysis plots as PNG files."""
        out = Path(output_dir) if output_dir else self.session_dir / "plots"
        out.mkdir(parents=True, exist_ok=True)
        saved = []
        plots = {
            "lap_overview":         self.lap_overview,
            "tyre_degradation":     self.tyre_degradation,
            "gforce_scatter":       self.gforce_scatter,
            "fuel_strategy":        self.fuel_strategy,
            "brake_point_analysis": self.brake_point_analysis,
        }
        for name, fn in plots.items():
            try:
                fig = fn()
                if fig is not None:
                    path = out / f"{name}.png"
                    fig.savefig(path, dpi=150, bbox_inches="tight",
                                facecolor=fig.get_facecolor())
                    plt.close(fig)
                    saved.append(path)
                    print(f"[Analyzer] Saved {path}")
            except Exception as exc:
                print(f"[Analyzer] {name} failed: {exc}")
        return saved


def load_session(session_dir: str) -> LapSession:
    return LapSession(Path(session_dir))