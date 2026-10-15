# Staging Raspberry Pi health metrics

The staging k3s node exporter already exposes CPU temperature, CPU scaling
frequency and the k3s data filesystem. The additional host collector covers
firmware throttling flags and the number of active iSCSI sessions.

The `stg_rpi_health_metrics` Ansible role runs on the four staging k3s nodes as
part of the base play. It installs `dev-embedded/raspberrypi-utils` and a
systemd timer. Every 30 seconds the collector runs `vcgencmd get_throttled`,
counts `/sys/class/iscsi_session/session*`, and atomically writes a Prometheus
textfile under `/run/homecluster-node-exporter`. The node exporter DaemonSet
mounts that host directory read-only. The root overlay is disposable, so the
base play recreates the package, script and units on each boot.

Metrics:

- `homecluster_rpi_throttle_state{condition,state}`: firmware bits for current
  and since-boot undervoltage, frequency capping, throttling and soft
  temperature limit. The bit assignments follow Raspberry Pi documentation.
- `homecluster_iscsi_sessions`: count of active host iSCSI sessions. The three
  iSCSI-backed nodes are expected to have one session; the local SSD node is
  expected to have zero.
- `homecluster_rpi_health_collection_success`: 1 on a complete collection,
  0 if `vcgencmd` fails or returns an unexpected response. A failed collection
  removes the previous firmware and iSCSI metrics from the textfile.
- `homecluster_rpi_health_collected_at_seconds`: time of the latest collection
  attempt. Check its age to detect a stopped timer.

Deployment order: merge source, stage the exact infra revision, run the base
play on all four nodes, then apply the staging Terraform observability root.
Accept only when the timer is active, `node_textfile_scrape_error` is zero,
collection success is one and fresh on all four nodes, the expected iSCSI
counts are present, and the existing node exporter targets remain up.
