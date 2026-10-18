#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Check, step by step, that run_all.sh will be able to collect target data.
#
#   usage: TARGET_HOST=<name-or-ip> ./check_target.sh       (no sudo)
#
# run_all.sh needs three things from the target per run: bdev_get_iostat
# before and after, and the [NDP-MEASURE] line carrying the run's ID. If
# any link in that chain is broken, run_all.sh still completes but those
# columns come back empty - silently, by design, so a long run is not
# lost. This script finds the broken link before the long run starts.
#
# It runs everything as the current user, which is what run_all.sh does
# too (its ssh calls drop from root to $SUDO_USER).
# ══════════════════════════════════════════════════════════════════════
set -u

TARGET_HOST=${TARGET_HOST:-target-server3}   # ~/.ssh/config alias, <target-lan-ip>:<ssh-port>
TARGET_RPC=${TARGET_RPC:-/home/${SUDO_USER:-$USER}/near_data_processing_for_video/spdk/scripts/rpc.py}
# nvmf_tgt writes to stdout, which the target operator tees to a file in
# ~kimkitae. Point this at whatever the running build is tee'd to.
TARGET_LOG=${TARGET_LOG:-/home/${SUDO_USER:-$USER}/nvmf_tgt_iotest.log}
# run_ids the smoke test sent in cdw13; finding them proves pairing works.
SMOKE_IDS=${SMOKE_IDS:-"575380003 575380004 575380005"}

ok()   { echo "  [ OK ] $*"; }
bad()  { echo "  [FAIL] $*"; FAILED=1; }
FAILED=0

[ -n "$TARGET_HOST" ] || { echo "set TARGET_HOST"; exit 2; }
S=(ssh -o BatchMode=yes -o ConnectTimeout=5 "$TARGET_HOST")

echo "1. name resolution"
# ssh -G applies ~/.ssh/config, so an alias such as target-server3 resolves
# to its real HostName and Port even with no /etc/hosts entry.
cfg_host=$(ssh -G "$TARGET_HOST" 2>/dev/null | awk '$1=="hostname"{print $2}')
port=$(ssh -G "$TARGET_HOST" 2>/dev/null | awk '$1=="port"{print $2}')
port=${port:-22}
if [[ "$cfg_host" =~ ^[0-9.]+$ ]]; then
  addr=$cfg_host
else
  addr=$(getent hosts "$cfg_host" | awk '{print $1; exit}')
fi
if [ -n "$addr" ]; then
  ok "$TARGET_HOST -> $addr:$port"
else
  bad "$TARGET_HOST does not resolve (no ssh config alias, /etc/hosts entry or DNS)"; exit 1
fi

echo "2. sshd reachable"
if timeout 4 bash -c "cat </dev/null >/dev/tcp/$addr/$port" 2>/dev/null; then
  ok "port $port open on $addr"
else
  bad "port $port refused/unreachable on $addr"; exit 1
fi

echo "3. key-based login (BatchMode, no password prompt)"
if who=$("${S[@]}" 'echo "$(whoami)@$(hostname)"' 2>/tmp/check_target.$$); then
  ok "logged in as $who"
else
  bad "ssh failed: $(tail -1 /tmp/check_target.$$)"
  echo "         fix: ssh-keygen -t ed25519   then   ssh-copy-id $TARGET_HOST"
  rm -f /tmp/check_target.$$; exit 1
fi
rm -f /tmp/check_target.$$

echo "4. bdev_get_iostat"
if out=$("${S[@]}" "$TARGET_RPC bdev_get_iostat" 2>&1); then
  echo "$out" | python3 -I -c '
import json, sys
d = json.load(sys.stdin)
b = d.get("bdevs", d) if isinstance(d, dict) else d
for e in b:
    print("         bdev %-16s bytes_read=%-16s bytes_written=%s" % (
        e.get("name"), e.get("bytes_read"), e.get("bytes_written")))
' 2>/dev/null && ok "iostat JSON parsed" || bad "iostat output is not JSON: $(echo "$out" | head -2)"
else
  bad "rpc call failed: $(echo "$out" | tail -1)"
  echo "         The SPDK socket is usually root-only. If so, either log in as"
  echo "         root, or allow passwordless 'sudo $TARGET_RPC' for this user and"
  echo "         set TARGET_RPC=\"sudo -n $TARGET_RPC\"."
fi

echo "5. target log and run-ID pairing"
if "${S[@]}" "test -r '$TARGET_LOG'" 2>/dev/null; then
  ok "$TARGET_LOG readable"
  n=$("${S[@]}" "grep -c 'NDP-MEASURE' '$TARGET_LOG' || true" 2>/dev/null); n=${n:-0}
  echo "         [NDP-MEASURE] lines: $n"
  last=$("${S[@]}" "grep -F '[NDP-MEASURE]' '$TARGET_LOG' | tail -1" 2>/dev/null)
  [ -n "$last" ] && echo "         last: ${last:0:160}"
  if [ "$n" = 0 ]; then
    ok "no [NDP-MEASURE] lines yet (fresh target) - pairing is checked on the first NDP run"
  elif echo "$last" | grep -q 'run_id='; then
    ok "target echoes run_id (target spec section 3-5 is in)"
    for id in $SMOKE_IDS; do
      hit=$("${S[@]}" "grep -F '[NDP-MEASURE]' '$TARGET_LOG' | grep -F 'run_id=$id' | tail -1" 2>/dev/null)
      if [ -n "$hit" ]; then ok "smoke run $id found"; else bad "smoke run $id not in log"; fi
    done
  else
    bad "[NDP-MEASURE] has no run_id= field: host/target rows cannot be paired"
  fi
else
  bad "$TARGET_LOG not readable as $(echo "$who" | cut -d@ -f1)"
fi

echo
if [ "$FAILED" = 0 ]; then
  echo "ALL OK - run_all.sh will collect target columns with TARGET_HOST=$TARGET_HOST"
else
  echo "Some checks failed; run_all.sh would leave those target columns empty."
fi
exit "$FAILED"
