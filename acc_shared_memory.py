"""
acc_shared_memory.py
--------------------
Reads Assetto Corsa Competizione shared memory maps on Windows.
Maps:  Local\\acpmf_physics   – 60 Hz physics data
       Local\\acpmf_graphics  – session / HUD data
       Local\\acpmf_static    – one-shot session metadata

Usage:
    reader = ACCSharedMemory()
    reader.start()
    snap = reader.snapshot()   # returns (physics, graphics, static) namedtuples
    reader.stop()
"""

import ctypes
import mmap
import struct
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Tuple

# ── ctypes structures matching ACC SDK ──────────────────────────────────────

class _Physics(ctypes.Structure):
    _fields_ = [
        ("packetId",              ctypes.c_int),
        ("gas",                   ctypes.c_float),
        ("brake",                 ctypes.c_float),
        ("fuel",                  ctypes.c_float),
        ("gear",                  ctypes.c_int),
        ("rpms",                  ctypes.c_int),
        ("steerAngle",            ctypes.c_float),
        ("speedKmh",              ctypes.c_float),
        ("velocity",              ctypes.c_float * 3),
        ("accG",                  ctypes.c_float * 3),   # [lateral, longitudinal, vertical]
        ("wheelSlip",             ctypes.c_float * 4),   # FL FR RL RR
        ("wheelLoad",             ctypes.c_float * 4),
        ("wheelsPressure",        ctypes.c_float * 4),
        ("wheelAngularSpeed",     ctypes.c_float * 4),
        ("tyreWear",              ctypes.c_float * 4),
        ("tyreDirtyLevel",        ctypes.c_float * 4),
        ("tyreCoreTemperature",   ctypes.c_float * 4),
        ("camberRAD",             ctypes.c_float * 4),
        ("suspensionTravel",      ctypes.c_float * 4),
        ("drs",                   ctypes.c_float),
        ("tc",                    ctypes.c_float),
        ("heading",               ctypes.c_float),
        ("pitch",                 ctypes.c_float),
        ("roll",                  ctypes.c_float),
        ("cgHeight",              ctypes.c_float),
        ("carDamage",             ctypes.c_float * 5),
        ("numberOfTyresOut",      ctypes.c_int),
        ("pitLimiterOn",          ctypes.c_int),
        ("abs",                   ctypes.c_float),
        ("kersCharge",            ctypes.c_float),
        ("kersInput",             ctypes.c_float),
        ("autoShifterOn",         ctypes.c_int),
        ("rideHeight",            ctypes.c_float * 2),
        ("turboBoost",            ctypes.c_float),
        ("ballast",               ctypes.c_float),
        ("airDensity",            ctypes.c_float),
        ("airTemp",               ctypes.c_float),
        ("roadTemp",              ctypes.c_float),
        ("localAngularVel",       ctypes.c_float * 3),
        ("finalFF",               ctypes.c_float),
        ("performanceMeter",      ctypes.c_float),
        ("engineBrake",           ctypes.c_int),
        ("ersRecoveryLevel",      ctypes.c_int),
        ("ersPowerLevel",         ctypes.c_int),
        ("ersHeatCharging",       ctypes.c_int),
        ("ersIsCharging",         ctypes.c_int),
        ("kersCurrentKJ",         ctypes.c_float),
        ("drsAvailable",          ctypes.c_int),
        ("drsEnabled",            ctypes.c_int),
        ("brakeTemp",             ctypes.c_float * 4),
        ("clutch",                ctypes.c_float),
        ("tyreTempI",             ctypes.c_float * 4),
        ("tyreTempM",             ctypes.c_float * 4),
        ("tyreTempO",             ctypes.c_float * 4),
        ("isAIControlled",        ctypes.c_int),
        ("tyreContactPoint",      ctypes.c_float * 12),
        ("tyreContactNormal",     ctypes.c_float * 12),
        ("tyreContactHeading",    ctypes.c_float * 12),
        ("brakeBias",             ctypes.c_float),
        ("localVelocity",         ctypes.c_float * 3),
    ]


