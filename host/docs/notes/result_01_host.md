# result_01_host — 논문 수치의 계측 방식 확인 (host: supercxl)

작성: 2026-10-17 / 읽기 전용 조사. 소스 수정·재빌드·실험 재실행 없음.

## 0. NVMe-oF 연결 상태 (먼저 확인)

**연결돼 있지 않다.**

```
$ mount | grep -i ext4
/dev/sda2 on / type ext4 (rw,relatime)        ← /mnt/ext4 없음

$ nvme list-subsys
nvme-subsys0 - NQN=...BTLN914202M51P6AGN  INTEL SSDPE2KE016T8
 +- nvme0 pcie 0000:01:00.0 live              ← 로컬 PCIe SSD 1개뿐, nvme1n1(=NVMe-oF) 없음
```

`nvme connect` / `mkfs.ext4` / `mount` 는 실행하지 않았다. 따라서 `/mnt/ext4` 에 의존하는 항목(H6-1)은 **host 로컬 사본**으로 수행했다:
- `~/darknet-for-ndp/testd_15s.mp4` (4K 15s, 실험에 쓴 것과 동일 파일 — 아래 H6-1에서 크기·프레임 수가 로그의 `bytes=83014801`, `frames=900` 과 일치함을 확인)
- `MOT17_11.mp4`, `MOT17_09-DPM.mp4` (로컬 존재)
- **MOT17-04 재인코딩본(`MOT17_04.mp4`)과 4K 30s/1min/10min 클립은 host 로컬에 없다 → 사양 직접 확인 불가.** 찾아본 위치: `~`(maxdepth 3), `~/darknet-for-ndp`.

---

## 결론 요약 (가장 중요한 두 가지)

1. **표 2(A, B)의 "종단간"은 wall-clock이 아니라 단계별 합이다.** 실제 측정된 wall-clock(`e2e=`)은 Baseline 35.507s / NDP q85 26.519s 인데, 논문에 실린 14.295s / 9.334s 는 `target_pre + io + host_pre + infer` 의 산술합과 소수 셋째 자리까지 정확히 일치한다. 차이(약 21s / 17s)는 타이머로 감싸지 않은 mAP 집계 구간이다. (H1-1, H3-3)
2. **표 2의 NDP 행은 `-resize sws`(swscale 커널) 결과이고, 같은 표의 Baseline 행은 `run_measure.sh` 쪽 로그에서 왔다.** 두 파일을 섞어 한 표로 만든 것이다. `run_measure.sh` 의 darknet 커널 NDP(`target_pre≈19.2s`)는 표 2 수치와 전혀 다르다. (H1-4, H3-2)

---

## H1. Baseline 지연시간의 계측 구간 (A, C, D)

### H1-1. 병렬/직렬 구조와 종단간 시간의 정체
- 결론: 논문 수치는 `demo()`(fetch/detect 2스레드)가 아니라 **완전 직렬 단일 스레드 함수 `validate_video_map()`** 에서 나왔다. 스레드가 없으므로 "전처리 시간"과 "추론 시간"의 합산 문제 자체가 없다. **종단간은 wall-clock(`e2e=`)으로도 측정돼 있지만, 논문에 실린 값은 wall-clock이 아니라 단계별 합이다.**
- 근거: `src/detector.c:1455-1503` (루프가 하나이고 각 단계가 순차 누적)
  ```c
  for (;;) {
      double t_stage = what_time_is_it_now();
      ...  im_input = get_image_from_stream_letterbox(cap, net.w, net.h, net.c, &in_img, 0);
      t_pre += what_time_is_it_now() - t_stage;
      ...
      t_stage = what_time_is_it_now();
      network_predict(net, im_input.data);
      t_infer += what_time_is_it_now() - t_stage;
  ```
  `src/detector.c:1630` `t_e2e = what_time_is_it_now() - t_e2e_start;`

  실측 로그(3회 평균, `maplogs/MOT17-04-DPM/measure/measure_raw.log`):
  ```
  baseline sr=1 : e2e=35.507  target_pre=0.000 io=0.000 host_pre=11.146 infer=3.149   (합 14.295)
  ```
  논문 A의 11.146 / 3.149 / 14.295 와 `host_pre` / `infer` / **합**이 정확히 일치. `e2e=35.507` 은 논문에 없다.
- 확신도: **확정** (수치가 소수 셋째 자리까지 일치).
- 주의: wall-clock 35.507s 중 약 21.2s는 `get_network_boxes` + NMS + IoU 매칭 + 라벨 파일 읽기 등 mAP 집계 구간이며, 어떤 타이머에도 들어가지 않는다 (`src/detector.c:1505-1575`). 논문이 "종단간"으로 단계 합을 쓴 것은 이 구간을 제외하려는 의도로 보이지만, 그렇다면 **"end-to-end"가 아니라 "stage sum"** 으로 기술해야 한다.

### H1-2. "전처리" 구간의 시작과 끝
- 결론: Baseline `t_pre` = `get_image_from_stream_letterbox()` 한 번의 호출 전체. 즉 **파일 읽기 + 디코딩(`grab`/`retrieve`) + 불필요한 전해상도 `cv::resize` + `cvtColor(RGB2BGR)` + `mat_to_image` + `letterbox_image`** 를 모두 포함한다. 파일 읽기와 디코딩이 이 안에 섞여 있어 분리돼 있지 않다.
- 근거: `src/image_opencv.cpp:859-890`
  ```c
  src = (cv::Mat*)get_capture_frame_cv(cap);            // 파일 read + 디코딩
  *in_img = (mat_cv *)new cv::Mat(src->rows, src->cols, CV_8UC(c));
  cv::resize(*src, **(cv::Mat**)in_img, ... INTER_LINEAR);   // 전해상도 복사(결과는 즉시 폐기)
  if (c>1) cv::cvtColor(*src, *src, cv::COLOR_RGB2BGR);
  image tmp = mat_to_image(*src);                       // 전해상도 float 변환
  image im = letterbox_image(tmp, w, h);                // 416 축소 + 패딩
  ```
  `in_img` 는 `src/detector.c:1481` 에서 받은 즉시 `release_mat()` 로 버려진다 → 4K에서 프레임당 24.9MB 복사가 순수 낭비로 `t_pre` 에 계상된다.
