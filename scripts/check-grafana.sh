#!/usr/bin/env bash
set -euo pipefail
echo '=== GRAFANA LOGS ==='
cd /mnt/d/IOT_Redis
docker compose logs --tail=80 grafana
echo
echo '=== DS API ==='
curl -sS -u admin:admin http://127.0.0.1:3000/api/datasources
echo
echo '=== DASHBOARDS ==='
curl -sS -u admin:admin 'http://127.0.0.1:3000/api/search?query=IoT'
echo
echo '=== DS HEALTH ==='
curl -sS -u admin:admin http://127.0.0.1:3000/api/datasources/uid/InfluxDB
echo
echo '=== FLUX VIA INFLUX ==='
curl -sS -X POST http://127.0.0.1:8086/api/v2/query?org=iot-org \
  -H "Authorization: Token iot-super-secret-token" \
  -H "Content-Type: application/vnd.flux" \
  -H "Accept: application/csv" \
  --data 'from(bucket: "iot_data") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "temperature") |> limit(n: 3)'
echo
echo '=== GRAFANA DS QUERY ==='
curl -sS -u admin:admin -H 'Content-Type: application/json' \
  -d '{"queries":[{"refId":"A","datasource":{"type":"influxdb","uid":"InfluxDB"},"query":"from(bucket: \"iot_data\") |> range(start: -1h) |> filter(fn: (r) => r._measurement == \"temperature\") |> limit(n: 3)"}]}' \
  http://127.0.0.1:3000/api/ds/query | head -c 2000
echo
