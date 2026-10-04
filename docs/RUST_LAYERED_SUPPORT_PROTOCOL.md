# Codebase Atlas Rust 分层支持协议

- 协议日期：2026-09-22
- 状态：`LAYERED_STAGE0A_NATIVE_QUALIFIED / STAGE0B_IN_PROGRESS / PRODUCT_ENABLEMENT_NOT_AUTHORIZED`
- 前序证据：`RUST_LANGUAGE_SUPPORT_PLAN.md` 的 SCIP-only Stage 0 保持
  `KEEP_EVALUATION_ONLY`
- 目标：像成熟的多语言代码导航产品一样，以明确降级和逐条 provenance 提供可用的
  Rust 代码地图，同时把“语法候选”与“编译器级语义事实”严格分开。

## 1. 为什么修订路线

第一次正式 Stage 0 没有证明 Rust 不可支持，只证明以下组合不可作为 Atlas Rust
唯一事实源：

```text
rust-analyzer scip
+ locked/offline full completeness
+ zero unexplained lint errors
+ explicit SCIP implementation relationships
```

在固定的 rust-analyzer 1.98.0 中，SCIP 生成器输出 definition/reference occurrences，
但 `SymbolInformation.relationships` 为空；最小仓库还出现 offline metadata 降级、重复
符号和大量 lint diagnostics。因此旧结果不得晋级为产品能力，也不得通过补依赖或放宽
门槛追认成功。

成熟工具并不要求所有语言一次性提供完全相同的编译器事实：

- GitHub 使用 tree-sitter 为 Rust 等多种语言自动提取定义和引用；
- Sourcegraph 把 search/syntactic navigation 与 precise SCIP navigation 组合，并在精确
  数据不可用时回退；
- Aider 用 tree-sitter definition/reference tags 构建文件图并排序，服务于代码上下文，
  不把名称边宣称为编译器调用图；
- OpenGrok 使用 Universal Ctags 提供广语言覆盖，再接受相应精度上限。

参考：

- https://docs.github.com/en/repositories/working-with-files/using-files/navigating-code-on-github
- https://sourcegraph.com/docs/code-navigation
- https://sourcegraph.com/docs/code-navigation/precise-code-navigation
- https://github.com/Aider-AI/aider/blob/main/aider/website/docs/repomap.md
- https://github.com/Aider-AI/aider/blob/main/aider/repomap.py
- https://oracle.github.io/opengrok/

## 2. 不可混淆的事实层

每条 Rust 结果必须携带 `fact_tier`、provider、版本、source range、generation 和
completeness。较低层不得冒充较高层。

### T0：源码与仓库事实

- exact Git root、commit/worktree identity；
- Git-aware 文件清单；
- Cargo manifests、lockfile、workspace/target/source path；
- 文件 hash、语言、更新时间和 ignore 状态。

这些是 Atlas 自身可验证事实，不依赖 Rust 语义 Provider。

### T1：语法代码地图

由固定版本 tree-sitter Rust grammar 和版本化 queries 产生：

- module、struct、enum、union、trait、type、function、method、macro 定义；
- `use`、`mod`、trait/impl 声明、测试 attribute 和调用形态的 source ranges；
- 基于相同 identifier 的候选引用；
- 文件级 candidate dependency graph 和重要性排序。

合同：

- 返回 `syntactic` 或 `candidate`，不返回 `exact_semantic`；
- 同名、宏展开、重导出、cfg 和类型推断不靠名称猜成唯一身份；
- 候选边可用于检索、代码地图和后续验证，不能直接成为 exact callers/impact。

### T2：按需语义确认

使用固定 rust-analyzer LSP 会话确认具体查询：

- definition；
- references；
- hover/type identity（仅用于消歧和 provenance）；
- workspace symbol（只作为候选入口）。

T1 先缩小文件和位置范围，T2 对用户实际请求做按需确认。T2 结果只有在 exact
repository/build context、请求位置和 LSP 响应范围都可复核时才标记
`exact_semantic`。

### T3：高级语义关系

- trait/type implementations；
- call hierarchy；
- related tests；
- impact。

每种关系单独晋级。优先评估 rust-analyzer LSP `textDocument/implementation` 和 call
hierarchy；没有明确响应或完整性边界时返回 unsupported/partial，不从 T1 名称边推导
exact 关系。

### T4：批量精确索引加速

SCIP 保留为可选 Provider：

- 可用于已验证的 definition/reference occurrences；
- 不再是 Rust 产品启用的前置唯一来源；
- 每个事实域独立通过 lint、identity、determinism、资源和 completeness 门后才能被
  admitted；
- 不存在的 relationship 不由 importer 猜测补齐。

## 3. 查询合并规则

统一查询按照以下顺序工作：

