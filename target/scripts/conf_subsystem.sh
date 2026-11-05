#!/bin/bash
# Configure the NDP NVMe-oF/TCP target once nvmf_tgt is up and its RPC socket
# exists: TCP transport, attach the local NVMe SSD as a bdev, export it as one
# namespace of one subsystem, and listen on the storage network.
#
# Same six RPCs as spdk/conf_subsystem.sh; only the settings are collected
# here so they can be changed in one place or overridden from the
# environment, e.g.  TARGET_IP=10.0.0.2 ./conf_subsystem.sh
#
# The transport's max I/O size (-i 2097152) sets MDTS to 2 MiB, which is why
# the host fetches 0xC2 results in 2 MiB chunks.

# ---- settings (defaults are the values used in the paper's experiments) ----
TARGET_IP=${TARGET_IP:-192.168.100.2}      # storage-network address of this node
TARGET_PORT=${TARGET_PORT:-4420}
NVME_PCIE_ADDR=${NVME_PCIE_ADDR:-0000:82:00.0}   # NVMe SSD (lspci)
CTRLR_NAME=${CTRLR_NAME:-nvme1}           # bdev controller name -> bdev "${CTRLR_NAME}n1"
SUBSYS_NQN=${SUBSYS_NQN:-nqn.2016-06.io.spdk:cnode1}
SUBSYS_SERIAL=${SUBSYS_SERIAL:-SPDK00000000000001}
DISCOVERY_NQN=nqn.2014-08.org.nvmexpress.discovery
# "sudo" when nvmf_tgt runs as root; set SUDO= when it runs as this user.
SUDO=${SUDO-sudo}
SPDK_DIR=${SPDK_DIR:-"$(cd "$(dirname "$0")/../spdk" && pwd)"}
# ----------------------------------------------------------------------------

RPC="$SUDO $SPDK_DIR/scripts/rpc.py"
BDEV=${CTRLR_NAME}n1

$RPC nvmf_create_transport -t TCP -u 131072 -m 20 -c 8192 -i 2097152
$RPC bdev_nvme_attach_controller -b "$CTRLR_NAME" -t PCIe -a "$NVME_PCIE_ADDR"
$RPC nvmf_create_subsystem "$SUBSYS_NQN" -a -s "$SUBSYS_SERIAL" -d "$BDEV"
$RPC nvmf_subsystem_add_ns "$SUBSYS_NQN" "$BDEV"
$RPC nvmf_subsystem_add_listener "$SUBSYS_NQN" -t tcp -a "$TARGET_IP" -s "$TARGET_PORT"
$RPC nvmf_subsystem_add_listener "$DISCOVERY_NQN" -t tcp -a "$TARGET_IP" -s "$TARGET_PORT"
