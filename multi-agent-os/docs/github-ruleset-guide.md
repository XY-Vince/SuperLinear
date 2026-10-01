# GitHub Branch Protection & Ruleset Recommended Configuration

在 GitHub 托管的项目中，将以下策略应用于 `main` 分支：

## 推荐门禁清单

| 配置项 | 推荐状态 | 说明 |
| :--- | :---: | :--- |
| **Require a pull request before merging** | ✅ | 强制任何改动必须通过 PR，禁止直接 push 到 main |
| **Require status checks to pass before merging** | ✅ | CI 必须绿灯 |
| **Require branches to be up to date before merging** | ✅ | 确保测试针对最新 main 运行 |
| **Block force pushes** | ✅ | 保证 git commit log 的不可篡改与线性追溯 |
| **Block deletions** | ✅ | 防止主分支被误删 |
| **Dismiss stale pull request approvals when new commits are pushed** | ✅ | 核心规则：一旦有新 commit，之前的 review 立即作废 |
| **Require review from Code Owners** | ✅ | 敏感路径、控制面文件必须经由指定 owner 批准 |
| **Require approval from someone other than the last pusher** | ✅ (R2/R3) | 杜绝提交者自批自改 |
| **Allow admin bypass** | ❌ (尽量关闭) | 避免日常手滑绕过门禁 |
| **Direct Agent merge** | ❌ (严厉禁止) | 只有经过授权的 Human 才能触发合流动作 |

---

## CODEOWNERS 推荐配置样例

在 `.github/CODEOWNERS` 中加入控制面保护：

```text
# Control Plane Assets (R3 - Human Only)
AGENT_PROTOCOL.md           @your-github-username
PROJECT.md                  @your-github-username
AGENTS.md                   @your-github-username
.github/workflows/**        @your-github-username
.github/CODEOWNERS          @your-github-username
control/**                  @your-github-username
```
