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
3. **`host_rss_mb` 에는 계측 하네스 자신의 메모리가 섞여 있다.** `dets_all[]` 이 클립 전체의 모든 탐지를 누적하므로(mAP 적분에 필요) 클립이 길고 사람이 많을수록 커진다. 엔트리당 40 B 다. 처음에는 MOT17-09 실측(프레임당 71개)으로 수 MB로 추정했으나, **§9 스모크에서 워킹투어 1080p 클립은 신뢰도 0.005 이상 탐지가 프레임당 364개**(3597프레임에 1,309,996개)로 나와 **약 52 MB** 다. Baseline RSS(1.6 GB)에서는 3%, NDP RSS(657 MB)에서는 **8%** 에 해당하므로 무시할 수준은 아니다. **"NDP가 메모리를 N배 적게 쓴다"를 주장할 때 분모·분자 양쪽에 공통으로 들어가는 하네스 몫이라는 점은 알고 있어야 한다.** 9월 코드에도 있던 것이라 과거 수치와의 비교에는 영향이 없다. `-dump_dets` 는 이 배열을 재사용하므로 RSS를 추가로 늘리지 않는다.
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

## 4-2. §6 경로 확인 테스트 결과 (host 측) — **PASS**

2026-10-19 05:56, 사용자가 볼륨을 연결·마운트하고 입력 12개를 복사한 직후 `sudo -E ./run_pathcheck.sh` 로 실행했다. 원시 스냅샷: `~/ndp_results/pathcheck/S{0..3}.hstat`, 실행 로그: `~/ndp_results_pathcheck.log`.

`/sys/block/nvme1n1/stat` 차분 (섹터 × 512 B):

| 구간 | 내용 | host 읽기 | host 쓰기 | io_ticks | 소요 | 처리율 |
|---|---|---|---|---|---|---|
| S0→S1 | `dd oflag=direct` 512 MiB 쓰기 + `sync` | 0 | **537,038,848** | 651 ms | 1.908 s | 281 MB/s |
| S1→S2 | `drop_caches` 후 읽기 | **536,870,912** | 0 | 459 ms | 0.461 s | 1.2 GB/s |
| S2→S3 | 캐시 **안 비우고** 다시 읽기 | **0** | 0 | **0 ms** | 0.024 s | 22.2 GB/s |

```
S0: rd_sect=15162    wr_sect=3456000  io_ticks=2620
S1: rd_sect=15162    wr_sect=4504904  io_ticks=3271
S2: rd_sect=1063738  wr_sect=4504904  io_ticks=3730
S3: rd_sect=1063738  wr_sect=4504904  io_ticks=3730
```

해석:
- **콜드 읽기는 명목값의 정확히 100.0%가 장치에 도달했다**(536,870,912 B = 1,048,576 섹터).
- **웜 읽기는 장치 트래픽 0, io_ticks 0이다.** 같은 512 MiB를 22.2 GB/s(메모리 대역폭)로 돌려줬다. **page cache에 있는 파일을 읽는 실행은 장치 I/O를 전혀 재지 않는다** — 명세 01의 지적("9월 Baseline은 `drop_caches` 없이 복사 직후 실행됐다")이 실제로 측정을 무효화하는 문제였다는 직접 증거다. 재측정에서 매 실행 전에 `drop_caches` 를 하는 근거이기도 하다.
- 쓰기가 명목값보다 **167,936 B(164 KiB) 많다.** ext4 메타데이터와 저널 기록분이다. 이 정도 오버헤드는 Baseline의 "파일 크기 = 전송량" 정의(명세 01 H7-3)가 실제 장치 I/O와 0.03% 이내로 맞는다는 뜻이기도 하다.
- 콜드 읽기 **1.2 GB/s ≈ 9.6 Gb/s** 로 10GbE 링크 속도에 거의 붙어 있다. NVMe-oF 경로가 **네트워크 대역폭에 묶여 있다**는 뜻이다. 4K 입력(`res2160_h264.mp4`, 378 MB)을 Baseline이 읽는 데 순수 전송만 약 0.31 s가 든다.
- S0의 `wr_sect=3,456,000` (1.77 GB)는 직전 사용자 `cp` 로 입력 12개(1.77 GB)를 쓴 분이다. 크기가 맞는다.

