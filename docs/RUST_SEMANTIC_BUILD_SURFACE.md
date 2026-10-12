# C0 构建执行面：保守库存与首个适配点

状态：只读源码证据与未应用适配草案；未获构建执行资格。不运行编译器、宏、
build.rs 或项目代码，不把源码审查当作安全/语义验收。

固定 Rust commit `88d9e12ae178fab0fb5cc050a94da85685d449ea` 的 RA lock
SHA-256 `fad2ba1bf5035457b33fa83cac5a8edad65e8caa7d0e22ca1f0ba3cfdf7b6404`，
以 ide 为根按全部 lock 依赖匹配，得到 211 个包（30 local、181 registry）。
逐包读取准确 Cargo.toml 的 `package.build`，或未禁用的默认根 build.rs，发现
27 个 registry 构建入口。不得只搜索名为根 build.rs 的文件：rustversion 使用
`build/build.rs`；其他目录的同名 Rust 模块并不因此成为执行入口。

固定版本入口清单：anyhow 1.0.102、borsh 1.6.1、camino 1.2.2、crossbeam-utils
0.8.21、fst 0.4.7、icu_normalizer_data/icu_properties_data 2.2.0、libc 0.2.186、
memoffset 0.9.1、object 0.37.3、parking_lot_core 0.9.12、paste 1.0.15、
portable-atomic 1.13.1、proc-macro2 1.0.106、pulldown-cmark 0.9.6、quote 1.0.45、
rayon-core 1.13.0、rustc_apfloat 0.2.3+llvm-462a31f5a5ab、rustversion 1.0.22、
serde/serde_core 1.0.228、serde_json 1.0.150、thiserror 2.0.18、tikv-jemalloc-sys
0.5.4+5.3.0-patched、typeid 1.0.3、valuable 0.1.1、zmij 1.0.21。

这是包含 dev/optional/其他 target 的上界，不是 27 个全部会实际执行的结论。
真正唯一 guest 根和 native build/proc-macro 图仍需解析。不得删除库存项来伪造
图已闭合，也不得把所有惰性已核验文件都当成任意执行已获批准。
独立 reviewer 复算相同 211/181/27 数量，无源码缺失，并确认 15 个 registry
proc-macro 与 2 个 local proc-macro（macros/query-group-macro）；同样不是实际图。

## 已具体确认的额外执行要求

- rustversion 的构建入口读取 RUSTC/RUSTC_WRAPPER，调用 `--version`，在 OUT_DIR
  写 version.expr。环境必须保持 wrapper 缺席及准确工具身份；生成文件只在自有目录。
- thiserror 2.0.18 构建入口不只查询版本，还编译固定 build/probe.rs，读取两个
  wrapper 和 CARGO_ENCODED_RUSTFLAGS，写/清理 OUT_DIR/probe。不能被“仅 metadata”
  执行卡隐式批准；build/probe.rs 和该命令精确参数需单独纳入可信构建清单。
- fst 0.4.7 的入口生成 tag/CRC 表到 OUT_DIR；读取完整入口未见外部命令，
  这不代替其依赖、生成文件或真实运行证据。
- tikv-jemalloc-sys 入口具有 shell/configure、C compiler、make、失败 tail 和可选
  tests 执行面。profile 把 jemalloc-ctl 标为 optional、由 jemalloc feature 启用。
  不能推断最终 guest 必然需要这套工具，也不能未解析图就视其已排除。

先前关键 12 包审查不覆盖本次全部 27 入口及所有 native proc-macro helper。
完整执行审查、实际图、source/output/env/argv 身份及资源监督仍未通过。

## 纯 WASM 诊断时钟适配草案

`prototypes/rust-semantic-guest/ide-db-clock.patch` 针对准确 upstream
apply_change.rs，原文件 SHA-256
`40d306a438d6c06c692c0ad3075d92a67f7a7798b884b84c8180fdf6f4d3779c`。
只为 target_arch=wasm32 且 target_os=unknown 排除诊断 Instant，返回 Duration::ZERO；
native 路径不变。trigger_cancellation → tracing → change.apply 顺序保留。
外层 wall deadline/RSS 和 guest 限额必须仍独立安装，不以诊断零值冒充资源证明。
补丁未应用/编译，无 WASM、线程、其他时钟或真实查询成功证据；须独立复核，
只可修改未来私有工作副本，并绑定补丁及修改后文件的独立身份。
独立 reviewer 已确认两个 hunk 准确对应原文件，无 P1/P2；只允许提交草案，
未批准应用/执行。主 agent 的 `git apply --check` 通过，不写原源码。
