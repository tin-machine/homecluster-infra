# PXE Gentoo: persistent official binary package cache

The official Gentoo HTTPS binhost and the home-built `PKGDIR` are **separate**
stores. Portage's `binrepos.conf/gentoo.conf` keeps
`location = /var/cache/binhost/gentoo` and signature verification enabled.

## Storage layout

| Source | Host backing | Inside rootfs / runtime |
| --- | --- | --- |
| Official signed downloads | `/srv/gentoo/official-binhost-cache/gentoo` | `/var/cache/binhost/gentoo` |
| Locally built packages | Existing `/srv/gentoo/binpkgs/...` | Existing `PKGDIR` |

The rootfs build role bind-mounts the **persistent** official cache inside the
OpenWrt chroot. The NFS server role exposes only the official cache subdirectory
as a separate writable export, keeping its existing `/srv/gentoo` export policy.
PXE Gentoo clients mount that export at the same cache location, before Portage
is invoked by `common/change_make_conf`. Its `state: mounted` also stores the
NFS entry in the client's fstab; runtime fstab changes may be ephemeral on
tmpfs-backed root overlays, so this role runs on each boot.

On PXE clients the NFS source is derived from the already deployed
`gentoo_binpkg_nfs_src`: the suffix `/binpkgs` becomes
`/official-binhost-cache/gentoo`. This intentionally preserves the existing
NFSv4 pseudoroot prefix (which is installation-specific).

The cache NFS integration follows the existing `gentoo_binpkg_nfs_enabled`
runtime switch; the OpenWrt NFS export follows
`openwrt_gentoo_official_binhost_enabled`. No unrelated Gentoo machine is
required to mount this NFS cache.

## Package selection

- General rootfs package installs and runtime use `getbinpkg`: use a
  compatible official or local binary when available, but build the currently
  selected ebuild version from source when necessary.
- Preserve `buildpkg` and `binpkg-multi-instance`: newly built packages are
  stored in the existing CPU/profile-aware `PKGDIR`.
- Keep existing rootfs **heavy preseed** (`--getbinpkgonly`) binary-only.
  This is intentional because OpenWrt cannot safely compile heavy toolchains
  at high parallelism; a missing compatible heavy binary still fails.
- Respect USE and changed binary dependencies. A newer permitted ebuild
  may be built from source even if an older official binary exists.
- Do not set `PORTAGE_BINHOST=file://...`: `PKGDIR` covers local packages
  and `binrepos.conf` describes the official remote source. Also remove the
  unused legacy `make.conf.d/binpkg.conf`.

The `priority` setting in `binrepos.conf` does **not** guarantee that the
official repository will be selected over local `PKGDIR` for all variants,
particularly with mismatched USE flags; verify the selected candidate.

## Checks before applying to a running PXE fleet

This code change alone does not establish a working live NFS mount. Confirm
the NFS export and client mounting separately, outside any in-progress rootfs
generation. Changes to `/etc/exports` can affect shared OpenWrt NFS service;
do not perform them concurrently with a generation or boot.

```bash
# On the OpenWrt server:
test -d /srv/gentoo/official-binhost-cache/gentoo
exportfs -v

# On a PXE Gentoo client after its base role has converged:
findmnt -M /var/cache/binhost/gentoo -o TARGET,SOURCE,FSTYPE,OPTIONS
portageq envvar FEATURES PKGDIR PORTAGE_BINHOST
emerge -pv --getbinpkg --binpkg-respect-use=y app-editors/neovim
```

Expected: `findmnt` reports NFS, `getbinpkg` and `buildpkg` are present
in `FEATURES`, `PORTAGE_BINHOST` is empty or unset, `PKGDIR` still
points at the existing cache, and the chosen `emerge` candidate is visible.

Observe rootfs generation time, local overlay RAM, re-downloads, NFS mount
state and signature failures before considering the migration accepted.
The old cache (if populated inside a lower rootfs) is not automatically copied
to the new persistent store; do not automatically delete it.

## Deferred

Concurrent Portage writers sharing a remote-cache `location`, NFS interruption
and `pkgdir-index-trusted` / `PKGDIR/Packages` consistency need separate
acceptance. The latter feature is already enabled in recent Portage defaults;
it is **not** enabled or modified by this change.

Reference: [Gentoo Binary package guide](https://wiki.gentoo.org/wiki/Binary_package_guide)
and [Portage make.conf(5)](https://github.com/gentoo/portage/blob/master/man/make.conf.5).
