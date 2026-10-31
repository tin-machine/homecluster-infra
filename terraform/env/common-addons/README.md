# Common add-ons

The NFS chart is pinned to 4.0.18. Configure NFS options under `nfs.mountOptions`;
a top-level `mountOptions` is silently ignored. The nested setting supplies both
the provisioner root PV/PVC and the StorageClass. Keep `Retain` for the root PV
and existing dynamically provisioned data.

An upgrade does not retrofit existing PVs or remount active consumers. Inspect
server identity, claim/PV UIDs and active users before a version migration. Set
compatible existing PV mount options without changing the NFS source or claim
binding; then remount each consumer in an appropriate maintenance window. Do
not delete PVCs or use a forced/lazy unmount to change the NFS version.

For inline Pod NFS volumes, Kubernetes has no per-volume mountOptions field.
The opt-in `common/nfs_client_policy` role writes server-specific NFS v3 defaults
from `nfs_client_v3_servers`. Supply the exact server names used by clients from
external inventory; no global NFS version override is made. The PXE catalog
preserves this setting in base and k3s server/agent boot paths. Explicit PV
options must agree, and an existing mount remains unchanged until remounted.
Verify actual `vers=3`, numeric ownership, fsync and interprocess file locking
before accepting an NFS v3 consumer. Keep remote locking enabled.
