# Rust 自有语义引擎：架构修订与原型门

2026-10-07；用户已明确批准改用官方开源库构建 Atlas 自有分析引擎。
基线 `b20e4640469d5a6f60b8fb6a18a81034d03b2793`。状态：独立修订复核通过，
仅可继续只读 C0 锁依赖/执行卡；获取、构建和 C1 尚未开始。
审查记录见 [独立报告](RUST_SEMANTIC_CORE_PLAN_REVIEW.md)。
这是新的架构路线，不把旧 B0/B1 的失败改写为通过，不恢复 heartbeat。
仍只写 `codex/rust-public-enablement` 与 Draft PR21，stable 0.27.0 Rust 关闭。

## 批准了什么、未批准什么

批准替换原 T2 官方 analyzer 可执行文件路径，使用明确标识为 Atlas 自有引擎的
宿主/语义模块。不得冒充官方工具原字节，保留完整上游来源、许可证、依赖锁和
构建身份。语义核心、宿主、协议、运行时各有独立版本/hash/receipt。
不重新签名旧官方工具、不补丁后沿用旧 hash，不修改旧 release/tag。

未批准提权、系统 profile/防火墙/全局配置、部署用户项目、真实新 Codex 连接、
公开开关/合并/Release。五平台、T0/T1/T2 语义和资源门不变；非位置候选仍是 T1，
位置定义与唯一声明引用仍须真实 T2，不能以字符串/语法搜索替代。
本次架构批准不代替尚未冻结的依赖获取/构建执行卡，不自动许可任意 build.rs。

## 为什么不是简单切换 rust-project.json

