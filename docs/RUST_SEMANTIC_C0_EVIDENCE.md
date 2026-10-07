# C0 固定源码证据与构建前缺口

2026-10-07；仅源码获取/静态审计，没有构建或语义原型通过。
执行卡见 [C0-A](RUST_SEMANTIC_SOURCE_CARD.md)。机器路径/逐文件 receipt 仅存于
忽略证据，未提交源码副本或导航索引。

## 已取得的源码身份

Rust commit `88d9e12ae178fab0fb5cc050a94da85685d449ea`，tree
`b10ad5e3f8014a7d88a67cb98273459a0d77e3ce`：rust-analyzer 与根许可证
共 2,333 个条目、21,754,997 字节，逐 Git blob/SHA-256 核验。
三处上游符号链接仅保留经核验的目标文本于独立证据，不创建/跟随链接。
首次拒绝链接的失败记录保留；重试复用准确对象，不重新获取。

Wasmtime 49.0.2 commit `3c8a3e79aa3188a1a12b3aebbaa097abccceac92`，tree
`8f5adb74e49531cbbb19810849f129d3c07ed45d`：选取 root Cargo manifests/lock/
LICENSE、wasmtime/core/cranelift/environ/fiber/jit-debug/jit-icache-coherence/
unwinder/versioned-export-macros、cranelift 与 pulley；2,508 条目、27,957,653
字节，逐 blob/SHA-256 核验。不是完整 workspace 或最终传递依赖锁。
首次宽选 crates 被 wasi-nn 示例的 14,246,826 字节 ONNX 非源码资产触发
8 MiB 单文件门，失败保留；只收窄选取范围，未提高限额、未获取 runtime/compiler
可执行资产。首份归档内含 ONNX 示例数据，未物化该文件或执行其中内容。

系统 Git 2.54.0（Apple Git-157），清空环境、禁全局配置/hooks/checkout/
filters/credentials/submodules，固定 HTTPS origin。获取进程组清理须确认组消失，
父进程 reaped 后方记录成功。采样磁盘峰值分别 102,291,837 /187,301,712 字节，
这是检测式限额证据，不是硬配额。八小时原型预算首次获取起点 02:21:13 UTC；
两份源码核验完成 02:25:37 UTC，无 Cargo/rustup/build.rs/源码执行。

## 具体适配点，不冒充不可行结论

静态保守库存含所有 target/optional 依赖，ide 的本地闭包 33 包；上游 lock 387
包，不能当成实际 wasm feature-resolved 图。识别两个本地编译期 proc-macro 包
`macros`/`query-group-macro`，库闭包未发现本地 build.rs；外部 registry 构建脚本
和编译宏尚未获取审查。编译工具自身的可信宏与不可信项目 proc macro 是不同角色。

- `ide-db/src/apply_change.rs:13` 无条件 `Instant::now()`，用于 cancellation 计时。
  无 WASI guest 的初始化不能假定原生时钟可用；需去除纯诊断时钟需求或冻结受限
  数据 callback，独立审查，保持真实 cancellation/语义更新不变。
- `ide-db/src/symbol_index.rs:250,264` 用 rayon 并行预热；workspace salsa 也启用
  rayon/inventory。仅顶层 ide 不直接调用子进程不证明传递依赖无线程/能力。
  需核对实际定义/引用路径和单线程配置，不能用字符串匹配替代语义查询。
- Wasmtime 的 `std,runtime,cranelift,pulley` 只是候选最小 feature 集，不是已冻结
  或构建成功。默认集禁用；Pulley 仍需 Cranelift 编译为 bytecode，不能称“不编译”。
  `crates/wasmtime/build.rs` runtime 在 Unix 会通过 cc 编译官方 helpers.c，意味着
  构建卡还须锁定实际 C compiler/linker、宏和其源码，不是仅允许 rustc 就足够。

下一道门：按准确 lock/checksum 获取惰性 registry 源码、完成传递构建脚本/宏审查、
复用并重新验证实际 compiler/target 身份，冻结 ABI/features/backend/资源监督后
独立审查构建执行卡。此文未宣称 C0 全部完成、C1、I0、阶段2或任一平台资格通过。

## C0-A1 关键源码出口

单独审查 [12 包源码卡](RUST_SEMANTIC_KEY_SOURCE_CARD.md) 后获取了固定 Rust lock
命中的 12 个关键包；694 个文件、4,778,445 字节，归档总 957,699 字节。
独立 reviewer 重算每份归档 checksum、逐文件 hash/字节/清单，无差异；控制器
20.378 秒并确认所属组消失、父进程回收，worker 响应均 closed/verified。
获取期间没有执行库、编译宏或 build.rs。

