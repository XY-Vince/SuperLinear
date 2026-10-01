# Multi-Agent OS v1.0.1 Final — Distribution Kit (SuperLinear Hardened)

一套面向 Human + 4-Agent（AntiGravity, Codex, Muse, WorkBuddy）的高可靠人机协作操作系统架构与工具包（AG-first, Codex-gated）。

---

## 核心设计与三硬原则

1. **Canonical truth lives in protected state, not in agents.**（规范真理存在于受保护的主分支状态中，而非存在于 Agent 的记忆中。）
2. **Agents may work freely; only validated state may enter canonical main.**（Agent 自由开发，仅经验证状态允许合入规范主分支。）
3. **Human controls consequential gates, not every keystroke.**（人类掌控关键风险与合流门禁，不干预每次击键。）

---

## 目录结构概览

```text
multi-agent-os-v1.0/
├── README.md                      # 本文档：架构总览与快速导航
├── SPEC_V1_FINAL.md               # 体系终稿规范全文（含 31 节完整决议）
├── MIGRATION_SETUP_GUIDE.md       # 新电脑迁移与初始化操作手册
│
├── core/                          # 规范基准核心资产
│   ├── AGENT_PROTOCOL.md          # Multi-Agent 规范主协议 v1.0
│   └── PROJECT.md                 # 项目规格说明书标准模板
│
├── adapters/                      # 角色适配器规范 (Control Plane Assets)
│   ├── antigravity/adapter.md     # AntiGravity 适配器协议 (Primary Builder / Architect & Reviewer)
│   ├── codex/AGENTS.md            # Codex 适配器协议 (Gatekeeper / Reviewer & Lease Supervisor)
│   ├── muse/adapter.md            # Muse 适配器协议 (External Web / Research)
│   ├── workbuddy/adapter.md       # WorkBuddy 适配器协议 (Office Artifacts)
│   └── global/GLOBAL_AGENTS.md    # 全局 NCS Protocol v1.2 与通信风格
│
├── control/tasks/                 # 任务授权与 Writer Lease 治理
│   ├── T-000-template.yaml        # 任务授权元数据模板 (Lease)
│   └── README.md                  # Lease 与门禁机制详解
│
├── templates/                     # 标准记录模板
│   ├── review-record.md           # 独立代码审查记录模板 (AntiGravity)
│   ├── evidence-promotion.md      # 外部证据晋升记录模板 (Muse)
│   └── adr-template.md            # 架构决策记录模板 (ADR)
│
├── docs/                          # 操作 SOP 与技术规范
│   ├── operational-cheat-sheet.md # 职责分配速查表与业务流向图
│   ├── risk-model.md              # R0–R3 风险模型与硬物理界限
│   ├── review-protocol.md         # 固定 SHA 独立审查规范与防锚定流程
│   ├── sop-day0-new-project.md    # 新项目 Day-0 极简启动规范 (Lite+)
│   ├── sop-existing-project.md    # 老项目现场保护与基线迁移 SOP
│   └── github-ruleset-guide.md    # GitHub 分支保护与 Ruleset 推荐配置
│
└── tools/                         # 实用验证与脚手架工具
    ├── verify_lease.py            # Lease 有效期、路径子集、风险门禁自动化校验脚本
    ├── init_project.py            # 一键初始化 Lite+ 项目脚手架工具
    └── worktree_review.sh         # AntiGravity 隔离工作树创建与销毁脚本
```

---

## 快速使用

- **阅读完整架构**：参见 [`SPEC_V1_FINAL.md`](SPEC_V1_FINAL.md)。
- **在新电脑上配置**：参见 [`MIGRATION_SETUP_GUIDE.md`](MIGRATION_SETUP_GUIDE.md)。
- **新建项目 (Day-0 Bootstrap)**：
  ```bash
  python3 tools/init_project.py new <new-repo-path>
  ```
- **存量项目接入 (Lite+ Adopt)**：
  ```bash
  python3 tools/init_project.py adopt <existing-repo-path>
  ```
- **验证 Lease 与物理门禁**：
  ```bash
  python3 tools/verify_lease.py control/tasks/T-xxx.yaml HEAD
  ```
- **开启隔离审查工作树**：
  ```bash
  tools/worktree_review.sh start <target_sha> /tmp/agent-review-<task>
  ```