- 확신도: **확정**.

### H1-3. Baseline의 I/O 시간
- 결론: **분리하지 않았다. 코드가 항상 `io=0.000` 을 출력한다.** 표 2의 "—"는 이 때문이다. **그림 7의 Baseline I/O 1.6%는 이 계측 코드에서 나올 수 없다.** 저장소·스크립트·홈 디렉터리 어디에도 `iostat`, `/sys/block/*/stat` 차분, `dd`, `/proc/<pid>/io` 를 쓰는 코드가 없다 → **출처 확인 불가.**
- 근거: `t_io` 는 `use_ndp` 분기 안에서만 대입된다 (`src/detector.c:1337-1342`); Baseline 경로는 `t_io` 를 건드리지 않고 초기값 0.0 그대로 출력(`src/detector.c:1303, 1772`). 실측 로그 전 행이 `io=0.000`.
  검색: `grep -rn "iostat|drop_caches|vmtouch|O_DIRECT|dd if=|diskstats|/sys/block" --include=*.sh --include=*.py --include=*.c --include=*.cpp .` → **0건**.
- 확신도: **확정**(코드가 0을 낸다는 점). 그림 7의 1.6% 출처는 **확인 불가**(찾아본 위치: 저장소 전체, `~/*.py`, `~/.bash_history`, `maplogs/`, `out/`).

### H1-4. 4K 실험과 MOT17 실험의 코드·계측 동일성
- 결론: **같은 함수·같은 계측이다.** 차이는 호출 스크립트와 커널 선택뿐:

| 항목 | MOT17 (A, B / 표 2·3) | 4K (C, D / 그림 6·7) |
|---|---|---|
| 스크립트 | `run_measure.sh` (+ `run_measure_sws.sh`) | `run_measure_4k.sh` |
| 계측 함수 | `validate_video_map()` | 동일 |
| `-labels` | 있음(mAP 계산) | **없음 → perf_only**, mAP 생략 |
| NDP 리사이즈 커널 | `-resize darknet` / `-resize sws` 둘 다 실행 | `sws`(기본값)만 |
| 표에 실린 NDP | **sws 쪽** (`measure_raw_sws.log`) | sws |
| 반복 | 3회 | 3회 |

- 근거: `run_measure.sh:42-47`, `run_measure_sws.sh:39-44`, `run_measure_4k.sh:50-55` — 세 스크립트 모두 동일한 `./darknet detector video_map ... -dont_show` 호출. `run_measure_4k.sh:38` `"performance-only run: no -labels, no mAP computed"`; `src/detector.c:1225` `const int perf_only = (label_dir == NULL);`
- **중요**: 표 2의 NDP 행(q85: target_pre 4.571 / host_pre 1.769 / io 0.035 / infer 2.959)은 `measure_raw_sws.log` 의 `ndp_sws_q85 sr=1` 3회 평균과 완전히 일치한다. 같은 표의 Baseline 행은 `measure_raw.log` 에서 왔다. 즉 **표 2는 두 로그 파일을 합성한 것**이고, `run_measure.sh` 의 darknet-커널 NDP(`target_pre≈19.2s`, 종단간 합 24.1s)와는 2.5배 이상 다르다. 본문에 "NDP는 swscale 1-step 커널 구성"임을 명시해야 한다.
- 확신도: **확정**.

### H1-5. 모델 로딩 / CUDA 초기화 / 워밍업
- 결론: **모델 로딩과 CUDA 초기화는 e2e 창 밖(제외). 워밍업은 없고, 첫 프레임의 cuDNN autotune 비용은 `t_infer` 안에 포함된다.**
- 근거: `src/detector.c:1234-1236` 에서 `load_weights()`/`fuse_conv_batchnorm()` 이 먼저 실행되고, Baseline의 `t_e2e_start` 는 프레임 루프 직전인 `src/detector.c:1452` 에서 찍힌다. NDP는 `src/detector.c:1317` (0xC0 직전). `video_map` 전체에 더미 `network_predict` 호출이 없다.
- 확신도: **확정**.
- 비대칭 주의: Baseline의 `t_e2e_start` 는 `get_capture_video_stream()`(컨테이너 open·probe, `src/detector.c:1250`) **뒤에** 찍히므로 영상 열기 비용이 Baseline e2e에서 빠진다. NDP e2e는 0xC0 발행부터 전부 포함한다. 단계 합을 쓰는 한 영향은 없지만 wall-clock 비교에는 편향이 된다.

---

## H2. `mat_to_image` 53%의 근거 (E)

### H2-1. 계측 코드·로그
- 결론: **확인 불가.** `mat_to_image` 단독을 재는 타이머가 host 코드에 없고, 53%를 계산한 스크립트·로그도 없다.
- 근거: `grep -n "mat_to_image" src/image_opencv.cpp` → 호출부만 존재, 타이머 없음. `t_pre` 는 `get_image_from_stream_letterbox()` 전체를 한 덩어리로 잰다(`src/detector.c:1463-1486`). 홈 디렉터리의 프로파일러 3종(`~/profile_pipeline.py`, `~/profile_convert.py`, `~/ndp_bench.py`)은 **Ultralytics YOLOv8 / OpenCV-Python 경로**를 계측하며 darknet `mat_to_image` 와 무관하다 (`~/profile_pipeline.py:14-22` 의 계측 대상 표: `grab/retrieve/LetterBox/preprocess/inference/postprocess`).
- 찾아본 위치: `src/*.c`, `src/*.cpp`, `*.sh`, `scripts/`, `maplogs/**`, `out/`, `~/*.py`, `~/.bash_history`.
- 확신도: **확인 불가**. 53%가 어떤 측정에서 나왔는지 본문에 쓰기 전에 원시 근거를 반드시 재확보해야 한다.

