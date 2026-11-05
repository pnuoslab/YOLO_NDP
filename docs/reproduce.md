# Reproducing the YOLO_NDP measurements

This guide covers both machines. Paths are relative to the repository root
unless stated otherwise. Items marked **unverified** were not checked end to
end on a fresh clone.

Two machines are connected point to point by a 10 GbE link
(host `192.168.100.1/30`, target `192.168.100.2/30` in our setup). NVMe/TCP is
the only traffic on that link. The addresses, NQN and device names below are
our defaults; every script reads them from environment variables.

---

## 1. Build

### 1.1 Target (storage node)

```bash
sudo apt install libavcodec-dev libavformat-dev libavutil-dev libswscale-dev \
                 libjpeg-turbo8-dev libopencv-dev
git submodule update --init --recursive    # target/spdk/{dpdk,isa-l,isa-l-crypto,intel-ipsec-mb,ocf,libvfio-user,xnvme}
cd target/spdk
sudo scripts/pkgdep.sh                     # SPDK's own dependencies (unverified: not recorded on our target)
./configure --enable-debug                 # REQUIRED to match the paper, see below
make -j"$(nproc)"
cp build/bin/nvmf_tgt build/bin/nvmf_tgt_measure_v3   # optional: name used in our logs
```

> **Build condition.** The measured binaries were built with
> `./configure --enable-debug`, so SPDK and our storage-side C code ran at
> `-O0 -g3 -march=broadwell`. FFmpeg 4.4.2 (libavcodec, libswscale) and
> libjpeg-turbo 2.1.2 are the optimized Ubuntu 22.04 builds. Decoding, color
> conversion and JPEG encoding times therefore reflect optimized library code.
> The Darknet-compatible resize (`Dk`) and the SPDK I/O path may run slower
> than in an optimized build. A plain `./configure` gives an optimized build
> whose numbers are **not** comparable with the paper. The measurements were
> not repeated with an optimized build.

- Build as a normal user. Building with `sudo make` leaves `dpdk/build`
  owned by root, and a later non-root `make` then fails in the DPDK step.
- Fresh-clone build of the whole tree: **unverified**. The submodule commits
  are the ones the original working tree used. Local modifications inside
  the submodules were not checked.
- Unit test of the NDP handlers:
  `cd test/unit/lib/nvmf/ctrlr_bdev.c && make && ./ctrlr_bdev_ut` (10/10 passed on our target).

Measured binaries and their commits on `main`:

| Binary | Commit | Used for |
|---|---|---|
| `nvmf_tgt_measure_v2_fix` | `b522d86` | main measurement: time and CPU |
| `nvmf_tgt_measure_v3` | `35016dd` | memory (`storage_mem_alloc_mb`) |

`target/spdk/lib/nvmf/ctrlr_bdev.c` at the tip is the same as at `35016dd`.

### 1.2 Host

```bash
cd host
make -j"$(nproc)"          # GPU=1 CUDNN=1 OPENCV=1 OPENMP=1 (see Makefile)
```

Weights: `yolov7-tiny.weights` (Darknet format) in `host/`. Ours has
sha256 `06be302b1564f2e2c3e1db1f5ec02477ac05d0ae82405a8c1b2b8e4111101b66`.
The weights are not in this repository.

> **Do not compare Baseline across builds.** Host Baseline preprocessing time
> moved by about 6 % between two builds of identical source. The cause was
> code alignment of the `mat_to_image` loop, confirmed with
> `-falign-loops=64`. Compare Baseline and NDP only within one `./darknet`
> binary. The paper's numbers come from a single build of the tree in `host/`.

---

## 2. Data preparation (host)

Inputs live in `~/ndp_dataset` and are copied onto the NVMe-oF volume
(`/mnt/ext4`) before measuring. All encodes are 8-bit YUV 4:2:0, keep the
source frame rate and frame count, and drop audio.

### 2.1 MOT17 / MOT20 (accuracy and scene experiments)

Download from MOTChallenge (CC BY-NC-SA 3.0; MOT17-02 also has a third-party
source, `http://vimeo.com/60139361`):

```
https://motchallenge.net/data/MOT17.zip   sha256 7b486bb2af5cfedbfabc020511ff933a3edbf12182ce4985eec1a210f20f9e06
https://motchallenge.net/data/MOT20.zip   sha256 ebcf0e3d44e4f50b5357d24817e5db485d777633d1b8ca9e8380d1c8437dbdd7
```