class _Graphics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId",              ctypes.c_int),
        ("status",                ctypes.c_int),   # 0=off 1=replay 2=live 3=pause
        ("session",               ctypes.c_int),   # 0=unknown 1=practice 2=qual 3=race
        ("currentTime",           ctypes.c_wchar * 15),
        ("lastTime",              ctypes.c_wchar * 15),
        ("bestTime",              ctypes.c_wchar * 15),
        ("split",                 ctypes.c_wchar * 15),
        ("completedLaps",         ctypes.c_int),
        ("position",              ctypes.c_int),
        ("iCurrentTime",          ctypes.c_int),   # ms
        ("iLastTime",             ctypes.c_int),
        ("iBestTime",             ctypes.c_int),
        ("sessionTimeLeft",       ctypes.c_float),
        ("distanceTraveled",      ctypes.c_float),
        ("isInPit",               ctypes.c_int),
        ("currentSectorIndex",    ctypes.c_int),
        ("lastSectorTime",        ctypes.c_int),
        ("numberOfLaps",          ctypes.c_int),
        ("tyreCompound",          ctypes.c_wchar * 33),
        ("replayTimeMultiplier",  ctypes.c_float),
        ("normalizedCarPosition", ctypes.c_float),
        ("activeCars",            ctypes.c_int),
        ("carCoordinates",        ctypes.c_float * 180),
        ("carID",                 ctypes.c_int * 60),
        ("playerCarID",           ctypes.c_int),
        ("penaltyTime",           ctypes.c_float),
        ("flag",                  ctypes.c_int),
        ("penalty",               ctypes.c_int),
        ("idealLineOn",           ctypes.c_int),
        ("isInPitLane",           ctypes.c_int),
        ("surfaceGrip",           ctypes.c_float),
        ("mandatoryPitDone",      ctypes.c_int),
        ("windSpeed",             ctypes.c_float),
        ("windDirection",         ctypes.c_float),
        ("isSetupMenuVisible",    ctypes.c_int),
        ("mainDisplayIndex",      ctypes.c_int),
        ("secondaryDisplayIndex", ctypes.c_int),
        ("tc",                    ctypes.c_int),
        ("tcCut",                 ctypes.c_int),
        ("engineMap",             ctypes.c_int),
        ("abs",                   ctypes.c_int),
        ("fuelXLap",              ctypes.c_float),
        ("rainLights",            ctypes.c_int),
        ("flashingLights",        ctypes.c_int),
        ("lightsStage",           ctypes.c_int),
        ("exhaustTemperature",    ctypes.c_float),
        ("wiperLV",               ctypes.c_int),
        ("driverStintTotalTimeLeft",  ctypes.c_int),
        ("driverStintTimeLeft",   ctypes.c_int),
        ("rainTyres",             ctypes.c_int),
        ("sessionIndex",          ctypes.c_int),
        ("usedFuel",              ctypes.c_float),
        ("deltaLapTime",          ctypes.c_wchar * 15),
        ("iDeltaLapTime",         ctypes.c_int),
        ("estimatedLapTime",      ctypes.c_wchar * 15),
        ("iEstimatedLapTime",     ctypes.c_int),
        ("isDeltaPositive",       ctypes.c_int),
        ("iSplit",                ctypes.c_int),
        ("isValidLap",            ctypes.c_int),
        ("fuelEstimatedLaps",     ctypes.c_float),
        ("trackStatus",           ctypes.c_wchar * 33),
        ("missingMandatoryPits",  ctypes.c_int),
        ("clock",                 ctypes.c_float),
        ("directionLightsLeft",   ctypes.c_int),
        ("directionLightsRight",  ctypes.c_int),
        ("globalYellow",          ctypes.c_int),
        ("globalYellow1",         ctypes.c_int),
        ("globalYellow2",         ctypes.c_int),
        ("globalYellow3",         ctypes.c_int),
        ("globalWhite",           ctypes.c_int),
        ("globalGreen",           ctypes.c_int),
        ("globalChequered",       ctypes.c_int),
        ("globalRed",             ctypes.c_int),
        ("mfdTyreSet",            ctypes.c_int),
        ("mfdFuelToAdd",          ctypes.c_float),
        ("mfdTyrePressureLF",     ctypes.c_float),
        ("mfdTyrePressureRF",     ctypes.c_float),
        ("mfdTyrePressureLR",     ctypes.c_float),
        ("mfdTyrePressureRR",     ctypes.c_float),
        ("trackGripStatus",       ctypes.c_int),
        ("rainIntensity",         ctypes.c_int),
        ("rainIntensityIn10min",  ctypes.c_int),
        ("rainIntensityIn30min",  ctypes.c_int),
        ("currentTyreSet",        ctypes.c_int),
        ("strategyTyreSet",       ctypes.c_int),
        ("gapAhead",              ctypes.c_int),
        ("gapBehind",             ctypes.c_int),
    ]


