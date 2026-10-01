# Multi-Agent OS v1.0 — New Machine Setup & Migration Guide

本指南说明如何在**新电脑**上快速解压并初始化 Multi-Agent OS 运行环境。

---

## 步骤一：创建系统级目录

在全新电脑的用户家目录下，创建 Multi-Agent OS 规范依赖的全局检疫与审查目录：

```bash
# 1. 创建 Muse 外部调研原始数据隔离检疫区
mkdir -p ~/AgentIngress/muse/raw

# 2. 创建独立代码审查产物存放区 (供 Local-only 项目使用)
mkdir -p ~/.agent-reviews

# 3. 如果使用 Codex / 全局 Agent 规则，设置全局配置目录
mkdir -p ~/.codex
```

---

## 步骤二：安装全局 Agent 规则 (可选/推荐)

将本包中的全局规范复制到全局目录，确保所有本地 Agent 均遵循 NCS Protocol v1.2：

```bash
# 复制全局规范到 ~/.codex/AGENTS.md
cp adapters/global/GLOBAL_AGENTS.md ~/.codex/AGENTS.md
```

---

## 步骤三：初始化新项目 (Day-0 Bootstrap)

要在新电脑上建立一个符合 Lite+ 标准的全新项目：

```bash
# 使用内置工具快速脚手架初始化
python3 tools/init_project.py /path/to/my-new-project

cd /path/to/my-new-project
git init
git add .
git commit -m "feat: bootstrap project with Multi-Agent OS Lite+"
```

该工具会自动为你生成：
- `PROJECT.md`（项目基准说明书）
- `AGENT_PROTOCOL.md`（规范主协议）
- `AGENTS.md`（Codex Builder Adapter）
- `control/tasks/T-000-template.yaml`（任务 Lease 模板）

---

## 步骤四：老项目迁移 (Lite+ Injection)

若从旧电脑拷贝了已有代码仓库，迁移步骤如下：

1. **备份现状**：
   ```bash
   cd /path/to/legacy-project
   git checkout -b pre-agent-os-backup
   git checkout main
   ```
2. **植入规范三件套**：
   ```bash
   python3 /path/to/multi-agent-os-v1.0/tools/init_project.py .
   ```
3. **补齐 `PROJECT.md` 中的 Validation 命令**：
   务必填写实际可执行的验证命令（如 `npm test`、`pytest`、`cargo test`）。
4. **提交基线**：
   ```bash
   git add PROJECT.md AGENT_PROTOCOL.md AGENTS.md control/
   git commit -m "chore: adopt Multi-Agent OS Lite+ v1.0"
   ```

---

## 步骤五：日常开发常用命令速查

### 1. 验证任务 Lease 与合流门禁
在分支开发完成后，运行此脚本校验是否满足合流条件：
```bash
python3 /path/to/multi-agent-os-v1.0/tools/verify_lease.py control/tasks/T-001.yaml HEAD
```

### 2. AntiGravity 独立 Review 物理隔离工作树
在审查目标 Target SHA 时，创建干净隔离的工作树，避免污染 Codex 当前活动目录：
```bash
# 开启隔离审查区
/path/to/multi-agent-os-v1.0/tools/worktree_review.sh start <target_sha> /tmp/review-T-001

# 进入临时工作区执行独立测试与审查
cd /tmp/review-T-001
# 执行 PROJECT.md 定义的测试...

# 审查完毕后，清理工作区
/path/to/multi-agent-os-v1.0/tools/worktree_review.sh clean /tmp/review-T-001
```
