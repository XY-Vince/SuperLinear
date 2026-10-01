# Operational Cheat Sheet — Multi-Agent OS v1.0.1 (AG-first, Codex-gated)

## Quick Delegation Matrix (敏捷派发矩阵)

| 任务类型 / 风险等级 | 默认执行者 (Builder) | 独立门禁 / 审查者 (Gatekeeper) | 验证模式与产物规范 |
| :--- | :--- | :--- | :--- |
| **R0/R1 研究、规划、实现、测试、文档** | **AntiGravity (AG)** | 自动化 CI / 单测；必要时开启新 AG Session | 隔离任务分支，遵守 `touched_areas` |
| **R2 功能模块开发与跨文件重构** | **AntiGravity (AG)** | **Codex** (简短固定 SHA 验收) + 新 AG Reviewer Session | `worktree_review.sh` 隔离验证，输出 Review Record |
| **R3 核心安全、架构、依赖与控制面** | **AntiGravity (AG)** (起草与实现) | **Codex** (完整固定 SHA 审查) + **Human** 批准 | 严格四眼原则，不可信代码进容器沙箱 |
| **Word / PPT / Excel / PDF 交付物** | **WorkBuddy (WB)** | **AG** (提供语义源) + **Codex/Human** (高危事实抽检) | 仅写 `deliverables/`，数字事实变更强制回流 |
| **任务 Lease 监督、风险定级、合流建议** | **Codex** | **Human** (最终审批与 Merge) | `verify_lease.py` 自动化门禁核查 |

> **核心原则**：
> 1. **AG 吞吐优先**：充分利用 AntiGravity 的推理吞吐优势承担主要的编写与重构工作。
> 2. **Codex 严格把门**：Codex 担任控制面与最终合流门禁把关人，保持高纪律性审查。
> 3. **会话隔离铁律**：同一个 AntiGravity Session **严禁自我审查**。审查必须由独立 Session 或 Codex 执行。

---

## 核心工作流闭环

### 1. 标准开发流 (R2 / R3)
```text
Human / Codex (Task Authorization + Lease T-xxx)
   │
   ▼
AntiGravity (Primary Builder: Branch Implementation + Automated Tests)
   │
   ▼ Validation PASS (Target SHA A)
Codex (Fixed-SHA Gatekeeper Review via Clean Worktree)
   │
   ├──► PASS ───────────────────────────────► Human (Merge to main)
   └──► BLOCKING ──► AG (Fix: Target SHA B) ──► Re-review (Max 2 cycles)
```

### 2. 轻量日常流 (R0 / R1)
```text
Human (Task Scope) ──► AG Builder (Implement) ──► Automated Tests ──► Codex/Human (Fast Gate)
```

### 3. 外部调研流 (Muse Pipeline)
```text
Public Web ──► Muse ──► External Quarantine (`~/AgentIngress/muse/raw/`)
                               │
                     Structured Extraction (Claim, URL, Date, Excerpt)
                               │
                               ▼
               Evidence Promotion Task (AG Verified)
                               │
                               ▼
                      Canonical Repository
```

### 4. 办公交付物流 (WorkBuddy)
```text
Canonical Semantic Source (AG) ──► WorkBuddy ──► `deliverables/report.docx`
                                                        │
                      Human / Codex Edits: Facts / Logic ┘
                             │
                             ▼ (Must back-port!)
              Canonical Semantic Source (Update)
```
