# result_02_host — 재측정 준비: 계측 보강, 데이터셋, 측정 스크립트 (host: supercxl)

작성: 2026-10-19. 명세 02-H 기준. **§6 경로 확인 테스트와 §9 스모크 테스트는 NVMe-oF 연결과 target 기동이 필요해 아직 수행하지 못했다** — §7절에 사용자 요청 사항을 정리했다.

## 사전 결정 사항 (사용자 확인 완료)

| 항목 | 결정 | 이유 |
|---|---|---|
| E1/E5 클립 길이 | **60초** (명세의 "원본 전체 길이" 대신) | 원본 71분이면 Baseline 4K 1회가 8.8시간, E1 Baseline만 136시간. 60초면 전체 357회가 약 8시간. 이전 그림 6의 "1min" 지점과도 호환 |
| MOT 데이터셋 | **MOT17.zip + MOT20.zip 모두 다운로드** | 명세 §5-2의 5개 시퀀스 전부 확보 |

---

## 1. 커밋 해시

| 시점 | 해시 | 내용 |
|---|---|---|
| §2 변경 전 스냅샷 | **`14dc85fb4f80931da642a3d59ea2c0140fc9b23c`** | `Snapshot of host code used for MOT17 experiments (Sep 2026)` |
| §10 계측 + 하네스 | **`644b677aa2779545d71c4495e1906659b67b415b`** | `Instrument the measurement path and add the re-measurement harness` |
| §10 경로 확인 + 라벨 폭 override | **`e323d7ce008e8ce666d9cdf91fa88a3b0196b179`** | `Add the section 6 path-check script; let label width be overridden` |
| §10 보고서 + PSNR 수정 | 이 문서를 담은 커밋 (`git log -1`) | `Add spec 01/02 host-side reports; fix PSNR frame pairing` |

### §2 커밋 내역
`git status`의 변경분 중 **실험에 쓰인 소스와 스크립트만** 담았다:

```
M src/detector.c           M src/image_opencv.cpp     M src/image_opencv.h
A run_measure.sh           A run_measure_sws.sh       A run_measure_4k.sh
A run_ablation.sh          A run_map_chroma.sh        A run_map_test.sh
A mot17_gt_to_yolo.py      A mot.data
```

제외한 것과 이유:
- `maplogs/`, `bad.list`, `out/` — 측정 로그·생성물 (명세 지시)
- `*.mp4`, `labels/`, `MOT17-*/` — 영상·데이터
- `src/detector_backup.c` — 2026-08-25자 `detector.c` 사본. 실험에 쓰인 소스가 아니라 작업 중 백업이므로 제외했다. 보존이 필요하면 별도로 알려주면 커밋한다.
- `result_01_host.md` — 보고서이므로 §10 커밋에 포함

---

## 2. §3 계측 보강

### 2-1. 변경 요약

| 항목 | 파일:줄 | 내용 |
|---|---|---|
| Baseline 세부 타이머 | [image_opencv.cpp:859-927](src/image_opencv.cpp:859) | 누적 변수 4개 + `bl_timers_reset()`, `get_image_from_stream_letterbox()` 4구간 계측 |
| 누적 변수 선언 | [image_opencv.h:52-68](src/image_opencv.h:52) | `extern double bl_t_*`, 각 구간의 정의를 주석으로 고정 |
| CPU 시간 헬퍼 | [detector.c:1204-1220](src/detector.c:1204) | `vmap_cpu_times()`, `vmap_cpu_total()` |
| 단계별 CPU 누적 | [detector.c:1492-1537](src/detector.c:1492) | 전처리·추론 구간 앞뒤 `getrusage` |
| 루프 CPU 총합 | [detector.c:1664-1670](src/detector.c:1664) | 루프 직전/직후 차분 |
| 탐지 덤프 | [detector.c:1789-1820](src/detector.c:1789) | `-dump_dets`, 모든 타이머 바깥에서 1회 기록 |
| run ID | [detector.c:2983-3040](src/detector.c:2983) | `-run_id`, 0xC0 **cdw13**에 실어 전송 |
| 전송량 64bit | [detector.c:1323](src/detector.c:1323) | `uint32_t` → `uint64_t transfer_bytes` |
| 타임아웃 | [detector.c:49](src/detector.c:49) | `NDP_NVME_TIMEOUT_MS` 120000 → **600000** |

`git diff --stat 14dc85f`: `src/detector.c` +144, `src/image_opencv.cpp` +36, `src/image_opencv.h` +17 (179 insertions, 18 deletions).

**Baseline 처리 경로는 바꾸지 않았다.** 전해상도 `cv::resize` 사본, `cvtColor`, `mat_to_image`, `letterbox_image` 모두 그대로다. 추가한 것은 `what_time_is_it_now()` 호출과 `getrusage()` 호출뿐이다.

### 2-2. 새 `[MEASURE]` 필드 정의

기존 18개 필드와 그 순서는 그대로 두고 뒤에 9개를 덧붙였다.

| 필드 | 단위 | 정의 |
|---|---|---|
| `run_id` | — | `-run_id` 로 받은 32bit 태그. 0xC0 cdw13으로 target에 전달됨. 0 = 미지정 |
| `bl_decode_s` | s | `get_capture_frame_cv()` + `wait_for_stream()` + `release_mat()` (파일 읽기 + 디코딩 + 디코드 버퍼 해제) |
| `bl_copy_s` | s | 전해상도 `cv::Mat` 사본 생성(`cv::resize`) + `cvtColor(RGB2BGR)` |
| `bl_mat2img_s` | s | `mat_to_image()` + 그 전해상도 float 버퍼의 `free_image()` |
| `bl_letterbox_s` | s | `letterbox_image()` |
| `host_cpu_user_s` | s | 프레임 루프 전체의 user CPU 시간 (`RUSAGE_SELF`, 전 스레드) |
| `host_cpu_sys_s` | s | 같은 구간의 sys CPU 시간 |
| `host_cpu_pre_s` | s | 전처리 구간에 귀속된 CPU 시간 (user+sys) |
| `host_cpu_infer_s` | s | 추론 구간에 귀속된 CPU 시간 (user+sys) |

구현 주의점:
- **`bl_*` 4개의 합 = `host_pre`** 가 되도록 각 구간이 자기가 할당한 버퍼의 해제까지 담당한다. 미포함은 호출자 쪽 `release_mat(&in_img)` 하나뿐이다. 실측 확인: §5-1.
- NDP 모드에서 `bl_*` 는 모두 0이다. 네 구간이 `get_image_from_stream_letterbox()` 안에 있고 NDP는 그 함수를 호출하지 않는다.
- `getrusage()` 는 **wall-clock 창 바깥**에서 읽는다(CPU 읽기 → 시계 읽기 → 작업 → 시계 읽기 → CPU 읽기). 따라서 `t_pre`/`t_infer` 값에 syscall 비용이 섞이지 않는다. 9월 측정값과 같은 정의가 유지된다.
- **CUDA 동기화 방식은 바꾸지 않았다.** 기본 설정이라 추론 중 CPU가 spin-wait 하며, 그 비용이 `host_cpu_infer_s` 에 포함된다.

### 2-3. §3-2 주의 2: 라벨 없는 실행에서 루프에 남는 비측정 작업

