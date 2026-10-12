# Rust 自有语义引擎架构：独立审查

2026-10-07；独立 reviewer `rust_bridge_plan_review`，主 agent 记录原始结论。
用户已批准改变分析引擎身份；本报告不扩大权限、产品能力或发布授权。
初稿 blob `4a9dbb100b79066b518e20eef737d0e28b2aa284`；
修订复核 blob `fe35900c3900eb8d1bd48ed783af9ca0296d66db`。
范围：完整新架构草案、父计划增补、冻结数值合同与 Wasmtime 官方资料。
只读，未获取依赖、构建、实验、查询 PR 状态或改变配置/索引。

## P1：资源限制须先于实例化/start

初稿明确未知 imports 在 start 前拒绝，但未明确资源/取消限制的生效时点。
零 import 的合法 start 函数仍可死循环或大量分配。要求覆盖输入解析、验证、
编译、实例化和 start，预先安装 fuel/epoch/memory/table/stack 限制，宿主外层
wall-time/RSS/取消覆盖 guest 内机制无法控制的阶段；不可观察/中断记 blocked。

已修订并独立复核通过。新增 zero-import start、初始分配、递归等负例，
不是在首个语义查询前才加限制。解决的是计划缺口，不是已验证运行时防护。

## P2：区分宿主后端和 guest JIT

初稿“guest JIT 不默认开启”容易与 Wasmtime 的编译模式混淆。
已改为区分 guest 自行生成/装载代码与可信宿主 interpreter/JIT/AOT，执行卡
须明确 backend/features、可执行页与签名权限；拒绝项目 native/cwasm 与未验证
反序列化输入。独立复核确认已解决，解释器本身不自动证明资源/平台资格。

## 最终结论与未完成卡项

无新增计划阻断项；仅可继续只读 C0 依赖锁和执行卡制定。未冻结卡项、独立审查
及执行确认前，不开始依赖获取、构建或 C1。计划通过不是引擎/WASM 可行性通过。

须继续固定：完整传递依赖与构建脚本、runtime/compiler/target/features/许可证、
模块字节 receipt 与 import/export ABI、输入输出长度/深度/数量与副本上限、
新 executable/argv/cwd/env/physical parent/input 各角色合同、五平台资产及
负例观察器、全部阶段时间/RSS/取消/清理方法。

宿主是 TCB，保留 scanner/安装/准备链安全门；不得用 guest 限制给宿主豁免。
保留 crate graph/cfg/features/sysroot 和旧冻结成功真值，不改 partial 掩盖退步。
新角色拓扑明确、不伪造 RA parent，旧 Cargo stdin 仅可凭无调用证据按角色 N/A。
五平台实际验收、Windows ARM64、大型留出复跑和独立公开授权均保持。

官方 runtime 五平台 Release 资产存在只算候选获取证据，不算任何平台通过。
官方能力文档没有证明此上游核心可编译、可精确查询或宿主安全。
