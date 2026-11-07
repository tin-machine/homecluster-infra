#!/bin/sh
# Read only fixed NAS logs. Header precedes exactly size bytes; no stdin protocol.
set -eu
PATH=/usr/bin:/bin:/sbin:/usr/sbin
export PATH
case "${SSH_ORIGINAL_COMMAND:-}" in
    nas-logs-v1) ;;
    nas-smart-v1) exec /bin/sh "$(dirname "$0")/nas_smart_snapshot.sh" ;;
    *) exit 64 ;;
esac
MAX_FILE_BYTES=2097152
MAX_TOTAL_BYTES=25165824
TOTAL_BYTES=0
printf 'NASLOG1\n'
emit_file() {
    kind=$1
    generation=$2
    file=$3
    if [ ! -e "$file" ]; then
        printf 'M %s %s\n' "$kind" "$generation"
        return
    fi
    [ -f "$file" ] && [ -r "$file" ] || exit 65
    exec 3< "$file"
    stat_out=$(/usr/bin/stat -L -c '%d %i %s %Y' "/proc/$$/fd/3") || exit 65
    set -f
    set -- $stat_out
    [ "$#" = 4 ] || exit 65
    for n in "$@"; do
        case "$n" in ''|*[!0-9]*) exit 65 ;; esac
        [ "${#n}" -le 18 ] || exit 65
    done
    device=$1 inode=$2 size=$3 mtime=$4
    [ "$size" -le "$MAX_FILE_BYTES" ] || exit 65
    TOTAL_BYTES=$((TOTAL_BYTES + size))
    [ "$TOTAL_BYTES" -le "$MAX_TOTAL_BYTES" ] || exit 65
    printf 'F %s %s %s %s %s %s\n' "$kind" "$generation" "$device" "$inode" "$size" "$mtime"
    /usr/bin/head -c "$size" <&3
    exec 3<&-
}

# Allow-list of log files with kind, generation, and path.
# Each: kind, generation, path
emit_file "kernel"   "current" "/mnt/HDA_ROOT/.logs/kmsg"
emit_file "kernel"   "1"      "/mnt/HDA_ROOT/.logs/kmsg.1"
emit_file "kernel"   "2"      "/mnt/HDA_ROOT/.logs/kmsg.2"

emit_file "samba"  "current" "/usr/local/samba/var/log.samba"
emit_file "samba"  "1"      "/usr/local/samba/var/log.samba.1"
emit_file "samba"  "2"      "/usr/local/samba/var/log.samba.2"

emit_file "smbd"   "current" "/usr/local/samba/var/log.smbd"
emit_file "smbd"   "old"     "/usr/local/samba/var/log.smbd.old"

emit_file "nmbd"   "current" "/var/log/log.nmbd"
emit_file "nmbd"   "old"     "/var/log/log.nmbd.old"

emit_file "winbindd" "current" "/usr/local/samba/var/log.winbindd-idmap"
emit_file "winbindd" "old"     "/usr/local/samba/var/log.winbindd-idmap.old"

emit_file "storage" "current" "/var/log/storage_lib.log"
emit_file "storage" "old"     "/var/log/storage_lib.log.bak"

emit_file "hal"     "current" "/var/log/hal_lib.log"
emit_file "hal"     "old"     "/var/log/hal_lib.log.bak"

emit_file "hal_daemon" "current" "/var/log/hal_daemon.log"
emit_file "hal_daemon" "old"     "/var/log/hal_daemon.log.bak"

emit_file "sdmd"    "current" "/var/log/SDMD.log"
emit_file "sdmd"    "old"     "/var/log/SDMD.log.bak"

emit_file "storage_util" "current" "/var/log/storage_util.log"

emit_file "iscsi"   "current" "/var/log/iscsi_util.log"

printf 'END\n'
exit 0
