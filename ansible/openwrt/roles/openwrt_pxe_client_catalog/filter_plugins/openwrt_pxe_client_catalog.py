from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return list(value)
    return [value]


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _append_clean_unique(items: list[str], value: Any) -> None:
    item = _clean_string(value)
    if item and item not in items:
        items.append(item)


_TERRAFORM_STG_GROUP_VAR_KEYS = (
    "k3s_observability_apply_package_atom",
    "k3s_observability_apply_package_accept_keywords",
    "k3s_observability_apply_package_licenses",
    "k3s_observability_apply_state_mount_src",
    "k3s_observability_apply_state_mount_path",
    "k3s_observability_apply_state_mount_fstype",
    "k3s_observability_apply_state_mount_opts",
    "k3s_observability_apply_stage_name",
    "k3s_observability_apply_infra_bundle_name",
    "k3s_observability_apply_site_input_bundle_name",
    "k3s_observability_apply_run_root",
    "k3s_observability_apply_work_root",
    "k3s_observability_apply_service_name",
    "k3s_observability_apply_kubeconfig_path",
    "k3s_observability_apply_k3s_data_dir",
    "k3s_observability_apply_lock_timeout",
    "k3s_observability_apply_parallelism",
    "k3s_observability_apply_wait_timeout_secs",
    "k3s_observability_apply_retry_secs",
    "k3s_observability_apply_post_ready_settle_secs",
    "k3s_observability_apply_metallb_crd_wait_timeout_secs",
    "k3s_observability_apply_metallb_ready_wait_timeout_secs",
    "k3s_observability_apply_cert_manager_ready_wait_timeout_secs",
    "k3s_observability_apply_after_units",
)


def _build_base_vars(hv: Mapping[str, Any], cfg: Mapping[str, Any]) -> dict[str, Any]:
    ansible_pull_cfg = cfg.get("ansible_pull")
    if not isinstance(ansible_pull_cfg, Mapping):
        ansible_pull_cfg = {}
    if not _as_bool(ansible_pull_cfg.get("distcc_vars_enabled", True)):
        return {}

    distcc_cfg = hv.get("distcc")
    if not isinstance(distcc_cfg, Mapping) or not _as_bool(distcc_cfg.get("enabled")):
        return {}

    result: dict[str, Any] = {"distcc": dict(distcc_cfg)}
    if "distcc_default_allow" in hv:
        result["distcc_default_allow"] = hv["distcc_default_allow"]
    return result


def _build_terraform_stg_group_vars(hv: Mapping[str, Any]) -> dict[str, Any]:
    return {key: hv[key] for key in _TERRAFORM_STG_GROUP_VAR_KEYS if key in hv}


