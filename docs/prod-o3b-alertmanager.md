# Production O3-B Alertmanager source

`scripts/prod_o3b_alertmanager_render.py` renders five additive resources in
`observability-prod`: an Alertmanager ConfigMap, Deployment, ClusterIP Service,
and two NetworkPolicies. It does not create a Secret, update Prometheus, or
contact Slack or Kubernetes.

The receiver uses an existing Slack Bot through `chat.postMessage`. The Bot
token must be supplied separately as key `token` in the
`prod-alertmanager-slack-bot` Secret. The private site file contains only a
`slack_channel_id` (a channel ID, not a name). Both the site file and rendered
manifest must stay outside Git and CI logs. The renderer accepts only an
owner-owned mode 0600 site file and exclusively creates a mode 0600 output
inside an owner-owned mode 0700 directory:

```sh
python3 scripts/prod_o3b_alertmanager_render.py \
  --site /path/to/private/site.json \
  --output /path/to/private/manifest.json
```

The Alertmanager route groups by alert name, waits 30 seconds, groups again
after five minutes, repeats after four hours, and sends resolved notifications.
The message template includes only status, alert name, and severity. It omits
alert annotations, target labels, internal links, and log bodies. The initial
single replica keeps notification state in a bounded `emptyDir`; a restart may
repeat notifications.

Only Prometheus Pods can reach Alertmanager TCP/9093. A separate additive
NetworkPolicy lets Prometheus send to it. Alertmanager may use DNS and outbound
TCP/443 for Slack. Standard Kubernetes NetworkPolicy cannot restrict that
egress to the Slack hostname, so the HTTPS rule is deliberately scoped to the
Alertmanager Pod but not to a destination hostname.

This source addition is not a production apply path. A separate reviewed
operation must verify the Secret, existing Prometheus baseline, and exact
source revision, then create these resources and configure Prometheus to send
alerts to the new Service. Delivery needs an explicitly approved test message
and post deployment acceptance; `auth.test` alone proves only Bot authentication.
