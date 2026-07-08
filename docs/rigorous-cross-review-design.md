# Cross-Review 严谨化设计文档

## 1. 背景

Cross-Review 当前是一个 Agent-native 的代码审查 skill 原型。它通过本地静态扫描生成项目图、变更文件、模块上下文、跨模块风险边和 `agent_review_pack.json`，再把实际语义判断交给 Codex、Claude Code 等宿主 Agent 完成。

当前版本已经具备基础可用性：

- `prepare` 模式不调用外部 LLM，不需要用户配置 API Key。
- 能按路径启发式切分物理模块。
- 能识别 Python import、简单事件契约、HTTP route、SQL table。
- 能生成 `agent_assignments`，引导宿主 Agent 按模块派发审查任务。
- 能生成 `semantic_module_splitter`，引导宿主 Agent 做语义模块判断。
- 能生成结构化 `handoff_artifact` 和 `memory_handoff`，表达“先审模块，再带着模块审查记忆审下游模块”的流程。

但它还不够严谨。当前系统验证的是“审查包能不能生成”，不是“它是否稳定发现真实跨模块破坏”。如果要作为开源 skill 让别人信任，需要从协议原型升级为可验证、可扩展、可评估的审查系统。

## 2. 目标

本设计的目标是把 Cross-Review 从 MVP 提升到严谨的开源级 Agent review skill。

核心目标：

1. 将模块切分从单纯路径启发式提升为“物理模块 + 语义模块候选 + 宿主 Agent 输出校验”的双层模型。
2. 将跨模块风险分析从模块级粗排提升到 contract / symbol / call-site 级证据链。
3. 将子代理链式审查从自然语言提示提升为结构化、可校验的 handoff 协议。
4. 建立回归样例和质量 benchmark，用真实跨模块 bug 测量 recall、false positive 和无效审查率。
5. 保持 Agent mode 不要求用户提供额外 API Key。
6. 保持 skill 可嵌入 Codex、Claude Code 等宿主 Agent，而不是变成绑定某个商业服务的 SaaS 工具。

## 3. 非目标

以下内容不属于本阶段目标：

- 不构建完整商业 PR review 平台。
- 不替代 Snyk、SonarQube 等安全和质量门禁工具。
- 不在默认 Agent mode 中直接调用 OpenAI、Anthropic、Gemini 等外部 API。
- 不承诺自动修复所有发现的问题。
- 不做跨仓库级大型架构治理。
- 不追求覆盖所有语言和框架，先以 Python 与 TypeScript/JavaScript 为重点。

## 4. 设计原则

### 4.1 本地确定性优先

`prepare` 阶段必须尽量可复现。它可以做静态扫描、启发式分析、索引构建和上下文打包，但不应依赖非确定性 LLM 输出。

### 4.2 宿主 Agent 负责语义判断

语义模块切分、架构意图判断、跨模块影响解释由宿主 Agent 完成。skill 的职责是提供明确输入、输出 schema、证据要求和校验规则。

### 4.3 证据先于结论

所有高风险结论必须绑定证据：

- changed file
- changed symbol
- provider contract
- downstream call-site
- affected target module
- line reference or snippet
- test coverage signal

没有证据的审查结果不能被当成真实 finding。

### 4.4 协议可校验

任何子代理输出都必须是结构化 artifact。系统应能检查字段是否存在、证据是否引用真实文件、cross-review 是否消费了 module-review 的 handoff。

### 4.5 失败时保守降级

当语义切分不确定、call-site 无法解析、依赖边证据不足时，系统应保守回退到物理模块 assignment，而不是生成看似精确但实际无证据的审查结论。

## 5. 当前架构

当前架构如下：

```text
changed files
    |
    v
ScoutScanner
    |
    v
ProjectGraph
    |
    v
ImpactScorer
    |
    v
ContextPackager
    |
    v
agent_review_pack.json
    |
    v
host Agent review
```

主要文件：

