# Independent Review Protocol (AntiGravity)

## 核心准则
1. **审查目标是静态 SHA**：审查对象必须是明确的 `Base SHA` 与 `Target SHA`，严禁在 Codex 正在编辑的活动工作区或动态 HEAD 上审查。
2. **Review 产物不入被审分支**：审查意见写在 PR Comment、GitHub Review，或存放于仓外的 `~/.agent-reviews/<project>/<task>/<target_sha>.md`，绝不在被审分支中直接 `git commit`，避免自指循环。
3. **独立验证**：Reviewer 必须在隔离环境中真正运行 `PROJECT.md` 中定义的验证命令。

---

## 防锚定阅读顺序 (Anti-Anchoring Order)

为防止被 Builder 的解释报告带偏，AntiGravity 必须严格按以下顺序阅读：
1. **Task Requirement**（任务需求与范围）
2. **Base / Target SHA**（版本锚点）
3. **Raw Diff**（代码物理变更本身）
4. **Tests & CI Log**（自动化测试与门禁结果）
5. **Relevant Source Code**（受影响上下文）
6. **Builder Notes**（最后才看 Builder 的自述说明）

---

## 隔离审查物理操作：Git Worktree

为保证环境完全干净，推荐使用 `git worktree`：

```bash
# 1. 建立只读审查工作区
git worktree add -d /tmp/review-T-014 <target_sha>

# 2. 进入临时工作区并独立执行验证
cd /tmp/review-T-014
pytest
ruff check .

# 3. 产出 Review 记录后，清理工作区
git worktree remove --force /tmp/review-T-014
```

---

## Review Loop 规则（最多 2 轮）

- **Round 1**:
  - SHA A $\to$ AntiGravity 审查 $\to$ 发现 BLOCKING 问题。
  - Codex 修改后提交产生 SHA B。
- **Round 2**:
  - Reviewer 验证：
    1. 前序 BLOCKING 是否真正修复；
    2. 审查 `SHA A..SHA B` 的新增变动；
    3. 针对 `Base SHA..SHA B` 进行全局回归扫描。
- **2 轮上限熔断 (Escalation Gate)**:
  - 若 Round 2 仍存在结构性 BLOCKING，**立即终止自动化修复循环**，转交 Human 仲裁（重新划定范围、拆分任务或重构设计）。
