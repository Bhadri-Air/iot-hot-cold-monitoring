#!/usr/bin/env bash
cd /mnt/d/IOT_Redis
echo '=== ENV IN GRAFANA ==='
docker compose exec -T grafana printenv | grep -E 'INFLUX|GF_SECURITY' || true
echo
echo '=== PROVISIONED FILES ==='
docker compose exec -T grafana ls -la /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards
echo
echo '=== DATASOURCE YML ==='
docker compose exec -T grafana cat /etc/grafana/provisioning/datasources/datasources.yml
echo
echo '=== DASHBOARD UID FILE ==='
docker compose exec -T grafana ls -la /etc/grafana/provisioning/dashboards
echo
echo '=== GRAFANA DB DASHBOARDS (sqlite) ==='
docker compose exec -T grafana sh -c 'command -v sqlite3 || true; ls /var/lib/grafana/'
echo
echo '=== WINDOWS PORT CHECK FROM WSL ==='
ss -lntp | grep -E ':3000|:8000' || netstat -lntp 2>/dev/null | grep -E ':3000|:8000'