코드로 확인한 결과, 성능 전용(`-labels` 없음) 실행에서도 프레임 루프 안에는 어떤 타이머에도 들어가지 않는 작업이 남는다:

| 작업 | 위치 | 라벨 없을 때도 실행? |
|---|---|---|
| `get_network_boxes()` | [detector.c:1537](src/detector.c:1537) | **예** (조건 분기 없음) |
| `do_nms_sort()` / `diounms_sort()` | [detector.c:1543](src/detector.c:1543) | **예** |
| `dets_all[]` 적재 + IoU 매칭 루프 | [detector.c:1560-1610](src/detector.c:1560) | **예** (`num_labels`가 0이라 IoU 내부 루프만 비어 있음) |
| `free_detections()`, `free_image()` | [detector.c:1640](src/detector.c:1640) | 예 |
| **`consume_frame()` × (sample_rate−1)** | [detector.c:1648](src/detector.c:1648) | 예 — Baseline만 |
| `read_boxes()` + 라벨 집계 | [detector.c:1546](src/detector.c:1546) | 아니오 (`perf_only` 가드) |

→ 즉 성능 전용 실행에서 `e2e − (target_pre+io+host_pre+infer)` 는 0이 아니고, 위 작업들의 합이다. 4K 15s 9월 로그에서 Baseline `e2e=111.854` vs 단계 합 `94.739` 의 차이 17.1s가 이것이다.

**가장 주의할 것(새로 발견):** `consume_frame()` 이 `t_pre` 창 **바깥**에 있다. 그래서 `sample_rate > 1` 인 Baseline 실행에서는 **건너뛴 프레임의 디코딩 비용이 `host_pre` 에 전혀 들어가지 않는다.** 샘플링 Baseline의 `host_pre` 는 실제 전처리 작업량을 과소평가하며, 그 차이는 `e2e` 에만 나타난다. 샘플링별 비교를 본문에 쓸 때 `host_pre` 를 그대로 쓰면 안 된다. (`e2e` 를 쓰거나, `consume_frame` 도 `bl_decode_s` 에 포함되게 고치는 것이 맞다 — §8의 결정 필요 항목에 올렸다.)

### 2-4. §3-3 탐지 덤프

- 옵션: `-dump_dets <path>`. 형식 `frame_idx,class_id,conf,x,y,w,h`.
- `frame_idx` = `image_index * sample_rate` (원본 영상 0-based 프레임 번호).
- 좌표는 **원본 해상도 픽셀, 좌상단 x·y + 폭·높이**. `get_network_boxes()` 에 원본 해상도를 넘기므로 박스는 이미 레터박스 역변환이 끝난 정규화 좌표이고, 거기에 `orig_w`/`orig_h` 를 곱해 중심→좌상단으로 변환한다.
- 신뢰도 하한 0.005, NMS 0.45 — mAP 경로와 동일. 모든 클래스 기록.
- **성능 전용 실행에서도 동작한다.** 박스 계산이 `perf_only` 로 분기되지 않기 때문에 별도 처리가 필요 없었다(§2-3 표). 덤프는 이미 적재된 `dets_all[]` 을 그대로 쓴다.
- 쓰기는 `measure_only:` 라벨 직후, **모든 타이머를 읽은 뒤** 한 번에 수행한다. 루프 안에 파일 I/O가 전혀 추가되지 않는다.
- **박스를 프레임 경계로 클리핑하지 않는다.** darknet의 `correct_yolo_boxes()` 가 클리핑하지 않으므로 mAP 경로가 보는 박스와 덤프가 같아야 하기 때문이다. 실측에서 x는 −6.5~1922.8 (W=1920), y는 −3.3~1080.0 범위로 프레임을 약간 벗어나는 박스가 있다. 정답 CSV도 같은 이유로 클리핑하지 않았다(§4).

### 2-5. §3-4 run ID

- `-run_id <n>` → `ndp_send_0xc0()` 의 `cmd.cdw13`. 기존 cdw10(extent 수)·cdw11(샘플링)·cdw12(커널·품질·크로마)는 건드리지 않았다.
- `[NDP] Sending 0xC0 (... run_id=%u)` 로 host 쪽에도 남고, `[MEASURE] ... run_id=` 로 출력된다.
- `ndp_test` 경로는 `run_id=0` 을 넘겨 기존 동작을 유지한다.
- target이 이 값을 자기 `[NDP-MEASURE]` 줄에 되돌려 주어야 짝짓기가 완성된다 — **target 명세 §3-5와 맞춰야 하는 지점.** `collect_row.py` 는 host와 target의 `run_id` 가 요청값과 다르면 그 행의 `status` 를 `fail_runid_mismatch` / `fail_tgt_runid_mismatch` 로 기록한다.

### 2-6. 계측에 남은 한계 (측정 전에 알고 있어야 할 것)

1. **`what_time_is_it_now()` 는 `gettimeofday()`, 즉 `CLOCK_REALTIME` 이다**([utils.c:55-62](src/utils.c:55)). NTP 보정이 들어오면 구간 시간이 왜곡될 수 있다. 9월 측정과 정의를 맞추려고 그대로 뒀다. 측정 중 `chronyd`/`systemd-timesyncd` 를 멈추는 편이 안전하다 — §8 결정 필요 항목.
2. **단계별 CPU 귀속은 근사치다.** `RUSAGE_SELF` 는 전 스레드를 센다. FFmpeg 디코더 스레드는 `get_capture_frame_cv()` 가 반환한 뒤에도 계속 돌기 때문에, 그 CPU가 때마침 실행 중인 다른 단계로 흘러 들어간다. 실측에서 1080p Baseline의 `host_cpu_pre_s=12.3s` vs `host_pre=1.85s` (약 6.7배)인 것이 이 멀티스레드 디코딩 때문이다.
3. **`host_rss_mb` 에는 계측 하네스 자신의 메모리가 섞여 있다.** `dets_all[]` 이 클립 전체의 모든 탐지를 누적하므로(mAP 적분에 필요) 클립이 길고 사람이 많을수록 커진다. 엔트리당 40 B 이므로 4K 3597프레임에서 약 10 MB, MOT20-03에서 약 7 MB 수준이라 4 GB 규모의 Baseline RSS에 비하면 작지만, **"NDP가 메모리를 N배 적게 쓴다"를 주장할 때 분모·분자 양쪽에 공통으로 들어가는 하네스 몫이라는 점은 알고 있어야 한다.** 9월 코드에도 있던 것이라 과거 수치와의 비교에는 영향이 없다. `-dump_dets` 는 이 배열을 재사용하므로 RSS를 추가로 늘리지 않는다.
4. **첫 프레임의 cuDNN autotune이 `t_infer` 에 포함된다**(워밍업 없음, 명세 §1대로 유지). 짧은 실행에서는 이게 지배적이다: 105프레임 실행의 `infer=0.364s`(3.5ms/frame) vs 1050프레임 실행의 2.959s(2.8ms/frame). **프레임 수가 다른 조건 간 추론 시간을 직접 비교하면 안 된다.**

---

## 3. §5 데이터셋

### 3-1. 원본(마스터) 영상과 그 출처