**target 측 대조는 하지 못했다.** `TARGET_HOST` 가 비어 있어 `bdev_get_iostat` 열이 `n/a` 다(§7-4의 ssh 문제). target 세션에서 같은 시각(05:56)의 iostat 차분을 받아 대조해야 명세 §6-6이 완료된다.

### 볼륨 상태에 대해 새로 확인된 것

1. **볼륨이 2026-08-31 이후 한 번도 다시 포맷되지 않았다.** `lost+found` 생성 시각이 `2026-08-31 16:51:22`, 옛 파일 `MOT17_04.mp4`(8/31)와 `testd_15s.mp4`(9/14)가 남아 있다. 원인은 사용자 로그에서 확인됐다 — 기존 ext4가 있으면 `mkfs.ext4` 가 묻는다:
   ```
   /dev/nvme1n1 contains a ext4 file system
           last mounted on /mnt/ext4 on Mon Sep 14 19:42:01 2026
   Proceed anyway? (y,N)
   ```
   여기서 Enter는 **N** 이고 mkfs는 아무 메시지 없이 취소된다. 9월 기록의 `mkfs.ext4` 들도 같은 식으로 넘어갔을 가능성이 크다(**추정**). 측정 정확성에는 영향이 없지만, "매번 깨끗한 볼륨에서 실험했다"고 본문에 쓰면 사실과 다르다.
2. **extent 수는 문제없다.** 오래된 파일시스템이라 조각화를 의심했지만 `filefrag` 결과 입력 12개 모두 **1~5 extent** 다. 0xC0 상한(`NDP_MAX_EXTENTS=128`)에 한참 못 미친다.
3. 파일시스템 크기 916 GB, 사용 1.8 GB(1%).

---

## 4-3. §9 스모크 테스트 결과

2026-10-19 06:05~06:09, target `NDP_IO_TRACE=0` 측정용 빌드, `sudo env RESULTS=~/ndp_results/smoke ./run_all.sh --smoke`. 5회 모두 `status=ok`. 원시 데이터: `~/ndp_results/smoke/{results.csv,env.txt,logs/,dumps/,io/}`, 실행 로그 `~/ndp_smoke.log`.

### results.csv 행 (주요 열)

| run_id | 조건 | dump | frames | e2e | target_pre | io | host_pre | infer | ap50 | bytes | host_read_bytes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 575380001 | E1 res1080_h264 BASE sr1 | 0 | 3597 | 101.148 | 0 | 0 | 36.487 | 11.536 | −1 | 152,589,505 | 152,596,480 |
| 575380002 | E1 res1080_h264 BASE sr1 | **1** | 3597 | 99.847 | 0 | 0 | 36.161 | 10.665 | −1 | 152,589,505 | 152,596,480 |
| 575380003 | E1 res1080_h264 NDP-q85 sr5 | 1 | 720 | 18.384 | 9.374 | 0.034 | 1.269 | 1.996 | −1 | 22,995,776 | 3,162,112 |
| 575380004 | E2 MOT17-04 NDP-raw sr1 | 1 | 1050 | 22.445 | 4.293 | 0.808 | 0.270 | 2.936 | **67.8749** | 545,130,604 | 2,342,912 |
| 575380005 | E5 res1080_av1 NDP-q85 sr1 | 0 | 3597 | 94.304 | **49.540** | 0.172 | 6.161 | 9.698 | −1 | 114,948,036 | 2,351,104 |

Baseline 세부 타이머:

| run_id | bl_decode | bl_copy | bl_mat2img | bl_letterbox | 합 | host_pre | 차이 |
|---|---|---|---|---|---|---|---|
| 575380001 | 4.153 | 3.353 | **18.607** | 10.369 | 36.482 | 36.487 | 0.005 s (0.014%) |
| 575380002 | 4.102 | 3.365 | **18.410** | 10.278 | 36.155 | 36.161 | 0.006 s (0.017%) |

### §9 확인 항목별 판정