有支持路线的具体源码证据，不是编译成功：rayon-core 1.13.0 的
`registry.rs::default_global_registry` 明确在 Unsupported threading 时使用当前
线程池；也可研究显式 `num_threads(1).use_current_thread()` 避免先尝试创建线程。
inventory 0.3.24 支持 wasm-unknown-unknown，但多次调用实例须冻结并调用 ctor
入口，限制必须先于 ctor。parking_lot_core 0.9.12 的 wasm parker 对 park 操作
会 panic，单线程真实路径仍须验证。salsa-macros 0.27.0 的 debug.rs 受
SALSA_DEBUG_MACRO 环境变量触发 rustfmt 子进程；构建环境不得继承该变量，
不以“只是编译宏”豁免执行面。

已只读重新校验原官方 1.98.0/macOS ARM64 安装 receipt 的全部 3,766 个文件和
工具 hash；未运行 compiler。随后 A3 已取得惰性 wasm target，尚未安装。官方固定
日期 channel manifest SHA-256 `3f7d139b73bbbd0004ef6e58b430831c68cdad2b1f64ee2eb35d54c09199489a`
列出的 target xz checksum 为
`3bb537c09555b96020a38a3a8810c38908b194eff3c2cb6184a45dda5c6828de`；
manifest 来源记录及后续 A3 资产核验分别保留，不运行 rustup 或改原安装。

## C0-A2 与 A3 后续证据

425 包保守 registry 库存的首次获取在第 27 包 TLS 握手超时；55.112 秒失败，
所属组消失、父进程回收，失败 receipt 保留。恢复获取器经独立审查后复用已下载
归档并重算 checksum，使用合计 900 秒剩余的 844 秒，不重置预算，不自动第二次
恢复。该恢复在 393 包后被 13,204,988 字节 Windows import library 触发
8 MiB 普通文件门，539.164 秒失败。另经独立审查的一次 source-only 范围尝试
不物化该库，但在 395 包后触发 384 MiB 解压 tar 总门，8.142 秒失败。
三次所属组均消失、父进程回收，失败 receipt 原样保留，不再自动获取。

独立 reviewer 重验 395 包 checksum、19,816 个物化文件/字节/hash/0600 和
唯一未物化库，partial reusable-source 出口通过。已核验压缩 52,373,618 字节、
tar 402,374,144 字节、普通文件 373,142,345 字节；该库 13,204,988 字节未物化，
不具构建资格。worker/controller 仍 failed，A2 整批不通过。
余 30 包中只有 11 包属于 ide 的保守 lock registry 闭包（181 registry、30 local）；
按 [guest 范围修订卡](RUST_SEMANTIC_GUEST_SOURCE_CARD.md) 独立审查新的明确资源
合同，只读复用既有源码，非完整 425 包门恢复，不重置时间或隐瞒限额变化。
首次新增 11 包及一次仅剩 3 包的网络恢复均遇 TLS EOF；失败与清理证据保留。
随后从既有本机 cache 限长重算三份原锁 checksum，经单独审查后仅离线物化
最后 3 包，禁止网络 fallback。独立核验 8+3 包共 417 文件及完整 181 registry
集合，通过保守 guest 源码出口。没有 Cargo-resolved 图、构建或语义成功。

已开始隔离的原型源码草稿：候选唯一 manifest、Unicode 坐标转换、真正上游
Analysis API 定义/引用适配；不依赖字符串 matcher，不接线产品，Rust 测试未
编译执行。graph admission、零 import ABI、线程/时钟适配及构建卡仍待完成。
新增 [Linux 观察能力探针卡](RUST_SEMANTIC_OBSERVER_PROBE_CARD.md) 的独立审查
已复核通过，最终退出限额与失败原始日志问题已修复；只验证短命受控 exec，不授权 metadata
或构建，不请求用户机器 root/EndpointSecurity 权限。

[A3 获取卡](RUST_SEMANTIC_WASM_TARGET_CARD.md) 与获取器独立审查通过后，已取得
上述准确官方 WASM std 组件。独立复核归档和全部 66 个普通文件/字节/hash/
非执行权限，无差异；压缩 22,441,312 字节，解压 tar 99,721,216 字节，普通文件
99,608,371 字节，worker verified/closed，controller 16.662 秒、所属组消失且
父进程回收。许可证及组件 manifests 齐全。channel manifest 的关联身份来自此前
读取，本次独立复核没有重新取得原 manifest，不称重新验证其来源。
这仅证明组件作为惰性数据可用：未安装、未接线 sysroot、未运行编译器。

补充静态证据：parking_lot_core 的 `parking_lot.rs:19–41` 已为纯 WASM 排除
fairness 的真实 Instant，用 TimeoutInstant；因此不能把此处误判为必然时钟
panic。parker 真正等待时仍会 panic，实际单线程语义路径仍未测试。

当前准确提交 `739adc4f913c805c489860bf7974329de35fad0c` 的常规 CI run
`37560533184` 与五平台内部安全 run `37560533215` 均完成且全部成功，包括
native Windows ARM64。它们不包含新核心构建或资格，不能作为 Rust 已可用的结论。