| 항목 | 값 |
|---|---|
| 경로 | `~/testd_video.mp4` |
| 코덱 | AV1 (Main), yuv420p **8bit** |
| 해상도 / fps | 3840×2160 / 60000/1001 (59.94) |
| 프레임 수 | 255,735 |
| 길이 / 크기 | 4266.574 s (71분 7초) / 7,148,357,243 B |
| 색 | BT.709 primaries / transfer / space → **SDR. 톤매핑 불필요** |
| 컨테이너 title 태그 | `New York City Walking Tour Part 1 - Midtown Manhattan (4k Ultra HD 60fps) – With Captions` |
| date 태그 | `2019:12:31 10:40:18`, encoder `Lavf58.29.100` |

**출처 URL·라이선스: 확인 불가.** `~/.bash_history` 에 `yt-dlp`/`youtube-dl`/`wget`/`curl` 로 이 파일을 받은 기록이 없고(2000줄 전체 검색), `.info.json` 류 부가 파일도 없다. 컨테이너 title만 남아 있다.

> **논문 Data Availability 경고.** 제목 형태로 보아 YouTube의 워킹 투어 영상이다. 그렇다면 표준 YouTube 라이선스일 가능성이 높아 **재배포가 불가능하고, 논문에 "공개 데이터"로 적을 수 없다.** 또한 재현성 측면에서 URL 없이는 제3자가 같은 입력을 얻을 수 없다. §8의 결정 필요 항목 1번으로 올렸다.

### 3-2. 발췌 구간

`-ss 00:03:05 -t 60`. 시작점은 이전 클립 세트가 쓴 값(`~/.bash_history`: `SRC=$HOME/testd_video.mp4 SS=00:03:05 DUR=30`)을 그대로 따랐다. 길이는 60초(사용자 결정).

### 3-3. 생성 스크립트

`~/ndp_dataset/make_dataset.sh` — 모든 생성 명령이 여기 들어 있다. `./make_dataset.sh [probe|res|codec|psnr]`.

설계 원칙(이전 `~/make_dataset.sh` 에서 계승):
1. 모든 변형을 **원본에서 직접** 생성. 4K→1440p→1080p 체인 인코딩은 손실이 누적되어 해상도 비교를 오염시킨다.
2. **GOP 고정**(`-g 60`). GOP 구조가 다르면 복호화 시간 차이가 코덱 때문인지 GOP 때문인지 구분되지 않는다.
3. **업스케일 없음** (모든 목표 해상도 ≤ 원본).
4. 전부 8bit yuv420p, 오디오 제거(`-an`), 축소는 `scale=W:H:flags=lanczos`.

사용한 인코더 (`ffmpeg -encoders` 로 확인: `libx264`, `libx265`, `libsvtav1`, `libaom-av1` 모두 존재):
- H.264 `libx264 -preset medium -crf 18`
- H.265 `libx265 -preset medium -crf 20 -tag:v hvc1`
- AV1 **`libsvtav1 -preset 6 -crf 30`** (`libaom-av1` 도 있으나 4K에서 수 시간이 걸려 제외)

### 3-4. 생성된 파일

| 파일 | 해상도 | 코덱 | 설정 | 프레임 | 비트레이트 | 크기 (B) | PSNR-Y (dB) |
|---|---|---|---|---|---|---|---|
| `res1080_h264.mp4` | 1920×1080 | H.264 | x264 medium crf 18 | 3597 | 20.3 Mbps | 152,589,505 | 40.58 |
| `res1440_h264.mp4` | 2560×1440 | H.264 | x264 medium crf 18 | 3597 | 29.5 Mbps | 221,391,396 | 41.81 |
| `res2160_h264.mp4` | 3840×2160 | H.264 | x264 medium crf 18 | 3597 | 50.4 Mbps | 377,828,726 | 44.03 |
| `res1080_hevc.mp4` | 1920×1080 | H.265 | x265 medium crf 20 | 3597 | 14.6 Mbps | 109,441,824 | 40.56 |
| `res2160_hevc.mp4` | 3840×2160 | H.265 | x265 medium crf 20 | 3597 | 31.5 Mbps | 236,454,199 | 44.26 |
| `res1080_av1.mp4` | 1920×1080 | AV1 | svtav1 preset 6 crf 30 | 3597 | 14.1 Mbps | 106,032,057 | 41.45 |
| `res2160_av1.mp4` | 3840×2160 | AV1 | svtav1 preset 6 crf 30 | 3597 | 30.9 Mbps | 231,453,240 | 44.64 |

**프레임 수가 7개 모두 3597로 동일하다.** 전부 8bit yuv420p, 오디오 없음, SDR BT.709.

PSNR 기준은 **같은 원본 발췌를 변형과 동일한 lanczos로 그 해상도까지 축소한 것**이다. 1080p 파일을 4K 기준과 비교하면 축소 손실을 재는 셈이 되므로, 이렇게 해야 "그 해상도에서 인코더가 잃은 양"이 나온다.

**화질은 CRF를 조정하지 않고 그대로 보고한다**(명세 §5-1.2 지시). 결과적으로 운이 좋았다:
- **같은 해상도에서 코덱 간 PSNR 차이가 작다.** 1080p는 40.56/40.56/41.45 (h264/hevc/av1), 4K는 44.03/44.26/44.64. 최대 0.9 dB 차이다. **따라서 E5의 코덱 비교가 화질 차이로 오염되지 않는다** — 복호화 비용 차이를 코덱 자체의 특성으로 읽을 수 있다.
- 같은 화질을 HEVC는 H.264의 **72%**, AV1은 **70%** 비트레이트로 달성했다(1080p 기준). 전송량 비교(그림 8 계열)에서 Baseline 쪽 코덱 선택이 결과를 크게 바꾼다는 뜻이므로, 본문에 Baseline이 어떤 코덱인지 명시해야 한다.
- 해상도가 올라가면 PSNR도 올라간다(40.6 → 41.8 → 44.0). 축소된 기준 영상이 더 매끄러워 같은 CRF로 더 쉽게 코딩되기 때문이다(**추정**). 해상도 간 PSNR을 "화질이 같다"는 근거로 쓸 수는 없다.

> **측정 중 걸러낸 함정 — 재현하는 사람을 위해 기록한다.** 첫 PSNR 측정은 7개 파일 전부 **22.8~23.0 dB** 라는 값을 냈다. crf 18 x264가 23 dB일 수는 없는데, 값이 균일해서 오류처럼 보이지 않았다. 원인은 `psnr` 필터의 framesync가 프레임을 **타임스탬프로 짝짓는다**는 점이다. 기준 입력에 `-ss` 를 쓰면 두 스트림의 PTS가 1프레임 미만 어긋나고, **첫 프레임만 맞고 그 뒤 전부 틀린 프레임끼리 비교된다**(`min:20.8 max:44.0` 로 드러났다). 단일 프레임을 추출해 직접 비교하니 0·50·500·1000·3000번 프레임 모두 약 44 dB였다. 양쪽 체인에 `settb=AVTB,setpts=N/TB` 를 넣어 **프레임 인덱스로 짝짓도록** 고쳤다. 이런 종류의 오류는 "그럴듯하게 낮은 값"으로 나와서 그대로 논문 표에 들어갈 수 있다.

### 3-5. MOT 시퀀스

