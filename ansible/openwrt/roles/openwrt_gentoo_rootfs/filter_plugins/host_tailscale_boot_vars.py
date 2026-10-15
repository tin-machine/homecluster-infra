"""Reject enrollment credentials in all generated PXE variable mappings."""
from collections.abc import Mapping


def host_tailscale_boot_vars_safe(value):
    if isinstance(value, Mapping):
        return all(k != 'host_tailscale_auth_key' and host_tailscale_boot_vars_safe(v)
                   for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(host_tailscale_boot_vars_safe(v) for v in value)
    return True


class FilterModule:
    def filters(self):
        return {'host_tailscale_boot_vars_safe': host_tailscale_boot_vars_safe}
