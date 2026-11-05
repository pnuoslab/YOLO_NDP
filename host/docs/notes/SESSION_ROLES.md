# 세션 역할 재정의 (2026-10-19, 사용자 승인)

**대상 독자**: target 측 Claude 세션(oslab-server3), 다음 명세(03 이후) 작성자, 그리고 이 실험을 이어받는 모든 세션.
**결정**: 사용자가 2026-10-19에 "host Claude가 target을 켜고 순서대로 다 할 수 있으면 그게 낫다"고 결정했다. 같은 날 host Claude가 target을 **root 없이** 기동해 본측정 357회를 끝까지 돌리면서 실제로 가능함이 확인됐다.

> ⚠️ `result_02_target.md` §0·§5의 "명세 03부터는 **target 측 Claude**가 `nvmf_tgt`를 직접 띄운다"는 이 결정으로 **대체된다.** 측정 중 target 프로세스는 **host Claude만** 다룬다.

## 1. 역할

| | 이전 (명세 01·02) | 지금 |
|---|---|---|
| **사용자** | target 기동·`chmod`·재기동, host `nvme connect`·`mkfs`·`mount`, 모든 sudo 명령을 대신 실행하고 결과를 붙여 넣음 | **host sudo 비밀번호를 Terminal 탭에 입력하는 것뿐.** 그 외에는 결정과 승인 |
| **host Claude** (supercxl) | host 계측·측정 스크립트 | 위에 더해 **target 기동·구성·종료, target 로그·iostat 수집, host 볼륨 연결·검증, 측정 전체 진행** |
| **target Claude** (oslab-server3) | target 기동·계측·빌드 | **target 코드 변경·빌드·유닛 테스트·target 동작 분석.** 측정 중에는 `nvmf_tgt`를 기동·종료·재구성하지 않는다 |

비밀번호는 대화로 주고받지 않는다. host Claude가 명령을 Terminal 탭에 넣고, 사용자가 그 탭에 비밀번호를 친다. sudo 인증은 탭마다 따로이므로 한 번에 끝나도록 스크립트로 묶는다(`start_main.sh`, `queue_main.sh`).

## 2. 이렇게 바꿀 수 있었던 근거

| 필요한 것 | 어떻게 해결했나 |
|---|---|
| host → target 접속 | supercxl(<HOST_LAN_IP>)과 target(<TARGET_LAN_IP>)이 같은 LAN. `~/.ssh/config` 별칭 `target-server3`(포트 <SSH_PORT>), 이 host 전용 키 `~/.ssh/id_ed25519_ndp`. bastion 불필요 |
| target을 sudo 없이 기동 | `/dev/vfio/4`·`/dev/hugepages`가 kimkitae 소유, memlock 8.2 GB, NVMe `82:00.0`이 `vfio-pci`. **`nvmf_tgt_measure`를 kimkitae 권한으로 기동 성공** — 프로세스·RPC 소켓·lock 파일 모두 kimkitae 소유 |
| `conf_subsystem.sh`의 `sudo rpc.py` | 소켓이 kimkitae 소유가 되므로 같은 RPC 6개를 sudo 없이 실행 |
| root로 떠 있던 target 종료 | RPC `spdk_kill_instance SIGTERM`(소켓 666) → 정상 종료 시 `/var/tmp`의 root 소유 lock·소켓 파일까지 SPDK가 스스로 지움 |

## 3. 정본 절차

### target 기동 (host Claude가 ssh로)
```bash
ssh target-server3 'cd ~/near_data_processing_for_video/spdk && setsid nohup build/bin/nvmf_tgt_measure > ~/nvmf_tgt_measure.log 2>&1 < /dev/null &'
```
RPC 소켓(`/var/tmp/spdk.sock`)이 생기면, `spdk/` 디렉터리에서 sudo 없이:
```bash
R=scripts/rpc.py
$R nvmf_create_transport -t TCP -u 131072 -m 20 -c 8192 -i 2097152
$R bdev_nvme_attach_controller -b nvme1 -t PCIe -a 0000:82:00.0
$R nvmf_create_subsystem nqn.2016-06.io.spdk:cnode1 -a -s SPDK00000000000001 -d nvme1n1
$R nvmf_subsystem_add_ns nqn.2016-06.io.spdk:cnode1 nvme1n1
$R nvmf_subsystem_add_listener nqn.2016-06.io.spdk:cnode1 -t tcp -a <TARGET_DATA_IP> -s 4420
$R nvmf_subsystem_add_listener nqn.2014-08.org.nvmexpress.discovery -t tcp -a <TARGET_DATA_IP> -s 4420
```
`pci_event ... Socket recv buffer is too small` 오류는 root가 아니라 핫플러그 소켓 버퍼를 못 늘린 것뿐이고 동작에는 영향이 없다.