| 항목 | 판정 | 근거 |
|---|---|---|
| `results.csv` 모든 열이 채워지는가 | **부분** | 44열 중 target iostat 4열만 비었다. target `[NDP-MEASURE]` 열은 아예 생기지 않았다. 원인은 `TARGET_HOST` 미해결(§7-4) — host 측 열은 전부 채워짐 |
| target 로그가 run ID로 짝지어지는가 | **확인 불가** | 위와 같은 이유. host `[MEASURE]` 의 `run_id` 는 5회 모두 요청값과 일치(불일치 시 `status` 가 바뀌도록 돼 있음) |
| Baseline 세부 타이머 합 ≈ `host_pre` | **통과** | 차이 0.005~0.006 s (0.014~0.017%) |
| 캐시 비운 Baseline의 host 블록 읽기 ≈ 파일 크기 | **통과** | 152,596,480 vs 152,589,505 B. +6,975 B는 4 KiB 페이지 올림 |
| 캐시 비운 Baseline의 target `bytes_read` ≈ 파일 크기 | **확인 불가** | `TARGET_HOST` |
| NDP의 target `bytes_read` ≈ 파일 크기 | **통과 (§4-4에서 갱신)** | target `ssd_read_bytes` 152,592,384 B vs 파일 152,589,505 B |
| MOT17-04 NDP-raw mAP ≈ 이전 값 | **통과 — 완전 일치** | **67.8749%**. 9월 `ndp_sws_raw sr1` 3회와 소수 넷째 자리까지 같다. target_pre 4.293 (9월 4.329~4.395), io 0.808 (9월 0.802~0.804) 도 재현됨 |
| 덤프 `frame_idx` 범위 | **통과** | sr1: 0..3596 (3597개, 간격 1) / sr5: 0..3595 (720개, 간격 5) / MOT17-04: 0..1049 |
| 덤프 좌표가 원본 해상도 안인가 | **조건부** | 박스의 4.3~8.1%가 프레임 경계를 최대 약 76 px 벗어난다. darknet이 클리핑하지 않기 때문이며 의도된 동작(§2-4). 분석에서 클리핑할 것 |
| 덤프가 지연시간에 영향을 주는가 (§8-1) | **영향 없음** | 같은 조건 off/on: e2e 101.148 / 99.847, host_pre 36.487 / 36.161, infer 11.536 / 10.665. 켠 쪽이 오히려 1% 빠르다 = 실행 간 잡음 수준. 덤프는 모든 타이머 뒤에 쓰므로 예상대로다 |

### 스모크에서 새로 드러난 것