固定 Rust 上游 commit `88d9e12ae178fab0fb5cc050a94da85685d449ea`：
[workspace::load_inline](https://github.com/rust-lang/rust/blob/88d9e12ae178fab0fb5cc050a94da85685d449ea/src/tools/rust-analyzer/crates/project-model/src/workspace.rs)
仍探测 target/version/cfg/data-layout，可能加载 sysroot Cargo metadata。
因此官方服务的 JSON 模式不证明“零外部执行”。
[ide 库](https://github.com/rust-lang/rust/blob/88d9e12ae178fab0fb5cc050a94da85685d449ea/src/tools/rust-analyzer/crates/ide/src/lib.rs)
具有 AnalysisHost 数据注入、goto_definition、find_all_refs 接口；
[Cargo.toml](https://github.com/rust-lang/rust/blob/88d9e12ae178fab0fb5cc050a94da85685d449ea/src/tools/rust-analyzer/crates/ide/Cargo.toml)
在 wasm32/emscripten 下排除直接 toolchain 依赖。这只是库分离依据，未证明
完整传递依赖能编译、没有外部能力或保持所有语义。

## 候选执行域与可信边界

优先研究纯 core WebAssembly guest + Atlas 受控宿主，不使用 WASI，不装载
项目提供的 WASM/plugin/dylib，不通过宿主 callback 暴露 spawn、网络、文件系统、
环境、任意路径加载、动态链接或执行代理。源码只作为数据库数据，不编译执行。
宿主只加载发布 receipt 命中的语义模块；未知 import 在任何 start 函数执行前拒绝，
不得用自动补 import、默认值或 unknown-import trap stub 掩盖依赖缺口。
初版优先零 import；若必须有时钟/熵等 callback，先逐项固定纯数据能力和预算，
独立审查后才能加入。线程/shared memory、guest 自行生成执行代码和任意项目
native module 不默认开启。“guest JIT”不混同可信宿主的 WASM 编译后端；执行卡
必须明确 interpreter/JIT/AOT 模式、是否创建可执行页及系统权限/签名兼容性。
项目不得提供预编译 native/cwasm；不调用 unsafe deserialize 加载未验证输入。

WebAssembly 的外部能力依赖 imports；参考
[Wasmtime 安全边界](https://docs.wasmtime.dev/security.html)及
[Linker 未满足 import 的错误](https://docs.wasmtime.dev/api/wasmtime/struct.Linker.html)。
这些滚动文档不是运行时版本锁或平台资格。Wasmtime 只是候选，需选择准确稳定
版本/commit 并审核依赖、安全公告与五平台支持；不得拿 dev API 当已取得资产。
如采用原生直接链接核心，不继承 WASM 隔离结论，须重新证明等强边界，先交接决定。

宿主/控制器属于可信计算基；WASM 不沙箱宿主本身。必须审查宿主的完整路径、
格式解析、错误处理、动态库/环境装载及继承 handle/fd，证明项目数据无法指使其
联网/exec。模块与输入不可信时仍无能力 fallback。旧 scanner/准备链安全门保留，
不能拿 guest 限制覆盖未隔离的 scanner、版本探测、安装或准备过程。

## 输入、语义与新身份合同

控制器冻结逐文件验证的 source/config/manifest/lock/dependency/sysroot 字节，
沿用隔离计划的确定性复制—修改—恢复检查。只注入有界数据：文件 ID/文本、
source root、crate graph、edition、target/cfg/features、依赖边及明确允许的 env。
不导入 Cargo project-model 的执行型发现层，不执行项目 build/check/test/run、
build.rs 或 proc macro。无法静态证明的 cfg/依赖/生成代码必须按原合同 partial/
unavailable；不能把原冻结成功用例改成 partial 来隐藏语义退步。

crate graph 构造不是扩展名库存：需核对 workspace/path dependency、别名、
features/default features、target cfg、模块边界、sysroot、版本/lock 和原 T0 scope。
先冻结与旧正确性证据/真值的对应关系；不能把缺 std 或未加载依赖称为完整。
所有数据库文件/结果只接受当前项目/generation admission 集，拒绝跨项目 ID/URI。
公共 Unicode codepoint 坐标与核心 byte offsets 单独双向验证，保持旧 CLI/MCP 接口。

physical parent 合同重新按实际拓扑记录：控制器→受验证宿主；guest 是实例而非
虚构原生子进程，不伪造 RA→Cargo→rustc parent。旧原生桥专项不再是 T2 查询
实现路线，但其失败证据保留；不存在的 Cargo stdin 项只能在新角色确无 Cargo
调用的证据及独立合同审查后标 N/A，不据此豁免 preparation/scanner 或宿主输入。

## C0：先锁定依赖与实验卡，独立审查

读取固定上游库及传递依赖的 feature/ABI/线程/时钟/熵/proc macro 需求，锁定
来源、逐文件/hash/许可证及可信构建脚本清单。复用已验证官方 compiler/cache，
不运行 rustup shim 或改全局 toolchain；缺 wasm target/runtime 要给独立获取方案。
获取、构建、查询隔离分开；查询期间永不解析依赖或联网。

提交独立审查：威胁仍是项目/config 并发修改与不可信输入，不扩大为同账户任意
调试攻击。未解决 P1 不开实验或生产接线。实验卡必须确定本机平台、源码与
运行时版本、允许构建/命令、imports、输入真值、采样、清理及所缺授权。
整个首轮原型主动工作上限 8 小时，包含获取核验、实现、编译、观察器、正负例与
最多两次有新假设的修订；单次构建上限 30 分钟。达到上限保留证据，不换 runtime
或平台重置预算；必要清理单列且必须完成。执行卡确认前不开始获取/构建实验。

## C1：最小真实语义原型，不接线产品

只在自有临时 fixture/store；先编译实际上游语义核心，不以空 WASM 或自有 matcher
代替。从原冻结 crate/workspace 请求集完成跨文件定义、跨 crate 定义、引用、
同名消歧、Unicode、cfg/features，三次独立新鲜宿主进程。源码不改，结果有明确
声明/文件范围来源；缺失请求/样本/平台记未完成。

负例含未知 import/start、越界 guest memory、畸形/超大输入、伪造 crate/file ID、
generation 变化、wrapper/下载诱饵、热修改、取消、死循环、内存增长、宿主/控制器
死亡。先用同能力域正向控制证明观察器有效，再记录 deny/attempt/actual execution；
不能仅靠 marker 缺失或 import 名单宣称完整禁网。覆盖所有原网络/IPC与继承对象门，
guest 与宿主分别说明覆盖面。无权限/原生观察器记缺口，不跳门。

采用原 request deadline、60 秒冷启动验收/查询门与 10 秒 cleanup；WASM fuel/
epoch/linear memory limiter 不是 wall-time 或 RSS 的替代。T2 ≤1536 MiB 包含
宿主、编译/实例化、guest、输入副本与所有所属进程，不仅 guest linear memory。
限制先于不可信输入解析、module validation/编译/实例化与 start 生效：外层
deadline/RSS 观察和取消覆盖整个宿主，入口先检查输入/模块大小、memory/table
声明和最大值；store fuel/epoch、linear memory/table/stack 限制先于实例化安装。
合法 zero-import 的 start 死循环、超大初始分配与递归也必须被预装限制拒绝/中断，
不能在 start 返回之后才设置预算。无可用资源监督或完整阶段限制则 blocked。
超限/失败 closed，无 native analyzer fallback；退出后才释放所属临时文件。

## C2：五平台资格后回原计划

单机原型不等于 I0/第二阶段通过。Linux x64/ARM64、macOS ARM64、Windows x64/
ARM64 使用同一 verified guest 和各自准确宿主资产；原生支持、签名及实际资源
逐项证明，尤其不能因 runtime 有 aarch64 backend 推断 Windows ARM64 合格。
逐平台缺工具/runner/结果均 blocked，不能 skipped pass。

新角色 I0/I1 独立审查后再改 runtime/receipt/schema/provider 与生命周期；旧 Rust
配置及 rollback 显式迁移，不破坏 Python/TS，不自动启用旧官方服务。
完成 C2/I3、准备并发/失败回滚和第二阶段全出口，才进入原 installed-wheel/fresh
stdio、两个冻结外仓、真实 Codex 独立授权、最终独立验收及公开启用独立授权。
新核心改变正确性路径，受影响的大型留出门须重跑；旧 RA 结果仅作对照基线。

## C0 首批只读证据（不是最终依赖锁）

固定上游 workspace 的最低 compiler 为 1.95，许可证 MIT OR Apache-2.0；
salsa 0.27.0 启用 rayon/inventory，ide-db 直接依赖 rayon/crossbeam，并非天然
单线程 guest。RootDatabase 接受文件文本和 source roots，仍须审核整个查询
路径/传递依赖、初始化和 drop；不得凭顶层函数没有 process 调用就宣布无执行面。

候选 runtime 的官方 [v49.0.2 Release](https://github.com/bytecodealliance/wasmtime/releases/tag/v49.0.2)
元数据显示 2026-10-02 发布，非 draft/prerelease；tag 直接对应 commit
`3c8a3e79aa3188a1a12b3aebbaa097abccceac92`。有五个承诺平台的 CLI/C API 资产，
包括 aarch64-windows。这只证明有获取候选，不证明归档来源/逐文件身份、可运行
或平台安全与资源资格。
[固定源码](https://github.com/bytecodealliance/wasmtime/blob/3c8a3e79aa3188a1a12b3aebbaa097abccceac92/Cargo.toml)
最低 compiler 1.96.0，许可证 Apache-2.0 WITH LLVM-exception；版本数值与
已固定 compiler 1.98.0 相容，实际构建仍未知。runtime 默认 features 包含
threads、parallel compilation、cache、profiling、component model 等，不得直接
采用默认集；C0 还需冻结最小 features、backend、所有传递依赖与构建脚本。
截至本记录未获取 archive/crate、安装、编译、执行原型或查询最新 PR CI 状态。