- `cross_review/scout.py`：扫描文件、切分模块、解析 import / route / event / SQL table。
- `cross_review/graph.py`：维护模块和依赖图。
- `cross_review/impact_scorer.py`：计算跨模块风险边。
- `cross_review/context_pack.py`：为模块审查和跨模块审查生成上下文。
- `cross_review/pipeline.py`：串联 prepare / review 流程，输出 agent pack。
- `cross_review/prompts/*.txt`：standalone 模式下使用的审查 prompt。
- `tests/test_pipeline.py`：当前主要测试入口。

## 6. 目标架构

目标架构引入四个明确层次：

```text
Layer 1: Static discovery
  - files
  - physical modules
  - imports
  - routes
  - events
  - db tables
  - changed symbols

Layer 2: Contract graph
  - provider contracts
  - consumer call-sites
  - symbol references
  - data/event/API boundaries

Layer 3: Agent orchestration pack
  - semantic_module_splitter
  - agent_assignments
  - handoff_artifact schema
  - memory_handoff requirements
  - evidence validation rules

Layer 4: Host Agent execution
  - semantic split decision
  - module review
  - downstream cross-review
  - arbiter merge
  - final report validation
```

## 7. 核心数据模型

### 7.1 PhysicalModule

物理模块来自本地扫描，代表文件系统层面的模块边界。

建议字段：

```json
{
  "name": "billing",
  "files": ["src/billing/client.py"],
  "criticality": "critical",
  "exports": ["charge_user"],
  "routes": [],
  "events": [],
  "db_tables": []
}
```

### 7.2 SemanticModuleCandidate

语义模块候选由本地启发式提供，供宿主 Agent 判断。

建议字段：

```json
{
  "semantic_module": "billing",
  "physical_modules": ["billing_api", "billing_core"],
  "reason": "Physical modules share the billing domain prefix.",
  "confidence": "candidate"
}
```

### 7.3 ContractSurface

ContractSurface 是严谨化的关键。它描述一个模块对外暴露或被其他模块依赖的契约。

建议字段：

```json
{
  "contract_id": "python:function:src/billing/client.py:charge_user",
  "module": "billing",
  "kind": "function",
  "name": "charge_user",
  "file": "src/billing/client.py",
  "line": 1,
  "signature": "charge_user(user_id: str, amount: int) -> ChargeResult",
  "return_shape": "ChargeResult",
  "evidence": "def charge_user(user_id: str, amount: int) -> ChargeResult:"
}
```

Contract kind 至少包括：

- `function`
- `class`
- `method`
- `route`
- `event`
- `db_table`
- `db_column`
- `config_key`
- `external_api_client`

### 7.4 ChangedContract

ChangedContract 描述本次变更中发生变化的契约。

建议字段：

```json
{
  "contract_id": "python:function:src/billing/client.py:charge_user",
  "change_type": "signature_changed",
  "before": "charge_user(user_id, amount)",
  "after": "charge_user(user_id, amount, currency)",
  "file": "src/billing/client.py",
  "line": 1,
  "risk_reason": "Downstream callers may still pass two arguments."
}
```

Change type 至少包括：

- `signature_changed`
- `return_shape_changed`
- `field_removed`
- `field_renamed`
- `route_changed`
- `event_payload_changed`
- `db_column_added_not_null`
- `db_column_removed`
- `behavioral_precondition_changed`

### 7.5 CallSite

CallSite 描述下游模块如何消费上游契约。

建议字段：

```json
{
  "callsite_id": "python:call:src/admin/panel.py:trigger_billing_override:4",
  "consumer_module": "admin",
  "provider_module": "billing",
  "contract_id": "python:function:src/billing/client.py:charge_user",
  "file": "src/admin/panel.py",
  "line": 4,
  "usage": "charge_user(user_id)",
  "evidence": "return charge_user(user_id)"
}
```

### 7.6 ImpactEdge

ImpactEdge 应从模块级边升级为带证据的 contract edge。

建议字段：

```json
{
  "from_module": "billing",
  "to_module": "admin",
  "edge_type": "static_import",
  "risk_score": 0.95,
  "force_triggered": true,
  "changed_contract_ids": ["python:function:src/billing/client.py:charge_user"],
  "callsite_ids": ["python:call:src/admin/panel.py:trigger_billing_override:4"],
  "reasons": [
    "billing is critical",
    "admin imports billing.charge_user",
    "changed signature may affect admin callsite"
  ]
}
```