Sequences: MOT17-02, MOT17-04, MOT17-09, MOT20-02, MOT20-03.

Frames to H.264 (libx264, CRF 18, frame rate from `seqinfo.ini`):

```bash
ffmpeg -framerate <frameRate> -i <seq>/img1/%06d.jpg \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -fps_mode passthrough -an <out>.mp4
```

MOT20-03 is 1173×880. An odd width cannot be stored as YUV 4:2:0, so it gets
one black column on the right. The offset 0:0 keeps every pixel coordinate:

```bash
ffmpeg -framerate 25 -i MOT20-03/img1/%06d.jpg -vf "pad=1174:880:0:0" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -fps_mode passthrough -an MOT20-03.mp4
```

Ground truth to Darknet labels (person = COCO class 0, pedestrian class 1,
visibility ≥ 0.25, one file per frame):

```bash
python3 host/mot17_gt_to_yolo.py --seq <seq-dir> --out labels/<seq> --emit-empty
python3 host/mot17_gt_to_yolo.py --seq MOT20-03 --out labels/MOT20-03 --emit-empty --width 1174   # normalise to padded width
```

### 2.2 Resolution and codec set (E1 / E5)

The source video is **not redistributed**. Ours is a YouTube 4K walking tour
whose container title is *"New York City Walking Tour Part 1 - Midtown
Manhattan (4k Ultra HD 60fps) - With Captions"* (AV1, 3840×2160,
60000/1001 fps, SDR BT.709). No download URL was kept. Provenance is the
title alone (**URL unverified**).

Excerpt: 60 s from 00:03:05 (3597 frames). Every variant is encoded directly
from the source (no chained re-encode), with a fixed GOP of 60, Lanczos
downscaling for 1080p and 1440p, and no upscaling:

| File | Resolution | Encoder |
|---|---|---|
| `res1080_h264.mp4`, `res1440_h264.mp4`, `res2160_h264.mp4` | 1920×1080, 2560×1440, 3840×2160 | libx264 `-preset medium -crf 18` |
| `res1080_hevc.mp4`, `res2160_hevc.mp4` | 1080p, 4K | libx265 `-preset medium -crf 20` |
| `res1080_av1.mp4`, `res2160_av1.mp4` | 1080p, 4K | SVT-AV1 1.7.0 `libsvtav1 -preset 6 -crf 30` |

```bash
SRC=<source.mp4> DST=~/ndp_dataset ./host/make_dataset_e1e5.sh        # SS=00:03:05 DUR=60 GOP=60 by default
```

---

## 3. Bring-up order

### 3.1 Target (after each boot)

```bash
cd target/spdk
sudo HUGEMEM=16384 scripts/setup.sh     # 16 GiB of 2 MB hugepages, bind the NVMe SSD to vfio-pci
sudo scripts/setup.sh status
```

Our target ended up with 8192 × 2 MB hugepages and the SSD on `vfio-pci`,
owned by the normal user. The exact command that produced this state was
not recorded, so `HUGEMEM=16384` reproduces the observed state (**unverified**).

Start the target. Root is not required after `setup.sh`. The reactor runs on
core 0 (default mask `0x1`), and NDP workers and decoder threads are pinned
to cores 2–15:

```bash
cd target/spdk
setsid nohup build/bin/nvmf_tgt_measure_v3 > ~/nvmf_tgt_measure.log 2>&1 < /dev/null &
cd ../..
SUDO= target/scripts/conf_subsystem.sh  # TCP transport, NVMe bdev, subsystem, listener (drop SUDO= if nvmf_tgt runs as root)
```

`conf_subsystem.sh` defaults: `TARGET_IP=192.168.100.2`,
`TARGET_PORT=4420`, `NVME_PCIE_ADDR=0000:82:00.0`,
NQN `nqn.2016-06.io.spdk:cnode1`. Its transport max I/O size of 2 MiB sets
MDTS = 2 MiB, which is the 0xC2 chunk size.

> **Never run `rpc.py bdev_get_bdevs`** on this SPDK tree. It crashes the
> target. Use `rpc.py bdev_get_iostat` instead.

Stop: `target/spdk/scripts/rpc.py spdk_kill_instance SIGTERM`.

### 3.2 Host

