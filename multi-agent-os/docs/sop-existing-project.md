# Existing Project Migration SOP: 老项目迁移规范

老项目迁移必须遵守“保护现场 $\to$ 还原真实 $\to$ 注入 Lite+”的节奏：

| Phase | 阶段名称 | 核心动作 |
| :--- | :--- | :--- |
| **0** | **Preserve (保护现场)** | 处理未提交 dirty 变更；创建 `pre-agent-os-YYYYMMDD` 标签或保底分支。 |
| **1** | **Recover Truth (还原真相)** | Codex 只读代码扫描；AntiGravity 独立推导当前实际架构与依赖边界。 |
| **2** | **Resolve Conflicts (仲裁冲突)** | 真实代码与测试状态高于一切；历史聊天记忆（Agent Memory）与旧文档冲突时以现状为准。 |
| **3** | **Install Lite+ (安装基线)** | 写入 `PROJECT.md`、`AGENT_PROTOCOL.md`、`AGENTS.md`，配置主分支保护与最小 CI。 |
| **4** | **Protect Control Plane (保护控制面)**| 将协议、Adapter、CI、治理配置文件列入保护范围。 |
| **5** | **Create T-001 (开启首个任务)** | 由 Human 签署首个任务范围、Lease、触碰路径与风险等级。 |
| **6** | **Execute (任务执行)** | Codex 在隔离任务分支开发。 |
| **7** | **Gate (合流门禁)** | R0/R1 快速路径；R2/R3 严格走固定 SHA 独立审查。 |
| **8** | **Merge (合流入库)** | 由 Human 执行合并操作。 |
| **9** | **Pilot Review (总结迭代)** | 运行 2–3 个任务后进行 Keep / Cut / Change 调整。 |

---

## 历史记忆准则 (Standing Memory Rule)
> **Historical memory is non-authoritative when it conflicts with current canonical state.**

迁移老项目时，**完全不需要大规模清理历史聊天记录**。协议层已经做出了权威等级裁决：任何 Agent 宣称的“我之前记得……”均不具有证据效力，主分支最新代码即是唯一真理。