class _Static(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("smVersion",             ctypes.c_wchar * 15),
        ("acVersion",             ctypes.c_wchar * 15),
        ("numberOfSessions",      ctypes.c_int),
        ("numCars",               ctypes.c_int),
        ("carModel",              ctypes.c_wchar * 33),
        ("track",                 ctypes.c_wchar * 33),
        ("playerName",            ctypes.c_wchar * 33),
        ("playerSurname",         ctypes.c_wchar * 33),
        ("playerNick",            ctypes.c_wchar * 33),
        ("sectorCount",           ctypes.c_int),
        ("maxTorque",             ctypes.c_float),
        ("maxPower",              ctypes.c_float),
        ("maxRpm",                ctypes.c_int),
        ("maxFuel",               ctypes.c_float),
        ("suspensionMaxTravel",   ctypes.c_float * 4),
        ("tyreRadius",            ctypes.c_float * 4),
        ("maxTurboBoost",         ctypes.c_float),
        ("deprecated1",           ctypes.c_float),
        ("deprecated2",           ctypes.c_float),
        ("penaltiesEnabled",      ctypes.c_int),
        ("aidFuelRate",           ctypes.c_float),
        ("aidTireRate",           ctypes.c_float),
        ("aidMechanicalDamage",   ctypes.c_float),
        ("aidAllowTyreBlankets",  ctypes.c_int),
        ("aidStability",          ctypes.c_float),
        ("aidAutoClutch",         ctypes.c_int),
        ("aidAutoBlip",           ctypes.c_int),
        ("hasDRS",                ctypes.c_int),
        ("hasERS",                ctypes.c_int),
        ("hasKERS",               ctypes.c_int),
        ("kersMaxJ",              ctypes.c_float),
        ("engineBrakeSettingsCount", ctypes.c_int),
        ("ersPowerControllerCount",  ctypes.c_int),
        ("trackSPlineLength",     ctypes.c_float),
        ("trackConfiguration",    ctypes.c_wchar * 33),
        ("ersMaxJ",               ctypes.c_float),
        ("isTimedRace",           ctypes.c_int),
        ("hasExtraLap",           ctypes.c_int),
        ("carSkin",               ctypes.c_wchar * 33),
        ("reversedGridPositions", ctypes.c_int),
        ("pitWindowStart",        ctypes.c_int),
        ("pitWindowEnd",          ctypes.c_int),
        ("isOnline",              ctypes.c_int),
        ("dryTyresName",          ctypes.c_wchar * 33),
        ("wetTyresName",          ctypes.c_wchar * 33),
    ]


# ── Friendly snapshot dataclasses ────────────────────────────────────────────

@dataclass
class PhysicsSnapshot:
    timestamp:        float
    speed_kmh:        float
    gear:             int
    rpms:             int
    throttle:         float
    brake:            float
    clutch:           float
    steer_angle:      float
    # g-forces  [lateral, longitudinal, vertical]
    g_lat:            float
    g_lon:            float
    g_vert:           float
    # tyres  order: FL FR RL RR
    tyre_temp_core:   list
    tyre_temp_inner:  list
    tyre_temp_middle: list
    tyre_temp_outer:  list
    tyre_pressure:    list
    tyre_wear:        list
    wheel_slip:       list
    suspension_travel: list
    # brakes
    brake_temp:       list
    brake_bias:       float
    # misc
    fuel:             float
    turbo_boost:      float
    tc_intervention:  float
    abs_intervention: float
    drs:              float
    air_temp:         float
    road_temp:        float
    heading:          float
    pitch:            float
    roll:             float


