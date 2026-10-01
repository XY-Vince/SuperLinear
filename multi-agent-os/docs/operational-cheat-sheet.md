# Operational Cheat Sheet — Multi-Agent OS v1.0

## Quick Delegation Matrix

| 我要做什么 | 默认交给谁 | 执行模式 / 产物路径 |
| :--- | :--- | :--- |
| **理清架构 / 系统设计 / 拆解任务** | **AntiGravity** | 输出方案至 `docs/adr/` 或 Plan 文档 |
| **真正编写代码 / 修改项目** | **Codex** | 任务分支，严格受限于 `touched_areas` |
| **第二视角代码审查 (R2 / R3)** | **AntiGravity** (新 Session) | 针对固定 Base/Target SHA，只读模式 |
| **上网搜索 / 查询文档 / 抓取外部信息** | **Muse** | 产物隔离于 `~/AgentIngress/muse/raw/` |
| **生成 Word / PPT / Excel / 报告交付物** | **WorkBuddy** | 仅写入 `deliverables/`，语义回流受控 |
| **风险定级 / 授权 Lease / 合并到 main** | **Human** | 控制权威，把控 Merge Gate |

---

## 核心工作流闭环

### 1. 复杂开发任务 (R2 / R3)
```text
Human (Task + Lease)
   │
   ▼
AntiGravity (Plan / Arch)
   │
   ▼
Codex (Task Branch Build) ──► Validation / CI
                                   │
   ┌───────────────────────────────┘
   ▼
AntiGravity (Clean Worktree Review on Target SHA)
   │
   ├──► PASS ───────────────► Human (Merge to main)
   └──► BLOCKING ──► Codex (Fix: SHA B) ──► Re-review (Max 2 cycles)
```

### 2. 轻量日常任务 (R1)
```text
Human (Task Scope) ──► Codex (Build) ──► Self-test + CI ──► Human (Merge)
```

### 3. 外部调研流 (Muse Pipeline)
```text
Public Web ──► Muse ──► External Quarantine (`~/AgentIngress/muse/raw/`)
                              │
                    Structured Extraction
                              │
                              ▼
               Evidence Promotion Task (R1/R2)
                              │
                              ▼
                     Canonical Repository
```

### 4. 办公交付物流 (WorkBuddy)
```text
Canonical Semantic Source ──► WorkBuddy ──► `deliverables/report.docx`
                                                  │
            Human Edits: Numbers / Facts / Logic ──┘
                   │
                   ▼ (Must back-port!)
      Canonical Semantic Source (Update)
```