1. **NDP의 0xC2 전송은 host 블록 카운터에 잡히지 않는다.** raw 실행은 0xC2로 545 MB를 받았는데 `host_read_bytes` 는 2.3 MB였다. 0xC0/0xC2는 ioctl passthru(`REQ_OP_DRV_IN`)라 `/sys/block/*/stat` 의 read/write 회계에 들어가지 않기 때문이다. 즉 **host 블록 카운터는 Baseline 전송량을 재는 데만 쓸 수 있고, NDP 전송량은 지금처럼 `bytes=`(cdw0)로 재야 한다.** 두 정의가 다르다는 명세 01 H7-3의 지적이 측정으로 확인된 셈이다.
2. **NDP 모드에서도 host가 영상 파일을 2.3~3.2 MB 읽는다.** NDP 실행의 `host_read_bytes` 가 바로 이것이다. `validate_video_map()` 이 해상도·프레임 수를 얻으려고 NDP 모드에서도 `get_capture_video_stream()` 으로 파일을 연다(`detector.c` 의 `/* ---- source geometry ---- */`). 컨테이너 헤더와 첫 몇 프레임을 읽는 양이다. Baseline의 152 MB에 비하면 2%지만, **"NDP는 host가 원본을 전혀 읽지 않는다"고 쓰면 사실과 다르다.** 해상도를 0xC0 응답이나 target 쪽에서 받아오면 없앨 수 있다(§8 결정 항목에 추가).
3. **target의 AV1 디코딩이 H.264보다 약 5배 느리다.** 같은 1080p 3597프레임에서 target_pre가 H.264(sr5, 전 프레임 디코딩) 9.374 s → AV1 49.540 s. 프레임당 2.6 ms → 13.8 ms. 그 결과 **NDP-AV1의 e2e(94.3 s)가 Baseline-H.264(101.1 s)와 거의 같다.** E5에서 NDP 이점이 코덱에 따라 크게 줄어들 수 있다는 뜻이고, 논문의 주요 결과가 될 수 있다. 4K AV1은 화소 4배라 target_pre가 약 200 s로 예상되며, 올려 둔 타임아웃 600 s 안에는 든다.
4. **단계별 CPU 귀속(`host_cpu_pre_s`, `host_cpu_infer_s`)은 JPEG 모드에서 쓸 수 없다.** 추론 wall 2.0 s 동안 CPU 37.3 s(약 18코어), AV1 실행은 9.7 s 동안 181.7 s(약 19코어)가 잡혔다. 반면 raw 모드는 2.9 s 동안 2.8 s(1코어 = CUDA spin-wait). 차이는 JPEG 모드에만 있는 OpenCV 호출(`imdecode`, `cvtColor`)이다. OpenCV 작업자 스레드가 병렬 구간이 끝난 뒤에도 잠시 busy-wait 하고, 그 CPU가 다음 단계인 추론으로 들어가는 것으로 보인다(**추정**). **루프 전체 합(`host_cpu_user_s + host_cpu_sys_s`)만 신뢰할 수 있다.**
5. **성능 전용 실행에서도 wall e2e의 절반 이상이 타이머 밖이다.** Baseline e2e 101.1 s 중 단계 합은 48.0 s뿐이고 53 s(프레임당 14.7 ms)가 비측정 구간이다. 신뢰도 0.005에서 프레임당 364개 박스가 나와 `get_network_boxes` + NMS + `dets_all` 적재가 무거워진 탓이다. 실제 배포(보통 신뢰도 0.25)와 다른 부하이므로, **wall e2e를 "종단간 지연"으로 쓰면 하네스 비용이 섞인다.** 논문은 계속 단계 합을 쓰되 그렇게 명시하거나, 비측정 구간을 별도 필드로 계측해야 한다(§8 결정 항목에 추가).
6. 덤프 크기: Baseline 1080p 1회 56 MB. 본측정 1회차의 덤프 119개를 합치면 수 GB 규모로, 디스크(658 GB 여유)에는 문제없다.
7. `env.txt`: CPU governor **`schedutil`** (128코어 전부), GPU는 측정 시작 시 유휴 클럭 210 MHz였다(최대 1800 MHz). 클럭 고정 여부는 §8 결정 항목 7번.

### 예상 소요 시간 재계산

스모크 실측으로 §6-3의 추정을 보정했다:
- Baseline 1080p 성능 전용: **28.1 ms/frame** (101.1 s / 3597). 앞서 쓴 14 ms는 MOT17-04에서 mAP 비용을 빼서 얻은 값인데, 성능 전용에서도 박스 처리 비용(5번 항목)이 남아 있어 과소추정이었다. 4K도 같은 비율로 늘어날 수 있다.
- NDP-AV1 1080p: 26.2 ms/frame. 4K AV1은 target_pre만 약 200 s.

이 값으로 다시 잡으면 E1 Baseline 1080p 몫이 2배, E5 AV1 몫이 크게 늘어 **전체 약 11~12시간**으로 본다(앞선 추정 8시간). 하룻밤에 끝나지 않을 수 있으니 E1+E5와 E2+E3을 두 번에 나눠 돌리는 것을 권한다(`./run_all.sh E1 E5`, `./run_all.sh E2 E3`).

---

## 4-4. target 수집 경로 확인 — **ALL OK** (2026-10-19 06:2x)

사용자 PC의 ssh 설정에서 target이 `target-server3` = **<TARGET_LAN_IP>:<SSH_PORT>**, 계정 `kimkitae` 임을 확인했다. supercxl(<HOST_LAN_IP>)이 같은 LAN이라 bastion 없이 직접 닿는다.

