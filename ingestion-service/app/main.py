"""
IoT ingestion service: dual-write to Redis TimeSeries (hot) and InfluxDB (cold).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from pydantic import BaseModel, Field
from redis import Redis
from redis.exceptions import ResponseError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
# 1 hour retention in milliseconds (Redis TimeSeries RETENTION is in ms)
REDIS_RETENTION_MS = int(os.getenv("REDIS_RETENTION_MS", "3600000"))

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "iot-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "iot_data")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "iot-super-secret-token")

METRICS = ("temperature", "pressure", "humidity")

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

redis_client = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

influx_client = InfluxDBClient(
    url=INFLUXDB_URL,
    token=INFLUXDB_TOKEN,
    org=INFLUXDB_ORG,
)
write_api = influx_client.write_api(write_options=SYNCHRONOUS)
query_api = influx_client.query_api()

app = FastAPI(
    title="IoT Ingestion Service",
    description="Dual-writes sensor readings to Redis TimeSeries (hot) and InfluxDB (cold).",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SensorReading(BaseModel):
    sensor_id: str = Field(..., examples=["plant-1"])
    metric: str = Field(..., examples=["temperature"])
    value: float = Field(..., examples=[25.4])
    timestamp: datetime = Field(..., examples=["2026-08-21T07:23:01Z"])


# ---------------------------------------------------------------------------
# Redis TimeSeries helpers
# ---------------------------------------------------------------------------


def ts_key(sensor_id: str, metric: str) -> str:
    return f"sensor:{sensor_id}:{metric}"


def ensure_timeseries(key: str, sensor_id: str, metric: str) -> None:
    """Create a Redis TimeSeries key with 1h retention if it does not exist."""
    try:
        redis_client.execute_command(
            "TS.CREATE",
            key,
            "RETENTION",
            REDIS_RETENTION_MS,
            "DUPLICATE_POLICY",
            "LAST",
            "LABELS",
            "sensor_id",
            sensor_id,
            "metric",
            metric,
        )
    except ResponseError as exc:
        # Redis Stack: "TSDB: key already exists" — safe to ignore
        if "already exists" not in str(exc).lower():
            raise


def redis_add(sensor_id: str, metric: str, value: float, ts: datetime) -> None:
    key = ts_key(sensor_id, metric)
    ensure_timeseries(key, sensor_id, metric)
    # Redis TimeSeries timestamps are milliseconds since epoch
    ms = int(ts.timestamp() * 1000)
    redis_client.execute_command(
        "TS.ADD",
        key,
        ms,
        value,
        "RETENTION",
        REDIS_RETENTION_MS,
        "ON_DUPLICATE",
        "LAST",
    )


def redis_latest(sensor_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {"sensor_id": sensor_id, "metrics": {}}
    for metric in METRICS:
        key = ts_key(sensor_id, metric)
        try:
            raw = redis_client.execute_command("TS.GET", key)
        except ResponseError:
            raw = None
        if raw:
            # TS.GET returns [timestamp_ms, value]
            ts_ms, val = raw[0], float(raw[1])
            result["metrics"][metric] = {
                "value": val,
                "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
            }
    return result


# ---------------------------------------------------------------------------
# InfluxDB helpers
# ---------------------------------------------------------------------------


def influx_write(sensor_id: str, metric: str, value: float, ts: datetime) -> None:
    point = (
        Point(metric)
        .tag("sensor_id", sensor_id)
        .field("value", float(value))
        .time(ts, WritePrecision.NS)
    )
    write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)


def influx_history(
    sensor_id: str,
    metric: Optional[str] = None,
    hours: int = 24,
) -> list[dict[str, Any]]:
    metric_filter = ""
    if metric:
        metric_filter = f'  |> filter(fn: (r) => r._measurement == "{metric}")\n'

    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r.sensor_id == "{sensor_id}")
{metric_filter}  |> filter(fn: (r) => r._field == "value")
  |> sort(columns: ["_time"])
'''
    tables = query_api.query(flux, org=INFLUXDB_ORG)
    points: list[dict[str, Any]] = []
    for table in tables:
        for record in table.records:
            points.append(
                {
                    "metric": record.get_measurement(),
                    "value": record.get_value(),
                    "timestamp": record.get_time().isoformat() if record.get_time() else None,
                    "sensor_id": record.values.get("sensor_id"),
                }
            )
    return points


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    redis_ok = False
    influx_ok = False
    errors: dict[str, str] = {}

    try:
        redis_ok = redis_client.ping() is True
    except Exception as exc:  # noqa: BLE001
        errors["redis"] = str(exc)

    try:
        # ready check against InfluxDB HTTP API
        health_obj = influx_client.health()
        influx_ok = getattr(health_obj, "status", "") == "pass"
        if not influx_ok:
            errors["influxdb"] = str(getattr(health_obj, "message", health_obj))
    except Exception as exc:  # noqa: BLE001
        errors["influxdb"] = str(exc)

    status = "ok" if redis_ok and influx_ok else "degraded"
    body: dict[str, Any] = {
        "status": status,
        "redis": redis_ok,
        "influxdb": influx_ok,
    }
    if errors:
        body["errors"] = errors
    if status != "ok":
        raise HTTPException(status_code=503, detail=body)
    return body


@app.post("/ingest")
def ingest(reading: SensorReading) -> dict[str, Any]:
    if reading.metric not in METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"metric must be one of {METRICS}",
        )

    ts = reading.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    try:
        redis_add(reading.sensor_id, reading.metric, reading.value, ts)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Redis write failed: {exc}") from exc

    try:
        influx_write(reading.sensor_id, reading.metric, reading.value, ts)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"InfluxDB write failed: {exc}") from exc

    return {
        "status": "accepted",
        "sensor_id": reading.sensor_id,
        "metric": reading.metric,
        "value": reading.value,
        "timestamp": ts.isoformat(),
        "hot": f"redis:{ts_key(reading.sensor_id, reading.metric)}",
        "cold": f"influx:{INFLUXDB_BUCKET}/{reading.metric}",
    }


@app.get("/latest/{sensor_id}")
def latest(sensor_id: str) -> dict[str, Any]:
    """Hot path: most recent values from Redis TimeSeries."""
    data = redis_latest(sensor_id)
    if not data["metrics"]:
        raise HTTPException(status_code=404, detail=f"No Redis data for sensor_id={sensor_id}")
    return data


@app.get("/history/{sensor_id}")
def history(
    sensor_id: str,
    metric: Optional[str] = Query(None, description="Optional metric filter"),
    hours: int = Query(24, ge=1, le=168, description="Lookback window in hours"),
) -> dict[str, Any]:
    """Cold path: historical range from InfluxDB."""
    if metric is not None and metric not in METRICS:
        raise HTTPException(status_code=400, detail=f"metric must be one of {METRICS}")
    try:
        points = influx_history(sensor_id, metric=metric, hours=hours)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"InfluxDB query failed: {exc}") from exc
    return {
        "sensor_id": sensor_id,
        "metric": metric,
        "hours": hours,
        "count": len(points),
        "points": points,
    }
