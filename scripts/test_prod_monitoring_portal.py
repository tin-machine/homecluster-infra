import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from prod_monitoring_portal_render import build_manifest, portal_html


SITE = {
    "prod_grafana_url": "https://grafana.tail0000.ts.net",
    "stg_grafana_url": "https://grafana-stg.tail0000.ts.net",
    "portal_hostname": "monitoring",
    "prod_grafana_hostname": "grafana",
}
RENDERER = Path(__file__).resolve().with_name("prod_monitoring_portal_render.py")


class PortalTests(unittest.TestCase):
    def test_only_two_tailnet_ingresses_and_restricted_nginx(self):
        items = build_manifest(SITE)["items"]
        by_name = {(obj["kind"], obj["metadata"]["name"]): obj for obj in items}
        ingresses = [obj for obj in items if obj["kind"] == "Ingress"]
        self.assertEqual({obj["metadata"]["name"] for obj in ingresses},
                         {"prod-monitoring-portal", "prod-grafana-tailnet"})
        self.assertTrue(all(obj["spec"]["ingressClassName"] == "tailscale"
                            for obj in ingresses))
        self.assertTrue(all("funnel" not in json.dumps(obj).lower()
                            for obj in ingresses))
        deployment = by_name["Deployment", "prod-monitoring-portal"]
        pod = deployment["spec"]["template"]["spec"]
        nginx_config = by_name["ConfigMap", "prod-monitoring-portal-content"]["data"]["nginx.conf"]
        for directive in ("pid", "client_body_temp_path", "proxy_temp_path",
                          "fastcgi_temp_path", "uwsgi_temp_path", "scgi_temp_path"):
            self.assertRegex(nginx_config, rf"{directive} /tmp/[^;]+;")
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(pod["containers"][0]["resources"]["limits"]["memory"], "64Mi")
        policy = by_name["NetworkPolicy", "prod-monitoring-portal-ingress"]["spec"]
        self.assertEqual(policy["policyTypes"], ["Ingress", "Egress"])
        self.assertNotIn("egress", policy)
        self.assertEqual(policy["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "tailscale"})

    def test_rejects_urls_with_token_path_port_or_wrong_scheme(self):
        for bad in ("http://grafana.tail0000.ts.net",
                    "https://grafana.tail0000.ts.net:443",
                    "https://grafana.tail0000.ts.net:bad",
                    "https://grafana.tail0000.ts.net/path",
                    "https://grafana.tail0000.ts.net?token=x",
                    "https://evil.example.com"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                portal_html(bad, SITE["stg_grafana_url"])
        html = portal_html(SITE["prod_grafana_url"], SITE["stg_grafana_url"])
        self.assertIn("要対応・Warn以上ログ", html)
        self.assertIn("Staging Grafana", html)
        for collision in (
            {"portal_hostname": "grafana"},
            {"portal_hostname": "grafana-stg"},
            {"stg_grafana_url": SITE["prod_grafana_url"]},
        ):
            with self.subTest(collision=collision), self.assertRaises(ValueError):
                build_manifest({**SITE, **collision})

    def test_site_input_must_be_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "site.json"
            site.write_text(json.dumps(SITE))
            query = json.dumps({"portal_site_path": str(site)})
            run = lambda: subprocess.run(
                [sys.executable, str(RENDERER)], input=query, text=True,
                capture_output=True, check=False)
            os.chmod(site, 0o644)
            self.assertNotEqual(run().returncode, 0)
            os.chmod(site, 0o600)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(json.loads(result.stdout)["manifest"])["items"]), 7)


if __name__ == "__main__":
    unittest.main()