조치:
- `~/.ssh/config` 에 `Host target-server3 oslab-server3` → `<TARGET_LAN_IP>:<SSH_PORT>` (`oslab-server3` 는 9월 스크립트가 쓰던 이름 — target의 실제 호스트명도 `oslab-server3` 다). `/etc/hosts` 수정 불필요, sudo 불필요.
- 이 host 전용 키 `~/.ssh/id_ed25519_ndp` 생성(무인 실행용, passphrase 없음), 사용자가 `ssh-copy-id` 로 target에 설치.
- target 호스트 키 고정: `SHA256:heL7+DuNrrLIQnBkP/AQ8oWKckY61LtiZjGwU5RLGsw` (ED25519, `[<TARGET_LAN_IP>]:<SSH_PORT>`).
- target의 실제 경로로 기본값 교체: RPC `~/near_data_processing_for_video/spdk/scripts/rpc.py`(소켓 `/var/tmp/spdk.sock` 이 `srw-rw-rw-` 라 sudo 불필요), 로그 `~/nvmf_tgt_iotest.log`(nvmf_tgt stdout을 `tee` 한 파일). 9월 스크립트의 `/root/spdk`, `/var/log/spdk_tgt.log` 는 둘 다 맞지 않았다.

`./check_target.sh` 결과: 이름 해석 → <SSH_PORT> 포트 → 키 로그인 → `bdev_get_iostat`(bdev `nvme1n1`) → 로그 읽기 → **`[NDP-MEASURE]` 가 `run_id` 를 echo** — 전부 OK.

### 스모크 실행의 소급 짝짓기 — 성공

스모크의 NDP 실행 3건 run_id가 target 로그에 모두 있다:

| run_id | 조건 | host `target_pre` (0xC0 왕복) | target `pre_total` | 차이 = NVMe-oF 명령 오버헤드 |
|---|---|---|---|---|
| 575380003 | res1080_h264 q85 sr5 | 9.374 | 9.133 | 0.241 s |
| 575380004 | MOT17-04 raw sr1 | 4.293 | 3.901 | 0.392 s |
| 575380005 | res1080_av1 q85 sr1 | 49.540 | 49.272 | 0.268 s |

→ 명세 01 H3-2의 "host가 잰 `target_pre` 에는 명령 왕복이 섞여 있다"가 실측으로 0.24~0.39 s 임이 확인됐다. 이제 매 실행마다 두 값이 같은 행에 들어간다.

(스모크 행의 target iostat 차분은 소급할 수 없다 — 실행 전후 스냅샷이 없었다. 본측정부터 채워진다.)

### 본측정 전에 반드시 해결할 것 — target 쪽 2건

1. **지금 target에서 도는 것은 측정용 빌드가 아니다.** 프로세스가 `.../spdk/build/bin/nvmf_tgt_iotrace` (05:49 기동)이고 `NDP_IO_TRACE=1` 빌드다. **§9 스모크는 이 빌드를 상대로 돌았다.** 따라서 스모크의 NDP `target_pre` 값은 I/O 추적 오버헤드를 포함할 수 있어 측정값으로 쓰면 안 된다(다만 MOT17-04 raw의 target_pre 4.293 s가 9월 4.33~4.40 s와 비슷해 오버헤드는 작아 보인다 — **추정**). 본측정 전에 측정용 빌드로 재기동해야 하고, 그 빌드의 stdout을 어느 파일로 `tee` 하는지 알려주면 `TARGET_LOG` 를 맞춘다.
2. ~~target의 단계별 분해가 `pre_total` 과 맞지 않는다~~ — **정정: 내 해석이 틀렸다.** `result_02_target.md` §2에 따르면 `decode`/`letterbox`/`encode` 는 `pre_total` 을 나누는 wall 구간이 아니라 **각 단계의 CPU busy 누적**이다(프레임 스레딩 때문에 합이 `pre_total` 을 넘을 수도 있다고 명시). 그러니 "합이 `pre_total` 의 절반"을 "미분해 52%"로 읽은 것은 정의를 잘못 안 것이다. 다만 H.264의 `decode=0.011 s` 는 디코딩이 libavcodec 프레임 스레드에서 돌아 워커의 타이머에 거의 잡히지 않는다는 뜻이므로, **target의 디코딩 비용으로 쓰면 안 된다.** target이 새로 넣은 **`cpu_storage_s`**(검증 완료, 오차 0.01% 미만)가 그 용도의 정답이다 — 575380003에서 85.675 s, 즉 `pre_total` 9.133 s 동안 약 9.4코어를 썼다.

