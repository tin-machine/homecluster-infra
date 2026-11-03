#!/bin/sh
# Sourced by the exitrd after systemd-shutdown. Keep the NFS backing alive.
[ -r /proc/self/mountinfo ] || return 0
while IFS= read -r entry; do
    case "$entry" in
        *' - nfs '*|*' - nfs4 '*) return 0 ;;
    esac
done < /proc/self/mountinfo
# Only this image's RAM-backed units are stopped, after all NFS users are gone.
for name in wpa networkd; do
    pidfile="/run/sd-netboot-state/$name.pid"
    [ -r "$pidfile" ] || continue
    read -r pid < "$pidfile"
    case "$pid" in ''|*[!0-9]*) continue ;; esac
    [ "$pid" -gt 1 ] || continue
    [ -r "/proc/$pid/root/etc/initrd-release" ] || continue
    kill -TERM "$pid" 2>/dev/null || :
done