### H2-2. 분모
- 결론: **확인 불가.** 다만 분모가 "전처리 합(`host_pre`)"이라면 산술적으로 그럴듯하다: 4K에서 `host_pre=91.984s` / 900프레임 = 102ms/frame 중 `mat_to_image` 가 전해상도 8.3M 픽셀 × 3채널 루프이므로 절반 수준은 자연스럽다. 분모가 "종단간 전체"일 경우 그림 7의 전처리 비중 95.0%와 곱해 50.4%가 되어 **두 해석이 거의 같은 값을 주므로 수치만으로는 구분되지 않는다.**
- 확신도: **추정**(근거 로그 없음, 산술적 정합성만).

### H2-3. 프레임마다 전해상도 float 버퍼를 새로 할당·해제하는가
- 결론: **그렇다. 프레임마다 전해상도 float 버퍼를 `calloc`(0으로 채움)으로 새로 잡고 바로 해제한다.** 4K 기준 프레임당 **99.5MB** 할당 + 제로필 + 8.3M 픽셀 단위 스칼라 변환 루프.
- 근거: `src/image_opencv.cpp:327-346`
  ```c
  image mat_to_image(cv::Mat mat) {
      int w = mat.cols; int h = mat.rows; int c = mat.channels();
      image im = make_image(w, h, c);                 // ← 전해상도 할당
      for (int y=0;y<h;++y) for(int k=0;k<c;++k) for(int x=0;x<w;++x)
          im.data[k*w*h + y*w + x] = data[y*step + x*c + k] / 255.0f;   // 스칼라, 캐시 비친화적 스트라이드
  ```
  `src/image.c:804-809`
  ```c
  image make_image(int w, int h, int c) {
      image out = make_empty_image(w,h,c);
      out.data = (float*)xcalloc(h * w * c, sizeof(float));   // calloc → 제로필 비용 동반
  ```
  해제는 `src/image_opencv.cpp:884` `free_image(tmp);`

  프레임당 할당량(계산):
  - 1080p: 1920×1080×3×4B = **23.7 MB**
  - 4K: 3840×2160×3×4B = **99.5 MB** (4.0배)

  여기에 H1-2의 낭비 `cv::resize` 사본(4K 24.9MB, 1080p 6.2MB)과 `letterbox_image` 내부 `resize_image()` 중간 버퍼(`src/image.c:1018`)가 더해진다.
- 픽셀 수 비 4배 대비 프레임당 시간이 ~8배로 늘어나는 원인 후보로 **코드 근거상 타당**: (a) 할당·제로필 바이트가 4배, (b) 4K에서 99.5MB 버퍼는 L3(AMD EPYC 9554, 256MB)를 프레임당 통째로 흘려보내 `k*w*h + y*w + x` 쓰기 스트라이드의 TLB/캐시 미스율이 1080p보다 높아짐, (c) `xcalloc` 99.5MB는 glibc에서 `mmap` 경로로 가 매 프레임 페이지 폴트가 새로 발생. **성능 측정은 하지 않았으므로 (b)(c)의 기여 비율은 추정이다.**
- 확신도: 할당 크기·횟수·제로필은 **확정**; 8배 증가의 원인 분해는 **추정**.

---

## H3. NDP 지연시간의 계측 구간 (B, C, D)

### H3-1. `ndp_test_detector()`의 `total_time` 이 출처인가
- 결론: **아니다.** 논문의 NDP 수치는 모두 `validate_video_map()` 의 `[MEASURE]` 에서 나왔다. `ndp_test_detector()` 의 `total_time` 은 네트워크 로딩을 포함하고 루프 안에서 프레임마다 `save_image()`·`draw_detections_v3()`·`printf` 를 수행하므로 비교에 쓸 수 없는 값이며, 실제로 어떤 논문 수치와도 대응하지 않는다.
- 근거: `src/detector.c:3033` `double time = what_time_is_it_now();` → 그 **뒤**에 `parse_network_cfg_custom` / `load_weights`(3035-3036); 루프 안 `src/detector.c:3194-3200` `draw_detections_v3(...)` + `save_image(im_input, save_name)` + `printf`; `src/detector.c:3250` `double total_time = (what_time_is_it_now() - time);`
  반면 `run_measure*.sh` 는 전부 `detector video_map` 을 호출한다(`run_measure.sh:42`, `run_measure_4k.sh:50`, `run_measure_sws.sh:39`). `~/.bash_history` 에서 `ndp_test` 는 7월 중 단발 디버깅용 호출만 보이고 반복 측정 흔적이 없다.
- 확신도: **확정**.

### H3-2. 표 2 NDP 단계별 값 각각의 출처
- 결론:
  - **host 전처리(`host_pre`)**: `ndp_jpeg_to_image()` 호출만. 즉 `cv::imdecode` + `cvtColor(BGR2RGB)` + **416×416 `mat_to_image`**. letterbox는 이미 target이 했으므로 host에서는 하지 않는다. (raw RGB24 모드는 `ndp_raw_to_image()`)
  - **I/O(`io`)**: **0xC2 반복 전송 시간만.** 0xC0은 포함되지 않는다.
  - **target 전처리(`target_pre`)**: **host가 잰 0xC0 왕복 시간(`t_c0`)이다. target의 `[NDP-timing]` 로그가 아니다.** 즉 커맨드 발행·완료 폴링·NVMe-oF 왕복 지연이 "target 전처리"에 섞여 있다.
