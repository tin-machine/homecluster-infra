# Gentoo world update

The role installs a weekly systemd timer and a Python helper. The helper keeps
shell quoting and systemd percent expansion out of the update commands.

| Host state | Timer behavior |
| --- | --- |
| No PXE source declaration | Maintain required world atoms, sync, update, rebuild, then depclean |
| Valid pinned PXE source declaration | Log the pinned-release policy and exit successfully without changing world, Git, or packages |
| Invalid or unreadable source declaration | Fail before changing world or running emerge |

Pinned PXE operating-system updates belong to the release-generation build and
acceptance workflow. Build-time commit pinning remains unchanged. Installing a
new helper in a running root overlay does not update the immutable boot release;
include the reviewed source in the next generation for persistence after reboot.

The declaration at `/etc/ansible-pull/source-declaration.json` uses the existing
`homecluster-pxe-source-declaration-v1` contract. It must be a small regular file
with the exact `schema`, `environment`, `build_id`, and `infra_revision` fields.
The current pinned contract supports the `staging` environment. Symbolic links,
duplicate fields, and malformed identities are rejected.

On mutable hosts, `app-admin/ansible` is removed from world by exact line match.
The required atoms are added once, existing comments are retained, and atomic
replacement preserves the file owner and mode. Any failed update phase stops
later phases. Exclusions apply to world update, preserved rebuild, and depclean.

The offline service/helper contract test is part of `scripts/ci/static-check.sh`.
It renders the actual role service, calls the helper with those arguments, and
uses temporary files and a recording command stub instead of running emerge.