### target 보고서와의 대조 (2026-10-19)

`result_02_target.md` 를 받아 host 기록과 맞춰 봤다.

**(a) target이 "host 테스트 대기"로 남긴 §5(b) 비교는 이미 가능하다.** host §6 `run_pathcheck.sh` 가 05:56에 돌았고, target T0는 그 직후 05:57:29에 수집됐다. 둘 다 연결 이후 누적값이라 그대로 비교된다:

| | host `/sys/block/nvme1n1/stat` (S3, 05:56) | target `bdev_get_iostat` T0 (05:57:29, 연결 전 몫 제외) | 차이 |
|---|---|---|---|
| 읽기 | 1,063,738 섹터 = 544,633,856 B | 1,063,778 블록 = 544,654,336 B | 40 섹터 (20,480 B, **0.004%**) |
| 쓰기 | 4,504,904 섹터 = 2,306,510,848 B | 4,505,200 블록 = 2,306,662,400 B | 296 섹터 (151,552 B, **0.007%**) |

두 스냅샷 사이(S3 → T0, 약 1분)에 `iotest.bin` 삭제와 `sync` 가 있었으므로 쓰기 쪽 잔차는 그 메타데이터·저널 기록분이다(**추정**). **host 블록 카운터와 target iostat이 0.01% 이내로 일치한다** — Baseline의 장치 읽기량을 어느 쪽으로 재도 된다는 뜻이다. 본측정에서는 실행마다 양쪽이 같은 행에 들어가므로 정확히 동시 비교가 된다.

**(b) §9의 "NDP의 target `bytes_read` ≈ 파일 크기" — 통과로 갱신.** target의 새 필드 `ssd_read_bytes` 로 확인된다: 575380003에서 **152,592,384 B** (파일 152,589,505 B, +2,879 B는 블록 올림). 0xC0이 원본 전체를 SSD에서 읽는다.

**(c) host `target_pre` 와 target `pre_total` 의 차이(0.24~0.39 s)는 "명령 오버헤드"만이 아니다.** target 정의상 `pre_total` 은 SSD 읽기·FFmpeg 초기화·직렬화를 **뺀** 값이다. 575380003: 차이 0.241 s = `ssd_read_ms` 0.046 + `serialize_ms` 0.013 + 나머지 0.182 s(FFmpeg 초기화 + NVMe-oF 왕복). 본측정에서는 이 세 몫이 모두 같은 행에 들어간다.

**(d) target 바이너리 기록이 현재와 다르다.** 보고서 §7은 `build/bin/nvmf_tgt` 를 04:16 측정용으로 적었지만, 지금 그 파일은 **05:30에 다시 빌드됐고** `nvmf_tgt_measure`(04:15)와 해시가 다르다(05:45 `prechange` A/B 시험 때 다시 빌드된 것으로 보인다 — **추정**). 문자열 검사로는 둘 다 `run_id` echo 있음·추적 코드 없음이라 측정용이 맞아 보이지만, **출처가 분명한 `nvmf_tgt_measure` 를 쓰는 편이 안전하다.**

**(e) target이 경고한 `nb_frames=0 → 1000프레임 절단`은 해당 없다.** 입력 12개 모두 컨테이너 `nb_frames` 가 채워져 있다(3597 ×7, 600, 1050, 525, 2782, 2405).