- 근거: `src/detector.c:1467-1477`
  ```c
  double t_stage = what_time_is_it_now();
  if (use_ndp) {
      ... im_input = ndp_jpeg_to_image(blob_base + blob_off, jpeg_sizes[image_index]);
  ...
  t_pre += what_time_is_it_now() - t_stage;
  ```
  `src/detector.c:1317-1342`
  ```c
  t_e2e_start = what_time_is_it_now();
  double t0 = what_time_is_it_now();
  if (ndp_send_0xc0(... &total_result_size) < 0 ...) {...}
  t_c0 = what_time_is_it_now() - t0;          // ← target_pre 로 출력됨
  ...
  t0 = what_time_is_it_now();
  if (ndp_send_0xc2(nvme_fd, result_buf, total_result_size) < 0) {...}
  t_io = what_time_is_it_now() - t0;          // ← io
  ```
  `src/detector.c:1772` `t_e2e, t_c0, t_io, t_pre, t_infer,` → `e2e= target_pre= io= host_pre= infer=` 순서로 출력.
  `src/image_opencv.cpp:358-371` (`ndp_jpeg_to_image`: imdecode → cvtColor → `mat_to_image`)
  `run_measure.sh:49-51` 이 target의 `[NDP-MEASURE]` 를 ssh로 따로 긁어오지만, **`[MEASURE]` 의 `target_pre` 는 그 값과 무관하다.**
- 확신도: **확정**.

### H3-3. NDP 종단간이 wall-clock인지 단계별 합인지
- 결론: 코드는 wall-clock(`e2e=`)을 출력하지만, **논문에 실린 9.334 / 10.330 은 단계별 합이다.**
- 근거: 실측 3회 평균(`measure_raw_sws.log`)
  ```
  ndp_sws_q85  sr=1 : e2e=26.519  target_pre=4.571 io=0.035 host_pre=1.769 infer=2.959  (합 9.334)
  ndp_sws_q100 sr=1 : e2e=24.089  target_pre=5.093 io=0.120 host_pre=2.188 infer=2.928  (합 10.330)
  ```
  논문 B의 모든 값이 `target_pre/io/host_pre/infer` 와 일치하고, 종단간은 합과 일치하며 `e2e=` 와는 2.3~2.8배 차이.
- 확신도: **확정**.

---

## H4. 추론 시간 차이 (C)

- 결론: **"추론" 구간의 정의는 두 경로에서 완전히 동일하다 — `network_predict()` 한 줄뿐.** `get_network_boxes`·NMS·`draw_detections`·화면 출력·저장은 Baseline/NDP 양쪽 모두 `t_infer` 밖이다. 또한 `validate_video_map()` 은 단일 스레드이므로 **fetch 스레드와의 CPU 경합은 존재하지 않는다**(그 구조는 `demo()`에만 있고 측정에 쓰이지 않았다). 따라서 그림 7의 17% 차이는 구간 정의 차이나 스레드 경합으로는 설명되지 않는다.
- 근거: `src/detector.c:1501-1503` (양쪽 공통 경로)
  ```c
  t_stage = what_time_is_it_now();
  network_predict(net, im_input.data);
  t_infer += what_time_is_it_now() - t_stage;
  ```
  `get_network_boxes`/NMS는 `src/detector.c:1505-1515` — 타이머 바깥.
- 실측에서도 같은 방향의 차이가 재현된다: 4K 15s `baseline infer=2.755` vs `ndp infer=2.484` (**9.8%**), MOT17-04 `3.149` vs `2.959` (**6.0%**). 그림 7의 17%보다는 작다.
- 남는 설명 후보(코드 근거 있음, 기여도는 **추정**): Baseline은 직전 단계에서 프레임당 99.5MB float 버퍼를 할당·제로필·순회하므로 `network_predict` 진입 시 L3(256MB)와 TLB가 비워져 있고, GPU로 보낼 입력이 cold인 상태로 H2D 복사가 일어난다. NDP는 직전 작업이 416×416(2MB)뿐이어서 입력이 캐시에 남아 있다. 또한 Baseline은 CPU가 100% 바쁜 상태로 GPU 커널을 런치하므로 런치 지연이 길어질 수 있다. **측정은 하지 않았다.**
- 확신도: 구간 정의와 스레드 경합 없음은 **확정**; 17% 차이의 원인은 **추정**(그림 7 원시 로그가 없어 재현 자체가 불가, H5-2 참조).

---

## H5. 페이지 캐시와 측정 반복

### H5-1. 페이지 캐시
- 결론: **비우지 않았다.** `drop_caches`·`vmtouch -e`·`O_DIRECT` 가 코드·스크립트·명령 기록 어디에도 없다. 스크립트가 넣은 것은 반복 사이 `sleep 3` 뿐이다. **Baseline은 host 페이지 캐시에서 읽혔을 가능성이 매우 높다.**
- 근거: `grep -rn "drop_caches|vmtouch|O_DIRECT" --include=*.sh --include=*.py --include=*.c --include=*.cpp .` → 0건. `grep -n "drop_caches" ~/.bash_history` → 0건.
  `run_measure.sh:54-56`
  ```
  # Let the target's page cache and the NIC settle between runs so the
  # first repeat is not systematically slower than the rest.
  sleep 3
  ```
  (주석이 명시적으로 "캐시가 안정되도록" 이라고 적혀 있다 — 즉 캐시를 비우는 게 아니라 **더운 상태로 고정**하는 설계다.)
- 확신도: **확정**.
- 영향: Baseline `t_pre` 에 디스크 읽기 비용이 거의 들어 있지 않다. 이것이 `io=0.000` 과 합쳐져 **Baseline I/O를 체계적으로 과소평가**한다. NDP 쪽은 target이 읽으므로 host 캐시와 무관하다 → 비대칭.

### H5-2. 1회 측정인가, 평균인가 / 표준편차 가능성
- 결론: **표 2·표 3(MOT17)과 4K 15s는 3회 측정이고 논문 값은 그 산술평균이다. 원시 로그가 남아 있어 표준편차를 낼 수 있다.** 반면 **그림 6의 30s·1min, 그림 7의 10분 입력은 원시 로그가 host에 전혀 없다 → 반복 횟수 확인 불가.**
- 근거: `run_measure.sh:19` `REPEATS=${3:-3}`; `~/.bash_history:1922` `... sudo -E ./run_measure.sh MOT17-04-DPM /mnt/ext4/MOT17_04.mp4 3`
  남아 있는 원시 로그: `maplogs/MOT17-04-DPM/measure/measure_raw.log`(3회), `.../measure_raw_sws.log`(3회), `maplogs/4k_testd_15s/measure_4k_raw.log`(3회).
  3회 산포(계산): baseline `host_pre` 11.035/11.150/11.254 (범위 0.219s, 약 ±1.0%), `infer` 3.142/3.152/3.153. `ndp_sws_q85 target_pre` 4.552/4.601/4.560. **재현성은 매우 좋다.**