다운로드: `https://motchallenge.net/data/MOT17.zip`, `https://motchallenge.net/data/MOT20.zip` → `~/ndp_dataset/downloads/`.

| 아카이브 | 페이지 표기 | 실제 크기 | sha256 |
|---|---|---|---|
| MOT17.zip | 5.5 GB | 5,860,214,001 B | `7b486bb2af5cfedbfabc020511ff933a3edbf12182ce4985eec1a210f20f9e06` |
| MOT20.zip | 5.0 GB | 5,028,926,248 B | `ebcf0e3d44e4f50b5357d24817e5db485d777633d1b8ca9e8380d1c8437dbdd7` |

(라벨만 받는 경로도 있다: `MOT17Labels.zip` 9.7 MB, `MOT20Labels.zip` 13.9 MB. 우리는 프레임이 필요해 전체를 받았다.)

**이용 조건**: 양쪽 모두 **CC BY-NC-SA 3.0** (출처 표시, 비상업적 이용, 동일 라이선스 공유). 별도 동의서·가입 절차는 없다. MOT17의 일부 시퀀스(MOT17-01/02, 05/06)는 제3자 출처(Vimeo, ETH)가 있어 원출처 조건이 추가로 적용될 수 있다 — **우리가 쓰는 MOT17-02가 여기 해당한다**(출처 `http://vimeo.com/60139361`). 제공처: MOTChallenge, TU München Computer Vision Group.

**주의**: motchallenge.net은 현재 **운영 종료(EOL) 상태의 정적 아카이브**다. 사이트 루트(`https://motchallenge.net/`)는 HTTP 410 Gone이고, 제출·평가가 닫혔으며 아카이브 리더보드는 2026-04-16자다. 데이터 페이지와 zip 링크는 아직 동작한다(위 URL로 실제 다운로드 성공). **인용용 BibTeX는 두 페이지 어디에도 없다** — arXiv 등 다른 출처에서 MOT16/MOT17 및 MOT20 벤치마크 논문 서지를 따로 확보해야 한다(§8 결정 필요 항목).

시퀀스 정보 (`seqinfo.ini` + MOTChallenge 아카이브 페이지):

| 시퀀스 | 해상도 | fps | seqLength | tracks | boxes | density | 설명 (페이지 원문) | 카메라 |
|---|---|---|---|---|---|---|---|---|
| MOT17-02 | 1920×1080 | 30 | 600 | 62 | 18,581 | 31.0 | "People walking around a large square." | **명시 없음** |
| MOT17-04 | 1920×1080 | 30 | 1050 | 83 | 47,557 | 45.3 | "Pedestrian street at night, elevated viewpoint" | **명시 없음** (고가 시점·야간만 명시) |
| MOT17-09 | 1920×1080 | 30 | 525 | 26 | 5,325 | 10.1 | "A pedestrian street scene filmed from a low angle." | **명시 없음** |
| MOT20-02 | 1920×1080 | 25 | 2782 | 296 | 202,215 | 72.7 | "Crowded indoor train station." | **명시 없음** |
| MOT20-03 | **1173×880** | 25 | 2405 | 735 | 356,728 | 148.3 | "People leaving entrance of stadium by night time, elevated viewpoint." | **명시 없음** |

- **카메라 고정 여부는 아카이브 페이지에 구조화된 필드가 없어 5개 모두 "명시 없음"이다.** 구버전 MOTChallenge 시퀀스 페이지에 있던 camera motion / viewpoint 속성표가 아카이브본에는 빠져 있다. 설명문에서 간접적으로 읽을 수 있는 것은 MOT17-04·MOT20-03이 고가(elevated) 시점이라는 점뿐이다. **"모두 고정 카메라"라고 본문에 쓸 근거가 없다** — §8 결정 필요 항목.
- **MOT20-03은 1173×880으로 1080p가 아니다.** 해상도가 다른 시퀀스를 같은 표에 넣으면 전처리 비용 비교가 흐려진다.
- MOT17은 시퀀스마다 DPM/FRCNN/SDP 3개 변형이 있으나 `img1`·`gt`는 동일하다. `-DPM` 변형을 사용했다.

### 3-6. 영상화

`mot17_gt_to_yolo.py` 가 출력하는 `[NEXT]` 줄과 `~/.bash_history:1662` 의 실제 명령이 일치했다. **그 명령을 그대로** 썼다:

```bash
ffmpeg -framerate <frameRate> -i <seq>/img1/%06d.jpg \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -fps_mode passthrough -an <out>.mp4
```

| 출력 | 해상도 | 프레임 수 | seqLength | 일치 | 크기 |
|---|---|---|---|---|---|
| `MOT17-02.mp4` | 1920×1080 | 600 | 600 | ✔ | 30,640,759 |
| `MOT17-04.mp4` | 1920×1080 | 1050 | 1050 | ✔ | **45,616,817** |
| `MOT17-09.mp4` | 1920×1080 | 525 | 525 | ✔ | 30,136,566 |
| `MOT20-02.mp4` | 1920×1080 | 2782 | 2782 | ✔ | 150,125,761 |
| `MOT20-03.mp4` | **1174×880** | 2405 | 2405 | ✔ | 77,501,466 |

**`MOT17-04.mp4` 가 45,616,817 B 로 9월 측정 로그의 `bytes=45616817` 과 바이트 단위로 일치한다.** 즉 이번에 만든 입력이 9월 실험에 쓰인 파일과 동일하다 — 과거·신규 수치를 직접 비교할 수 있다는 뜻이고, 명세 01에서 "확인 불가"였던 MOT17_04.mp4 사양도 이로써 복원됐다(1920×1080, 30fps, 1050프레임). `MOT17-09.mp4` 도 기존 로컬 사본과 크기가 같다(30,136,566 B).

#### MOT20-03만 명령이 다르다 (폭이 홀수)

MOT20-03은 원본이 **1173×880** 이고 **1173은 홀수**다. yuv420p는 크로마를 가로로 2:1 서브샘플링하므로 폭이 짝수여야 하고, 위 명령은 `Conversion failed!` 로 죽는다. 짝수로 **패딩**해서 해결했다(크롭 대신 패딩: 영상 내용을 잃지 않는다).

```bash
ffmpeg -framerate 25 -i <seq>/img1/%06d.jpg -vf "pad=1174:880:0:0" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -fps_mode passthrough -an MOT20-03.mp4
```

패딩을 `0:0` 오프셋으로 넣어 원본이 좌상단에 놓이므로 **모든 픽셀 좌표가 보존된다**(오른쪽에 1px 검은 열이 붙을 뿐).

그런데 darknet은 **영상의 실제 폭(1174)** 으로 박스를 정규화하므로, 라벨도 1174로 정규화해야 어긋나지 않는다. `mot17_gt_to_yolo.py` 에 `--width`/`--height` override를 추가해 이 시퀀스만 1174로 정규화했다. 클리핑 경계는 원본 1173을 유지한다(패딩 영역은 검은 띠이고 거기에 객체가 있을 수 없으므로, 클리핑 경계를 늘리면 프레임 밖 박스를 잘못 살려두게 된다):

```
[INFO] MOT20-03  clip=1173x880  normalise=1174x880  2405 frames @ 25fps
```