```bash
cd host
./setup_volume.sh --check                 # identify the device; changes nothing
sudo ./setup_volume.sh --format --copy    # first time only: connect, mkfs.ext4, mount /mnt/ext4, copy inputs
sudo ./setup_volume.sh                    # later: connect + mount if needed
```

`setup_volume.sh` runs `nvme connect -t tcp -n nqn.2016-06.io.spdk:cnode1 -a 192.168.100.2 -s 4420`.
Before `mkfs` it proves that the device is the namespace of that NQN over
TCP. Do not type `mkfs` by hand: `/dev/nvme0n1` on our host is a local SSD,
and `mkfs.ext4` without `-F` aborts silently at its `(y,N)` prompt.
`--copy` also checks that no input exceeds the 0xC0 extent limit.

**After any target restart**, run

```bash
sudo ./remount_verify.sh
```

before measuring. nvme-tcp's automatic reconnect brings the controller back
**without** its namespace, and ext4 then shuts down. The script disconnects
and reconnects, replays the journal, runs a read-only `e2fsck -fn`, and
compares every input on the volume with its local copy by sha256.

Take the volume down with `sudo ./teardown_volume.sh`.

---

## 4. Measurement

### 4.1 One run

All runs read the input from the NVMe-oF volume. Baseline reads it as a file.
NDP sends its extents in 0xC0 on the raw device.

```bash
cd host
# Baseline: host decode (OpenCV/FFmpeg) + Darknet letterbox
./darknet detector video_map mot.data cfg/yolov7-tiny.cfg yolov7-tiny.weights \
  -file /mnt/ext4/res1080_h264.mp4 -sample_rate 1 -label_base 1 -points 101 -coco_map \
  -dont_show -run_id 1 -post_thresh 0.25 -warmup 3

# NDP: add -ndp -dev and the payload / resize options
./darknet detector video_map mot.data cfg/yolov7-tiny.cfg yolov7-tiny.weights \
  -file /mnt/ext4/res1080_h264.mp4 -sample_rate 1 -label_base 1 -points 101 -coco_map \
  -dont_show -run_id 2 -post_thresh 0.25 -warmup 3 \
  -ndp -dev /dev/nvme1n1 -jpeg_q 85 -chroma 420 -resize sws
```

| Option | Values | Meaning |
|---|---|---|
| `-sample_rate` | 1, 2, 5 | keep every N-th frame (0xC0 cdw11) |
| `-jpeg_q` | 85, 100, -1 | JPEG quality; -1 = raw RGB24 (0xC0 cdw12[7:0] = 101) |
| `-chroma` | 420 (444) | JPEG chroma subsampling (cdw12 bit 8); not used with raw |
| `-resize` | `sws`, `darknet` | storage-side resize: libswscale, or Darknet-compatible 2-tap (`Dk`, cdw12 bit 9) |
| `-dec_threads` | 0 = auto, N | target decoder threads (cdw14) |
| `-cv_threads` | N | host OpenCV threads (Baseline) |
| `-labels` | `labels/<seq>` | compute mAP against MOT labels |
| `-run_id` | integer | tags host `[MEASURE]` and target `[NDP-MEASURE]` (cdw13) |
| `-dump_dets` | path | per-detection CSV |

The six NDP configurations are `-resize {sws,darknet}` × `-jpeg_q {85,100,-1}`.

### 4.2 Full matrix

`run_all.sh` drops the host page cache before every run, repeats each
condition 3 times (repeat as the outermost loop), and writes one CSV row per
run from the host `[MEASURE]` line, the host and target NIC counters, the
target block iostat, and the target `[NDP-MEASURE]` line (fetched over ssh
when `TARGET_HOST` is set).

```bash
cd host
sudo -E ./run_all.sh --smoke                # 4 sanity checks
sudo -E ./run_all.sh E1 E5                  # or any of the steps below; no argument = all
sudo ./start_main.sh E1 E5                  # same, detached, after remount_verify.sh
```

| Step | Inputs | Pipelines | Sampling |
|---|---|---|---|
| E1 resolution | `res{1080,1440,2160}_h264` | Baseline, q85, q100, raw (sws) | 1, 2, 5 |
| E5 codec | `res{1080,2160}_{hevc,av1}` | Baseline, q85 (sws) | 1 |
| E2 scene + accuracy | 5 MOT sequences | Baseline, q85, q100, raw (sws) | 1, 2, 5 |
| E3 resize ablation | 5 MOT sequences | q85, q100, raw (darknet) | 1 |

