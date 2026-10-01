# Day-0 SOP: 新项目极简启动规范 (Lite+ 默认)

新项目切忌在启动时过度设计。仅需完成以下 6 步：

```text
git init
   │
   ▼
1. 编写 PROJECT.md (目标、架构、关键约束、可执行 Validation 命令)
   │
   ▼
2. 植入 AGENT_PROTOCOL.md + AGENTS.md (Codex Adapter)
   │
   ▼
3. 推送远程，锁定 protected main (禁止直接 push，开启 PR 审查)
   │
   ▼
4. 配置最小可执行 CI (GitHub Actions: lint + test)
   │
   ▼
5. Human 创建 Task T-001 (明确 Scope, Risk, Touched Areas, Lease)
   │
   ▼
6. Codex 切分支开工 (feat/T-001)
```

---

## 落地文件清单
一个标准的 Lite+ 新项目根目录下仅需要：
```text
my-project/
├── .github/workflows/ci.yml     # 最小自动化门禁
├── AGENT_PROTOCOL.md            # Multi-Agent Protocol v1.0
├── AGENTS.md                    # Codex Adapter
├── PROJECT.md                   # 项目基准说明书
└── control/tasks/               # 任务 Lease 存放处 (可选，也可用 GitHub Issue 代替)
```
不要在一开始创建十几个冗余的治理目录。