override를 쓰지 않으면 x축에 0.085%(1173px에서 1픽셀)의 체계적 오차가 생긴다. 작지만 공짜로 없앨 수 있는 오차다. 기본 동작은 바뀌지 않았다(옵션 미지정 시 `seqinfo.ini` 값 사용).

### 3-7. OpenCV의 HEVC / AV1 열기 확인 — **통과**

두 가지로 확인했다. (a) 1080p HEVC/AV1은 `darknet video_map` 을 라벨 없이 실제 실행, (b) 4K 3종과 MOT20-03은 `cv2.VideoCapture` 로 전 프레임 `grab()` 하며 보고 프레임 수와 실제 디코드 수를 대조.

| 파일 | 해상도 | 보고 프레임 수 | 실제 디코드 | 일치 | 확인 방법 |
|---|---|---|---|---|---|
| `res1080_hevc.mp4` | 1920×1080 | 3597 | 3597 | ✔ | darknet video_map |
| `res1080_av1.mp4` | 1920×1080 | 3597 | 3597 | ✔ | darknet video_map |
| `res2160_h264.mp4` | 3840×2160 | 3597 | 3597 | ✔ | cv2 |
| `res2160_hevc.mp4` | 3840×2160 | 3597 | 3597 | ✔ | cv2 |
| `res2160_av1.mp4` | 3840×2160 | 3597 | 3597 | ✔ | cv2 |
| `res1440_h264.mp4` | 2560×1440 | 3597 | 3597 | ✔ | cv2 |
| `MOT20-03.mp4` | **1174×880** | 2405 | 2405 | ✔ | cv2 |

darknet 실행에서 `[MAP] frame N: bad image` 경고가 한 건도 없었다. **OpenCV 4.6.0 + FFmpeg 6.1.1 조합에서 H.264·HEVC·AV1 모두, 1080p부터 4K까지 정상이고 `CAP_PROP_FRAME_COUNT` 도 정확하다.** 패딩한 MOT20-03도 문제없다.

60초 발췌의 프레임 수는 7개 변형 모두 **3597** 로 동일하다(60 s × 59.94 fps ≈ 3596.4). 해상도·코덱 간 비교에서 프레임 수가 교란 변수가 되지 않는다.

> 1080p 두 실행의 **지연시간 값은 사용하지 않는다.** 4K 인코딩이 동시에 돌고 있어 CPU를 경합했고 `drop_caches` 도 하지 않았다. 확인 목적은 열림 여부와 프레임 수뿐이다. (단 §5-1의 `bl_mat2img_s` 비율은 같은 실행 안의 상대값이라 영향이 적다.)

---

## 4. §7 정답 변환

새 스크립트: [mot_gt_to_csv.py](mot_gt_to_csv.py). 기존 `mot17_gt_to_yolo.py` 는 그대로 두고 YOLO 라벨도 계속 생성했다.

출력 `gt_csv/gt_<seq>.csv`:
```
frame_idx,track_id,x,y,w,h,visibility,considered
0,1,1363.00,569.00,103.00,241.00,0.860140,1
```
- `frame_idx` = **MOT `gt.txt` 프레임 번호 − 1**. MOT는 1부터 세고, 덤프의 `frame_idx` 는 0부터 세므로 이렇게 맞춰야 같은 기준이 된다. 어긋나면 전체가 1프레임씩 밀리고 다른 증상이 없다. 스크립트가 `frame_idx` 최대값이 `seqLength` 이상이면 경고한다.
- `x,y,w,h` = 원본 해상도 픽셀, 좌상단 + 크기, `gt.txt` 값 그대로. **클리핑하지 않는다**(덤프도 클리핑하지 않으므로 양쪽 기준을 맞춤).
- MOT 클래스 1(pedestrian)만 유지. `visibility`·`considered` 는 기록만 하고 필터하지 않는다.
- **MOT20의 `gt.txt` 형식이 MOT17과 같은지**는 MOT20 추출 후 확인한다(§7).

시퀀스별 인물 수(고유 track ID):

| 시퀀스 | 유지 행 / 전체 | 고유 track ID | visibility≥0.25 & considered=1 | 프레임 밖 박스 |
|---|---|---|---|---|
| MOT17-02 | 18,581 / 30,003 | **62** | 9,393 | — |
| MOT17-04 | 47,557 / 108,005 | **83** | 39,821 | 12,319 |
| MOT17-09 | 5,325 / 10,411 | **26** | 3,745 | 592 |
| MOT20-02 | 154,742 / 202,215 | **270** | 93,107 | 4,055 |
| MOT20-03 | 313,658 / 356,728 | **702** | 278,148 | 10,514 |
| MOT17-11 (참고, 계획 외) | 9,436 / 10,617 | 75 | 7,518 | 684 |

YOLO 라벨 파일도 5개 시퀀스 전부 생성했다(프레임 수 = `seqLength`: 600 / 1050 / 525 / 2782 / 2405, 빈 프레임 0개).

**교차 검증 — 변환이 맞다는 독립적 근거.** MOTChallenge 아카이브 페이지의 공개 수치와 비교하면:

| 시퀀스 | 페이지 tracks | 내 class-1 tracks | 페이지 boxes | 내 전체 행 | 내 class-1 행 |
|---|---|---|---|---|---|
| MOT17-02 | 62 | **62** ✔ | 18,581 | 30,003 | **18,581** ✔ |
| MOT17-04 | 83 | **83** ✔ | 47,557 | 108,005 | **47,557** ✔ |
| MOT17-09 | 26 | **26** ✔ | 5,325 | 10,411 | **5,325** ✔ |
| MOT20-02 | 296 | 270 | 202,215 | **202,215** ✔ | 154,742 |
| MOT20-03 | 735 | 702 | 356,728 | **356,728** ✔ | 313,658 |

- MOT17은 페이지의 box 수가 **class 1만** 센 값이고, MOT20은 **전 클래스**를 센 값이다(MOT20-02: 154,742 + 4,021 + 11,128 + 32,324 = 202,215 로 정확히 맞는다). 벤치마크 자신의 집계 기준이 두 데이터셋에서 다르다. **논문 표에 MOT20의 "boxes"를 페이지 값 그대로 쓰면 MOT17과 다른 것을 세는 셈이 된다** — class 1 기준으로 통일해야 한다.
- track 수도 같은 이유로 MOT20에서 차이가 난다(296 vs class-1 270, 735 vs 702). 차이가 class 6/7/11/13(vehicle, static person 등)의 궤적이다.
- MOT20의 `gt.txt` 는 **MOT17과 동일한 9열 형식**이다(명세 §7 확인 항목). 다만 MOT20에서는 `considered`(7열)가 class 1 행에서 항상 1, 그 외 클래스에서 항상 0이어서 class 필터와 사실상 같은 조건이 된다.

---

## 5. §9 사전 검증 (NVMe-oF 없이 가능한 부분)

target과 NVMe-oF가 필요한 4개 스모크 실행은 아직 못 했지만, **Baseline 경로와 덤프·CSV 조립은 로컬 영상으로 검증했다.**

### 5-1. Baseline 세부 타이머 합 ≈ `host_pre` — **통과**

`MOT17_09-DPM.mp4`, 라벨 있음, `sample_rate 5`, 105프레임:

```
[MEASURE] mode=baseline ... frames=105 e2e=4.169 target_pre=0.000 io=0.000
 host_pre=1.754 infer=1.397 bytes=30136566 host_rss_mb=1567.7
 ap50=89.7474 ap5095=54.6461 run_id=424242
 bl_decode_s=0.688 bl_copy_s=0.178 bl_mat2img_s=0.583 bl_letterbox_s=0.305
 host_cpu_user_s=18.161 host_cpu_sys_s=2.114 host_cpu_pre_s=13.941 host_cpu_infer_s=2.251
```

`0.688 + 0.178 + 0.583 + 0.305 = 1.754` = `host_pre` **정확히 일치**. 두 번째 실행에서는 합 1.850 vs `host_pre` 1.851 (차이 1 ms = 타이머 호출 비용 + 호출자 `release_mat`). 오차 0.05% 수준.

부수적으로 얻은 값 — **명세 01에서 "확인 불가"였던 `mat_to_image` 53% 항목의 대체 근거가 된다.** `bl_mat2img_s / host_pre` 비율:

| 실행 | 해상도 | 프레임 | `bl_mat2img_s` / `host_pre` | 비율 |
|---|---|---|---|---|
| MOT17-09, sr=5, 라벨 있음 | 1080p | 105 | 0.583 / 1.754 | **33.2%** |
| res1080_hevc, sr=1, 성능 전용 | 1080p | 3597 | 16.647 / 34.362 | **48.4%** |
| res1080_av1, sr=1, 성능 전용 | 1080p | 3597 | 19.179 / 37.690 | **50.9%** |

1080p에서 이미 48~51%가 나온다. 두 짧은/긴 실행의 차이는 주로 `bl_decode_s` 몫이다(짧은 실행 39% vs 긴 실행 12%) — 콘텐츠와 코덱이 다르고, 긴 실행에서 멀티스레드 디코더가 충분히 더워진 영향으로 보인다(**추정**). **E1의 1080p/1440p/2160p 3점에서 이 비율을 뽑으면 "전처리의 절반이 `mat_to_image`" 주장을 해상도 의존성과 함께 코드 근거로 직접 제시할 수 있다.**

### 5-2. 덤프 CSV — **통과**

```
frame_idx,class_id,conf,x,y,w,h
475,0,0.959639,1783.90,410.67,67.09,220.35
```
- 7,480행, `frame_idx` 0..520, **고유 프레임 105개** (= `sample_rate 5` × 105 = 525 프레임 영상) ✔
- 신뢰도 0.005001..0.959639 — 하한 0.005 ✔
- 클래스 1종(0=person) — `mot.data` names가 1클래스이므로 정상
- 좌표 범위 x −6.5..1922.8 (W=1920), y −3.3..1080.0 (H=1080) → **일부 박스가 프레임을 약간 벗어난다.** 의도된 동작(§2-4)

### 5-3. `results.csv` 조립 — **통과**

`collect_row.py` 를 실제 로그 + 가짜 target JSON/iostat으로 검증:
- 49열 생성, 모든 조건 열·`[MEASURE]` 27필드·host 블록 I/O 3필드·target iostat 4필드·target `[NDP-MEASURE]` 필드가 채워짐
- host/target `run_id` 가 요청값(777)과 일치 확인, 불일치 시 `status` 에 반영되는 경로도 구현
- target의 `bytes_read` 와 iostat 차분의 `bytes_read` 가 이름 충돌할 때 후자를 `tgt_m_bytes_read` 로 피신시킴 (덮어쓰기 방지)

### 5-4. 오케스트레이터 배관 드라이런 — **통과 (버그 1건 발견·수정)**

`run_all.sh` 는 root와 NVMe-oF 볼륨을 요구하므로 §9 없이는 돌지 않는다. 배관만 확인하기 위해 `ALLOW_NO_NVME=1` 탈출구를 넣었다(페이지 캐시 드롭과 마운트 검사를 건너뛰고, **그 실행의 모든 행에 `status=ok_nodrop` 를 찍어** 실제 측정값과 섞이지 않게 한다). 이 옵션으로 E2를 로컬 영상 1개(MOT17-09)에 대해 12회 실행:

| pipeline | sr | status | frames | e2e | host_pre | infer | ap50 |
|---|---|---|---|---|---|---|---|
| BASE | 1 | `ok_nodrop` | 525 | 10.531 | 5.797 | 1.641 | 89.8878 |
| BASE | 2 | `ok_nodrop` | 263 | 6.427 | 3.503 | 0.940 | 90.1118 |
| BASE | 5 | `ok_nodrop` | 105 | 3.047 | 1.616 | 0.375 | 89.7474 |
| q85 / q100 / raw | 1,2,5 | `fail_no_measure` (9건) | | | | | |

- NDP 9건이 실패한 것은 **정상**이다(`/dev/nvme1n1` 이 없음). 실패가 건너뛰어지지 않고 `status` 에 기록된 뒤 매트릭스가 끝까지 진행된다는 것을 확인했다.
- 샘플링 프레임 수: 525 → 263(=⌈525/2⌉) → 105 ✔
- `env.txt` 에 커밋 해시·바이너리 sha256·governor·GPU 클럭이 기록됨 ✔

**드라이런에서 찾은 버그**: Baseline 실행에 `-dump_dets` 가 꺼져 있었다. 오프라인 분석은 NDP 정확도를 **Baseline 대비 상대값**으로 재고, 4K 클립은 정답이 아예 없어 Baseline 덤프가 유일한 기준이다. Baseline 덤프가 없으면 E1/E5의 정확도 분석이 불가능해진다. E1/E2/E5의 Baseline도 1회차에 덤프를 켜도록 수정했다. 스모크 테스트에는 같은 조건을 덤프 off/on으로 한 쌍(실행 1, 1b) 넣어 §8-1이 요구한 "덤프가 지연시간에 영향을 주는지" 비교가 바로 되게 했다 — 그래서 스모크는 4회가 아니라 **5회**다.

---

## 6. §8 측정 스크립트

| 파일 | 역할 |
|---|---|
| [run_all.sh](run_all.sh) | 오케스트레이터. `sudo -E ./run_all.sh [E1 E2 E3 E5]`, `--smoke` |
| [collect_row.py](collect_row.py) | 한 실행의 산출물 → `results.csv` 한 행. 열 순서가 한 곳에만 정의됨 |
| [run_pathcheck.sh](run_pathcheck.sh) | §6 경로 확인 테스트. 볼륨이 마운트되면 바로 실행 가능 |
| [make_dataset_e1e5.sh](make_dataset_e1e5.sh) | §5-1 입력 세트 생성 (`~/ndp_dataset/make_dataset.sh` 와 동일) |
| [mot_gt_to_csv.py](mot_gt_to_csv.py) | §7 사람 단위 정답 CSV |

출력: `$HOME/ndp_results/` 아래 `results.csv`, `env.txt`, `logs/`, `dumps/`, `io/`.

