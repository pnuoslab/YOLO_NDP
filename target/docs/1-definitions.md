## Definition

---

이 문서에서는 플랫폼 구축 시 필수로 알아야 하는 용어에 대해 정의합니다.

#### Basic

- `NVMe over Fabric`: NVMe over Fabric(NVMeF)은 네트워크 패브릭(예: RDMA, TCP, FC)을 통해 원격 스토리지에 NVMe 프로토콜을 확장하여 로컬 NVMe 스토리지와 유사한 고성능, 저지연 액세스를 제공하는 기술입니다. 
NVMe over PCIe와 달리 host와 target 드라이버로 이루어집니다. 본 논문에선 `NVMe over Fabric TCP`를 사용합니다.

- `host`: NVMe over Fabric TCP Host 커널 드라이버를 수행하는 주체입니다. 이 드라이버를 구동하는 물리 서버 혹은 드라이버 그 자체를 의미합니다.

- `controller(target)`: NVMe over Fabric TCP Target 커널 드라이버를 수행하는 주체입니다. 이 드라이버를 구동하는 물리 서버 혹은 드라이버 그 자체를 의미합니다. target과 controller를 유사한 의미로 생각하셔도 됩니다.

- `opcode`: 컴퓨터 과학에서 사용하는 operation code(명령 코드)의 약자로, 본 논문에서는 사용자 정의 NVMe 드라이버 기능을 추가 및 호출 시 사용합니다.

#### Networking(host to controller)

본 논문에서는 NVMe over Fabric TCP를 사용합니다. 아래는 이 프로토콜에서 숙지해야하는 필수 용어들입니다.

- `PDU`: NVMe over Fabric TCP는 PDU(Protocol Data Unit)이라는 패킷으로, host와 controller 간 명령어 및 데이터를 송수신합니다.

- `h2c`: host to controller 데이터 전송 타입의 약자로, 호스트에서 컨트롤러 방향으로 데이터를 전송할 때 사용합니다(ex. NVMe Write).

- `c2h`: controller to host 데이터 전송 타입의 약자로, 컨트롤러에서 호스트 방향으로 데이터를 전송할 때 사용합니다(ex. NVMe Read).

- `inline-data`: NVMe over Fabric TCP에서는 PDU의 최대 데이터 전송량을 초과하지 않는 작은 양의 데이터 전송 시, 한 개의 PDU에 모든 데이터를 담아 전송하며 이를 inline data라고 합니다.

- `SGL(Scatter-Gather List)`: NVMe over Fabric TCP에서는 한 PDU의 최대 데이터 전송량을 넘는 데이터 전송 시, Scatter Gather List 방식으로 데이터를 나눠 PDU에 담아 데이터를 전송합니다.

#### File System(host only)

본 논문에서 제시한 플랫폼은 extent 기반 파일시스템에 대해서 near data processing을 지원하도록 설계되었습니다.
파일시스템은 호스트 시스템에 존재하며, nvme-cli와 통합되어 사용됩니다.

- `extent`: extent는 리눅스 파일시스템(ex. ext4)에서 사용하는 파일과 블록(데이터 저장 단위) 간 매핑입니다. 
  - 용량이 작은 파일의 경우, 데이터가 표현되는 Logical Block Address의 범위가 연속적이며 단일 extent로 표현될 수 있습니다. 
  - 용량이 큰 파일의 경우, 데이터가 표현되는 Logical Block Address의 범위는 일반적으로 불연속적이며 한 개 이상의 extent들의 집합으로 표현될 수 있습니다.

#### Data Processing(controller only)

본 논문에서는 연산을 처리하기 위해 두 종류의 `연산 메타데이터 파일`과 `연산 대상 파일`을 사용합니다.

예시로 로그 파일에서 grep 연산을 수행한다고 가정하면 로그 파일이 `연산 대상파일`이고, grep할 키워드가 쓰여있는 파일이 `연산 메타데이터 파일`입니다.

