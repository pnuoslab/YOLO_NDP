# YOLO_NDP

Near-data preprocessing for video object detection over NVMe-oF. The host runs
YOLOv7-tiny in Darknet. Instead of reading a compressed video from a networked
SSD and preprocessing it locally, the host sends two vendor-specific NVMe
commands to an SPDK NVMe-oF/TCP target. **0xC0** (PREPROCESS) carries the
file's extents and makes the storage node read the video, decode it, sample
every N-th frame, letterbox-resize it to 416×416 and encode it (JPEG q85/q100
or raw RGB24). **0xC2** (GET_RESULT) fetches the result buffer back in 2 MiB
chunks. The host then goes straight to inference.

## Layout

```
YOLO_NDP/
├── README.md
├── docs/reproduce.md      how to rebuild the setup and rerun the measurements
├── host/                  Darknet (AlexeyAB/darknet @ 59596d7) + NDP host path + harness
│   ├── src/detector.c     video_map: Baseline and NDP pipelines, [MEASURE] line
│   ├── src/image_opencv.* OpenCV decode / resize helpers used by video_map
│   ├── *.sh, *.py         measurement harness, dataset preparation, analysis
│   └── docs/notes/        research notes (Korean), internal addresses masked
├── target/                NDP platform: SPDK v24.09-pre + nvme-cli (+ HEaaN NDP docs)
│   ├── spdk/lib/nvmf/ctrlr_bdev.c   0xC0/0xC2 handlers, decode/resize/encode worker,
│   │                                [NDP-MEASURE] line
│   ├── spdk/lib/nvmf/ctrlr.c, include/spdk/nvme_spec.h, nvmf_transport.h
│   │                                opcode definition, dispatch, data direction
│   ├── spdk/lib/nvmf/nvmf_image.*, ndp_cv.cpp   image helpers (ndp_cv.cpp: linked, not on the paper's path)
│   ├── spdk/test/ndp/               stand-alone probes used to check the target code
│   └── scripts/           conf_subsystem.sh, target_probe.sh, monitor_rss.sh, ...
└── .gitmodules            SPDK submodules (target/spdk/{dpdk,isa-l,...})
```

### What each side changes

| Side | Files | Role |
|---|---|---|
| host | `src/detector.c` (+1.8 k lines), `src/image_opencv.cpp/.h`, `src/demo.c` | `detector video_map`: reads the video either locally (Baseline: OpenCV decode + Darknet letterbox) or through 0xC0/0xC2 (`-ndp`), runs inference, computes mAP against MOT labels, prints one `[MEASURE]` line per run |
| host | `Makefile` | `GPU=1 CUDNN=1 OPENCV=1 OPENMP=1` |
| host | `cfg/yolov7-tiny.cfg`, `mot.data` | batch=1; MOT person-class data file |
| host | `run_all.sh`, `collect_row.py`, `setup_volume.sh`, `remount_verify.sh`, ... | measurement harness (see `docs/reproduce.md`) |
| target | `spdk/lib/nvmf/ctrlr_bdev.c` (+1.5 k lines) | 0xC0: read extents into a DMA buffer, spawn a worker that decodes with FFmpeg, samples, resizes (`sws` = libswscale, or `Dk` = Darknet-compatible 2-tap resize), encodes with libjpeg-turbo; 0xC2: return the result in chunks |
| target | `spdk/lib/nvmf/ctrlr.c`, `include/spdk/nvme_spec.h`, `include/spdk/nvmf_transport.h`, `lib/nvmf/Makefile`, `mk/spdk.app.mk` | opcode 0xC0/0xC2, dispatch, data direction, link FFmpeg / libjpeg / OpenCV |

## Environment (measured machines)

| | Host | Storage node (target) |
|---|---|---|
| CPU | AMD EPYC 9554, 1 × 64 cores, 128 threads | 2 × Intel Xeon E5-2620 v4 @ 2.10 GHz, 32 threads |
| Memory | 755 GiB (`MemTotal`) | 62.8 GiB (`MemTotal`) |
| GPU | NVIDIA RTX A6000 48 GB, driver 580.178.04, CUDA 12.0, cuDNN 9.20 | — |
| SSD | — (remote namespace via NVMe/TCP) | Intel DC P4500 (U.2), 512 B LBA |
| NIC | Intel X540-AT2 10 GbE, point-to-point /30 link | Intel X540-AT2 10 GbE |
| OS / kernel | Ubuntu 24.04.2, 7.0.0-38-generic | Ubuntu 22.04.5, 6.8.0-87-generic |
| Compiler | gcc 13.3.0 | gcc 11.4.0 |
| Libraries | OpenCV 4.6.0, FFmpeg 6.1.1 (via OpenCV), nvme-cli 2.10.2 / libnvme 1.11 | SPDK 24.09-pre, DPDK 23.07, FFmpeg 4.4.2, libdav1d 0.9.2, libjpeg-turbo 2.1.2, OpenCV 4.5.4 |

These are read from the two machines. They have not been checked against the
paper's Table 1.

**Build condition of the target.** The measured target was built with
`./configure --enable-debug` (`-O0`). FFmpeg, libswscale and libjpeg-turbo are
the optimized Ubuntu builds. Decoding, color conversion and JPEG encoding times
therefore reflect optimized library code. The Darknet-compatible resize and the
SPDK I/O path may be slower than in an optimized build. To compare with the
paper, build the target the same way (see `docs/reproduce.md`).

## Build (summary)

```bash
git clone git@github.com:pnuoslab/YOLO_NDP.git && cd YOLO_NDP

# host
cd host && make -j"$(nproc)"          # ./darknet; needs CUDA, cuDNN, OpenCV 4 dev packages

# target
git submodule update --init --recursive
cd target/spdk && ./configure --enable-debug && make -j"$(nproc)"
```

Full steps, data preparation and measurement commands: [`docs/reproduce.md`](docs/reproduce.md).

## License

- `host/`: Darknet's original YOLO License (`host/LICENSE`, "YOLO LICENSE Version 2, July 29 2016"), kept as upstream ships it.
- `target/spdk/`: SPDK, BSD-3-Clause (`target/spdk/LICENSE`, `target/spdk/licenses/`). The SPDK submodules carry their own licenses.
- `target/nvme-cli/`: GPL-2.0 (`target/nvme-cli/LICENSE`).
- Our changes are under the license of the component they modify.

## Citation

TBD
