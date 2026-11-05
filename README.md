# YOLO_NDP

Near-data video preprocessing for object detection over NVMe-oF.

## What this is

In a typical detection pipeline the inference server reads a compressed video
from storage, decodes it, and resizes every frame to the network input size
before running the detector. When the video sits on a networked SSD, the whole
compressed file crosses the network, and the server's CPU spends much of its
time decoding and resizing.

YOLO_NDP moves that preprocessing to the storage node. The inference host
(Darknet, YOLOv7-tiny) sends two vendor-specific NVMe commands to an SPDK
NVMe-oF/TCP target:

| Command | Direction | What happens |
|---|---|---|
| **0xC0 PREPROCESS** | host → storage | Carries the video file's extents (LBA ranges). The storage node reads the file from its local SSD, decodes it with FFmpeg, keeps every N-th frame, letterbox-resizes each kept frame to 416×416 and encodes it as JPEG (quality 85 or 100) or leaves it as raw RGB24. Completes when the result is ready. |
| **0xC2 GET_RESULT** | storage → host | Returns the result buffer in chunks of up to 2 MiB. |

The host then feeds the frames straight to the detector. The repository also
contains the host's ordinary path (**Baseline**: read the file, decode and
resize on the host) and a measurement harness that runs both paths on the
same files from the same device and records time, CPU, memory, network bytes
and detection accuracy (mAP on MOT17/MOT20).

```
 host (GPU)                                  storage node
 ┌──────────────────────────┐   NVMe/TCP    ┌──────────────────────────────────┐
 │ darknet detector         │  ── 0xC0 ──▶  │ SPDK nvmf_tgt                    │
 │   video_map -ndp ...     │  extents      │   read extents from SSD          │
 │                          │               │   FFmpeg decode → sample 1/N     │
 │   inference + mAP        │  ◀─ 0xC2 ──   │   resize 416×416 → JPEG / raw    │
 └──────────────────────────┘  frames       └──────────────────────────────────┘
```

## Repository layout

```
YOLO_NDP/
├── README.md
├── docs/reproduce.md      step-by-step setup, data preparation, measurement commands
├── host/                  Darknet (AlexeyAB/darknet @ 59596d7) + NDP host path + harness
│   ├── src/detector.c     `detector video_map`: Baseline and NDP pipelines, mAP, [MEASURE] line
│   ├── src/image_opencv.* OpenCV decode / resize helpers used by video_map
│   ├── *.sh, *.py         volume setup, measurement harness, dataset preparation, analysis
│   └── docs/notes/        development and measurement notes (Korean)
├── target/                NDP storage platform: SPDK v24.09-pre + nvme-cli
│   ├── spdk/lib/nvmf/ctrlr_bdev.c   0xC0/0xC2 handlers and the decode/resize/encode worker
│   ├── spdk/lib/nvmf/ctrlr.c, include/spdk/nvme_spec.h, include/spdk/nvmf_transport.h
│   │                                opcode definition, dispatch, data direction
│   ├── spdk/lib/nvmf/nvmf_image.*, ndp_cv.cpp   image helpers (ndp_cv.cpp is linked but unused)
│   ├── spdk/test/ndp/               stand-alone probes for the target code
│   └── scripts/           conf_subsystem.sh (export the SSD), target_probe.sh, monitor_rss.sh, ...
└── .gitmodules            SPDK submodules (target/spdk/{dpdk,isa-l,...})
```

## Requirements

Two Linux machines, connected by a dedicated Ethernet link (we use 10 GbE,
point to point).

| | Host (inference) | Storage node (target) |
|---|---|---|
| Hardware | NVIDIA GPU with CUDA | NVMe SSD that SPDK can take over (vfio-pci), hugepages |
| Software | CUDA, cuDNN, OpenCV 4 (with FFmpeg), nvme-cli | FFmpeg dev packages (libavcodec, libavformat, libavutil, libswscale), libjpeg-turbo, OpenCV 4, SPDK build dependencies |
| Root needed for | `nvme connect`, mkfs/mount, page-cache drop in the harness | `scripts/setup.sh` (hugepages, binding the SSD) |

The configuration we ran on:

| | Host | Storage node |
|---|---|---|
| CPU | AMD EPYC 9554, 64 cores / 128 threads | 2 × Intel Xeon E5-2620 v4, 32 threads |
| Memory | 755 GiB | 62.8 GiB |
| GPU | NVIDIA RTX A6000 48 GB (CUDA 12.0, cuDNN 9.20) | — |
| SSD | — (remote namespace over NVMe/TCP) | Intel DC P4500 (U.2) |
| NIC | Intel X540-AT2 10 GbE | Intel X540-AT2 10 GbE |
| OS | Ubuntu 24.04.2, kernel 7.0.0-38, gcc 13.3 | Ubuntu 22.04.5, kernel 6.8.0-87, gcc 11.4 |
| Libraries | OpenCV 4.6.0, FFmpeg 6.1.1, nvme-cli 2.10.2 | SPDK 24.09-pre, DPDK 23.07, FFmpeg 4.4.2, libjpeg-turbo 2.1.2, OpenCV 4.5.4 |

## Quick start

The full procedure, with every option explained, is in
[`docs/reproduce.md`](docs/reproduce.md). In short:

**1. Build and start the storage node**

```bash
git clone git@github.com:pnuoslab/YOLO_NDP.git && cd YOLO_NDP
git submodule update --init --recursive
cd target/spdk
./configure --enable-debug && make -j"$(nproc)"     # see "Build note" below
sudo HUGEMEM=16384 scripts/setup.sh                 # hugepages + bind the SSD to vfio-pci
setsid nohup build/bin/nvmf_tgt > ~/nvmf_tgt.log 2>&1 < /dev/null &
cd ../.. && SUDO= target/scripts/conf_subsystem.sh  # export the SSD over NVMe/TCP
```

Edit the settings at the top of `target/scripts/conf_subsystem.sh`
(`TARGET_IP`, `NVME_PCIE_ADDR`, ...) or pass them as environment variables.

**2. Build the host and attach the volume**

```bash
cd host
make -j"$(nproc)"                         # produces ./darknet (GPU, cuDNN, OpenCV, OpenMP)
# put yolov7-tiny.weights (Darknet format) in host/
ADDR=<target-ip> ./setup_volume.sh --check               # identify the device, change nothing
sudo ADDR=<target-ip> ./setup_volume.sh --format --copy  # connect, mkfs, mount /mnt/ext4, copy inputs
```

Use `setup_volume.sh` rather than typing `mkfs` yourself: it checks that the
device really is the NVMe/TCP namespace before formatting. After restarting
the target, run `sudo ./remount_verify.sh` before measuring again.

**3. Prepare inputs** (see `docs/reproduce.md` §2): MOT17/MOT20 frames
re-encoded to H.264, labels made with `mot17_gt_to_yolo.py`, plus any video
you want to test at other resolutions or codecs (`make_dataset_e1e5.sh`).

**4. Run**

```bash
# Baseline: decode and resize on the host
./darknet detector video_map mot.data cfg/yolov7-tiny.cfg yolov7-tiny.weights \
  -file /mnt/ext4/MOT17-04.mp4 -labels labels/MOT17-04 \
  -sample_rate 1 -label_base 1 -points 101 -coco_map -dont_show

# NDP: preprocessing on the storage node, JPEG q85, libswscale resize
./darknet detector video_map mot.data cfg/yolov7-tiny.cfg yolov7-tiny.weights \
  -file /mnt/ext4/MOT17-04.mp4 -labels labels/MOT17-04 \
  -sample_rate 1 -label_base 1 -points 101 -coco_map -dont_show \
  -ndp -dev /dev/nvme1n1 -jpeg_q 85 -chroma 420 -resize sws
```

Each run prints one `[MEASURE]` line on the host. The target prints one
`[NDP-MEASURE]` line per request in its log. To run the whole condition
matrix with repeats and collect everything into a CSV, use
`sudo -E ./run_all.sh` (see `docs/reproduce.md` §4).

### Build note

The target here is built with `./configure --enable-debug` (`-O0`), which is
how our measurements were taken. FFmpeg, libswscale and libjpeg-turbo come
from the distribution and are optimized either way. The Darknet-compatible
resize (`-resize darknet`) and the SPDK I/O path run slower at `-O0`. A plain
`./configure` gives an optimized build. It works the same way but its timings
will differ from ours.

## License

- `host/`: Darknet's YOLO License (`host/LICENSE`), as shipped upstream.
- `target/spdk/`: SPDK, BSD-3-Clause (`target/spdk/LICENSE`, `target/spdk/licenses/`). Submodules carry their own licenses.
- `target/nvme-cli/`: GPL-2.0 (`target/nvme-cli/LICENSE`).
- Our changes are under the license of the component they modify.

The repository contains no datasets, videos or model weights. MOT17/MOT20 are
available from MOTChallenge under CC BY-NC-SA 3.0.