### 7.7 HandoffArtifact

HandoffArtifact 是子代理链式心智传递的结构化载体。

建议字段：

```json
{
  "artifact_id": "module-review-memory:billing",
  "producer_agent_id": "module-billing-reviewer",
  "summary": "billing charge_user signature changed.",
  "changed_contracts": [],
  "public_api_changes": [],
  "data_schema_changes": [],
  "event_contract_changes": [],
  "route_changes": [],
  "internal_findings": [],
  "assumptions": [],
  "evidence_refs": [],
  "downstream_questions": []
}
```

### 7.8 CrossReviewFinding

最终 finding 必须能链接到上游变更和下游消费证据。

建议字段：

```json
{
  "severity": "high",
  "confidence": 0.86,
  "from_module": "billing",
  "to_module": "admin",
  "changed_contract_id": "python:function:src/billing/client.py:charge_user",
  "callsite_id": "python:call:src/admin/panel.py:trigger_billing_override:4",
  "file": "src/admin/panel.py",
  "line": 4,
  "evidence": "admin still calls charge_user with the old argument shape.",
  "suggested_fix": "Update admin callsite or add compatibility wrapper.",
  "validation_status": "evidence_backed"
}
```

## 8. 模块切分设计

### 8.1 当前问题

当前模块切分主要按路径：

- `src/billing/client.py` -> `billing`
- `apps/backend/src/billing/client.py` -> `billing`
- `packages/core/foo.py` -> `core`
- `main.py` -> `main`

这个规则简单、稳定，但不理解语义。例如：

- `billing_api` 与 `billing_core` 可能应合并为 `billing`
- `user_profile` 与 `account_settings` 可能同属 `identity`
- `payments` 与 `billing` 是否合并取决于业务边界

### 8.2 目标方案

保留物理模块切分，新增语义模块候选和宿主 Agent 决策。

本地 prepare 输出：

- physical modules
- prefix-based alias candidates
- import-density candidates
- changed-file focused candidates
- output schema
- assignment rewrite policy

宿主 Agent 输出：

- semantic modules
- module aliases
- assignment rewrite decisions
- rationale
- confidence

系统校验：

- semantic module 必须引用真实 physical module
- rewrite 后不得丢失 `module_context_index`
- rewrite 后不得丢失 `cross_review_context_index`
- rewrite 后不得丢失 changed files
- 不确定时必须回退到 physical assignments

### 8.3 Assignment rewrite policy

第一阶段只允许 merge，不允许 split。

原因：

- merge 可以复用现有 context indexes。
- split 需要重新构建 module context，容易引入无效索引。
- 对开源 skill 来说，保守合并比激进拆分更可靠。

规则：

```json
{
  "may_merge_assignments": true,
  "may_split_assignments": false,
  "must_preserve_context_indexes": true,
  "must_preserve_primary_files": true,
  "must_preserve_cross_review_targets": true,
  "fallback": "If uncertain, use physical-module assignments unchanged."
}
```

## 9. 依赖与契约分析设计

### 9.1 Python

第一阶段应支持：

- import dependency
- `from x import y` symbol mapping
- top-level function/class export
- class method export
- function signature extraction
- simple call-site extraction
- decorator route extraction
- event publish/listen extraction

重要改进：

1. 建立 `symbol -> defining module/file/line` 索引。
2. 建立 `import alias -> provider symbol` 索引。
3. 建立 `callsite -> provider contract` 索引。
4. diff 阶段识别 changed contract，而不仅是 changed file。

### 9.2 TypeScript / JavaScript

第二阶段支持：

- ES import / export
- CommonJS require
- function / class / interface export
- Express / Next.js route
- fetch / axios API client
- event emitter pattern

建议通过 tree-sitter 或轻量解析器实现，不要用纯正则承担全部职责。

### 9.3 SQL 与 ORM

第一阶段已有 SQL table 识别，但还不够。

应新增：

