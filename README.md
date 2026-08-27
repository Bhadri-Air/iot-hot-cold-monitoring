# IoT Hot/Cold Dual-Write Dashboard

Simulated IoT sensors continuously produce temperature, pressure, and humidity readings. An ingestion service **dual-writes** each reading to:

- **Redis TimeSeries (hot)** — last 1 hour, low-latency “latest value” reads with automatic retention expiry
- **InfluxDB 2.x (cold)** — full history for long-range trend queries

Grafana visualizes the cold path live. This mirrors a common industrial pattern (and a smaller version of Redis TimeSeries + InfluxDB work often done in asset/telemetry platforms).

## Architecture

```
  Sensor Simulator                 Ingestion (FastAPI :8000)
  (simulate.py)  ──POST /ingest──►  ├─ TS.ADD ──► Redis TimeSeries (hot, 1h)
                                    └─ write   ──► InfluxDB bucket iot_data (cold)
                                                          │
                                                          ▼
                                                   Grafana (:3000)
```

| Path | Store | Window | Used for |
|------|--------|--------|----------|
| Hot | Redis TimeSeries | Last 1 hour (`RETENTION 3600000` ms) | `GET /latest/{sensor_id}` |
| Cold | InfluxDB | Full history (demo bucket) | `GET /history/{sensor_id}`, Grafana panels |

## Quick start

### Prerequisites

- Docker Desktop / Docker Compose
- Python 3.10+ (for the host-side simulator only)

### 1. Configure env

```bash
cp .env.example .env
```

Defaults are fine for local demos (`INFLUXDB_TOKEN=iot-super-secret-token`, etc.).

### 2. Start the stack

```bash
docker compose up -d --build
```

Wait until containers are healthy (`docker compose ps`). Services:

| Service | URL |
|---------|-----|
| Ingestion API | http://localhost:8000/docs |
| Grafana | http://localhost:3000 (admin / admin) |
| InfluxDB | http://localhost:8086 |
| Redis | localhost:6379 |

Health check:

```bash
curl http://localhost:8000/health
```

### 3. Run the sensor simulator

```bash
cd simulator
pip install -r requirements.txt
python simulate.py
```

Every 2 seconds it POSTs three readings (`temperature`, `pressure`, `humidity`) for `sensor_id=plant-1`.

### 4. Open Grafana

1. Open http://localhost:3000 and log in (`admin` / `admin`)
2. Open **Dashboards → IoT Hot/Cold Dual-Write Dashboard**
3. Panels refresh every 5s against the InfluxDB Flux datasource (auto-provisioned)

### Useful API calls

```bash
# Hot path — Redis TimeSeries
curl http://localhost:8000/latest/plant-1

# Cold path — InfluxDB last 24h
curl "http://localhost:8000/history/plant-1?hours=24"

# Filter one metric
curl "http://localhost:8000/history/plant-1?metric=temperature&hours=6"
```

## Design decisions

### Why dual-write instead of a longer Redis TTL?

Redis TimeSeries is excellent for **sub-millisecond recent reads** and built-in retention/downsampling. Stretching Redis to hold 7 days of high-cardinality IoT points is the wrong tool: memory cost grows linearly, and long range scans are not what Redis is optimized for.

InfluxDB stores time series in a **columnar, compressed** layout designed for “show me pressure over the last 7 days.” Dual-write keeps each system doing what it is good at:

- Dashboard “live/current” → Redis (`TS.GET`)
- Historical trend lines → InfluxDB (Flux `range`)

A single store with a long TTL would either waste RAM (Redis) or add latency to hot reads (Influx-only).

### Retention: Redis vs InfluxDB

- **Redis**: `TS.CREATE` / `TS.ADD` with `RETENTION 3600000` (1 hour). Older samples are evicted automatically by the TimeSeries module — no cron job required.
- **InfluxDB**: demo bucket keeps data indefinitely. In production you would attach a **bucket retention period** or Influx tasks (downsample + delete), analogous to Redis retention but at cold-storage scale.

### Key Redis / Influx concepts to explain in interviews

| Concept | Where | Notes |
|---------|--------|------|
| `TS.CREATE` / `TS.ADD` | Redis | Key pattern `sensor:{id}:{metric}`; labels `sensor_id`, `metric` |
| `TS.GET` / `TS.RANGE` | Redis | Hot latest vs short-window range |
| Measurement / tag / field | Influx | measurement = metric name; tag = `sensor_id`; field = `value` |
| Flux `from() \|> range() \|> filter()` | Influx / Grafana | Cold historical queries |

## Project layout

```
├── docker-compose.yml
├── .env.example
├── simulator/
│   ├── requirements.txt
│   └── simulate.py
├── ingestion-service/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/main.py
└── grafana/provisioning/
    ├── datasources/datasources.yml
    └── dashboards/
        ├── dashboards.yml
        └── iot-dashboard.json
```

## What this demo deliberately skips

- Auth / security hardening
- Kafka (or other) message bus between simulator and ingestion
- Kubernetes / Nginx
- Custom React dashboard (Grafana is enough for visual payoff)
- Redis downsampling rules / Influx continuous tasks beyond the default bucket

## Stop / reset

```bash
docker compose down
# wipe volumes if you want a clean slate:
docker compose down -v
```