### 6-1. 매 실행마다 하는 일
1. `sync; echo 3 > /proc/sys/vm/drop_caches` — **Baseline·NDP 모두.** (그래서 root 필요)
2. `/sys/block/nvme1n1/stat` 저장 + ssh로 target `bdev_get_iostat` 저장
3. 실행 (`-run_id` 부여, 1회차만 `-dump_dets`)
4. 양쪽 다시 저장 + target 로그에서 `run_id` 로 `[NDP-MEASURE]` 추출
5. `collect_row.py` 로 한 행 추가. 원본 stdout은 `logs/<조건>.log` 로 별도 보존
6. `sleep 3`

- **Baseline도 NVMe-oF 볼륨에서 읽는다.** 양쪽이 같은 장치에서 같은 바이트를 읽어야 host 블록 카운터가 비교 가능하고, 명세 01에서 지적된 "Baseline은 warm cache" 문제가 해소된다.
- 실패는 건너뛰지 않고 `status=fail_exit` / `fail_no_measure` / `fail_runid_mismatch` 로 기록하고 계속 진행한다.
- 입력 파일이 없으면 `[SKIP]` 한 줄 찍고 넘어간다 — 한 파일 때문에 8시간 매트릭스가 중단되지 않게.
- `env.txt`: 양쪽 커밋 해시, 바이너리 mtime·sha256, weights sha256, OpenCV/FFmpeg 버전, **CPU governor 집계**, `nvidia-smi -q -d CLOCK`, `nvme list-subsys`, target SPDK 버전/커밋.
- `run_id` = `(epoch % 100000) * 10000 + 순번`. 357회가 순번 범위 안에 들어가고 uint32를 넘지 않으며, 스크립트를 다시 돌려도 이전 run_id와 겹치지 않는다.

### 6-2. 조건 수

| 실험 | 입력 | 파이프라인 | 샘플링 | 라벨 | 반복 | 실행 수 |
|---|---|---|---|---|---|---|
| E1 해상도 | res{1080,1440,2160}_h264 (3) | BASE, q85, q100, raw (sws) (4) | 1, 2, 5 (3) | 없음 | 3 | **108** |
| E5 코덱 | res{1080,2160}_{hevc,av1} (4) | BASE, q85 (sws) (2) | 1 | 없음 | 3 | **24** |
| E2 장면·정확도 | MOT 5개 | BASE, q85, q100, raw (sws) (4) | 1, 2, 5 (3) | 있음 | 3 | **180** |
| E3 커널 ablation | MOT 5개 | q85, q100, raw (**darknet**) (3) | 1 | 있음 | 3 | **45** |
| | | | | | **합계** | **357** |

- NDP-raw 옵션값은 코드에서 확인: **`-jpeg_q -1`** → `detector.c:3477` 에서 `NDP_JPEG_Q_RAW`(=101)로 치환된다. raw에서는 `-chroma` 가 무의미해 붙이지 않았다.
- 반복이 **가장 바깥 루프**다(1회차 전체 → 2회차 → 3회차). 같은 조건을 연속 3번 돌리지 않으므로, 머신이 드리프트하면 조건 내 추세가 아니라 회차 간 산포로 드러난다.
- 덤프는 **1회차에만** 켠다. 덤프 결과는 결정적이고, 2·3회차가 자연히 "덤프 없는 대조군"이 되어 덤프가 지연시간에 영향을 주는지 바로 확인된다.

### 6-3. 예상 소요 시간

실측값(9월 로그 + 이번 로컬 실행) 기반 추정:

| 근거 | 값 |
|---|---|
| Baseline 4K perf-only | 124.3 ms/frame (`4k_testd_15s` e2e 111.854 / 900프레임) |
| Baseline 1080p perf-only | ≈ 14 ms/frame (MOT17-04 e2e 35.5 − mAP 21.2, / 1050) |
| NDP 4K sr1 | 21.8 ms/frame (e2e 19.641 / 900) |
| 클립 프레임 수 | 60 s × 59.94 ≈ 3,596 |

| 실험 | 1회차 | ×3 |
|---|---|---|
| E1 | ≈ 56 분 | **2.8 h** |
| E5 | ≈ 33 분 | **1.6 h** |
| E2 | ≈ 46 분 | **2.3 h** |
| E3 | ≈ 11 분 | **0.6 h** |
| 실행 간 오버헤드 (357 × ~6 s: drop_caches + sleep 3 + ssh 4회) | | **0.6 h** |
| | | **≈ 8 시간** |

4K AV1(E5)의 target 측 소프트웨어 디코딩이 예상보다 느리면 이 추정이 가장 크게 틀릴 부분이다. 그래서 `NDP_NVME_TIMEOUT_MS` 를 600 s로 올려 뒀다(§2-1). **스모크 테스트 4번(res1080_av1)에서 실제 0xC0 시간을 보고 E5 추정을 다시 계산하는 것이 좋다.**

---

## 7. 사용자에게 요청 (§6, §9 수행 조건)

아래가 준비되면 §6 경로 확인 테스트와 §9 스모크 테스트를 이어서 수행한다. **`mkfs.ext4` 는 직접 실행하지 않는다.**

### 7-0. §6을 위한 스크립트는 준비돼 있다

[run_pathcheck.sh](run_pathcheck.sh) 가 명세 §6의 8단계를 자동으로 수행한다. 볼륨 마운트 후:

```bash
sudo -E TARGET_HOST=oslab-server3 ./run_pathcheck.sh
```

하는 일: ① 먼저 §6-7(ssh로 target `rpc.py bdev_get_iostat` 호출 가능 여부)을 확인하고 bdev 이름을 출력 → ② S0 저장 → ③ `dd ... oflag=direct` 512MiB 쓰기 + `sync` → S1 → ④ `drop_caches` → ⑤ 콜드 읽기 → S2 → ⑥ 캐시 안 비우고 재읽기 → S3 → ⑦ S0~S3 차분을 host 블록 카운터와 target `bdev_get_iostat` **양쪽** 표로 출력하고 자동 대조 → ⑧ 테스트 파일 삭제.

핵심 판정이 자동화돼 있다: 웜 캐시 읽기의 장치 트래픽이 명목값의 5% 미만이면 `PASS` 를 찍는다. **이것이 "9월 Baseline 측정은 장치에서 읽은 게 아니다"의 직접 증거가 된다.** 보고 로직은 합성 데이터로 미리 검증했다(차분·백분율·판정 모두 정상).

측정용 입력은 건드리지 않고 `iotest.bin` 하나만 쓰고 지운다.

### 7-1. target 기동 — **현재 `nvmf_tgt` 가 떠 있지 않다 (확인된 문제)**

`nvme connect` 가 실패하는 이유는 입력 오류가 아니다. target에 SPDK가 안 떠 있다:

```
$ ping -c2 192.168.100.2        → 0% loss, 0.2 ms      (머신과 링크는 정상)
$ /dev/tcp/192.168.100.2/4420   → Connection refused   (nvmf_tgt 미동작)
$ nvme list-subsys              → 로컬 PCIe SSD 하나뿐, nvme1n1 없음
$ ls -ld /mnt/ext4              → No such file or directory
```

링크는 살아 있으니 target에서 `nvmf_tgt` → `conf_subsystem.sh` 만 실행하면 된다.


1. target에서 `nvmf_tgt` 를 **`NDP_IO_TRACE=1` 빌드로** 기동 (§6용) → `conf_subsystem.sh`
2. §6이 끝나면 **측정용 `NDP_IO_TRACE=0` 빌드로 재기동** (§9용)
3. target이 `run_id`(cdw13)를 `[NDP-MEASURE]` 줄에 echo 하도록 target 명세 §3-5가 반영돼 있어야 한다