---|---|---|---|---|---|---|
   | 575380003 (H.264 q85) | **0.011** | 3.382 | 0.962 | 4.355 | 9.133 | 4.778 s (**52%**) |
   | 575380004 (raw) | 0.002 | 3.791 | 0.000 | 3.793 | 3.901 | 0.108 s (3%) |
   | 575380005 (AV1 q85) | 3.193 | 18.181 | 5.097 | 26.471 | 49.272 | 22.801 s (**46%**) |

   H.264 1080p 3597프레임의 `decode=0.011 s` 는 프레임당 3 µs로 물리적으로 불가능하다. decode 타이머가 실제 디코딩을 잡지 못하고 있다(예: 비동기 `avcodec_send_packet` 만 재고 `avcodec_receive_frame` 을 빠뜨렸거나, 디코딩이 다른 스레드에서 돈다 — **추정**). JPEG 모드에서 `pre_total` 의 절반가량이 어느 단계에도 속하지 않는다. **논문에 target 측 "디코딩 X% / 리사이즈 Y% / JPEG Z%" 분해를 쓸 계획이라면 target 계측을 먼저 고쳐야 한다.** `pre_total` 자체는 host의 `target_pre` 와 0.24~0.39 s 차이로 정합하므로 총량은 믿을 수 있다.

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

### 7-1. target 기동 — 05:5x 해결됨 (아래는 그 전 상태 기록)

target 세션이 `nvmf_tgt` 를 띄운 뒤 4420이 열렸고(`4420 OPEN`), 사용자가 연결·마운트·복사를 마쳤다. 아래는 그 전에 관찰한 상태다.

`nvme connect` 가 실패하는 이유는 입력 오류가 아니다. target에 SPDK가 안 떠 있다:

```
$ ping -c2 <TARGET_DATA_IP>        → 0% loss, 0.2 ms      (머신과 링크는 정상)
$ /dev/tcp/<TARGET_DATA_IP>/4420   → Connection refused   (nvmf_tgt 미동작)
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
sudo nvme connect -t tcp -n nqn.2016-06.io.spdk:cnode1 -a <TARGET_DATA_IP> -s 4420
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

$ ssh <TARGET_DATA_IP>
ssh: connect to host <TARGET_DATA_IP> port 22: Connection refused
```

즉 storage 링크(<TARGET_DATA_IP>)에는 sshd가 없고, `oslab-server3` 라는 이름을 풀 방법도 남아 있지 않다. 9월에는 동작했으므로 DNS 또는 root 계정의 ssh 설정이 바뀐 것으로 보인다(**추정** — `/root/.ssh` 는 읽을 수 없다. 9월 실행이 `sudo -E ./run_measure.sh` 였으므로 ssh는 root로 나갔다).

**영향**: ssh가 없으면 `run_all.sh` 의 target 관련 열(`tgt_bytes_read` 등 iostat 차분, target `[NDP-MEASURE]`)이 전부 비고, §6-7 대조도 못 한다. 스크립트는 ssh 실패를 무시하고 진행하도록 돼 있어 측정 자체는 멈추지 않지만, **"NDP의 0xC0이 SSD에서 파일 크기만큼 읽었는가"를 확인할 수 없게 된다** — 명세 §9의 확인 항목 중 하나다.

**필요한 것**: target의 관리용 IP 또는 호스트명과, 거기로 비밀번호 없이 ssh가 되는 설정(`/etc/hosts` 항목 또는 `~/.ssh/config`). 추가로 `rpc.py bdev_get_iostat` 호출 권한이 필요하다(`TARGET_RPC` 기본값 `/root/spdk/scripts/rpc.py`; 경로가 다르면 알려주세요).

---

## 7-5. 볼륨 준비 자동화와 명세 §1 규칙 변경안

사용자 요청(2026-10-19): 볼륨 준비를 Claude가 할 수 있게 명세를 바꿀 것. 두 가지로 나눠 처리했다.

**(a) 스크립트 — 완료.** [setup_volume.sh](setup_volume.sh), [teardown_volume.sh](teardown_volume.sh).