- migration operation extraction
- column add / drop / rename
- NOT NULL without default 检测
- table reader/writer 检测
- ORM model field mapping

SQL 风险应强制触发更多下游审查，因为 DB schema 是典型跨模块共享契约。

## 10. 风险评分设计

### 10.1 当前问题

当前 `risk_score` 主要基于模块级信号：

- 是否有静态依赖
- 是否 critical module
- 是否 DB migration
- 是否有 co-change
- 是否缺测试

这适合排序，但不够精确。

### 10.2 新评分模型

建议评分：

```text
risk_score =
  0.30 * contract_change_score +
  0.25 * callsite_match_score +
  0.15 * criticality_score +
  0.10 * dependency_strength_score +
  0.10 * test_gap_score +
  0.05 * cochange_score +
  0.05 * blast_radius_score
```

字段定义：

- `contract_change_score`：是否存在签名、返回值、route、event、schema 等契约变更。
- `callsite_match_score`：下游是否有明确调用点或消费点。
- `criticality_score`：auth、billing、payment、db 等高危模块。
- `dependency_strength_score`：静态 import、runtime event、DB shared 等依赖强度。
- `test_gap_score`：是否缺少跨模块测试。
- `cochange_score`：历史共同修改概率。
- `blast_radius_score`：受影响模块数量和路径长度。

### 10.3 Force trigger

以下情况应强制进入 Top-K，即使总分不高：

- auth/security 边界
- billing/payment 边界
- DB migration
- public API route contract
- event payload contract
- deletion or rename of exported symbol
- NOT NULL column without default

## 11. Agent 编排设计

### 11.1 Assignment 生命周期

每个 assignment 的执行顺序：

```text
1. Read module_context
2. Review primary_module
3. Produce handoff_artifact
4. For each cross_review_target:
   4.1 Read cross_review_context
   4.2 Read handoff_artifact
   4.3 Verify downstream call-sites
   4.4 Produce evidence-backed findings
5. Return assignment result to arbiter
```

### 11.2 子代理角色

每个模块 reviewer 应拥有一个稳定角色：

```text
module-<sanitized-module-name>-reviewer
```

示例：

- `billing` -> `module-billing-reviewer`
- `billing_api` -> `module-billing-api-reviewer`
- `Billing_Module!` -> `module-billing-module-reviewer`

### 11.3 Arbiter

Arbiter 负责合并所有子代理结果。

职责：

- 去重
- 过滤无证据 finding
- 校验 severity
- 标记 confidence
- 汇总风险路径
- 输出最终报告

Arbiter 不应新增没有证据的 finding。它只能提升、降级、合并或丢弃子代理发现。

## 12. 输出校验设计

### 12.1 Module review 校验

Module review 输出必须满足：

- 有 `handoff_artifact`
- `changed_contracts` 可以为空，但必须存在字段
- 每个 contract change 必须有 file reference
- 每个 internal finding 必须有 evidence
- 不得把 mock/example finding 当成真实结果

### 12.2 Cross review 校验

Cross review 输出必须满足：

- 引用一个 `source_artifact_id`
- 引用至少一个 downstream file 或明确说明未找到 downstream usage
- 如果报告 break，必须同时引用 changed contract 和 call-site
- 如果没有 finding，必须说明 residual risk

### 12.3 Final report 校验

Final report 输出必须满足：

- severity 在允许枚举内
- confidence 在 0 到 1 之间
- file 路径真实存在
- line 为正整数
- evidence 非空
- high/blocking finding 必须有 changed contract + downstream evidence

## 13. 测试策略

### 13.1 单元测试

覆盖：

- module name inference
- agent id sanitizer
- import parser
- route parser
- event parser
- SQL parser
- semantic alias candidate generation
- assignment rewrite invariant
- handoff artifact schema
- output validator

### 13.2 Fixture 回归测试

新增目录：

```text
examples/regression_cases/
  python_signature_break/
  python_return_shape_break/
  fastapi_route_param_break/
  event_payload_rename/
  sql_not_null_without_default/
  ts_export_rename/
  express_route_response_break/
```

每个 case 包含：