@dataclass
class GraphicsSnapshot:
    timestamp:            float
    status:               int
    session_type:         int
    completed_laps:       int
    position:             int
    current_lap_ms:       int
    last_lap_ms:          int
    best_lap_ms:          int
    sector_index:         int
    last_sector_ms:       int
    normalized_pos:       float
    session_time_left:    float
    distance_traveled:    float
    is_in_pit:            bool
    is_in_pit_lane:       bool
    is_valid_lap:         bool
    tyre_compound:        str
    delta_lap_ms:         int
    is_delta_positive:    bool
    fuel_x_lap:           float
    fuel_used:            float
    fuel_est_laps:        float
    gap_ahead_ms:         int
    gap_behind_ms:        int


@dataclass
class StaticSnapshot:
    car_model:     str
    track:         str
    player_name:   str
    player_nick:   str
    sector_count:  int
    max_rpm:       int
    max_fuel:      float
    track_length:  float
    dry_tyre_name: str
    wet_tyre_name: str


# ── Reader class ──────────────────────────────────────────────────────────────

class ACCSharedMemory:
    """
    Opens and reads ACC shared memory maps.
    Falls back to a mock/demo mode when not running on Windows or
    when ACC is not open, so development works cross-platform.
    """

    _PHYSICS_MAP  = "Local\\acpmf_physics"
    _GRAPHICS_MAP = "Local\\acpmf_graphics"
    _STATIC_MAP   = "Local\\acpmf_static"

    def __init__(self, mock: bool = False):
        self._mock    = mock
        self._running = False
        self._lock    = threading.Lock()
        self._phy_mm  = None
        self._grp_mm  = None
        self._sta_mm  = None
        self._physics_snap  : Optional[PhysicsSnapshot]  = None
        self._graphics_snap : Optional[GraphicsSnapshot] = None
        self._static_snap   : Optional[StaticSnapshot]   = None
        self._thread: Optional[threading.Thread] = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self, poll_hz: int = 60) -> None:
        """Open shared memory and start background polling thread."""
        import platform
        if platform.system() != "Windows":
            print("[ACCSharedMemory] Non-Windows OS detected – enabling mock mode.")
            self._mock = True

        if not self._mock:
            try:
                self._phy_mm = mmap.mmap(-1, ctypes.sizeof(_Physics),  tagname=self._PHYSICS_MAP,  access=mmap.ACCESS_READ)
                self._grp_mm = mmap.mmap(-1, ctypes.sizeof(_Graphics), tagname=self._GRAPHICS_MAP, access=mmap.ACCESS_READ)
                self._sta_mm = mmap.mmap(-1, ctypes.sizeof(_Static),   tagname=self._STATIC_MAP,   access=mmap.ACCESS_READ)
                print("[ACCSharedMemory] Connected to ACC shared memory.")
            except Exception as exc:
                print(f"[ACCSharedMemory] Could not open shared memory ({exc}) – enabling mock mode.")
                self._mock = True

        self._running = True
        self._interval = 1.0 / poll_hz
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        for mm in (self._phy_mm, self._grp_mm, self._sta_mm):
            if mm:
                mm.close()

    # ── snapshot access ──────────────────────────────────────────────────────

    def snapshot(self) -> Tuple[Optional[PhysicsSnapshot],
                                Optional[GraphicsSnapshot],
                                Optional[StaticSnapshot]]:
        with self._lock:
            return self._physics_snap, self._graphics_snap, self._static_snap

    # ── internal polling ─────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            t0 = time.perf_counter()
            if self._mock:
                self._generate_mock()
            else:
                self._read_real()
            elapsed = time.perf_counter() - t0
            sleep_t = max(0.0, self._interval - elapsed)
            time.sleep(sleep_t)

    def _read_real(self) -> None:
        """Read all three shared memory maps."""
        try:
            now = time.time()
            # Physics
            self._phy_mm.seek(0)
            raw_phy = self._phy_mm.read(ctypes.sizeof(_Physics))
            phy = _Physics.from_buffer_copy(raw_phy)

            # Graphics
            self._grp_mm.seek(0)
            raw_grp = self._grp_mm.read(ctypes.sizeof(_Graphics))
            grp = _Graphics.from_buffer_copy(raw_grp)

            # Static
            self._sta_mm.seek(0)
            raw_sta = self._sta_mm.read(ctypes.sizeof(_Static))
            sta = _Static.from_buffer_copy(raw_sta)

            with self._lock:
                self._physics_snap  = self._parse_physics(phy, now)
                self._graphics_snap = self._parse_graphics(grp, now)
                self._static_snap   = self._parse_static(sta)
        except Exception as exc:
            print(f"[ACCSharedMemory] Read error: {exc}")

    @staticmethod
    def _parse_physics(p: _Physics, t: float) -> PhysicsSnapshot:
        return PhysicsSnapshot(
            timestamp=t,
            speed_kmh=p.speedKmh,
            gear=p.gear,
            rpms=p.rpms,
            throttle=p.gas,
            brake=p.brake,
            clutch=p.clutch,
            steer_angle=p.steerAngle,
            g_lat=p.accG[0],
            g_lon=p.accG[1],
            g_vert=p.accG[2],
            tyre_temp_core=[p.tyreCoreTemperature[i] for i in range(4)],
            tyre_temp_inner=[p.tyreTempI[i] for i in range(4)],
            tyre_temp_middle=[p.tyreTempM[i] for i in range(4)],
            tyre_temp_outer=[p.tyreTempO[i] for i in range(4)],
            tyre_pressure=[p.wheelsPressure[i] for i in range(4)],
            tyre_wear=[p.tyreWear[i] for i in range(4)],
            wheel_slip=[p.wheelSlip[i] for i in range(4)],
            suspension_travel=[p.suspensionTravel[i] for i in range(4)],
            brake_temp=[p.brakeTemp[i] for i in range(4)],
            brake_bias=p.brakeBias,
            fuel=p.fuel,
            turbo_boost=p.turboBoost,
            tc_intervention=p.tc,
            abs_intervention=p.abs,
            drs=p.drs,
            air_temp=p.airTemp,
            road_temp=p.roadTemp,
            heading=p.heading,
            pitch=p.pitch,
            roll=p.roll,
        )

    @staticmethod
    def _parse_graphics(g: _Graphics, t: float) -> GraphicsSnapshot:
        return GraphicsSnapshot(
            timestamp=t,
            status=g.status,
            session_type=g.session,
            completed_laps=g.completedLaps,
            position=g.position,
            current_lap_ms=g.iCurrentTime,
            last_lap_ms=g.iLastTime,
            best_lap_ms=g.iBestTime,
            sector_index=g.currentSectorIndex,
            last_sector_ms=g.lastSectorTime,
            normalized_pos=g.normalizedCarPosition,
            session_time_left=g.sessionTimeLeft,
            distance_traveled=g.distanceTraveled,
            is_in_pit=bool(g.isInPit),
            is_in_pit_lane=bool(g.isInPitLane),
            is_valid_lap=bool(g.isValidLap),
            tyre_compound=g.tyreCompound.strip(),
            delta_lap_ms=g.iDeltaLapTime,
            is_delta_positive=bool(g.isDeltaPositive),
            fuel_x_lap=g.fuelXLap,
            fuel_used=g.usedFuel,
            fuel_est_laps=g.fuelEstimatedLaps,
            gap_ahead_ms=g.gapAhead,
            gap_behind_ms=g.gapBehind,
        )

    @staticmethod
    def _parse_static(s: _Static) -> StaticSnapshot:
        return StaticSnapshot(
            car_model=s.carModel.strip(),
            track=s.track.strip(),
            player_name=(s.playerName + " " + s.playerSurname).strip(),
            player_nick=s.playerNick.strip(),
            sector_count=s.sectorCount,
            max_rpm=s.maxRpm,
            max_fuel=s.maxFuel,
            track_length=s.trackSPlineLength,
            dry_tyre_name=s.dryTyresName.strip(),
            wet_tyre_name=s.wetTyresName.strip(),
        )

    # ── mock data generator ──────────────────────────────────────────────────

    def _generate_mock(self) -> None:
        """Simulate a plausible ACC telemetry stream for testing."""
        import math
        t = time.time()
        cycle = t % 90.0       # 90-second lap simulation
        speed = 80 + 120 * abs(math.sin(cycle * 0.12))
        rpm   = int(4000 + 4000 * abs(math.sin(cycle * 0.13)))
        on_throttle = math.sin(cycle * 0.12) > 0

        phy = PhysicsSnapshot(
            timestamp=t,
            speed_kmh=round(speed, 2),
            gear=max(1, min(7, int(speed / 50) + 1)),
            rpms=rpm,
            throttle=round(max(0.0, math.sin(cycle * 0.12)), 3),
            brake=round(max(0.0, -math.sin(cycle * 0.12) * 0.8), 3),
            clutch=0.0,
            steer_angle=round(math.sin(cycle * 0.3) * 0.3, 4),
            g_lat=round(math.sin(cycle * 0.3) * 2.5, 3),
            g_lon=round(math.sin(cycle * 0.12) * 1.8, 3),
            g_vert=round(1.0 + math.sin(cycle * 0.6) * 0.15, 3),
            tyre_temp_core=[round(85 + 15 * abs(math.sin(cycle * 0.05 + i)), 1) for i in range(4)],
            tyre_temp_inner=[round(90 + 10 * abs(math.sin(cycle * 0.05 + i)), 1) for i in range(4)],
            tyre_temp_middle=[round(87 + 12 * abs(math.sin(cycle * 0.05 + i)), 1) for i in range(4)],
            tyre_temp_outer=[round(84 + 14 * abs(math.sin(cycle * 0.05 + i)), 1) for i in range(4)],
            tyre_pressure=[round(27.5 + 0.5 * math.sin(cycle * 0.02 + i), 2) for i in range(4)],
            tyre_wear=[round(max(0.0, 1.0 - cycle / 3600.0 - i * 0.002), 4) for i in range(4)],
            wheel_slip=[round(max(0.0, math.sin(cycle * 0.2) * 0.05), 4) for i in range(4)],
            suspension_travel=[round(0.05 + 0.02 * math.sin(cycle * 0.8 + i), 4) for i in range(4)],
            brake_temp=[round(200 + 150 * max(0, -math.sin(cycle * 0.12)), 1) for i in range(4)],
            brake_bias=round(0.56 + 0.01 * math.sin(cycle * 0.1), 3),
            fuel=round(max(0, 62.0 - cycle * 0.042), 2),
            turbo_boost=round(1.0 + 0.3 * max(0, math.sin(cycle * 0.12)), 3),
            tc_intervention=0.0,
            abs_intervention=round(max(0, -math.sin(cycle * 0.12)) * 0.2, 3),
            drs=0.0,
            air_temp=25.0,
            road_temp=35.0,
            heading=round(math.sin(cycle * 0.07) * 3.14, 4),
            pitch=round(math.sin(cycle * 0.12) * 0.05, 4),
            roll=round(math.sin(cycle * 0.3) * 0.03, 4),
        )

        lap_ms = int((cycle / 90.0) * 120_000)
        grp = GraphicsSnapshot(
            timestamp=t,
            status=2,
            session_type=3,
            completed_laps=int(t / 90.0) % 20,
            position=1,
            current_lap_ms=lap_ms,
            last_lap_ms=118_542,
            best_lap_ms=117_832,
            sector_index=int(cycle / 30.0) % 3,
            last_sector_ms=39_200,
            normalized_pos=round(cycle / 90.0, 4),
            session_time_left=max(0.0, 3600.0 - t % 3600.0),
            distance_traveled=round(cycle * 64.0, 1),
            is_in_pit=False,
            is_in_pit_lane=False,
            is_valid_lap=True,
            tyre_compound="DHF",
            delta_lap_ms=int(math.sin(cycle * 0.05) * 500),
            is_delta_positive=math.sin(cycle * 0.05) > 0,
            fuel_x_lap=3.8,
            fuel_used=round(cycle * 0.042, 2),
            fuel_est_laps=round(max(0, 62.0 - cycle * 0.042) / 3.8, 1),
            gap_ahead_ms=0,
            gap_behind_ms=0,
        )

        sta = StaticSnapshot(
            car_model="ferrari_296_gt3",
            track="monza",
            player_name="Demo Driver",
            player_nick="DEMO",
            sector_count=3,
            max_rpm=8500,
            max_fuel=62.0,
            track_length=5793.0,
            dry_tyre_name="DHF",
            wet_tyre_name="WH",
        )

        with self._lock:
            self._physics_snap  = phy
            self._graphics_snap = grp
            self._static_snap   = sta