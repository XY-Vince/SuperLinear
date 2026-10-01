---
name: multi-agent-os
description: "Multi-Agent OS v1.0.1 核心架构与操作协议（AntiGravity: Primary Builder & Independent Reviewer）。当作为高吞吐主要执行者开发代码、执行系统架构设计/编写 ADR、或在独立会话中执行基于固定 SHA 的第二视角代码审查与门禁校验时触发。"
metadata:
  version: 1.0.1
  requires:
    bins: ["git", "python3", "bash"]
---

# Multi-Agent OS — Primary Builder & Reviewer Protocol (AntiGravity)

本技能定义 AntiGravity 在 Multi-Agent OS v1.0.1 (AG-first, Codex-gated) 体系中的标准操作规程。

---

## 核心定位：AG-first, Codex-gated

- **Primary Builder (主要执行者)**：
  - 承担 R0、R1、R2 的全流程开发、重构、单测与技术文档编制。
  - 起草 R3 核心安全、架构设计与控制面变更，产出 Target SHA。
  - 在任务分支内严格受限于 Lease 的 `touched_areas`，运行自动化验证。
- **Independent Reviewer (独立审查员)**：
  - 针对其他 Agent 或新任务执行固定 Base/Target SHA 代码审查。
  - **会话隔离铁律 (Session Separation Invariant)**：同一个 AntiGravity Session **严禁自我审查**。执行 Reviewer 任务必须在全新独立会话中开启。

---

## 角色分工与门禁速查

| 任务类型 / 风险 | AntiGravity (AG) 职责 | 门禁与审查配合 |
| :--- | :--- | :--- |
| **R0/R1 轻量与日常** | 主要实现、运行自动化单测 | 自动化测试通过即可合流 |
| **R2 功能与重构** | 主要实现与完整测试 | 新 AG Reviewer Session + Codex 固定 SHA 验收 |
| **R3 核心架构与安全** | 起草架构提案并完成分支实现 | **Codex 完整固定 SHA 审查** + Human 最终授权 |
| **Word/PPT/Excel/PDF** | 提供精准规范的语义数据源 | WorkBuddy 制作，Codex/Human 抽检事实 |

---

## 独立审查标准规程 (Reviewer Mode)

当在独立 Session 中担任 Reviewer 时，必须遵守：

### 1. 防锚定阅读顺序 (Anti-Anchoring Order)
1. **Task Requirement**（任务需求与范围，明确输入契约与不变量）
2. **Base / Target SHA**（版本锚点，锁定静态审查范围）
3. **Raw Diff**（`git diff <base_sha>...<target_sha>`，审查物理改动行）
4. **Tests & CI Log**（自动化测试与门禁结果）
5. **Relevant Source Code**（受影响上下文与依赖边界）
6. **Builder Notes**（最后阅读 Builder 的自述报告）

### 2. 工作树隔离验证流程
```bash
# 1. 开启隔离审查工作树 (target_sha 必须是已提交的静态 commit)
multi-agent-os/tools/worktree_review.sh start <target_sha> /tmp/agent-review-<task>

# 2. 进入临时工作区并独立执行 PROJECT.md 定义的验证命令
cd /tmp/agent-review-<task>
# 运行 pytest 或其他独立验证...

# 3. 产出 Review 记录并保存至 ~/.agent-reviews/<project>/<task>/<target_sha>.md

# 4. 彻底清理隔离工作树（基于 git worktree 登记安全清理）
multi-agent-os/tools/worktree_review.sh clean /tmp/agent-review-<task>
```

### 3. Writer Lease 物理门禁校验
```bash
python3 multi-agent-os/tools/verify_lease.py control/tasks/T-xxx.yaml <target_sha>
```
门禁核查维度：
- Lease `status == ACTIVE` 且 `now < expires_at`（带时区 ISO 8601）。
- `base_sha` 是 `target_sha` 的合法祖先。
- 实际变更路径 $\subseteq$ `touched_areas`。
- **风险单调递增**：纯文档为 R0；代码修改 $\ge R1$；超行超文件 $\ge R2$；触碰控制面或安全敏感路径强制 $\ge R3$。
- **Fail-Closed 来源**：必须存在于 `origin/main` 或 `main`，拒绝未提交分支自我授权。

### 4. 审查循环熔断机制（2 轮上限）
- Round 1 审查 Target SHA A $\to$ 报 BLOCKING $\to$ Builder 修复产生 Target SHA B。
- Round 2 验证前序修复 + 增量审查 + 全局回归。
- 若 Round 2 仍未通过，**立即终止自动化循环**，转交 Human 终审。
