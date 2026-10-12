# Production Grafana: staging container logs

`scripts/prod_grafana_render.py` provisions the production Loki datasource and a
separate **Staging container logs (prod Loki)** dashboard. The existing
Prometheus datasource remains the default, and its eight outside-in panels
are unchanged. Grafana reaches the internal `prod-loki:3100` Service through
the O4 NetworkPolicy created with the Loki backend.

The logs panel starts with a 15-minute range and this LogQL query:

```logql
{service_name=~".+"} | environment="staging"
```

`service_name` selects indexed streams; `environment` filters the structured
metadata sent by the staging Collector. The datasource limits a query to
500 lines. Operators can open the panel in Explore to narrow the time range
or add Pod and namespace filters. This covers the existing Collector's Pod
container logs; it does not imply collection from the staging control-plane
node, systemd journal, or OpenWrt syslog.

The renderer still emits the same five Grafana Kubernetes resources. Its
ConfigMap gains the datasource and dashboard, and the Deployment gains the
dashboard file mount and a new provisioning checksum. Applying the new source
requires a Grafana Pod replacement. The Grafana database is ephemeral, so
source provisioning is the durable definition. Existing Loki log storage and
Prometheus storage are separate from the Grafana Pod.

After the dedicated update operation, acceptance must check that both
datasources are provisioned, the existing eight metrics panels still query,
the new logs panel returns staging Pod records, and Grafana can still reach
Loki after a Pod replacement. Source rendering alone does not prove network
or query behavior.

References: [Grafana Loki datasource provisioning](https://grafana.com/docs/grafana/latest/datasources/loki/configure/),
[Loki query editor](https://grafana.com/docs/grafana/latest/datasources/loki/query-editor/).