- before/after 或 git diff
- changed files
- expected impact edges
- expected changed contracts
- expected affected call-sites
- expected findings

### 13.3 Benchmark

至少维护 20 个样例，记录：

- recall
- precision
- false positive count
- false negative count
- invalid finding count
- no-evidence finding count
- average prepare time
- pack size

建议初始门槛：

```text
contract edge recall >= 0.80
invalid finding rate <= 0.10
prepare time for fixture repo <= 5s
all generated pack schemas valid
```

## 14. CLI 设计

### 14.1 prepare

当前命令保留：

```powershell
python -m cross_review.cli prepare --root <repo-root> --worktree
```

新增可选参数：

```powershell
--languages python,typescript
--max-edges 5
--emit-contracts
--strict-pack-validation
```

### 14.2 validate-pack

新增命令：

```powershell
python -m cross_review.cli validate-pack --pack .cross-review/agent_review_pack.json
```

职责：

- 校验 pack schema
- 校验 context indexes
- 校验 file references
- 校验 assignment handoff schema

### 14.3 validate-report

新增命令：

```powershell
python -m cross_review.cli validate-report --pack .cross-review/agent_review_pack.json --report .cross-review/final_report.json
```

职责：

- 校验宿主 Agent 输出
- 过滤无证据 finding
- 输出 invalid reasons

### 14.4 benchmark

新增命令：

```powershell
python -m cross_review.cli benchmark --cases examples/regression_cases
```

职责：

- 跑所有 fixture
- 比较 expected vs actual
- 输出质量指标

## 15. 文件结构建议

建议逐步拆分当前大文件职责：

```text
cross_review/
  scanner/
    python_scanner.py
    typescript_scanner.py
    sql_scanner.py
  contracts/
    models.py
    extractor.py
    diff.py
    callsites.py
  orchestration/
    assignments.py
    semantic_splitter.py
    handoff.py
  validation/
    pack_validator.py
    report_validator.py
  benchmark/
    runner.py
    metrics.py
```

迁移原则：

- 不做一次性大重构。
- 每新增一个严谨能力，就从 `pipeline.py` 或 `scout.py` 中提取对应 helper。
- 每次提取必须有测试保护。

## 16. Prompt 设计

### 16.1 Semantic splitter prompt

宿主 Agent 应收到明确任务：

```text
Inspect physical_modules, dependencies, changed_files, and suggested_alias_schema.
Produce semantic_modules and assignment_rewrite_decisions.
Do not invent modules not backed by physical modules.
If uncertain, keep physical assignments unchanged.
```

输出必须符合 schema。

### 16.2 Module review prompt

模块审查 prompt 应强制输出：

- changed contracts
- public API changes
- schema changes
- route changes
- event changes
- assumptions
- downstream questions
- evidence refs

### 16.3 Cross review prompt

跨模块审查 prompt 应强制消费：

- source handoff artifact
- changed contracts
- target module context
- downstream call-sites
- integration test signal

高风险 finding 必须说明：

```text
changed provider contract -> downstream consumer evidence -> failure mode -> suggested fix
```

### 16.4 Arbiter prompt

Arbiter prompt 应明确：

- 不新增无证据问题
- 合并重复 finding
- 降级 speculative finding
- 输出 residual risks
- 标记 invalid findings

## 17. 开源定位

建议定位：

```text
Cross-Review is an open, Agent-native code review skill for finding cross-module contract breaks.
It prepares deterministic dependency and contract context locally, then lets host agents such as Codex or Claude Code perform structured multi-agent review without requiring separate model API keys.
```

中文定位：

```text
Cross-Review 是一个开源、Agent-native 的跨模块代码审查 skill。它在本地确定性生成依赖图、契约图和子代理任务包，再交给 Codex / Claude Code 等宿主 Agent 做结构化多代理审查，不要求用户额外配置模型 API Key。
```

与热门工具差异：

- 相比 CodeRabbit / Greptile：更轻量、更透明、更适合嵌入 Agent 工作流。
- 相比 Claude Code Code Review：更聚焦模块依赖与 downstream impact，而不是通用多视角 PR review。
- 相比 Copilot Review：更强调结构化上下文、handoff 和证据链。
- 相比 Snyk / Sonar：不是安全/规则扫描器，而是跨模块契约影响审查器。

