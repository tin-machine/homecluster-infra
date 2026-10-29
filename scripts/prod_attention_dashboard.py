"""Grafana dashboard for actionable severity levels in existing prod Loki streams."""

LOKI = {"type": "loki", "uid": "prod-loki"}
PROMETHEUS = {"type": "prometheus", "uid": "prod-prometheus"}
WARN_LEVELS = "warn|warning|error|err|critical|crit|fatal|alert|emerg"
SELECTOR = ('{environment=~"${environment:raw}",service_name=~"${service:raw}",'
            'detected_level=~"${minimum_level:raw}"}')
UNKNOWN = ('{environment=~"${environment:raw}",service_name=~"${service:raw}",'
           'detected_level=~"unknown|^$"}')


def _variable(name: str, label: str, value: str) -> dict:
    return {"name": name, "label": label, "type": "textbox", "query": value,
            "current": {"text": value, "value": value}, "hide": 0,
            "skipUrlSync": False}


def _panel(number: int, title: str, query: str, *, kind: str = "logs",
           datasource: dict = LOKI) -> dict:
    target = {"refId": "A", "datasource": datasource, "expr": query,
              "queryType": "range"}
    return {
        "id": number, "title": title, "type": kind, "datasource": datasource,
        "targets": [target],
        "gridPos": {"x": 0 if kind == "logs" else 12, "y": (number - 1) * 8,
                    "w": 24 if kind == "logs" else 12, "h": 8},
        "fieldConfig": {"defaults": {"noValue": "未観測"}, "overrides": []},
        "options": ({"showTime": True, "showLabels": False,
                     "wrapLogMessage": True, "enableLogDetails": True,
                     "sortOrder": "Descending"} if kind == "logs" else
                    {"legend": {"displayMode": "table", "placement": "bottom"}}),
    }


def dashboard() -> dict:
    minimum = {
        "name": "minimum_level", "label": "ログlevel", "type": "custom",
        "query": f"Warn以上 : {WARN_LEVELS},全level : .*",
        "current": {"text": "Warn以上", "value": WARN_LEVELS},
        "options": [
            {"text": "Warn以上", "value": WARN_LEVELS, "selected": True},
            {"text": "全level", "value": ".*", "selected": False},
        ],
        "multi": False, "includeAll": False, "hide": 0, "skipUrlSync": False,
    }
    return {
        "uid": "home-attention", "title": "要対応：アラートとログ",
        "tags": ["home-monitoring", "logs", "attention"],
        "schemaVersion": 39, "version": 1, "editable": False,
        "timezone": "browser", "refresh": "1m",
        "time": {"from": "now-1h", "to": "now"},
        "templating": {"list": [
            minimum,
            _variable("environment", "環境（正規表現）", "staging"),
            _variable("service", "サービス（正規表現）", ".*"),
            _variable("namespace", "Pod namespace（正規表現）", ".+"),
            _variable("host", "syslog host（正規表現）", ".+"),
        ]},
        "panels": [
            _panel(1, "Warn以上の最新ログ（全levelへ切替可）", SELECTOR),
            _panel(2, "level不明の最新ログ", UNKNOWN),
            _panel(3, "Podログ：namespace指定", SELECTOR[:-1] +
                   ',k8s_namespace_name=~"${namespace:raw}"}'),
            _panel(4, "syslog：host指定", SELECTOR[:-1] +
                   ',hostname=~"${host:raw}"}'),
            _panel(5, "Warn以上の件数", "sum(count_over_time(" + SELECTOR + "[$__interval]))",
                   kind="timeseries"),
            _panel(6, "level不明の件数", "sum(count_over_time(" + UNKNOWN + "[$__interval]))",
                   kind="timeseries"),
            _panel(7, "発火中のwarning/criticalアラート",
                   'ALERTS{alertstate="firing",severity=~"warning|critical"}',
                   kind="timeseries", datasource=PROMETHEUS),
        ],
    }