### target 종료
```bash
ssh target-server3 '~/near_data_processing_for_video/spdk/scripts/rpc.py spdk_kill_instance SIGTERM'
```

### ⚠️ target을 재기동했으면 host는 반드시 다시 연결한다
```bash
sudo ./remount_verify.sh
```
**target 재기동 뒤 nvme-tcp 자동 재연결에 맡기면 안 된다.** 2026-10-19에 그렇게 했다가 컨트롤러는 5초 만에 살아났지만 **namespace는 돌아오지 않았다**(새 target이 다른 식별자로 내보냄). 블록 장치가 사라지면서 마운트된 ext4가 비상 정지했다. `remount_verify.sh`는 disconnect → connect → 저널 재생 → `e2fsck -n` → mount → 입력 12개 sha256 대조까지 하고, 하나라도 어긋나면 멈춘다.

### 측정 시작 / 상태 확인
```bash
sudo ./start_main.sh E1 E5      # 볼륨 검증 후 백그라운드로 측정 시작 (비밀번호 1회)
sudo ./queue_main.sh E2 E3      # 지금 블록이 끝나면 자동 시작
./status_main.sh                # sudo 불필요. 진행률·실패·target·볼륨
./check_target.sh               # target 수집 경로 점검
```
측정 프로세스는 `setsid nohup`으로 떠서 **사용자 PC를 꺼도 끝까지 돈다.**

## 4. 모든 세션이 지킬 것

1. **측정 중에는 target Claude가 `nvmf_tgt`를 건드리지 않는다.** 재기동하면 진행 중인 측정이 깨지고 host 볼륨이 정지한다.
2. **`bdev_get_bdevs` RPC 호출 금지** — 이 SPDK 트리의 기존 버그로 target이 segfault한다(`result_02_target.md` §8-1).
3. **추적 빌드(`nvmf_tgt_iotrace`)로 측정하지 않는다.** `run_all.sh`가 실행 중인 바이너리를 확인해 추적 빌드면 시작을 거부한다(2026-10-19에 "재기동했다"고 보고됐지만 실제로는 추적 빌드가 그대로 떠 있었다).
4. **실행 중인 스크립트를 고치지 않는다.** bash는 스크립트를 조금씩 읽어 가므로 측정 중에 `run_all.sh`를 수정하면 진행 중인 측정이 깨진다.
5. 프로세스 확인은 **명령줄 기준, 시작 위치 고정** 패턴으로 한다(`^bash (\./|/.../)run_all\.sh`, `build/bin/nvmf_tgt`). 고정하지 않으면 그 패턴을 담은 감시 셸 자신이 걸린다 — 실제로 감시가 영원히 끝나지 않을 뻔했다.
6. target 로그 경로는 기동할 때 정한 `~/nvmf_tgt_measure.log`다. 다른 이름으로 띄우면 `TARGET_LOG`를 맞춰야 하고, 안 맞으면 `run_all.sh`가 시작을 거부한다.

## 5. 다음 명세 작성자에게

- "사용자가 직접 실행" 규칙은 **sudo 비밀번호가 꼭 필요한 host 단계에만** 남긴다. target 쪽은 host Claude가 한다.
- host·target이 만나는 절차는 **양쪽 명세에 같은 명령을 직접 적는다.** 각 Claude는 자기에게 온 명세만 본다(target 보고서 §10-4와 같은 요청).
- 정확한 경로와 이름(이번에 실제로 쓴 값):
  target `target-server3` = <TARGET_LAN_IP>:<SSH_PORT> · 바이너리 `~/near_data_processing_for_video/spdk/build/bin/nvmf_tgt_measure` · RPC `~/near_data_processing_for_video/spdk/scripts/rpc.py`(기본 소켓) · 로그 `~/nvmf_tgt_measure.log` · host 볼륨 `/dev/nvme1n1` → `/mnt/ext4` · 결과 `~/ndp_results/main/`
