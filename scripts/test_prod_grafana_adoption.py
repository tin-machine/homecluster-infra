#!/usr/bin/env python3
import copy
import unittest

from check_prod_grafana_adoption_plan import IDENTITIES, check
from prod_grafana_render import build_manifest
from prod_grafana_terraform_input import terraform_manifest


def plan():
    return {"resource_changes": [
        {"mode": "managed", "address": f'kubernetes_manifest.grafana["{name}"]',
         "change": {"actions": ["update"], "after_unknown": {},
                    "before": {"manifest": None, "field_manager": [], "object": {"unchanged": True}},
                    "after": {"manifest": {"data": {}}, "object": {"unchanged": True},
                              "field_manager": [{"name": "terraform-prod-grafana", "force_conflicts": False}]}}}
        for name in sorted(IDENTITIES)
    ]}


class AdoptionTests(unittest.TestCase):
    def test_existing_provisioning_and_identity_are_preserved(self):
        original = build_manifest()
        normalized = terraform_manifest()
        expected = copy.deepcopy(original)
        for obj in expected["items"]:
            if obj["kind"] == "NetworkPolicy" and obj["spec"].get("ingress") == []:
                self.assertIn("Ingress", obj["spec"]["policyTypes"])
                del obj["spec"]["ingress"]
        self.assertEqual(normalized, expected)
        self.assertEqual(build_manifest(), original)

    def test_state_population_is_accepted(self):
        self.assertEqual(check(plan())["state_adoption_updates"], 5)

    def test_live_change_unknown_or_replacement_is_blocked(self):
        for mutate in (
            lambda c: c["after"]["object"].update({"changed": True}),
            lambda c: c.update({"after_unknown": {"object": {"data": True}}}),
            lambda c: c.update({"actions": ["delete", "create"]}),
            lambda c: c["after"]["field_manager"][0].update({"force_conflicts": True}),
        ):
            data = plan()
            mutate(data["resource_changes"][0]["change"])
            with self.assertRaises(ValueError):
                check(data)


if __name__ == "__main__":
    unittest.main()