def _merge_nested_mapping(
    base: Mapping[str, Any],
    overlay: Mapping[str, Any],
    path: tuple[str, ...] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    merged = dict(base)
    conflicts: list[dict[str, Any]] = []
    for key, value in overlay.items():
        key_text = str(key)
        current_path = path + (key_text,)
        if key_text not in merged:
            merged[key_text] = value
            continue

        current = merged[key_text]
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            child_merged, child_conflicts = _merge_nested_mapping(
                current,
                value,
                current_path,
            )
            merged[key_text] = child_merged
            conflicts.extend(child_conflicts)
        elif current != value:
            conflicts.append({"path": ".".join(current_path)})
    return merged, conflicts


def _mapping_legacy_mismatches(
    legacy_mapping: Mapping[str, Any],
    generated_mapping: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for key, legacy_value in legacy_mapping.items():
        key_text = str(key)
        if key_text not in generated_mapping:
            mismatches.append({"key": key_text, "type": "missing_generated"})
            continue
        if generated_mapping[key_text] != legacy_value:
            mismatches.append({"key": key_text, "type": "different_value"})
    return mismatches


def _sorted_inventory_hosts(groups: Mapping[str, Any], hostvars: Mapping[str, Any]) -> list[str]:
    all_group = groups.get("all") if isinstance(groups, Mapping) else None
    if isinstance(all_group, Sequence) and not isinstance(all_group, (str, bytes, bytearray)):
        return sorted(str(host) for host in all_group)
    return sorted(str(host) for host in hostvars.keys())


def _normalise_client(client: Mapping[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: client.get(field) for field in fields if field in client}


def _item_key(item: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = _clean_string(item.get(key))
        if value:
            return value
    return ""


def _non_shadowed_legacy(
    legacy_items: Sequence[Mapping[str, Any]],
    generated_items: Sequence[Mapping[str, Any]],
    keys: Sequence[str],
) -> list[dict[str, Any]]:
    generated_keys = {_item_key(item, keys) for item in generated_items}
    generated_keys.discard("")
    return [
        dict(item)
        for item in legacy_items
        if _item_key(item, keys) not in generated_keys
    ]


def _legacy_mismatches(
    legacy_items: Sequence[Mapping[str, Any]],
    generated_items: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    keys: Sequence[str],
) -> list[dict[str, Any]]:
    legacy_by_key = {
        _item_key(item, keys): item
        for item in legacy_items
        if _item_key(item, keys)
    }
    mismatches: list[dict[str, Any]] = []
    for generated in generated_items:
        item_key = _item_key(generated, keys)
        legacy = legacy_by_key.get(item_key)
        if legacy is None:
            continue
        generated_norm = _normalise_client(generated, list(fields))
        legacy_norm = _normalise_client(legacy, list(fields))
        if generated_norm != legacy_norm:
            differing_fields = sorted(
                field
                for field in fields
                if generated_norm.get(field) != legacy_norm.get(field)
            )
            mismatches.append(
                {
                    "key": item_key,
                    "fields": differing_fields,
                }
            )
    return mismatches


def _option_value_for_client(
    cfg: Mapping[str, Any],
    overlay_cfg: Mapping[str, Any],
    overlay_id: str,
    role: Any,
) -> str:
    option_value = _first_present(overlay_cfg.get("option_value"), cfg.get("option_value"))
    if option_value is not None and _clean_string(option_value):
        return _clean_string(option_value)

    parts = [f"overlay_id={overlay_id}"]
    role_text = _clean_string(role)
    if role_text:
        parts.append(f"role={role_text}")

    roles = _as_list(_first_present(cfg.get("roles"), overlay_cfg.get("roles")))
    roles_text = ",".join(_clean_string(item) for item in roles if _clean_string(item))
    if roles_text:
        parts.append(f"roles={roles_text}")

    stage = _clean_string(_first_present(cfg.get("stage"), cfg.get("tftp_stage")))
    if stage:
        parts.append(f"stage={stage}")
    return ",".join(parts)


def _build_k3s_local_storage_vars(
    hv: Mapping[str, Any],
    default_ephemeral_agent_data: bool,
    default_node_password_sync_enabled: bool | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "k3s_local_storage_enabled": _as_bool(
            _first_present(hv.get("k3s_local_storage_enabled"), False)
        ),
        "k3s_local_storage_mountpoint": "/var/lib/rancher/k3s",
        "k3s_local_storage_filesystem_type": "ext4",
        "k3s_local_storage_mount_options": "defaults,noatime,lazytime,nodiscard,errors=remount-ro",
        "k3s_local_storage_allow_format": True,
        "k3s_local_storage_force_format": False,
        "k3s_local_storage_ephemeral_agent_data": _as_bool(
            _first_present(
                hv.get("k3s_local_storage_ephemeral_agent_data"),
                default_ephemeral_agent_data,
            )
        ),
    }

    if default_node_password_sync_enabled is not None or (
        "k3s_local_storage_node_password_sync_enabled" in hv
    ):
        result["k3s_local_storage_node_password_sync_enabled"] = _as_bool(
            _first_present(
                hv.get("k3s_local_storage_node_password_sync_enabled"),
                default_node_password_sync_enabled,
            )
        )

    device = _clean_string(hv.get("k3s_local_storage_device"))
    if device:
        result["k3s_local_storage_device"] = device
    return result


def _build_k3s_stg_agent_vars(
    hostname: str,
    hv: Mapping[str, Any],
    cfg: Mapping[str, Any],
    overlay_id: str,
) -> dict[str, Any]:
    node_ip = _clean_string(
        _first_present(
            hv.get("k3s_node_ip"),
            hv.get("ansible_host"),
            cfg.get("ip"),
        )
    )
    k3s_agent_cfg = hv.get("k3s_agent")
    if not isinstance(k3s_agent_cfg, Mapping):
        k3s_agent_cfg = {}

    labels: list[str] = []
    for item in _as_list(k3s_agent_cfg.get("node-label")):
        _append_clean_unique(labels, item)
    for item in _as_list(hv.get("k3s_agent_extra_node_labels")):
        _append_clean_unique(labels, item)
    if not labels:
        labels = ["example.com/k3s-workload=true"]

    result: dict[str, Any] = {
        "k3s_agent": {
            "node-ip": node_ip,
            "node-name": overlay_id or hostname,
            "node-label": labels,
            "snapshotter": "native",
            "data-dir": "/var/lib/rancher/k3s",
        },
        "k3s_build_cluster": True,
        "k3s_control_node": False,
        "k3s_primary_control_node": False,
        "k3s_control_delegate": "k3s_stg_server",
        "k3s_controller_list": ["k3s_stg_server"],
    }
    result.update(
        _build_k3s_local_storage_vars(
            hv,
            default_ephemeral_agent_data=True,
        )
    )
    return result


def _build_k3s_stg_server_vars(
    hostname: str,
    hv: Mapping[str, Any],
    cfg: Mapping[str, Any],
    overlay_id: str,
) -> dict[str, Any]:
    node_ip = _clean_string(
        _first_present(
            hv.get("k3s_node_ip"),
            hv.get("ansible_host"),
            cfg.get("ip"),
        )
    )
    node_name = overlay_id or hostname

    result: dict[str, Any] = {
        "k3s_server": {
            "disable": ["traefik", "servicelb"],
            "node-ip": node_ip,
            "node-name": node_name,
            "node-external-ip": [node_ip] if node_ip else [],
            "tls-san": [item for item in (node_ip, node_name) if item],
            "snapshotter": "native",
            "data-dir": "/var/lib/rancher/k3s",
            "write-kubeconfig-mode": "0644",
        },
    }
    result.update(
        _build_k3s_local_storage_vars(
            hv,
            default_ephemeral_agent_data=False,
            default_node_password_sync_enabled=False,
        )
    )
    return result


def _mapping_value(cfg: Mapping[str, Any], nested_cfg: Mapping[str, Any], key: str) -> Any:
    return _first_present(nested_cfg.get(key), cfg.get(key))


def _build_generated_client(
    hostname: str,
    hv: Mapping[str, Any],
    cfg: Mapping[str, Any],
    option_code: Any,
    default_filename: Any,
    default_serveraddress: Any,
    default_servername: Any,
    default_force: Any,
) -> dict[str, Any]:
    pxe_host_cfg = cfg.get("pxe_host")
    if pxe_host_cfg is None:
        pxe_host_cfg = cfg.get("host")
    if pxe_host_cfg is None:
        pxe_host_cfg = {}
    if not isinstance(pxe_host_cfg, Mapping):
        pxe_host_cfg = {}

    boot_cfg = cfg.get("pxe_boot")
    if boot_cfg is None:
        boot_cfg = cfg.get("boot")
    if boot_cfg is None:
        boot_cfg = {}
    if not isinstance(boot_cfg, Mapping):
        boot_cfg = {}

    overlay_cfg = cfg.get("overlay")
    if overlay_cfg is None:
        overlay_cfg = cfg.get("overlay_client")
    if overlay_cfg is None:
        overlay_cfg = {}
    if not isinstance(overlay_cfg, Mapping):
        overlay_cfg = {}

    name = _clean_string(_mapping_value(cfg, pxe_host_cfg, "name")) or hostname
    networkid = _clean_string(_mapping_value(cfg, pxe_host_cfg, "networkid")) or name

    roles = _as_list(cfg.get("roles"))
    role = _mapping_value(cfg, overlay_cfg, "role")
    if role is None and roles:
        role = roles[0]

    overlay_id = _clean_string(
        _first_present(
            overlay_cfg.get("id"),
            cfg.get("overlay_id"),
            cfg.get("id"),
            name,
        )
    )
    option_value = _option_value_for_client(cfg, overlay_cfg, overlay_id, role)

    pxe_host: dict[str, Any] = {
        "name": name,
        "networkid": networkid,
    }

    host_field_candidates = {
        "mac": _mapping_value(cfg, pxe_host_cfg, "mac"),
        "ip": _first_present(_mapping_value(cfg, pxe_host_cfg, "ip"), hv.get("ansible_host")),
        "filename": _first_present(
            _mapping_value(cfg, pxe_host_cfg, "filename"),
            _mapping_value(cfg, pxe_host_cfg, "bootfile"),
            default_filename,
        ),
        "force": _first_present(_mapping_value(cfg, pxe_host_cfg, "force"), default_force),
        "board_hash": _mapping_value(cfg, pxe_host_cfg, "board_hash"),
        "tftp_stage": _first_present(
            _mapping_value(cfg, pxe_host_cfg, "tftp_stage"),
            cfg.get("stage"),
        ),
        "tftp_release": _mapping_value(cfg, pxe_host_cfg, "tftp_release"),
        "rootfs_release": _mapping_value(cfg, pxe_host_cfg, "rootfs_release"),
        "cmdline_root_arg": _mapping_value(cfg, pxe_host_cfg, "cmdline_root_arg"),
        "cmdline_ip": _mapping_value(cfg, pxe_host_cfg, "cmdline_ip"),
        "cmdline_extra_args": _mapping_value(cfg, pxe_host_cfg, "cmdline_extra_args"),
    }
    for key, value in host_field_candidates.items():
        if value is not None and _clean_string(value):
            pxe_host[key] = value

    tags = _mapping_value(cfg, pxe_host_cfg, "tags")
    if tags is None:
        tag = _clean_string(_first_present(cfg.get("stage"), pxe_host.get("tftp_stage")))
        tags = [tag] if tag else []
    tags_list = [_clean_string(item) for item in _as_list(tags) if _clean_string(item)]
    if tags_list:
        pxe_host["tags"] = tags_list

    dhcp_options = _mapping_value(cfg, pxe_host_cfg, "dhcp_options")
    if dhcp_options is None and option_value:
        dhcp_options = [f'{_clean_string(option_code)},"{option_value}"']
    dhcp_options_list = [
        _clean_string(item) for item in _as_list(dhcp_options) if _clean_string(item)
    ]
    if dhcp_options_list:
        pxe_host["dhcp_options"] = dhcp_options_list

    pxe_boot: dict[str, Any] | None = None
    if _as_bool(boot_cfg.get("enabled", True)):
        filename = _clean_string(
            _first_present(
                boot_cfg.get("filename"),
                boot_cfg.get("bootfile"),
                pxe_host.get("filename"),
                default_filename,
            )
        )
        serveraddress = _clean_string(
            _first_present(
                boot_cfg.get("serveraddress"),
                cfg.get("serveraddress"),
                default_serveraddress,
            )
        )
        servername = _clean_string(
            _first_present(
                boot_cfg.get("servername"),
                cfg.get("servername"),
                default_servername,
                serveraddress,
            )
        )
        if filename or serveraddress or servername:
            pxe_boot = {
                "name": _clean_string(boot_cfg.get("name")) or name,
                "networkid": _clean_string(boot_cfg.get("networkid")) or networkid,
                "filename": filename,
                "serveraddress": serveraddress,
                "servername": servername,
            }

    overlay_client: dict[str, Any] | None = None
    if _as_bool(overlay_cfg.get("enabled", True)) and overlay_id:
        overlay_client = {"id": overlay_id}
        if role is not None and _clean_string(role):
            overlay_client["role"] = _clean_string(role)
        if option_value:
            overlay_client["option_value"] = option_value
        for key in ("ansible_extra_vars", "mac"):
            value = _first_present(overlay_cfg.get(key), cfg.get(key))
            if value is not None and _clean_string(value):
                overlay_client[key] = value

    ansible_pull_cfg = cfg.get("ansible_pull")
    if not isinstance(ansible_pull_cfg, Mapping):
        ansible_pull_cfg = {}

    ansible_pull_group_vars = ansible_pull_cfg.get("group_vars")
    if not isinstance(ansible_pull_group_vars, Mapping):
        ansible_pull_group_vars = {}

    role_names = [_clean_string(item) for item in roles if _clean_string(item)]
    ansible_pull_client_vars: list[dict[str, Any]] = []
    extra_vars_cfg = ansible_pull_cfg.get("extra_vars")
    if not isinstance(extra_vars_cfg, Mapping):
        extra_vars_cfg = {}

    if "terraform_stg" in role_names and _as_bool(
        ansible_pull_cfg.get("terraform_stg_group_vars_enabled", True)
    ):
        generated_terraform_vars = _build_terraform_stg_group_vars(hv)
        if generated_terraform_vars:
            explicit_terraform_vars = ansible_pull_group_vars.get("role_terraform_stg")
            if isinstance(explicit_terraform_vars, Mapping):
                merged_terraform_vars, _ = _merge_nested_mapping(
                    generated_terraform_vars,
                    explicit_terraform_vars,
                )
            else:
                merged_terraform_vars = generated_terraform_vars
            ansible_pull_group_vars["role_terraform_stg"] = merged_terraform_vars

    role_extra_vars: dict[str, Any] = {}
    if "base" in role_names:
        base_vars = _build_base_vars(hv, cfg)
        if base_vars:
            role_extra_vars["base"] = base_vars

    if "k3s_stg_server" in role_names and _as_bool(
        ansible_pull_cfg.get("k3s_stg_server_vars_enabled", True)
    ):
        role_extra_vars["k3s_stg_server"] = _build_k3s_stg_server_vars(
            hostname,
            hv,
            cfg,
            overlay_id,
        )

    if "k3s_stg_agent" in role_names and _as_bool(
        ansible_pull_cfg.get("k3s_stg_agent_vars_enabled", True)
    ):
        role_extra_vars["k3s_stg_agent"] = _build_k3s_stg_agent_vars(
            hostname,
            hv,
            cfg,
            overlay_id,
        )

    for role_name, role_vars in extra_vars_cfg.items():
        role_name_text = _clean_string(role_name)
        if not role_name_text or not isinstance(role_vars, Mapping):
            continue
        current = role_extra_vars.get(role_name_text, {})
        merged, _ = _merge_nested_mapping(current, role_vars)
        role_extra_vars[role_name_text] = merged

    for role_name in sorted(role_extra_vars.keys()):
        if not isinstance(role_extra_vars[role_name], Mapping):
            continue
        ansible_pull_client_vars.append(
            {
                "overlay_id": overlay_id,
                "role": role_name,
                "vars": dict(role_extra_vars[role_name]),
            }
        )

    return {
        "pxe_host": pxe_host,
        "pxe_boot": pxe_boot,
        "overlay_client": overlay_client,
        "ansible_pull_group_vars": dict(ansible_pull_group_vars),
        "ansible_pull_client_vars": ansible_pull_client_vars,
    }


def build_openwrt_pxe_client_catalog(
    hostvars: Mapping[str, Any],
    groups: Mapping[str, Any],
    router_name: str,
    legacy_pxe_hosts: Any = None,
    legacy_pxe_boots: Any = None,
    legacy_overlay_clients: Any = None,
    enabled: Any = True,
    compare_overlay_fields: Any = None,
    compare_pxe_host_fields: Any = None,
    compare_pxe_boot_fields: Any = None,
    option_code: Any = "224",
    default_filename: Any = "start4.elf",
    default_serveraddress: Any = "",
    default_servername: Any = "",
    default_force: Any = 1,
    static_dhcp_hosts: Any = None,
    legacy_ansible_pull_group_vars: Any = None,
) -> dict[str, Any]:
    legacy_pxe_host_items = [
        item for item in _as_list(legacy_pxe_hosts) if isinstance(item, Mapping)
    ]
    static_dhcp_host_items = [
        item for item in _as_list(static_dhcp_hosts) if isinstance(item, Mapping)
    ]
    legacy_pxe_boot_items = [
        item for item in _as_list(legacy_pxe_boots) if isinstance(item, Mapping)
    ]
    legacy_overlay = [
        item for item in _as_list(legacy_overlay_clients) if isinstance(item, Mapping)
    ]
    if not isinstance(legacy_ansible_pull_group_vars, Mapping):
        legacy_ansible_pull_group_vars = {}
    overlay_compare_fields = [str(field) for field in _as_list(compare_overlay_fields)]
    if not overlay_compare_fields:
        overlay_compare_fields = ["id", "role", "option_value", "ansible_extra_vars"]
    pxe_host_compare_fields = [str(field) for field in _as_list(compare_pxe_host_fields)]
    if not pxe_host_compare_fields:
        pxe_host_compare_fields = [
            "name",
            "mac",
            "ip",
            "networkid",
            "filename",
            "force",
            "tags",
            "dhcp_options",
            "board_hash",
            "tftp_stage",
            "tftp_release",
            "rootfs_release",
            "cmdline_extra_args",
        ]
    pxe_boot_compare_fields = [str(field) for field in _as_list(compare_pxe_boot_fields)]
    if not pxe_boot_compare_fields:
        pxe_boot_compare_fields = [
            "name",
            "networkid",
            "filename",
            "serveraddress",
            "servername",
        ]

    generated_pxe_hosts: list[dict[str, Any]] = []
    generated_pxe_boots: list[dict[str, Any]] = []
    generated_overlay: list[dict[str, Any]] = []
    generated_ansible_pull_group_vars: dict[str, Any] = {}
    ansible_pull_group_var_conflicts: list[dict[str, Any]] = []
    generated_ansible_pull_client_vars: list[dict[str, Any]] = []
    if _as_bool(enabled):
        for hostname in _sorted_inventory_hosts(groups, hostvars):
            hv = hostvars.get(hostname)
            if not isinstance(hv, Mapping):
                continue
            cfg = hv.get("openwrt_pxe_client")
            if not isinstance(cfg, Mapping) or not _as_bool(cfg.get("enabled")):
                continue
            target_router = str(cfg.get("router") or router_name).strip()
            if target_router != str(router_name):
                continue

            generated = _build_generated_client(
                hostname,
                hv,
                cfg,
                option_code,
                default_filename,
                default_serveraddress,
                default_servername,
                default_force,
            )
            if isinstance(generated.get("pxe_host"), Mapping):
                generated_pxe_hosts.append(dict(generated["pxe_host"]))
            if isinstance(generated.get("pxe_boot"), Mapping):
                generated_pxe_boots.append(dict(generated["pxe_boot"]))
            if isinstance(generated.get("overlay_client"), Mapping):
                generated_overlay.append(dict(generated["overlay_client"]))
            if isinstance(generated.get("ansible_pull_group_vars"), Mapping):
                generated_ansible_pull_group_vars, conflicts = _merge_nested_mapping(
                    generated_ansible_pull_group_vars,
                    generated["ansible_pull_group_vars"],
                )
                ansible_pull_group_var_conflicts.extend(conflicts)
            for item in _as_list(generated.get("ansible_pull_client_vars")):
                if isinstance(item, Mapping):
                    generated_ansible_pull_client_vars.append(dict(item))

    pxe_hosts = _non_shadowed_legacy(
        legacy_pxe_host_items,
        generated_pxe_hosts,
        ("name", "networkid", "mac"),
    )
    pxe_hosts.extend(generated_pxe_hosts)
    pxe_boots = _non_shadowed_legacy(
        legacy_pxe_boot_items,
        generated_pxe_boots,
        ("name", "networkid"),
    )
    pxe_boots.extend(generated_pxe_boots)
    overlay_clients = _non_shadowed_legacy(legacy_overlay, generated_overlay, ("id",))
    overlay_clients.extend(generated_overlay)
    dhcp_hosts = list(static_dhcp_host_items)
    dhcp_hosts.extend(pxe_hosts)

    ansible_pull_group_vars, ansible_pull_group_var_conflicts_legacy = (
        _merge_nested_mapping(legacy_ansible_pull_group_vars, generated_ansible_pull_group_vars)
    )
    ansible_pull_group_var_conflicts.extend(ansible_pull_group_var_conflicts_legacy)

    pxe_host_mismatches = _legacy_mismatches(
        legacy_pxe_host_items,
        generated_pxe_hosts,
        pxe_host_compare_fields,
        ("name", "networkid", "mac"),
    )
    pxe_boot_mismatches = _legacy_mismatches(
        legacy_pxe_boot_items,
        generated_pxe_boots,
        pxe_boot_compare_fields,
        ("name", "networkid"),
    )
    overlay_mismatches = _legacy_mismatches(
        legacy_overlay,
        generated_overlay,
        overlay_compare_fields,
        ("id",),
    )
    ansible_pull_group_var_mismatches = _mapping_legacy_mismatches(
        legacy_ansible_pull_group_vars,
        generated_ansible_pull_group_vars,
    )

    return {
        "pxe_hosts": pxe_hosts,
        "dhcp_hosts": dhcp_hosts,
        "pxe_boots": pxe_boots,
        "overlay_clients": overlay_clients,
        "ansible_pull_group_vars": ansible_pull_group_vars,
        "ansible_pull_client_vars": generated_ansible_pull_client_vars,
        "generated_pxe_hosts": generated_pxe_hosts,
        "generated_pxe_boots": generated_pxe_boots,
        "generated_overlay_clients": generated_overlay,
        "generated_ansible_pull_group_vars": generated_ansible_pull_group_vars,
        "generated_ansible_pull_client_vars": generated_ansible_pull_client_vars,
        "pxe_host_legacy_mismatches": pxe_host_mismatches,
        "pxe_boot_legacy_mismatches": pxe_boot_mismatches,
        "overlay_legacy_mismatches": overlay_mismatches,
        "ansible_pull_group_var_legacy_mismatches": ansible_pull_group_var_mismatches,
        "ansible_pull_group_var_conflicts": ansible_pull_group_var_conflicts,
    }


class FilterModule(object):
    def filters(self) -> dict[str, Any]:
        return {
            "build_openwrt_pxe_client_catalog": build_openwrt_pxe_client_catalog,
        }