- 그림 6·7 값 탐색 결과: `grep -rlE "3178|431\.5|291\.6|81\.9|158\.7|313\.3"` 를 `*.log *.txt *.csv *.json *.jsonl *.md` 대상으로 `~` 전체에 돌렸으나 **해당 수치를 담은 측정 로그 없음**(히트는 전부 무관한 패키지·데이터셋 파일). `[MEASURE]` 를 담은 파일은 `maplogs/MOT17-04-DPM/measure/*` 와 `maplogs/4k_testd_15s/*` 뿐이다.
- 확신도: 3회 평균(표 2·3, 4K 15s)은 **확정**; 그림 6·7은 **확인 불가**.

### H5-2b. 유일하게 남은 4K 로그가 그림 6·7과 맞지 않는다 (중요)
- 결론: 4K 15s에 대해 host에 남은 유일한 측정값은 그림 6의 값과 일치하지 않는다.

| 4K 15s (900프레임) | 그림 6/7 (논문) | `maplogs/4k_testd_15s` 3회 평균 |
|---|---|---|
| Baseline 종단간 | 81.9 s | 단계 합 **94.739** s / wall `e2e=111.854` s |
| NDP 샘플링 미적용 | 8.6 s | 단계 합 **12.477** s / wall `e2e=19.641` s |
| NDP 1/5 샘플링 | 5.9 s | 단계 합 **6.761** s / wall `e2e=8.201` s |
| Baseline host RSS | 5,748~5,839 MB (표 1) | **4,060.9** MB |

- 근거: `maplogs/4k_testd_15s/measure_4k_raw.log` (전 9행), 위 평균은 해당 파일 awk 집계.
- 해석(**추정**): 그림 6·7은 **다른 입력 클립 세트**(명령 기록상 `testc_15s/testc_1min/testc_10min.mp4`; 현재 로그의 클립은 `testd_15s.mp4`)로, 또는 **이번 `[MEASURE]` 계측이 들어가기 전의 코드**로 측정된 것으로 보인다. 어느 쪽인지 host 자료만으로는 가릴 수 없다.
- 확신도: 불일치 자체는 **확정**; 원인은 **확인 불가**.

### H5-3. 실험 준비 절차
- 결론: 명세의 수동 절차가 그대로 기록돼 있고, **영상 복사 후 `sync` 는 했지만 페이지 캐시는 비우지 않았다.** 따라서 복사 직후 돌린 Baseline은 파일이 host 페이지 캐시에 더운 상태였다.
- 근거: `~/.bash_history:1975-1995` (4K 실험 직전 구간, 발췌)
  ```
  1975  sudo nvme connect -t tcp -n nqn.2016-06.io.spdk:cnode1 -a <TARGET_DATA_IP> -s 4420
  1976  sudo mkfs.ext4 /dev/nvme1n1
  1978  sudo mount /dev/nvme1n1 /mnt/ext4
  1980  sudo cp testd_15s.mp4 /mnt/ext4/
  1981  sync
  1983  sudo ./run_measure_4k.sh /mnt/ext4/testd_15s.mp4 3
  ```
  MOT17 구간도 동일 패턴(`1915-1922`: connect → mkfs → mount → `run_measure.sh ... 3`). `drop_caches` 없음.
- 2) 원본 영상 host 로컬 위치:
  - 4K 15s: `~/darknet-for-ndp/testd_15s.mp4` (83,014,801 B — 로그의 `bytes=83014801` 과 일치) ✔
  - **4K 30s / 1min / 10min: 잘라낸 클립 자체는 host 로컬에 없음** (`find ~ -maxdepth 3 -name "*.mp4"` 결과: `people-detection.mp4`, `testd_video.mp4`, `MOT17_11.mp4`, `MOT17_09-DPM.mp4`, `testd_15s.mp4`). 명령 기록에 보이는 `testc_*.mp4` 는 `/mnt/ext4` 에만 있었고 **매 실험마다 `mkfs.ext4` 를 했으므로 이미 소실**되었을 가능성이 높다.
  - 단, **원본 마스터로 보이는 파일은 남아 있다**: `~/testd_video.mp4` — **av1**, 3840×2160, 60000/1001 fps, 255,735 프레임, 4266.57 s(약 71분), 7,148,357,243 B. `testd_15s.mp4`(h264, 3840×2160, 60000/1001 fps)와 해상도·프레임레이트가 같으므로 여기서 잘라 h264로 재인코딩한 것으로 보인다(**추정** — 재인코딩 명령은 `~/.bash_history` 에 남은 구간(`677` 행 `testc_4k_60fps.mp4` → `testc_15s_cut.mp4`)과 파일명 계열이 달라 `testd` 계열의 생성 명령은 기록에서 확인 불가). **이 마스터가 있으므로 30s/1min/10min 클립은 동일 절차로 재생성해 그림 6·7을 재측정할 수 있다.**
  - **MOT17-04 재인코딩본(`MOT17_04.mp4`): host 로컬에 없음.** 디렉터리 `MOT17-04-DPM/`(원본 프레임·gt)는 있으나 mp4는 없다. 측정 로그에 `bytes=45616817`, `frames=1050` 으로만 남아 있다.
- 확신도: **확정**(절차·`sync`·캐시 미삭제·파일 부재 모두 직접 확인).

---

## H6. 실험 조건

### H6-1. 입력 영상 사양
- 결론: 확인 가능한 것만:

| 파일 | codec | 해상도 | fps | 프레임 수 | bit_rate | pix_fmt | size | duration |
|---|---|---|---|---|---|---|---|---|
| `testd_15s.mp4` (4K 15s) | h264 | 3840×2160 | 60000/1001 | **900** | 44,088,514 | yuv420p | 83,014,801 | 15.015 s |
| `MOT17_11.mp4` | h264 | 1920×1080 | 30/1 | 900 | 16,409,986 | yuv420p | 61,549,748 | 30.000 s |
| `MOT17_09-DPM.mp4` | h264 | 1920×1080 | 30/1 | 525 | 13,773,247 | yuv420p | 30,136,566 | 17.500 s |
| **MOT17_04.mp4** | — | — | — | **1050** (로그) | — | — | **45,616,817** (로그) | — |
| **4K 30s/1min/10min** (클립) | — | — | — | — | — | — | — | — |
| `~/testd_video.mp4` (마스터 추정) | **av1** | 3840×2160 | 60000/1001 | 255,735 | 13,259,811 | yuv420p | 7,148,357,243 | 4266.574 s |

- 근거: 명세의 ffprobe 명령 그대로 실행. MOT17_04는 파일이 없어 `maplogs/MOT17-04-DPM/measure/measure_raw.log` 의 `frames=1050 bytes=45616817` 로 대체(해상도는 `[MAP][VERIFY]` 줄로 확인 가능하나 그 줄이 남은 로그 기준 1920×1080).
- 확신도: 앞 3행 **확정**; MOT17_04는 **로그 기반 확정(프레임 수·바이트)** / 나머지 사양 **확인 불가**; 4K 30s·1min·10min **확인 불가**.
- 주의: 4K 15s는 **60fps**다. "15초 = 900프레임"이며, MOT17-04 1050프레임(30fps, 35초분)과 프레임 수가 비슷하다. 논문에서 클립 길이를 초로만 쓰면 4K 쪽 프레임 수가 2배라는 사실이 가려진다.

### H6-2. 4K 실험의 JPEG 품질·샘플링
- 결론: **q85, chroma 4:2:0, 리사이즈 커널 swscale(기본값). 샘플링은 `sample_rate 1`(미적용)과 `5`(1/5) 두 조건.**
- 근거: `run_measure_4k.sh:69-71`
  ```
  run baseline 1
  run ndp_q85  1 -ndp -jpeg_q 85 -chroma 420
  run ndp_q85  5 -ndp -jpeg_q 85 -chroma 420
  ```
  로그 확인: `mode=ndp q=85 chroma=420 resize=sws` (`maplogs/4k_testd_15s/measure_4k_raw.log`). `[MAP][VERIFY] ... sample_rate=1/N` 출력도 존재(`src/detector.c:1262-1272`).
- 확신도: **확정**(단, 그림 6·7이 이 설정으로 측정됐다는 보장은 H5-2b 때문에 없음).

### H6-3. Baseline 디코딩 스레드 수
- 결론: **코드·스크립트 어디에서도 설정하지 않았다 → OpenCV/FFmpeg 기본값.** `cv::setNumThreads()` 호출이 없고, `OPENCV_FFMPEG_CAPTURE_OPTIONS`·`OMP_NUM_THREADS` 도 설정하지 않는다. OpenCV 4.6.0 + FFmpeg 6.1.1 기본값은 `threads=0`(auto) 이므로 **H.264 프레임 스레딩이 코어 수에 맞춰 자동 활성**된다. 이 host는 128 논리 코어이므로 디코딩은 멀티스레드로, 그 뒤의 `mat_to_image`/`letterbox_image` 는 단일 스레드로 수행된다.
- 근거: `grep -rn "setNumThreads" src/` → 0건. `grep -rn "OPENCV_FFMPEG_CAPTURE_OPTIONS\|OMP_NUM_THREADS" src/ *.sh` → 0건(홈의 `~/profile_pipeline.py:79-81` 에만 존재, 이 실험과 무관). `src/image_opencv.cpp:639` `cap = new cv::VideoCapture(path);` — 옵션 없이 생성.
- 확신도: 설정 부재는 **확정**; "기본값 = auto 스레딩"은 **추정**(런타임 확인은 실행이 필요해 하지 않았다).
- 주의: 디코딩만 128코어로 병렬화되고 `t_pre` 의 나머지(전해상도 float 변환)는 단일 스레드다. 즉 Baseline 전처리 비용의 대부분은 **병렬화 안 된 부분**이며, 이는 "전처리가 95%"라는 결론을 오히려 강화하지만, 동시에 "Baseline이 host 자원을 제대로 쓰지 못한 구현"이라는 reviewer 지적을 받을 수 있다.

---

## H7. 메모리와 전송량 계측 (F, G)

### H7-1. host RSS 측정 방식
- 결론: `getrusage(RUSAGE_SELF)` 의 `ru_maxrss`(**프로세스 최대 RSS**, KB → MB 환산). 즉 종료 시점 값이 아니라 **피크**이며, mAP 집계 구간까지 포함한 프로세스 전체 생애의 피크다. **GPU 메모리는 포함되지 않는다.**
- 근거: `src/detector.c:1760-1776`
  ```c
  struct rusage ru;
  getrusage(RUSAGE_SELF, &ru);
  printf("[MEASURE] ... host_rss_mb=%.1f host_blob_mb=%.1f ...",
         ..., (double)ru.ru_maxrss / 1024.0,
         (double)host_blob_bytes / (1024.0 * 1024.0), ...);
  ```
  (주석 `src/detector.c:1754-1757` 이 "GPU 메모리는 포함하지 않으며 target 쪽은 `[NDP-mem]` 을 본다"고 명시.)
  `~/.bash_history:1884` 에 `/usr/bin/time -f "... maxrss=%MkB"` 로 교차 확인한 흔적도 있다(별도 수동 확인, 표의 출처는 아님).
- 확신도: **확정**.
- 표 3 대조: 논문 1,547.6 / 671.6 / 725.2 MB = `measure_raw.log` baseline 3회 평균 1547.6, `measure_raw_sws.log` `ndp_sws_q85` 671.6, `ndp_sws_q100` 725.2 와 **정확히 일치**(확정).