- 포맷 대상 장치를 고정 이름(`/dev/nvme1n1`)으로 받지 않는다. sysfs에서 **NQN `nqn.2016-06.io.spdk:cnode1` 서브시스템의 네임스페이스**를 찾고, 그 서브시스템의 **모든 경로가 transport `tcp`** 인지 확인한다. 마운트돼 있거나 루트 파일시스템이면 포맷하지 않는다.
- `--check` 는 root 없이 아무것도 바꾸지 않고 판정만 출력한다. 실측:
  ```
  subsystem nqn.2016-06.io.spdk:cnode1 -> /dev/nvme1n1 (all paths tcp)
  size: 931.5G   mounted at: /mnt/ext4
  ```
- **음성 시험**: NQN을 로컬 PCIe SSD의 것으로 바꿔 주면 `[STOP] ... has a path with transport 'pcie', not tcp` 로 멈춘다(exit 1). 로컬 `nvme0n1` 을 지울 수 있는 경로가 막혀 있다는 확인이다.
- 검증 통과 후에만 `mkfs.ext4 -F` 로 `(y,N)` 질문을 건너뛴다 — 포맷이 일어나거나, 스크립트가 크게 실패하거나 둘 중 하나다(조용한 취소 없음).
- `--copy` 는 입력 12개 복사 + `sync` + `filefrag` 로 extent ≤ 128 검사까지 한다.
- 정리 스크립트는 `rm -rf /mnt/ext4` 대신 **`rmdir`** 을 쓴다. `umount` 가 실패한 상태에서 `rm -rf` 는 마운트를 통해 **볼륨 안의 파일을 지운다** — 사용자가 쓰던 정리 순서에 숨어 있던 위험이다.
- **이번에는 포맷을 실행하지 않았다.** 볼륨에 이미 입력 12개가 복사돼 있고 extent도 정상이라, 포맷하면 다시 복사해야 할 뿐 얻는 것이 없다.

**(b) 명세 파일 수정 — 하지 못했다.** `spec_02_host.md` 를 고치려 하자 권한 분류기가 거부했다(`Instruction Poisoning`). 제가 따라야 하는 규칙 문서를 제가 고쳐 스스로 mkfs 권한을 주는 형태이기 때문으로 보인다. 우회하지 않았다. 사용자가 직접 반영할 수 있도록 바꿀 문구를 아래에 둔다.

`spec_02_host.md` §1 의 14행을 다음으로 교체:

```markdown
- NVMe-oF 준비(`nvme connect`, `mkfs.ext4`, `mount`)와 정리(`umount`, `nvme disconnect`)는 Claude가 `setup_volume.sh` / `teardown_volume.sh`로만 수행할 수 있다(2026-10-19 변경. 이전: "사용자가 직접 한다, `mkfs.ext4`는 절대 직접 실행하지 않는다").
  - 실행은 사용자 Terminal 패널 탭에서 하고, sudo 비밀번호는 사용자가 그 탭에 직접 입력한다. 비밀번호를 대화로 주고받지 않는다.
  - `mkfs.ext4`는 `setup_volume.sh --format`으로만 실행한다. 스크립트가 포맷 전에 장치를 sysfs로 검증한다(NQN `nqn.2016-06.io.spdk:cnode1`의 네임스페이스, 모든 경로 transport `tcp`, 미마운트). 어긋나면 멈춘다. 다른 장치에 대한 mkfs는 금지.
  - 입력이 이미 복사돼 있으면 포맷하지 않는다. 포맷이 필요하다고 판단되면 이유를 적고 사용자에게 먼저 묻는다.
  - 정리 시 `rm -rf /mnt/ext4` 대신 `rmdir`을 쓴다.
```

§6 의 82행 "host 준비(connect, mkfs, mount)를 마친 뒤" → "host 준비를 `setup_volume.sh`로 마친 뒤".

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
9. **NDP 모드의 host 측 파일 읽기(2.3~3.2 MB) 제거 여부**(§4-3 새로 드러난 것 2번). 해상도·프레임 수를 0xC0 응답으로 받으면 없앨 수 있지만 target 쪽 변경이 필요하다. 그대로 두고 본문에 명시해도 된다.
10. **비측정 구간(박스 후처리)을 별도 필드로 계측할지**(§4-3 새로 드러난 것 5번). wall e2e의 절반 이상이다. `t_post` 필드를 추가하면 "e2e = 단계 합 + 후처리" 로 완전히 분해된다.
