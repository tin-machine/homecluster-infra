#!/bin/sh
# Fixed read-only SMART query; invoked only by the existing forced-command entry.
set -eu
PATH=/usr/bin:/bin:/sbin:/usr/sbin
export PATH
[ "${SSH_ORIGINAL_COMMAND:-}" = nas-smart-v1 ] || exit 64

identity() {
    section="PhysicalDisk_$1"
    port=$(/sbin/getcfg "$section" port_id -f /etc/enclosure_0.conf) || return 65
    [ "$port" = "$1" ] || return 65
    serial=$(/sbin/getcfg "$section" serial_no -f /etc/enclosure_0.conf) || return 65
    wwn=$(/sbin/getcfg "$section" wwn -f /etc/enclosure_0.conf) || return 65
    model=$(/sbin/getcfg "$section" model -f /etc/enclosure_0.conf) || return 65
    [ -n "$serial" ] && [ -n "$wwn" ] && [ -n "$model" ] || return 65
    digest=$(printf '%s\n%s\n%s\n' "$serial" "$wwn" "$model" | /usr/bin/openssl dgst -sha256) || return 65
    digest=${digest##* }
    [ "${#digest}" -eq 64 ] || return 65
    case "$digest" in *[!0-9a-f]*) return 65 ;; esac
    printf '%s\n' "$digest"
}

printf 'NAS_SMART_1\n'
for bay in 1 2 3 4; do
    before=$(identity "$bay") || exit 65
    printf 'BAY %s %s\n' "$bay" "$before"
    /sbin/get_hd_smartinfo -d "$bay" || exit 65
    after=$(identity "$bay") || exit 65
    [ "$before" = "$after" ] || exit 65
    printf 'END_BAY\n'
done
printf 'END\n'
