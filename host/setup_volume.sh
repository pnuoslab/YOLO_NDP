#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Bring up the NVMe-oF measurement volume on the host.
#
#   usage: sudo ./setup_volume.sh [--format] [--copy]
#          ./setup_volume.sh --check          (no root, changes nothing)
#
#   (default)  connect if needed, mount at /mnt/ext4 if needed
#   --format   mkfs.ext4 the volume first. WIPES EVERY FILE ON IT.
#   --copy     copy the measurement inputs from ~/ndp_dataset, sync, and
#              check that no file exceeds the 0xC0 extent limit
#   --check    only identify the device and report what would happen
#
# The reason this script exists rather than four typed lines: the device
# that gets formatted has to be proven to be the SPDK namespace before
# mkfs runs. On this host /dev/nvme0n1 is a local PCIe SSD; a typo or a
# changed enumeration order would otherwise wipe it. So the target device
# is never taken from a fixed name. It is found through sysfs as the
# namespace of the subsystem whose NQN is $NQN, and every path of that
# subsystem must be transport tcp. Anything else stops the script before
# mkfs.
#
# mkfs.ext4 asks "Proceed anyway? (y,N)" when a filesystem already exists,
# and Enter means N. That prompt is why the volume had not been formatted
# since 2026-08-31 despite mkfs appearing in the shell history. Once the
# checks above pass, this script uses -F so the format either happens or
# the script fails loudly.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

NQN=${NQN:-nqn.2016-06.io.spdk:cnode1}
ADDR=${ADDR:-192.168.100.2}
PORT=${PORT:-4420}
MNT=${MNT:-/mnt/ext4}
SRC_DIR=${SRC_DIR:-/home/${SUDO_USER:-$USER}/ndp_dataset}
MAX_EXTENTS=${MAX_EXTENTS:-128}   # NDP_MAX_EXTENTS in src/detector.c

INPUTS=(res1080_h264 res1440_h264 res2160_h264 res1080_hevc res2160_hevc
        res1080_av1 res2160_av1 MOT17-02 MOT17-04 MOT17-09 MOT20-02 MOT20-03)

DO_FORMAT=0; DO_COPY=0; CHECK_ONLY=0
for a in "$@"; do
  case "$a" in
    --format) DO_FORMAT=1 ;;
    --copy)   DO_COPY=1 ;;
    --check)  CHECK_ONLY=1 ;;
    *) echo "[ERR] unknown option: $a"; exit 2 ;;
  esac
done

die() { echo "[STOP] $*" >&2; exit 1; }

# Prints the block device name (e.g. nvme1n1) of the namespace that belongs
# to the subsystem with our NQN, after checking every path is tcp. Prints
# nothing if the subsystem is not present.
find_device() {
  local sub dev ctrl tr found=""
  for sub in /sys/class/nvme-subsystem/nvme-subsys*; do
    [ -r "$sub/subsysnqn" ] || continue
    [ "$(cat "$sub/subsysnqn")" = "$NQN" ] || continue
    # Every controller (path) of this subsystem must be a tcp fabric path.
    for ctrl in "$sub"/nvme[0-9]*; do
      [ -e "$ctrl/transport" ] || continue
      tr=$(cat "$ctrl/transport")
      [ "$tr" = tcp ] || die "subsystem $NQN has a path with transport '$tr', not tcp"
    done
    # Namespace head devices are nvme<X>n<Y>; skip ng* char devs and the
    # hidden per-path nvme<X>c<Z>n<Y> nodes.
    for dev in "$sub"/nvme*n*; do
      dev=$(basename "$dev")
      [[ "$dev" =~ ^nvme[0-9]+n[0-9]+$ ]] || continue
      [ -z "$found" ] || die "subsystem $NQN has more than one namespace ($found, $dev); refusing to guess"
      found=$dev
    done
  done
  echo "$found"
}