### H7-2. Baseline 4K RSS 약 5.7GB의 구성
- 결론: **"원본 해상도 프레임 버퍼"라고 쓰는 것은 방향은 맞지만 숫자가 맞지 않는다.** 코드에서 식별되는 전해상도 할당의 **동시 상주량 합은 프레임당 약 134MB**이고, 남아 있는 실측값은 4,060.9MB다(논문의 5,748~5,839MB도 이 로그와 다름, H5-2b). 4GB 수준의 RSS는 프레임 버퍼 자체가 아니라 **할당·해제 반복에서 반환되지 않은 힙과 OpenCV/FFmpeg 내부 풀, CUDA 호스트 측 할당**이 누적된 피크로 보인다.
- 근거(4K = 3840×2160 기준 코드별 할당 크기 계산):
  | 할당 위치 | 크기 | 비고 |
  |---|---|---|
  | `src/image_opencv.cpp:881` `new cv::Mat(rows,cols,CV_8UC3)` (`in_img`) | 23.7 MB | 즉시 폐기되는 낭비 사본 |
  | `src/image_opencv.cpp:882` `mat_to_image(*src)` → `make_image` | **94.9 MB** | `xcalloc(h*w*c, 4)` |
  | `src/image.c:1018` `letterbox_image` → `resize_image` 중간 버퍼 | ~10 MB | `416×2160×3×4B` 수준 |
  | `src/image.c:1019` `make_image(416,416,3)` | 2.0 MB | |
  | **소계(동시 상주)** | **≈ 131 MB** | 프레임마다 할당·해제 반복 |
  | FFmpeg 디코더 프레임 풀 | 측정 안 함 | `threads=auto` 에서 참조 프레임 × 스레드 수만큼 증가 |

  (위 표의 MB는 1MB = 1024²B 기준 계산값. `mat_to_image` 는 3840×2160×3×4 = 99,532,800 B = 94.9 MiB.)
- 확신도: 개별 할당 크기와 "프레임마다 전해상도 버퍼를 새로 잡는다"는 사실은 **확정**(H2-3). **"5.7GB가 프레임 버퍼 때문"이라는 서술은 뒷받침되지 않는다 → 추정이며, 본문에 쓰지 않는 쪽을 권한다.** 원인을 쓰려면 `massif`/`heaptrack` 같은 실제 프로파일이 필요하다(이번 조사 범위 밖).

### H7-3. 전송량 측정 방식
- 결론: **둘 다 네트워크 실측이 아니다.**
  - Baseline: **`stat()` 으로 읽은 소스 파일 크기**(블록 I/O 카운터 아님).
  - NDP: **0xC0이 cdw0로 돌려준 결과 버퍼 총 바이트(`total_result_size`)**. 프레임별 통계(`frame_min/avg/max`)는 헤더를 제외한 프레임 payload 크기.
- 근거: `src/detector.c:1444-1450`
  ```c
  struct stat st;
  if (0 == stat(video_path, &st)) {
      transfer_bytes = (uint32_t)st.st_size;
      if (total_frames > 0) frame_avg = (double)st.st_size / total_frames;
  }
  ```
  `src/detector.c:1326-1327`
  ```c
  t_c0 = what_time_is_it_now() - t0;
  transfer_bytes = total_result_size;      // 0xC0 cdw0
  ```
  `src/detector.c:2968` `printf("[NDP] 0xC0 done: cdw0=%u bytes\n", total);`
  실측 확인: baseline `bytes=45616817` = MOT17_04.mp4 파일 크기; 4K baseline `bytes=83014801` = `testd_15s.mp4` 파일 크기(ffprobe `format.size` 와 일치) ✔
- 확신도: **확정**.
- 주의: `transfer_bytes` 가 `uint32_t` 다. **4GB 초과 입력에서 오버플로한다.** 4K 10분 클립이 4GB를 넘었다면 그림 8의 Baseline 전송량이 잘못될 수 있다(해당 로그가 없어 확인 불가). 또 NDP 수치는 NVMe-oF/TCP 헤더·재전송을 포함하지 않는 **payload 하한**이고, Baseline은 **파일 크기**이므로 양쪽 정의가 비대칭이다(한쪽은 "읽어야 할 바이트", 다른 쪽은 "실제로 받은 payload").

---

## H8. 실행 환경 (논문 실험 환경 표용)

| 항목 | 값 | 출처 |
|---|---|---|
| CPU | AMD EPYC 9554 64-Core, 1 소켓, 64코어/128스레드, 1 NUMA 노드, 최대 3764 MHz | `lscpu` |
| 메모리 | 755 GiB (`free -g` total) | `free -g` |
| 커널 | 7.0.0-38-generic | `uname -r` |
| GPU | NVIDIA RTX A6000, 49,140 MiB | `nvidia-smi` |
| NVIDIA 드라이버 / CUDA | 580.178.04 / CUDA 13.0 | `nvidia-smi` |
| OpenCV | **4.6.0** | `pkg-config --modversion opencv4` |
| FFmpeg | **6.1.1-3ubuntu5** | `ffmpeg -version` |
| Darknet 커밋 | `bfb91d3ce5e6d2224134f708c8e8ca4cbe2bae0c` (2026-08-14) "Add per-frame image/log output and remove scaler ablation option" | `git log -1` |
| NVMe-oF NIC | `enp133s0f0`, **10,000 Mb/s (10GbE)**, <HOST_DATA_IP>/30 | `ethtool`, `ip route get <TARGET_DATA_IP>` |
| target 주소 | <TARGET_DATA_IP>:4420, NQN `nqn.2016-06.io.spdk:cnode1`, transport tcp | `~/.bash_history:1975` |
| NVMe-oF 연결 | **현재 미연결** (로컬 `nvme0n1` = INTEL SSDPE2KE016T8 만 live) | `nvme list-subsys` |

