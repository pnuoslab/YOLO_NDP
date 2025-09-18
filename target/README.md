# Near-data-processing-platform

A Near-Data-Processing Platform Implementation With SPDK and NVMe-over-Fabric

### Abstract
The increasing demand for large-scale data processing, driven by applications such as AI
training, has highlighted the bottleneck caused by massive data movement. To address this
challenge, the concept of Near-Data Processing (NDP) has gained significant attention. This
paper proposes an NDP platform tailored for networked storage environments, which are
commonly used in data centers. By extending the NVMe-over-Fabric protocol and modifying
the SPDK user-level storage driver, our proposed platform aims to provide a user-friendly and
efficient NDP solution without requiring additional hardware modifications.


### Overall Architecture

<img src="./docs/assets/ndp-overall.svg"/>

### Dependency
- nvme-cli version 2.10.2 (git 2.10.2-35-gf7c7953)
- libnvme version 1.11 (git 1.11-4-gf1ddb96)
- spdk version [24.09](https://github.com/spdk/spdk/releases/tag/v24.09) (git sha 0e983c5)
- Ubuntu 20.04.6 LTS (Kernel Version 5.15.0-125-generic)
  - (Note) host and target configuration are same

### Docs

This documentation covers the implementation and operation of the platform(available in Korean).

[Read Doc](./docs/0-index.md)

### Contact
Feel free to get in touch with me or open a GitHub issue!
- Seunghun Yu ( yungs0917@naver.com )