port_open() { timeout 4 bash -c "cat </dev/null >/dev/tcp/$ADDR/$PORT" 2>/dev/null; }

echo "── target $ADDR:$PORT ──"
port_open && echo "  port $PORT open" || die "port $PORT on $ADDR refused: nvmf_tgt is not running on the target"

DEV=$(find_device)
if [ "$CHECK_ONLY" = 1 ]; then
  echo "── check only ──"
  if [ -z "$DEV" ]; then
    echo "  subsystem $NQN: not connected (setup would run nvme connect)"
  else
    echo "  subsystem $NQN -> /dev/$DEV (all paths tcp)"
    echo "  size: $(lsblk -dno SIZE /dev/$DEV 2>/dev/null)"
    echo "  mounted at: $(findmnt -nro TARGET -S /dev/$DEV 2>/dev/null || true)"
  fi
  echo "  local disks NOT touched: $(lsblk -dno NAME | grep -v "^${DEV:-none}\$" | tr '\n' ' ')"
  exit 0
fi

[ "$(id -u)" -eq 0 ] || die "run with sudo"

echo "── connect ──"
if [ -z "$DEV" ]; then
  nvme connect -t tcp -n "$NQN" -a "$ADDR" -s "$PORT"
  for _ in $(seq 1 20); do
    DEV=$(find_device); [ -n "$DEV" ] && [ -b "/dev/$DEV" ] && break
    sleep 0.5
  done
  [ -n "$DEV" ] || die "connected but no namespace of $NQN appeared"
else
  echo "  already connected"
fi
echo "  device: /dev/$DEV  ($(lsblk -dno SIZE /dev/$DEV))"

if [ "$DO_FORMAT" = 1 ]; then
  echo "── format /dev/$DEV ──"
  # Last line of defence: the device must be the one find_device() just
  # proved, it must not be mounted anywhere, and it must not hold /.
  [ "$DEV" = "$(find_device)" ] || die "device identity changed between checks"
  if findmnt -S "/dev/$DEV" >/dev/null 2>&1; then
    die "/dev/$DEV is mounted at $(findmnt -nro TARGET -S /dev/$DEV); unmount first (or drop --format)"
  fi
  ROOTSRC=$(findmnt -nro SOURCE /)
  [ "$ROOTSRC" != "/dev/$DEV" ] || die "/dev/$DEV holds the root filesystem"
  mkfs.ext4 -F "/dev/$DEV"
fi

echo "── mount ──"
mkdir -p "$MNT"
if findmnt -S "/dev/$DEV" -T "$MNT" >/dev/null 2>&1; then
  echo "  already mounted at $MNT"
else
  findmnt "$MNT" >/dev/null 2>&1 && die "$MNT already has something else mounted on it"
  mount "/dev/$DEV" "$MNT"
fi
findmnt "$MNT"

if [ "$DO_COPY" = 1 ]; then
  echo "── copy inputs ──"
  for n in "${INPUTS[@]}"; do
    [ -f "$SRC_DIR/$n.mp4" ] || die "missing input $SRC_DIR/$n.mp4"
  done
  for n in "${INPUTS[@]}"; do cp "$SRC_DIR/$n.mp4" "$MNT/"; done
  sync
  echo "── extent check (0xC0 limit $MAX_EXTENTS) ──"
  bad=0
  for n in "${INPUTS[@]}"; do
    e=$(filefrag "$MNT/$n.mp4" | grep -oE '[0-9]+ extents? found' | grep -oE '^[0-9]+')
    printf "  %-18s %3s extents\n" "$n.mp4" "$e"
    [ "$e" -le "$MAX_EXTENTS" ] || bad=1
  done
  [ "$bad" = 0 ] || die "a file exceeds $MAX_EXTENTS extents; 0xC0 would truncate it"
fi

echo
echo "volume ready: /dev/$DEV on $MNT"
df -h "$MNT" | tail -1