1. T0 确认 exact repository、scope 和 freshness；
2. T1 找到定义或引用候选及其 source ranges；
3. 如果调用方要求精确语义，T2/T3 对候选位置执行确认；
4. 可用且已晋级的 T4 事实可以直接命中或加速 T2；
5. 去重键必须包含 repository、package/crate、path、range、owner 和 provider identity；
6. 返回整体 completeness，同时保留每条结果的事实层和 provenance。

允许的结果状态：

- `complete_exact`：该查询域的 required scope 已由晋级语义 Provider 完整覆盖；
- `exact_hits_partial_scope`：命中精确，但已知 scope 不完整；
- `syntactic_candidates`：只有 T1 候选；
- `unsupported`：该层没有晋级 Provider；
- `unavailable`：工具、依赖、执行授权或 build context 不满足；
- `stale`：generation 与当前源码/配置不一致。

空结果只有在完整 scope 已验证时才能表示“没有”；否则必须返回 partial、unsupported
或 unavailable。

## 4. 安全和执行边界

- T1 只解析源码，不运行 Cargo、build script、proc macro、应用或测试；
- LSP 默认禁用 build scripts 和 proc macros；如某项语义必须执行项目代码，预览必须
  单列并取得显式授权；
- 查询期间不得联网或安装工具；
- dependency prepare 与 query/index 分离；
- 所有子进程由 Atlas 拥有、采样、超时、取消并清理；
- cache、grammar、query 和 tool 均版本化，产品仓库不写机器绝对路径或索引产物。

## 5. 新的评估阶段

### Layered Stage 0A：机制资格

使用不进入产品的独立 fixture：

- 固定 tree-sitter runtime、Rust grammar、queries 和 rust-analyzer；
- 覆盖定义、同名 method、trait impl、re-export、macro、cfg、unit/integration test；
- 证明 T1 在无 Cargo/无执行/无网络条件下确定性运行；
- 证明 LSP 能在 build script/proc macro 禁用时按需返回 definition/reference；
- 记录 T1 candidates 与 T2 exact responses 的差异，而不是把差异隐藏为错误。

退出：`LAYERED_MECHANISM_QUALIFIED` 或 `KEEP_LAYERED_EVALUATION_ONLY`。

### Layered Stage 0B：开发仓库可行性

机制资格后才冻结新的开发 corpus 和 source-first truth。不得把 SCIP-only Stage 0 的
正式失败重标为本协议的成功结果。

真值至少包括：

- T1：24 个 definition/import/impl/test/macro source-range facts；
- T1 negatives：12 个同名或错误 owner/path 候选隔离案例；
- T2：12 definitions 和 12 references；
- T3：6 implementations，只有 LSP capability 资格通过后才成为 required；
- 至少一个 build-script repo 和一个 proc-macro repo，用于证明默认不执行项目代码。

为满足旧失败纪律，新的正式 corpus、truth、runner、start marker 和 protocol hash 必须
重新冻结；旧 raw output 和 decision 永久保留。

### Layered Stage 1：产品合同

只有已经通过 Stage 0B 的事实层进入合同。允许首版只发布：

- Rust detection/Cargo scope；
- T1 代码地图和候选检索；
- T2 definition/reference；
- 对 T3/T4 明确 unsupported 或 evaluation-only。

广语言覆盖不再依赖“所有高级关系同时通过”。每个能力单独开关、单独 freshness、单独
错误码、单独回滚。

### Layered Stage 2 及以后

- 实现 Provider-neutral language registry；
- Python/TypeScript 行为保持不变；
- Rust T1 shard 与 T2 session 分离；
- 再做开发仓库、密封留出、跨平台、安装态和发布门；
- 产品启用、版本、合并和 Release 仍需另行授权。

## 6. 机制资格硬门

T1：

- required source constructs recall = 1.000；
- source range precision = 1.000；
- negative owner/path isolation = 1.000；
- 三次 normalized output hash 一致；
- 不启动 Cargo/rustc/rust-analyzer，不产生 repository-local cache；
- ordinary fixture process-tree peak <= 512 MiB。

T2：

- admitted definition/reference precision = 1.000；
- wrong identity hits = 0；
- build context 和 coverage 缺口显式返回；
- build scripts/proc macros 默认不执行；
- cancel/timeout 后 residual process = 0；
- ordinary repository process-tree peak <= 1,536 MiB。

T3 和 T4 不通过不得阻塞已经独立通过的 T1；T2 不通过时 T1 仍只能以
`syntactic_candidates` 形式存在，不能宣传精确导航。

## 7. 首个最小实验

在产品仓库之外建立版本化 evaluation 目录，顺序固定：

