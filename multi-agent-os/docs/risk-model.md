# Risk Model & Path Rules (R0 – R3)

## Risk Levels & Minimum Merge Gates

| Risk | 类型 | 定义与范围 | 最低 Merge 门槛 |
| :--- | :--- | :--- | :--- |
| **R0** | 零风险 / 元数据 | 文档、代码注释、格式化、非运行时配置 | Basic validation |
| **R1** | 小规模运行时 | 非敏感、小规模变更（单点 bug 修复、小函数） | CI + self-test |
| **R2** | 实质性实现 | 功能模块开发、跨文件交互、重构 | CI + AntiGravity 独立 Review |
| **R3** | 敏感 / 高危资产 | 安全、认证、权限、架构迁移、依赖、CI/CD、破坏性操作、控制面文件 | Human Approval + 独立审查 + 强化测试 |

---

## R1 物理硬门槛

若任务声明为 R1，其物理变更必须满足：
1. `max_changed_lines: 100`
2. `max_changed_files: 5`
3. **严禁触碰任何受保护敏感路径**：
   - `auth/**`
   - `security/**`
   - `migrations/**`
   - `schema/**`
   - 依赖清单与 Lockfile (`package.json`, `package-lock.json`, `poetry.lock`, `Cargo.lock`, `requirements.txt` 等)
   - `.github/**`
   - `control/**`
   - `AGENT_PROTOCOL.md`
   - `PROJECT.md`
   - `AGENTS.md` 及其他 Agent Adapter 文件
   - 部署与容器配置 (`Dockerfile`, `docker-compose.yml`, `k8s/**`)

若代码实际触碰上述任何路径，任务必须自动提权至 **R2 或 R3**。

---

## 风险单调递增原则 (Monotonic Rule)

$$\text{Effective Risk} = \max(\text{Human Declared Risk}, \text{Automatically Computed Minimum Risk})$$

- Agent 可以提权：“经过分析，此处涉及鉴权状态隐蔽变更，建议从 R2 提升至 R3。”（**允许**）
- Agent 严禁降权：“代码改动虽然涉及 auth，但只有两行，所以我当成 R1 提交。”（**严厉禁止**）
