"""Source-managed production Grafana views for the two desktop hosts."""

DATASOURCE = {"type": "prometheus", "uid": "prod-prometheus"}


def panel(number: int, title: str, expr: str, unit: str, *, legend: str = "",
          kind: str = "timeseries") -> dict:
    return {
        "id": number, "title": title, "type": kind, "datasource": DATASOURCE,
        "targets": [{"refId": "A", "expr": expr, "legendFormat": legend}],
        "gridPos": {"x": (number - 1) % 2 * 12, "y": ((number - 1) // 2) * 8,
                    "w": 12, "h": 8},
        "fieldConfig": {"defaults": {"unit": unit, "noValue": "未観測"}, "overrides": []},
        "options": ({"reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                     "orientation": "auto", "colorMode": "value"}
                    if kind == "stat" else
                    {"legend": {"displayMode": "table", "placement": "bottom"},
                     "tooltip": {"mode": "multi"}}),
    }


def dashboard(host: str) -> dict:
    if host not in ("desktop", "typevt"):
        raise ValueError("unknown host dashboard")
    job = "ubuntu-desktop-node-exporter" if host == "desktop" else "typevt-node-exporter"
    selector = '{job="' + job + '"}'
    rows = [
        ("Scrape UP", "up" + selector, "short", "", "stat"),
        ("CPU busy (5m)", '100 * (1 - avg(rate(node_cpu_seconds_total{job="' + job + '",mode="idle"}[5m])))', "percent", "", "timeseries"),
        ("Available memory", "node_memory_MemAvailable_bytes" + selector, "bytes", "", "timeseries"),
        ("Swap used", "node_memory_SwapTotal_bytes" + selector + " - node_memory_SwapFree_bytes" + selector, "bytes", "", "timeseries"),
        ("CPU and board temperature", "node_hwmon_temp_celsius" + selector, "celsius", "{{chip}} {{sensor}}", "timeseries"),
        ("Root filesystem available", 'node_filesystem_avail_bytes{job="' + job + '",mountpoint="/"}', "bytes", "", "timeseries"),
        ("Disk read rate", 'rate(node_disk_read_bytes_total{job="' + job + '",device!~"loop.*|ram.*"}[5m])', "Bps", "{{device}}", "timeseries"),
        ("Disk write rate", 'rate(node_disk_written_bytes_total{job="' + job + '",device!~"loop.*|ram.*"}[5m])', "Bps", "{{device}}", "timeseries"),
        ("I/O pressure", "rate(node_pressure_io_waiting_seconds_total" + selector + "[5m])", "percentunit", "", "timeseries"),
        ("Last scrape age", "time() - timestamp(up" + selector + ")", "s", "", "stat"),
    ]
    if host == "desktop":
        rows += [
            ("GPU collection success", "homecluster_gpu_collection_success" + selector, "short", "", "stat"),
            ("GPU utilization", "homecluster_gpu_utilization_gpu_percent" + selector, "percent", "GPU {{gpu}}", "timeseries"),
            ("GPU VRAM used", "homecluster_gpu_memory_used_megabytes" + selector + " * 1024 * 1024", "bytes", "GPU {{gpu}}", "timeseries"),
            ("GPU temperature", "homecluster_gpu_temperature_celsius" + selector, "celsius", "GPU {{gpu}}", "timeseries"),
            ("GPU power", "homecluster_gpu_power_watts" + selector, "watt", "GPU {{gpu}}", "timeseries"),
            ("SMART discovery success", "homecluster_smart_discovery_success" + selector, "short", "", "stat"),
            ("SSD SMART health (1=pass)", "homecluster_smart_health_passed" + selector, "short", "{{device}}", "stat"),
            ("SSD SMART collection success", "homecluster_smart_collection_success" + selector, "short", "{{device}}", "stat"),
            ("SSD temperature", "homecluster_smart_temperature_celsius" + selector, "celsius", "{{device}}", "timeseries"),
            ("GPU collection age", "time() - homecluster_gpu_last_success_timestamp_seconds" + selector, "s", "", "stat"),
            ("SMART collection age", "time() - homecluster_smart_last_attempt_timestamp_seconds" + selector, "s", "", "stat"),
        ]
    else:
        rows += [
            ("BAT0 capacity", 'node_power_supply_capacity{job="' + job + '",power_supply="BAT0"}', "percent", "", "stat"),
            ("ADP1 AC online", 'node_power_supply_online{job="' + job + '",power_supply="ADP1"}', "short", "", "stat"),
            ("Battery energy (Wh)", 'node_power_supply_energy_watthour{job="' + job + '",power_supply="BAT0"}', "short", "", "timeseries"),
            ("Battery status", 'node_power_supply_info{job="' + job + '",power_supply="BAT0"}', "short", "{{status}}", "stat"),
        ]
    return {
        "uid": "ubuntu-desktop-host" if host == "desktop" else "typevt-host",
        "title": "Ubuntu Desktop host" if host == "desktop" else "TypeVT host",
        "tags": ["home-monitoring", host], "schemaVersion": 39, "version": 1,
        "editable": False, "timezone": "browser", "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "panels": [panel(index + 1, title, expr, unit, legend=legend, kind=kind)
                   for index, (title, expr, unit, legend, kind) in enumerate(rows)],
    }
