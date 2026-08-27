"""
IoT sensor simulator — posts temperature, pressure, and humidity readings
to the ingestion service every few seconds with realistic Gaussian noise.
"""

from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime, timezone

import requests

INGEST_URL = os.getenv("INGEST_URL", "http://localhost:8000/ingest")
SENSOR_ID = os.getenv("SENSOR_ID", "plant-1")
INTERVAL_SECONDS = float(os.getenv("INTERVAL_SECONDS", "2"))

# Baseline values with noise std-dev so the series looks realistic
BASELINES: dict[str, tuple[float, float]] = {
    "temperature": (25.0, 0.8),   # °C
    "pressure": (1013.0, 2.5),    # hPa
    "humidity": (55.0, 3.0),      # %
}


def sample(metric: str) -> float:
    baseline, sigma = BASELINES[metric]
    return round(random.gauss(baseline, sigma), 2)


def post_reading(metric: str, value: float) -> None:
    payload = {
        "sensor_id": SENSOR_ID,
        "metric": metric,
        "value": value,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    resp = requests.post(INGEST_URL, json=payload, timeout=5)
    resp.raise_for_status()
    print(f"OK  {payload['timestamp']}  {metric}={value}", flush=True)


def main() -> None:
    print(f"Simulator starting → {INGEST_URL}  sensor_id={SENSOR_ID}  interval={INTERVAL_SECONDS}s")
    print("Metrics:", ", ".join(BASELINES.keys()))
    print("Ctrl+C to stop.\n")

    while True:
        for metric in BASELINES:
            try:
                post_reading(metric, sample(metric))
            except requests.RequestException as exc:
                print(f"ERR {metric}: {exc}", file=sys.stderr, flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