Main settings in `run_all.sh` (environment overrides): `NVME_DEV=/dev/nvme1n1`,
`NVME_MNT=/mnt/ext4`, `HOST_NIC`, `TARGET_NIC`, `TARGET_HOST`, `TARGET_LOG`,
`TARGET_RPC`, `RESULTS=~/ndp_results`, `REPEATS=3`. The other blocks in
`run_all.sh` (M4*, S*, pilot steps) are the follow-up measurements described
in `host/docs/notes/`.

### 4.3 What the paper reports

From the target `[NDP-MEASURE]` line:

| Quantity | Field | Note |
|---|---|---|
| storage preprocessing time | `pre_total` | decode loop wall time; excludes SSD read, FFmpeg init, serialization |
| SSD read | `ssd_read_ms` | |
| serialization | `serialize_ms` | |
| storage CPU | `cpu_storage_s` | process (user + sys) − reactor thread CPU |
| storage memory | `storage_mem_alloc_mb` | `dma_readbuf + dma_blob + heap_inuse`; heap part is a lower bound. Use `nvmf_tgt_measure_v3` |

The `decode` field is the time spent waiting in `avcodec_receive_frame()` and
is near 0 for frame-threaded H.264/HEVC. It is **not** decode time. Do not use
`heap_peak_delta_mb` or `storage_mem_total_mb`: they are RSS-based and depend
on request order.

Units: transfer sizes in MB (10⁶ B), memory in MiB (2²⁰ B). The NIC byte
count equals payload plus the NDP metadata read when the file is opened
(3–5 MB), times 1.0456–1.0486 for protocol overhead.

Analysis scripts in `host/` (`analysis_04.py`, `analysis_05.py`,
`analysis_04_acc.py`, `map_bootstrap.py`, `person_followup.py`,
`seq_2x2.py`, `codec_breakdown.py`, `make_fig13.py`) turn the CSVs and
detection dumps into the paper's tables and figure data.

---

## 5. Command format (0xC0 / 0xC2)

Opcodes: `target/spdk/include/spdk/nvme_spec.h`. Handlers:
`target/spdk/lib/nvmf/ctrlr_bdev.c`.

**0xC0 PREPROCESS** (data host → controller; the direction is forced in
`nvmf_transport.h`). The data buffer is the file's extent list: 16 B per
extent, `u64 lba, u64 blocks`, little endian, in 4 KiB units.

| Field | Meaning |
|---|---|
| cdw10 | number of extents |
| cdw11[15:0] | sampling interval (0 → 1) |
| cdw12[7:0] | JPEG quality 1–100, 101 = raw RGB24 (0 or out of range → 85) |
| cdw12[8] | 1 = JPEG 4:4:4 (default 4:2:0) |
| cdw12[9] | resize: 0 = sws, 1 = Darknet-compatible |
| cdw13 | run_id |
| cdw14 | decoder thread count, 0 = auto |

The command completes only after preprocessing ends. `cdw0` is the result
size in bytes. Output frames are 416×416, letterboxed with value 128. A
second 0xC0 during processing is rejected, but only after its SSD read, so
the host must issue 0xC0 serially.

**0xC2 GET_RESULT** (controller → host). cdw10 is the byte offset. The host
reads in chunks of up to 2 MiB (MDTS), advancing the offset. The chunk with
`offset + length ≥ total` frees the buffer and returns the target to idle.

Result buffer: `u32 count`, `u32 size[count]`, then `count` payloads (JPEG
files, or raw RGB24 416×416×3 HWC).

---

## 6. Not verified

| Item | Reason |
|---|---|
| Full build from a fresh clone (host and target) | only `make` of the host tree was checked on the rewritten history |
| CMake build of `host/` | the upstream CCM submodule was removed; the Makefile build is the supported path |
| `pkgdep.sh` on the target | not recorded |
| Exact hugepage command | the recorded command (`HUGEMEM=8192`) does not match the observed 16 GiB |
| Exact SPDK upstream commit | 24.09-pre was imported as a file snapshot |
| Source video URL | only the container title survived |
| Hardware values vs. the paper's Table 1 | read from the machines, not cross-checked |