- **중요**: 측정에 쓴 바이너리(`./darknet`, 2026-09-14 20:00)는 **커밋되지 않은 작업 트리**에서 빌드됐다. `git diff --stat` → `src/detector.c` +250/-28, `src/image_opencv.cpp` +22, `src/image_opencv.h` +1. 위에서 인용한 줄번호는 모두 **작업 트리(미커밋) 상태**를 가리킨다. 논문 제출 전에 커밋해 재현 가능한 리비전을 확보해야 한다.
- 확신도: **확정**(각 명령 출력 그대로).

---

## 논문 수치와 어긋나거나 주의가 필요한 점

1. **"종단간(end-to-end)"이 wall-clock이 아니다 (표 2, A·B).** 실제 wall-clock은 Baseline 35.507s / NDP q85 26.519s 인데, 논문은 단계 합 14.295s / 9.334s 를 종단간으로 적었다. 빠진 약 21s / 17s 는 mAP 집계·라벨 로딩 구간이다. 표 2의 합이 "정확히" 일치하는 이유가 바로 이것. **표현을 "stage-sum latency"로 바꾸거나, 집계 구간을 타이머로 감싸 실제 wall-clock으로 다시 내야 한다.** (H1-1, H3-3)
2. **표 2의 Baseline과 NDP가 서로 다른 로그 파일에서 왔다.** NDP는 `-resize sws` 결과(`measure_raw_sws.log`), Baseline은 `measure_raw.log`. 같은 스크립트의 darknet-커널 NDP는 `target_pre≈19.2s`(sws의 4.2배)로 전혀 다르다. 본문에 커널 구성을 명시하지 않으면 재현 불가. (H1-4)
3. **그림 6·7의 원시 로그가 host에 전혀 없고, 남은 4K 15s 로그와 값이 맞지 않는다.** 그림 6의 Baseline 81.9s vs 로그 94.739s(단계 합)/111.854s(wall), NDP 8.6s vs 12.477s, 5.9s vs 6.761s. 표 1의 4K Baseline RSS 5,748~5,839MB vs 로그 4,060.9MB. 명령 기록상 그림 6·7은 `testc_*.mp4`(다른 클립 세트) 또는 `[MEASURE]` 도입 이전 코드로 측정된 것으로 보인다. **그림 6·7·표 1의 4K 부분은 현재 코드로 재측정하지 않으면 본문과 코드가 불일치한다.** 다행히 원본 마스터(`~/testd_video.mp4`, 4K 60fps 71분)가 남아 있어 15s/30s/1min/10min 클립을 모두 재생성할 수 있다. (H5-2, H5-2b, H5-3)
4. **Baseline I/O는 측정된 적이 없다.** 코드가 항상 `io=0.000` 을 출력하고, 어떤 I/O 계측 수단도 저장소·홈·명령 기록에 없다. 그림 7의 "Baseline I/O 1.6%" 는 출처 불명이다. (H1-3)
5. **페이지 캐시를 비우지 않았다.** 매 실험마다 `mkfs.ext4` 후 `cp` + `sync` 로 영상을 올리고 곧바로 Baseline을 돌렸다. Baseline은 host 페이지 캐시에서 읽혔을 가능성이 크고(755GB RAM, 파일 83MB/45MB), 스크립트 주석도 캐시를 "안정시킨다"고 적고 있다. NDP는 target이 읽으므로 host 캐시와 무관 → **I/O 비교가 구조적으로 Baseline에 유리하게 편향**돼 있다. 최소한 "Baseline은 warm-cache 조건"이라고 명시해야 한다. (H5-1, H5-3)
6. **`mat_to_image` 53%의 근거를 찾을 수 없다.** 단독 타이머가 코드에 없고 계산 스크립트·로그도 없다. 홈의 프로파일러 3종은 Ultralytics/OpenCV-Python 경로로 darknet과 무관하다. 분모가 전처리 합인지 종단간인지도 두 해석이 거의 같은 값을 주어 구분되지 않는다. **원시 근거 재확보 또는 재측정 필요.** (H2-1, H2-2)
7. **표 1의 5.7GB를 "원본 해상도 프레임 버퍼" 탓으로 쓰기 어렵다.** 코드에서 식별되는 전해상도 동시 상주량은 프레임당 약 131MB이고, 남은 실측 피크는 4,060.9MB다. 두 자릿수 차이를 프레임 버퍼로 설명할 수 없다. (H7-2)
8. **전송량 정의가 비대칭이고 32비트 오버플로 위험이 있다.** Baseline = `stat()` 파일 크기, NDP = 0xC0 cdw0 payload. 둘 다 네트워크 실측이 아니다. `transfer_bytes` 가 `uint32_t` 이므로 4GB 초과 입력(4K 10분 후보)에서 값이 깨진다. (H7-3)
9. **Baseline 구현에 명백한 낭비가 있다.** `get_image_from_stream_letterbox()` 가 결과를 즉시 버리는 전해상도 `cv::resize` 사본을 매 프레임 만든다(4K 23.7MB). 이 비용이 `t_pre` 에 그대로 들어가 Baseline 전처리를 실제보다 나쁘게 만든다. reviewer가 "straw-man baseline" 으로 지적할 수 있는 지점. (H1-2)
10. **4K 클립이 60fps다.** "15초"가 900프레임이고 MOT17-04 "1050프레임"과 프레임 수가 비슷하다. 길이를 초로만 표기하면 두 실험의 작업량 관계가 왜곡돼 보인다. (H6-1)
11. **추론 시간 차이는 구간 정의로 설명되지 않는다.** 양쪽 모두 `t_infer` 는 `network_predict()` 한 줄뿐이고 단일 스레드이므로 fetch 경합도 없다. 실측 차이는 4K 9.8% / MOT17 6.0% 로 그림 7의 17%보다 작다. 원인 설명이 필요하면 캐시/런치 지연 가설을 **측정으로** 뒷받침해야 한다. (H4)
12. **측정 바이너리가 미커밋 작업 트리에서 빌드됐다.** `src/detector.c` +250/-28 등이 커밋되지 않은 상태다. 재현 가능한 리비전 확보가 필요하다. (H8)