### 7-2. host 볼륨 준비 (사용자 직접)

사용자가 쓰는 정확한 순서 (**`mkdir` 이 필요하다** — 앞선 판에서 빠져 있었다):

```bash
sudo nvme connect -t tcp -n nqn.2016-06.io.spdk:cnode1 -a 192.168.100.2 -s 4420
sudo mkfs.ext4 /dev/nvme1n1        # 볼륨 전체를 지움 — 직접 실행해 주세요
sudo mkdir /mnt/ext4
sudo mount /dev/nvme1n1 /mnt/ext4
```

측정 종료 시:

```bash
sudo umount /dev/nvme1n1
sudo nvme disconnect -n nqn.2016-06.io.spdk:cnode1
sudo rm -rf /mnt/ext4
```

`mkfs.ext4` 는 매번 볼륨을 비우므로 그 뒤에 §7-3의 입력 복사를 다시 해야 한다.

### 7-3. 입력 복사
```bash
sudo cp ~/ndp_dataset/res1080_h264.mp4 ~/ndp_dataset/res1440_h264.mp4 \
        ~/ndp_dataset/res2160_h264.mp4 ~/ndp_dataset/res1080_hevc.mp4 \
        ~/ndp_dataset/res2160_hevc.mp4 ~/ndp_dataset/res1080_av1.mp4 \
        ~/ndp_dataset/res2160_av1.mp4 \
        ~/ndp_dataset/MOT17-02.mp4 ~/ndp_dataset/MOT17-04.mp4 \
        ~/ndp_dataset/MOT17-09.mp4 ~/ndp_dataset/MOT20-02.mp4 \
        ~/ndp_dataset/MOT20-03.mp4 /mnt/ext4/
sync
```
합계 약 1.1 GB. **이번에는 복사 후 매 실행 직전에 `drop_caches` 를 하므로**, 9월처럼 Baseline이 page cache에서 읽는 문제는 생기지 않는다.

### 7-4. ssh 접근 — **현재 끊겨 있다 (확인된 문제)**

`run_measure.sh` 가 쓰던 `TARGET_HOST=oslab-server3` 가 **이 host에서 더 이상 이름 해석되지 않는다**:

```
$ ssh oslab-server3
ssh: Could not resolve hostname oslab-server3: Temporary failure in name resolution

$ grep oslab /etc/hosts        → 없음
$ ls ~/.ssh/config             → 파일 없음

$ ssh 192.168.100.2
ssh: connect to host 192.168.100.2 port 22: Connection refused
```

즉 storage 링크(192.168.100.2)에는 sshd가 없고, `oslab-server3` 라는 이름을 풀 방법도 남아 있지 않다. 9월에는 동작했으므로 DNS 또는 root 계정의 ssh 설정이 바뀐 것으로 보인다(**추정** — `/root/.ssh` 는 읽을 수 없다. 9월 실행이 `sudo -E ./run_measure.sh` 였으므로 ssh는 root로 나갔다).

**영향**: ssh가 없으면 `run_all.sh` 의 target 관련 열(`tgt_bytes_read` 등 iostat 차분, target `[NDP-MEASURE]`)이 전부 비고, §6-7 대조도 못 한다. 스크립트는 ssh 실패를 무시하고 진행하도록 돼 있어 측정 자체는 멈추지 않지만, **"NDP의 0xC0이 SSD에서 파일 크기만큼 읽었는가"를 확인할 수 없게 된다** — 명세 §9의 확인 항목 중 하나다.

**필요한 것**: target의 관리용 IP 또는 호스트명과, 거기로 비밀번호 없이 ssh가 되는 설정(`/etc/hosts` 항목 또는 `~/.ssh/config`). 추가로 `rpc.py bdev_get_iostat` 호출 권한이 필요하다(`TARGET_RPC` 기본값 `/root/spdk/scripts/rpc.py`; 경로가 다르면 알려주세요).

---

## 8. 전체 측정 전에 결정이 필요한 점

1. **4K 마스터 영상의 출처와 라이선스.** URL 기록이 없고 제목만 남아 있다(§3-1). YouTube 영상이면 재배포 불가라 논문에 공개 데이터로 쓸 수 없고 재현도 안 된다. **라이선스가 명확한 4K 60fps 소재(예: CC-BY 영상, Netflix/Xiph 공개 테스트 시퀀스)로 교체할지 결정이 필요하다.** 교체하면 E1/E5를 다시 인코딩해야 하므로 전체 측정 전에 결정하는 편이 낫다.
2. **`consume_frame()` 이 `t_pre` 바깥에 있는 문제**(§2-3). 샘플링 Baseline의 `host_pre` 가 건너뛴 프레임의 디코딩을 포함하지 않는다. (a) 그대로 두고 본문에서 `e2e` 만 쓰거나, (b) `consume_frame` 도 `bl_decode_s`/`t_pre` 에 넣도록 고칠 수 있다. **(b)를 권한다** — "샘플링이 전처리 비용을 얼마나 줄이는가"가 논문의 주장 중 하나라면, 지금 정의로는 그 주장을 뒷받침할 수 없다.
3. **MOT 시퀀스의 카메라 고정 여부를 본문에 쓸 수 없다**(§3-5). 아카이브 페이지에 해당 필드가 없다. 영상에서 직접 판정하거나(예: 프레임 간 전역 모션 추정), 본문에서 그 주장을 빼야 한다.
4. **MOT20-03이 1173×880** 이다(§3-5). 같은 표에 1080p 시퀀스와 나란히 놓으면 전처리 비용 비교가 흐려진다. 별도 표기 또는 제외 여부 결정이 필요하다.
5. **MOTChallenge BibTeX를 다른 출처에서 확보해야 한다**(§3-5). 아카이브 페이지에 인용 정보가 없고 사이트 루트는 410 Gone이다. 또 사이트가 EOL이라 zip 링크가 언제 사라질지 모르니, **받은 zip의 sha256을 논문 보충자료에 남기는 것**을 권한다(스크립트가 이미 계산 중).
6. **NTP 비활성화 여부**(§2-6.1). 계측이 `CLOCK_REALTIME` 이라 8시간 측정 중 시각 보정이 들어가면 구간 시간이 왜곡될 수 있다. 측정 중 `timesyncd`/`chronyd` 를 멈출지 결정.
7. **CPU governor / GPU 클럭 고정 여부.** `env.txt` 에 기록만 하게 해 뒀다. 8시간 동안 열에 의한 클럭 변동이 회차 간 산포로 나타날 수 있으므로, `performance` governor와 `nvidia-smi -lgc` 고정을 할지 결정이 필요하다.
8. **프레임 수가 다른 조건 간 추론 시간 비교 금지**(§2-6.4). 워밍업이 없어 첫 프레임 autotune이 `infer` 에 들어간다. 명세 §1이 "워밍업을 넣지 말라"가 아니라 "CUDA 동기화를 바꾸지 말라"였으므로, **워밍업 추론 1회를 타이머 바깥에 추가하는 것을 권한다.** 결정이 필요하다.
