#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Reattach the measurement volume after the target was restarted, and
# prove the inputs on it are intact before anything is measured.
#
#   usage: sudo ./remount_verify.sh
#
# Why this exists. On 2026-10-19 the target was restarted while the host
# kept /mnt/ext4 mounted, on the theory that nvme-tcp's automatic
# reconnect (ctrl_loss_tmo=600) would carry an idle volume through a short
# outage. The controller did come back live within 5 s - but WITHOUT its
# namespace: the restarted target presented nvme1n1 under new identifiers,
# the kernel would not reattach it to the old head, the block device
# vanished, and ext4 went into emergency shutdown on top of it. So a target
# restart always needs a full disconnect / connect on the host, and that is
# what this script does.
#
# Steps, each of which stops the script on failure:
#   1. unmount the dead filesystem (lazy if the device is already gone)
#   2. nvme disconnect, nvme connect, wait for the namespace to appear
#   3. mount once so the kernel replays the ext4 journal, unmount
#      (e2fsck -n cannot replay a journal and would report problems that
#      the replay itself fixes)
#   4. e2fsck -fn - read-only; any problem stops here and nothing is
#      repaired automatically
#   5. mount, then compare every input with its local copy by sha256
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

NQN=${NQN:-nqn.2016-06.io.spdk:cnode1}
ADDR=${ADDR:-192.168.100.2}
PORT=${PORT:-4420}
MNT=${MNT:-/mnt/ext4}
SRC_DIR=${SRC_DIR:-/home/${SUDO_USER:-$USER}/ndp_dataset}
INPUTS=(res1080_h264 res1440_h264 res2160_h264 res1080_hevc res2160_hevc
        res1080_av1 res2160_av1 MOT17-02 MOT17-04 MOT17-09 MOT20-02 MOT20-03)

die() { echo "[STOP] $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "run with sudo"

# Namespace head of our subsystem, found through sysfs (never by a fixed
# name), with every path required to be tcp. Same rule as setup_volume.sh.
find_device() {
  local sub dev ctrl found=""
  for sub in /sys/class/nvme-subsystem/nvme-subsys*; do
    [ "$(cat "$sub/subsysnqn" 2>/dev/null)" = "$NQN" ] || continue
    for ctrl in "$sub"/nvme[0-9]*; do
      [ -e "$ctrl/transport" ] || continue
      [ "$(cat "$ctrl/transport")" = tcp ] || die "a path of $NQN is not tcp"
    done
    for dev in "$sub"/nvme*n*; do
      dev=$(basename "$dev")
      [[ "$dev" =~ ^nvme[0-9]+n[0-9]+$ ]] || continue
      [ -z "$found" ] || die "more than one namespace under $NQN"
      found=$dev
    done
  done
  echo "$found"
}

echo "── 1. unmount ──"
if findmnt "$MNT" >/dev/null 2>&1; then
  if umount "$MNT" 2>/dev/null; then
    echo "  unmounted"
  else
    # The backing device is gone, so a normal unmount can fail; a lazy one
    # detaches the dead mount without touching any device.
    umount -l "$MNT" && echo "  lazily unmounted (device was gone)"
  fi
else
  echo "  not mounted"
fi

echo "── 2. reconnect ──"
if nvme list-subsys 2>/dev/null | grep -qF "$NQN"; then
  nvme disconnect -n "$NQN"
  sleep 1
fi
timeout 4 bash -c "cat </dev/null >/dev/tcp/$ADDR/$PORT" 2>/dev/null || die "target $ADDR:$PORT not listening"
nvme connect -t tcp -n "$NQN" -a "$ADDR" -s "$PORT"
DEV=""
for _ in $(seq 1 40); do
  DEV=$(find_device); [ -n "$DEV" ] && [ -b "/dev/$DEV" ] && break
  sleep 0.5
done
[ -n "$DEV" ] && [ -b "/dev/$DEV" ] || die "connected, but no namespace of $NQN appeared"
echo "  device: /dev/$DEV ($(lsblk -dno SIZE /dev/$DEV))"

echo "── 3. journal replay ──"
mkdir -p "$MNT"
mount "/dev/$DEV" "$MNT"
umount "$MNT"
echo "  mounted and unmounted once"

echo "── 4. read-only check ──"
e2fsck -fn "/dev/$DEV" || die "e2fsck -n reported problems. Nothing was changed; decide on a repair by hand."

echo "── 5. mount and verify inputs ──"
mount "/dev/$DEV" "$MNT"
findmnt -no OPTIONS "$MNT" | grep -q shutdown && die "filesystem is in shutdown state"
bad=0
for n in "${INPUTS[@]}"; do
  a=$(sha256sum "$MNT/$n.mp4" 2>/dev/null | cut -d' ' -f1) || true
  b=$(sha256sum "$SRC_DIR/$n.mp4" | cut -d' ' -f1)
  if [ -n "$a" ] && [ "$a" = "$b" ]; then
    printf "  %-18s ok\n" "$n.mp4"
  else
    printf "  %-18s %s\n" "$n.mp4" "${a:+MISMATCH}${a:-READ FAILED}"; bad=1
  fi
done
[ "$bad" = 0 ] || die "an input on the volume differs from its local copy"

echo
echo "volume reattached and verified: /dev/$DEV on $MNT"
