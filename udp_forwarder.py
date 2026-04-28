# udp_forwarder.py - runs on the ACC gaming PC
import socket
import time
import json
from acc_shared_memory import ACCSharedMemory

REMOTE_IP = "192.168.29.91"  # <- IP address of the analyzer PC
REMOTE_PORT = 9996
SEND_HZ = 60

sm = ACCSharedMemory()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sm.start(poll_hz=SEND_HZ)

print(f"Forwarding ACC telemetry -> {REMOTE_IP}:{REMOTE_PORT} at {SEND_HZ} Hz")

interval = 1.0 / SEND_HZ

while True:
    t0 = time.perf_counter()

    phy, grp, sta = sm.snapshot()

    if phy and grp:
        try:
            payload = json.dumps({
                "phy": vars(phy),
                "grp": vars(grp),
                "sta": vars(sta) if sta else {},
            }, default=str).encode()  # default=str prevents crashes

            sock.sendto(payload, (REMOTE_IP, REMOTE_PORT))

        except Exception as e:
            print("Serialization error:", e)

    time.sleep(max(0, interval - (time.perf_counter() - t0)))