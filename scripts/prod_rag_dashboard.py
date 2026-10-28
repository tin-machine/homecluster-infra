"""Content-free dashboard for an external shared RAG metrics endpoint."""
from __future__ import annotations

UID = "shared-rag-overview"
JOB = '{job="desktop-rag"}'


def dashboard() -> dict:
    panels = []
    y = 0

    def row(title: str) -> None:
        nonlocal y
        panels.append({"id": len(panels) + 1, "type": "row", "title": title,
                       "collapsed": False, "panels": [],
                       "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}})
        y += 1

    def panel(title: str, expressions: list[tuple[str, str]], unit: str = "short",
              *, stat: bool = False, description: str = "", health: bool = False) -> None:
        nonlocal y
        index = sum(p["type"] != "row" for p in panels)
        x = 12 * (index % 2)
        panels.append({
            "id": len(panels) + 1, "title": title, "type": "stat" if stat else "timeseries",
            "description": description,
            "datasource": {"type": "prometheus", "uid": "prod-prometheus"},
            "gridPos": {"x": x, "y": y, "w": 12, "h": 7},
            "targets": [{"refId": chr(65+i), "expr": query, "legendFormat": legend}
                        for i, (query, legend) in enumerate(expressions)],
            "fieldConfig": {"defaults": {
                "unit": unit, "noValue": "未観測",
                "color": ({"mode": "fixed", "fixedColor": "blue"} if stat
                          else {"mode": "palette-classic"}),
            }, "overrides": []},
            "options": {"reduceOptions": {"values": False, "calcs": ["last"], "fields": ""},
                        "legend": {"displayMode": "table", "placement": "bottom"}},
        })
        if health:
            panels[-1]["fieldConfig"]["defaults"].update({
                "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": [
                    {"color": "red", "value": None}, {"color": "green", "value": 1}]},
                "mappings": [{"type": "special", "options": {
                    "match": "null", "result": {"text": "未観測", "color": "gray"}}}],
            })
        if x:
            y += 7

    row("稼働")
    panel("収集・DB・更新監視（1=正常）", [("up"+JOB, "scrape"),
          ("rag_database_up"+JOB, "DB"), ("rag_watcher_ready"+JOB, "watcher")], stat=True, health=True)
    panel("最終メトリクス収集からの経過", [("time() - rag_snapshot_timestamp_seconds"+JOB, "age")], "s", stat=True)
    panel("RAG稼働時間", [("time() - process_start_time_seconds"+JOB, "uptime")], "s", stat=True)
    panel("CPU専用・常用reranker（1=有効）", [("rag_cpu_only"+JOB, "CPU only"),
          ("rag_reranker_enabled"+JOB, "reranker")], stat=True, health=True)

    row("索引")
    panel("登録ファイル・chunk・stale", [("rag_sources"+JOB, "files"),
          ("rag_chunks"+JOB, "chunks"), ("rag_stale"+JOB, "stale")])
    panel("最終全対象照合の成功からの経過", [(
        "time() - rag_index_last_full_success_timestamp_seconds"+JOB, "age")], "s", stat=True,
        description="原本に変更がなくても照合の成功で更新する。部分scopeの成功では更新しない。")
    panel("最新jobの状態", [("rag_index_job_state"+JOB+" == 1", "{{state}}")], stat=True)
    panel("最新jobの進捗", [("rag_index_files"+JOB, "{{result}}")])

    row("検索")
    rate = 'sum(rate(rag_search_requests_total{job="desktop-rag"}[5m]))'
    panel("検索頻度（5分）", [(rate, "requests/s")], "ops")
    panel("失敗・混雑拒否の割合（5分）", [(
        '(100 * (sum(rate(rag_search_requests_total{job="desktop-rag",outcome=~"error|busy"}[5m])) or vector(0)) / '
        + rate + ') and (' + rate + ' > 0)', "error / busy")], "percent")
    bucket = 'sum by (le) (rate(rag_search_duration_seconds_bucket{job="desktop-rag"}[15m]))'
    count = 'sum(rate(rag_search_duration_seconds_count{job="desktop-rag"}[15m]))'
    panel("検索時間 p50 / p95（15分・待ち時間込み）", [
        (f'histogram_quantile({q}, {bucket}) and on() ({count} > 0)', label)
        for q, label in ((0.5, "p50"), (0.95, "p95"))], "s",
        description="histogramの推定値。観測がない場合は未観測。少数の検索で長期p95を判断しない。")
    panel("処理中・推論待ち・起動後の観測件数", [("rag_search_inflight"+JOB, "inflight"),
          ("rag_inference_waiting_searches"+JOB, "inference waiting"),
          ("sum(rag_search_duration_seconds_count"+JOB+")", "observations since start")])

    row("資源")
    panel("アプリ PSS / SwapPss", [("rag_pss_bytes"+JOB, "PSS"),
          ("rag_swap_pss_bytes"+JOB, "SwapPss")], "bytes")
    panel("アプリ・DB cgroupメモリ", [("rag_cgroup_memory_bytes"+JOB, "{{component}} memory"),
          ("rag_cgroup_swap_bytes"+JOB, "{{component}} swap")], "bytes")
    panel("CPU使用コア数", [('rate(rag_cgroup_cpu_seconds{job="desktop-rag"}[5m])', "{{component}}")])
    panel("DB・chunk索引サイズ・SSD空き", [("rag_database_bytes"+JOB, "DB total"),
          ("rag_index_bytes"+JOB, "chunk indexes"),
          ("rag_filesystem_available_bytes"+JOB, "SSD available")], "bytes",
          description="DB totalは索引を含む。SSD空きはモデルcacheと同一filesystemの空き。")
    panel("OOM・メモリ制限到達（cgroup累積）", [("rag_cgroup_oom_kills"+JOB, "{{component}} OOM"),
          ("rag_cgroup_high_events"+JOB, "{{component}} high"),
          ("rag_cgroup_max_events"+JOB, "{{component}} max")])
    panel("資源取得と観測した再起動", [("rag_app_resources_up"+JOB, "app resource collection"),
          ("rag_db_resources_up"+JOB, "DB resource collection"),
          ('changes(rag_pid{job="desktop-rag"}[24h])', "observed PID changes / 24h")], stat=True,
          description="scrape間に複数回起きた再起動は数えられない。")
    return {"uid": UID, "title": "共有RAG — 稼働・索引・検索・資源", "schemaVersion": 39,
            "version": 1, "editable": False, "refresh": "30s", "timezone": "browser",
            "time": {"from": "now-6h", "to": "now"}, "tags": ["rag", "production-monitoring"],
            "panels": panels}
