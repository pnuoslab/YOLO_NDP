#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Take the NVMe-oF measurement volume down on the host.
#
#   usage: sudo ./teardown_volume.sh
#
# Equivalent to the hand-typed
#     umount /dev/nvme1n1; nvme disconnect -n <nqn>; rm -rf /mnt/ext4
# with one deliberate difference: the mount point is removed with rmdir,
# not rm -rf. If umount fails (a shell still sitting in /mnt/ext4, a
# process holding a file open), rm -rf would go on to delete every file
# ON THE VOLUME through the still-live mount. rmdir only succeeds on an
# empty directory, so a failed unmount stops here instead.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

NQN=${NQN:-nqn.2016-06.io.spdk:cnode1}
MNT=${MNT:-/mnt/ext4}

[ "$(id -u)" -eq 0 ] || { echo "[STOP] run with sudo" >&2; exit 1; }

if findmnt "$MNT" >/dev/null 2>&1; then
  sync
  umount "$MNT" || {
    echo "[STOP] umount $MNT failed. Holders:" >&2
    fuser -vm "$MNT" 2>&1 >&2 || true
    exit 1
  }
  echo "unmounted $MNT"
else
  echo "$MNT not mounted"
fi

if nvme list-subsys 2>/dev/null | grep -qF "$NQN"; then
  nvme disconnect -n "$NQN"
else
  echo "$NQN not connected"
fi

if [ -d "$MNT" ]; then
  rmdir "$MNT" && echo "removed $MNT" || echo "[WARN] $MNT not empty after unmount; left in place"
fi