1. 冻结 tree-sitter runtime、Rust grammar、query files 和 checksums；
2. 写入 source-first fixture truth；
3. 验证 parser/query runner 三次 deterministic；
4. 用 lazy-child-aware sampler 测 T1；
5. 启动禁用 build scripts/proc macros 的 rust-analyzer LSP；
6. 对相同 source positions 比较 T1 candidate 与 T2 definition/reference；
7. 验证 cancellation、cleanup 和 repository cleanliness；
8. 输出 `QUALIFY`、`REFINE_WITHIN_PROTOCOL` 或 `KEEP_EVALUATION_ONLY`。

在上述结果前，不修改 Atlas language choices、配置 schema、CLI、MCP 或发布包。

## 8. 当前授权边界

已授权：

- 本协议和 evaluation-only manifests/runners；
- 产品仓库之外的固定工具与 fixture；
- Layered Stage 0A 机制资格实验。

未授权：

- 产品 Rust language choice；
- 修改现有 Python/TypeScript 查询行为；
- 提交机器路径、下载缓存、评估索引或 corpus；
- 执行第三方仓库 build script/proc macro/application/test；
- 合并、发布、打 tag 或创建 Release。

## 9. Stage 0A 核心探针结果

2026-09-22 的首轮独立 fixture 核心探针得到 `REFINE_WITHIN_PROTOCOL`。随后冻结新的
`qualification-v2` fixture、truth、runner、manifest 与 start marker，正式结果为
`LAYERED_MECHANISM_QUALIFIED`，仍不是产品启用结论：

- 固定并校验 `tree-sitter-language-pack` 1.20.0 与 `tree-sitter` 0.26.0 wheel；
- T1 冻结 35 条 definition/import/impl/test/macro source-range 真值，recall 与 precision
  均为 1.000；4 组同名 owner/path identity isolation 全部通过，0 parse errors；
- T1 三次 normalized output SHA-256 均为
  `764671a9b545fe298ed9af3ec7c38dc30ee6f1aae72e4fa0062397781568c5e5`；
- T1 完整进程树只有 runner 本身，无子进程和残留；采样峰值 25,408 KiB；
- rust-analyzer 1.98.0 在禁用 build scripts、proc macros 且 Cargo offline 时，冻结的
  3 个 definition 与 2 个 references 检查全部通过；两个同名 `run` 调用分别定位到正确
  文件，wrong-identity hits 为 0；
- 显式取消返回 `RequestCancelled`；强制超时后整组进程无残留；正常 T2 进程树峰值
  681,584 KiB；
- build-script 与 proc-macro 两类执行 sentinel 在所有正式运行后均未出现；冻结 runner
  与 truth 的 hash 在正式运行后仍匹配 manifest。

因此 T1 + 按需 T2 的机制已满足本协议 Stage 0A 硬门。结论不覆盖 T3/T4，不代表真实
开发仓库 coverage，也不得据此修改 Atlas 产品语言选择或宣传 Rust 已受支持；下一门是
使用全新冻结 corpus/truth 的 Layered Stage 0B。

## 10. Stage 0B 开发发现与 T1 Provider 修订

Stage 0B 在全新固定提交的 `bytes`、`thiserror`、`libc`、`regex` 语料上发现了小 fixture
未覆盖的两个问题：

- `tree-sitter-language-pack` 1.20.0 内置 Rust grammar 会把合法 Unicode char literal
  （如 `'K'`、`'ſ'`）误判为语法错误；
- Python 3.13.7 + Tree-sitter 0.26 Node/Query 遍历在大型仓库循环中出现 native memory
  corruption，不能作为稳定产品 Provider。

因此 v2 的 fixture 结论仍保留，但其 Python T1 Provider 被撤出候选。新的 v3 使用固定
Rust 原生 helper、`tree-sitter` 0.27.0 与官方 `tree-sitter-rust` 0.24.2；正式资格结果：

- 原 35 条 source-range truth 全部精确通过；
- 新增 3 条 Unicode regression truth 全部精确通过，0 parse errors；
- 三次 base 与 Unicode 输出各自 hash 完全一致；
- 无子进程、无残留；34 文件普通仓库资源探针峰值 9,632 KiB；
- Rust LSP T2 的 v2 资格不受 T1 Provider 替换影响。

新 native T1 Provider 结论为 `NATIVE_T1_PROVIDER_QUALIFIED`。Stage 0B 开发扫描中，
`bytes`、`thiserror`、`regex` 的 Rust 文件均为 0 parse-error；`libc` 只有 Jinja 风格模板
和故意 invalid-syntax 测试两个非编译 source-scope 文件报错。这证明 T0 不能只按 `.rs`
扩展名定义完整性，正式 Stage 0B 必须冻结 Cargo/module source scope，并把模板、生成输入
和负向 parser fixture 显式排除或标记 partial。产品代码仍未修改。
