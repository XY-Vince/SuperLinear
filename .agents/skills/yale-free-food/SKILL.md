---
name: yale-free-food
description: "耶鲁大学及高校校园免费食物与活动发现引擎（Yale Free Food & Campus Events Discovery Engine）。当用户询问耶鲁大学近期有什么免费食物、披萨、午餐讲座、迎新酒会（Reception）、茶歇活动、创投/科技社群聚会，或者需要生成校园日程与蹭饭指南时触发。"
metadata:
  version: 2.4.0-closeout-verified
  requires:
    bins: ["python3"]
---

# Yale Free Food & Events Discovery Engine (校园活动与免费餐饮发现引擎)

本技能用于主动发现耶鲁大学（Yale University）及纽黑文（New Haven）周边公开活动、免费餐饮、科技创投研讨会及学生学者社群聚会。
采用“先扩大召回，再多维度核验，再生成实用日程”的工作流，支持两跳递归主办方发现、智能体搜索桥接、断点恢复与多格式日报输出。

## 核心架构与发现原则

1. **先扩大召回，先存后判**：
   - 发现阶段不以“是否含 food 关键词”决定是否丢弃；
   - 创业讲座、技术沙龙、社群聚会如无餐饮，标记为 `user_interest` 妥善保存，在日报中单独呈现，绝不漏抓。
2. **两跳递归主办方发现 (2-Hop Expansion)**：
   - 路径：`活动 → 主办方/合作方日历 → 同期其他活动`；
   - 自动提取 host 与主办方日历主页，递归追踪下级活动，避免需要用户反复喂链接。
3. **正交五维分类标准**：
   - `food_status`：`provided` / `likely` / `unclear` / `none`（供餐与否事实）
   - `food_cost`：`free` / `paid` / `byo` / `unknown`（餐食费用）
   - `admission_cost`：`free` / `paid` / `unknown`（门票与入场费，避免免费点心但门票昂贵的情况）
   - `eligibility`：面向全体、仅限院系、特定身份
   - `rsvp_status`：报名截止、需提前 RSVP、先到先得
4. **运行配置档位 (Profiles)**：
   - `quick`（快速核验）：10 详情预算 / 15 抓取上限（8 个主动查询任务）
   - `daily`（每日默认）：30 详情预算 / 50 抓取上限（18 个主动查询任务）
   - `deep`（深度全网回溯）：100 详情预算 / 200–500 爬取上限（全量查询任务，支持断点恢复）

---

## 常用命令速查

### 1. 默认完整编排运行（多源发现 + 目标窗口汇聚 + 日报周报输出）
```bash
# 实时模式（默认读取当前美东日期，抓取当期主日历与多源种子，生成今日与本周日程）
python3 scripts/build_verified_runtime.py --mode live --profile daily --state-dir data/runtime

# 回放模式（复现特定历史/测试窗口，使用本地缓存快照）
python3 scripts/build_verified_runtime.py --mode replay --from-date 2026-09-27 --days 7 --clean --state-dir data/runtime

# 结合历史基线快照导入（可选）
python3 scripts/build_verified_runtime.py --mode replay --from-date 2026-09-27 --import-baseline data/baseline/day-2026-09-27 --state-dir data/runtime
```

### 2. 深度主动发现与断点恢复 (`scripts/discovery.py`)
```bash
# 步骤 A: 生成未来 7 天的 6 大查询族任务及种子抓取清单
python3 scripts/discovery.py plan --from-date 2026-09-27 --days 7 --profile deep --state-dir data/runtime

# 步骤 B: 智能体调用公开搜索工具后，将 JSONL 研究成果批量汇入统一存储
python3 scripts/discovery.py ingest-research --input data/research-results.jsonl --state-dir data/runtime

# 步骤 C: 执行两跳递归爬虫，支持断点恢复 (--resume)
python3 scripts/discovery.py run --resume --profile daily --state-dir data/runtime
```

### 3. 多端日报格式导出 (`scripts/briefing.py`)
一键编译输出 Markdown (`daily-briefing.md`)、JSON (`daily-briefing.json`)、CSV (`daily-briefing.csv`)：
```bash
python3 scripts/briefing.py --date 2026-09-27 --state-dir data/runtime --out-dir reports/
```
日报结构包含：
- 🎯 **优先行动与报名提示**（即将截止、需审批、时间冲突）
- 🍔 **明确免费餐饮日程**（明确供餐且餐食免费）
- 🥗 **明确供餐 / 餐费待核实活动**（说明供餐但餐费未独立标明）
- 💡 **用户关注与精选科技社群活动**（科技创投、学术沙龙、用户关注）
- 📋 **待核实线索队列**（无明确时间或时区不明线索）

### 4. 外部平台与用户线索录入 (`scripts/ingest_multi_source.py`)
```bash
# 导入 Lu.ma 活动（可附加 --user-interest 保留非餐饮创业活动）
python3 scripts/ingest_multi_source.py --luma "https://luma.com/..." --user-interest

# 导入 Eventbrite 活动
python3 scripts/ingest_multi_source.py --eventbrite "https://www.eventbrite.com/e/..."

# 导入微信推文或邮件线索
python3 scripts/ingest_multi_source.py --wechat-file path/to/wechat.txt
python3 scripts/ingest_multi_source.py --scan-emails data/incoming_emails/
```

---

## 存储优先级与安全性

- **补充库优先级**：`--supplement/--store` > `YALE_FREE_FOOD_STORE` > `~/.yale-free-food/supplemental_events.json`
- **运行状态隔离**：状态数据库 (`state.db`)、Frontier 待抓队列与发现账本 (`discovery-ledger.jsonl`) 统一存储于 `--state-dir`。
- **安全保障**：全流程强制 TLS 证书链校验；文件写入通过原子临时文件与进程文件锁保障并发一致性；不访问私有账户，不自动代填表单。