## 18. 实施路线图

### Phase 1: 协议严谨化

目标：

- 完成 pack schema 定义。
- 完成 pack validator。
- 完成 report validator。
- 完成 handoff artifact 校验。

验收：

- 无效 index 不允许进入 pack。
- 缺少 evidence 的 finding 被 validator 标为 invalid。
- 所有当前 tests 通过。

### Phase 2: Contract graph

目标：

- 提取 Python function/class/method signatures。
- 识别 changed contracts。
- 建立 import alias 和 call-site 映射。
- ImpactEdge 绑定 changed contract 和 callsite。

验收：

- Python signature break fixture 能稳定命中。
- downstream call-site evidence 出现在 pack 中。

### Phase 3: Regression benchmark

目标：

- 建立至少 20 个 regression cases。
- 新增 benchmark CLI。
- 输出质量指标。

验收：

- benchmark 可在 CI 中运行。
- contract edge recall 达到初始门槛。

### Phase 4: TypeScript / JavaScript 支持

目标：

- 支持 ES import/export。
- 支持 Express/Next route。
- 支持 fetch/axios API client。

验收：

- TS export rename 和 Express route response break fixture 能稳定命中。

### Phase 5: 开源打磨

目标：

- README 清晰说明能力边界。
- 添加 examples。
- 添加 CONTRIBUTING。
- 添加 benchmark badge 或质量说明。
- 明确隐私和 API Key 策略。

验收：

- 新用户能在 5 分钟内运行 toy example。
- 新用户能理解 Agent mode 与 standalone mode 的差异。
- 不夸大能力，不把启发式说成严格证明。

## 19. 关键风险

### 19.1 LLM 输出不可控

风险：宿主 Agent 不按 schema 输出，或输出空泛结论。

缓解：

- 提供 strict output schema。
- 提供 validate-report。
- 把无证据 finding 标为 invalid。

### 19.2 静态分析误判

风险：动态语言、反射、运行时 import 导致依赖漏报。

缓解：

- 标记 confidence。
- 输出 residual risk。
- 支持用户配置 module aliases 和 custom dependency hints。

### 19.3 上下文包过大

风险：大项目 pack 太大，宿主 Agent 难以处理。

缓解：

- Top-K 风险边。
- contract-level slicing。
- context budget。
- 只包含相关 call-sites 和 contract snippets。

### 19.4 多语言扩展失控

风险：过早支持太多语言，质量下降。

缓解：

- 先 Python，再 TypeScript。
- 每种语言必须有 fixture 和 benchmark。

## 20. 验收标准

开源前最低标准：

1. `python -m pytest tests/` 全部通过。
2. skill validator 通过。
3. `prepare` 在 toy examples 上稳定生成 pack。
4. `validate-pack` 能发现缺失 index、缺失 handoff、缺失文件引用。
5. 至少 10 个 regression cases。
6. 至少覆盖 Python signature、route、event、SQL migration 四类跨模块破坏。
7. README 明确说明：
   - Agent mode 不需要 API Key。
   - Standalone mode 才可能需要 API Key。
   - 模块切分不是完美语义分析。
   - finding 必须由宿主 Agent 结合证据判断。
8. 不把 mock finding 当真实审查结果。

## 21. 结论

Cross-Review 的方向是有差异化的。市面上已有多 Agent review、repo graph review、PR automation 和安全扫描工具，但少见一个开源 skill 明确采用：

```text
本地确定性 prepare
-> 模块/契约/风险边打包
-> 宿主 Agent 语义模块判断
-> 按模块派发子代理
-> 主模块 handoff
-> downstream cross-review
-> arbiter 合并
```

要让它真正严谨，下一阶段重点不应继续堆自然语言 prompt，而应补三类硬能力：

1. contract / call-site 级静态证据。
2. handoff 和 final report 校验器。
3. regression benchmark。

完成这些后，它才适合作为可信的开源 cross-module review skill 发布。
