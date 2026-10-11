# 工具与数据来源

## ABC 与 Yosys

项目使用 Ubuntu 24.04 noble 官方归档中的 `yosys 0.33-5build2` 和 `yosys-abc 0.33-5build2`。这里的 ABC 为 YosysHQ 维护的 ABC 分支打包版本。下载地址和 SHA-256 保存在 `manifest.json`，二进制校验和保存在每次实验的 `experiment.json`。

- https://github.com/berkeley-abc/abc
- https://github.com/YosysHQ/yosys
- https://archive.ubuntu.com/ubuntu/pool/universe/y/yosys/

软件包的版权说明随解压文件保存在 `.tools/usr/share/doc/`。本项目使用这些现成工具，不声称实现了底层综合、映射或 SAT 算法。

## EPFL 基准

- 仓库：https://github.com/lsils/benchmarks
- 固定提交：`82d8cc6910419298e713a46644ed59fd3df53038`
- MIT 许可证副本：`benchmarks/EPFL_LICENSE`

初始电路按预先划分分为两个开发电路与十个测试电路；后者用于扩展实验诊断后，不再视作未观察测试集。另有八个新候选电路的来源与校验和记录在 `manifest_holdout.json`。原始电路文件不作手工改动。

公开仓库附带原始 MIT 许可证与上游 README，不包含 ABC/Yosys 的二进制或源码副本。工具下载脚本按固定官方软件包地址获取工具；下载后的版权说明位于 `.tools/usr/share/doc/`。

## 相关论文

FlowTune: End-to-end Automatic Logic Optimization Exploration via Domain-specific Multi-armed Bandit.
https://arxiv.org/abs/2202.07721

该论文用于建立研究背景。本项目的束搜索实现不是 FlowTune 算法复现，报告不得写成复现了该论文的实验提升。
