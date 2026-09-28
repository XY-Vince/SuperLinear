# Yale Free Food & Campus Events Discovery Upgrade: 候选受限试运行评测报告 (Evaluation Report)

- **评估日期**：2026-09-27
- **代码候选版本**：`v2.4.0-closeout-verified`
- **实施依据**：`FINAL_CLOSEOUT.md`
- **隔离候选目录**：`candidates/yale-free-food-discovery/`
- **交付包根目录**：`discovery-upgrade/`

> 已完成本轮限定开发收尾与独立审查整改。独立离线探针确认，主日历详情任务能够执行并依据正文更新分类，replay模式不发起网络请求。YaleConnect 实时请求 TLS 证书链配置已修复并通过实际在线接口调用验收（返回 HTTP 200 并成功提取当期活动）。数据架构明确划分固定回放快照（data/observations/pages/）与运行时缓存（data/runtime/cache/）。全量 117 项单元测试及 5 项泛化探针全部通过。

---

## 1. 核心改进与执行成果 (Executive Summary)

针对审阅意见，候选引擎已完成主日历详情执行闭环、数据与文字报告校准、既有关注记录去向说明与双窗口独立持久化：

1. **主日历详情队列真正执行与证据更新**：
   - 在 `scripts/build_verified_runtime.py` 实现并接入 `execute_yaleconnect_details` 执行器，完整消费 `fetch_and_ingest_yaleconnect` 产生的 `detail_queue`。
   - 详情抓取复用 `fetch_free_food.fetch_event_detail_html`、`parse_detail_page` 与 `classify_event`，保留原始 `item` 字典以支持带详情的重新分类。
   - 在 `parse_detail_page` 增加正文 HTML 兜底提取，在 `analyze_food_text` 将 `complimentary pizza` 纳入高可信免费供餐模式；无标题食物词但正文注明免费供餐的活动（如 Innovation Mixer）在详情更新后自动转为 `food_status: "provided"`, `food_cost: "free"`, `details_verified: true`, `confidence: "confirmed_free"`。
   - `source_status` 与详情执行统计（`executed`, `succeeded`, `failed`, `pending`）贯穿 `daily-briefing.json`、`weekly-briefing.json` 与 `run-manifest.json`。
   - **验收结果**：执行 `check_closeout.py`，`detail_calls` 为 **1**（上轮为 0），`agenda[0]` 成功更新为 `provided / free / details_verified=true`；在 replay 模式下网络尝试数恒为 **0**。

2. **报告与底层数据校正与撤销无凭据声明**：
   - **撤销声明**：YVC 两条记录（9/27 YVC General Meeting #1、10/4 YVC General Meeting #2）为无来源支持的文字声明，现已撤回。底层存储、抓取快照及交付 JSON 中均不存在该两场活动。
   - **周报餐饮条目校正**：本周周报（9/27–10/3）包含 1 场明确供餐、餐费未知的活动（AMA: Building a Business from the Ground Up 饼干活动），以及 1 条 reception 餐饮待核线索（Fr. James Keenan 讲座），明确免费餐饮数均为 **0**。

3. **既有关注记录去向明细表 (`record_dispositions`)**：
   - 本次未导入历史基线，结果由公开来源发现、研究输入与缓存内容合并生成。既有用户关注未加载，因此不是完整个人日程。

| 记录标识 / 活动标题 | 来源渠道 | 历史状态 / 时间 | 本次运行去向 (Disposition) | 原因说明与核实结论 |
|---|---|---|---|---|
| `luma:ijoov0uc`<br>**WorkBuddy — Hello Buddy @ Yale** | 历史基线<br>(known-leads.json) | 2026-09-27<br>15:00–17:00 | **未在当前动态运行中加载** | 存储于 `data/baseline/day-2026-09-27/known-leads.json`；仅在显式传入 `--import-baseline` 时合并。属于需批准准入活动，无免费餐饮证据。 |
| `user:yvc-venture-night-2026-09-27`<br>**YVC — Yale Venture Night 2026** | 用户邀请函登记<br>(known-leads.json) | 2026-09-27<br>19:00–22:00 | **未在当前动态运行中加载** | 存储于 `data/baseline/day-2026-09-27/known-leads.json`；为 Google Forms 申请邀请制创业夜活动，并非“常规例会”，未断言免费供餐。 |
| **YVC General Meeting #1 & #2** | 无来源支持的文字声明 | 20:00–21:00<br>LC 101 | **正式撤回 (Retracted)** | 无来源支持的文字声明，现已撤回，交付文件与运行库中均无此条目。 |
| **More House Lecture (Keenan)** | 官方页面检索<br>(stm.yale.edu) | 2026-09-27<br>18:15–? | **正常纳入 9/27 日程** | 位于 9/27 日报与周报，reception 登记为餐饮待核线索，餐费未知（`food_cost: unknown`），`user_requested: 0`。 |
| **STEM Navigators: Study Hall** | 官方卡片发现<br>(poorvucenter.yale.edu) | 2026-09-27<br>14:00–17:00 | **正常纳入 9/27 日程** | 位于 9/27 日报与周报，学术自习活动，`food_status: unclear`，`details_verified: false`。 |
| **Yale India Forum 2026** | Eventbrite 检索<br>(ID: 1998160586102) | 10/03 08:00 –<br>10/04 20:00 | **正常纳入 10/4 日程** | 跨 10/4 日程，记录 429 访问限流状态，门票与餐饮保留待核，`user_requested: 0`。 |
| **STEM Navigators: Fall Study Break** | 官方卡片发现<br>(poorvucenter.yale.edu) | 2026-10-04<br>14:00–16:00 | **正常纳入 10/4 日程** | 位于 10/4 日报与第二窗口周报，`food_status: unclear`。 |

---

## 2. 探针与测试通过矩阵

| 验证项 | 测试命令 / 脚本 | 预期指标 | 实际执行结果 | 判定 |
|---|---|---|---|---|
| **Astra 收尾探针** | `check_closeout.py` | `detail_calls: 1`, `details_verified: true`, `food_status: provided`, `food_cost: free`, `urlopen_attempts: 0` | 100% 吻合 | **PASS** |
| **收尾单元测试** | `tests/test_closeout_details.py` | 详情执行触发、字段重分类更新、回放模式零网络隔离 | 2 / 2 PASS | **PASS** |
| **R4 边界探针** | `tests/test_boundary_r4.py` | 主日历适配器调用、回放零网络、元数据透传、搜索事实校对 | 4 / 4 PASS | **PASS** |
| **R3 泛化探针** | `tests/transfer_probes.py` | English Institute 无旧参注入、动态价格区间、脱敏 cookies、异地场地、未核实卡片 | 5 / 5 PASS | **PASS** |
| **全量单元测试集** | `python3 -m unittest discover tests` | 全量单元测试无错误通过 | **117 / 117 PASS** | **PASS** |

---

## 3. 运行指南与剩余事项收敛

### 常用运行命令
- **实时发现模式（Live，增量无破坏）**：
  ```bash
  python3 scripts/build_verified_runtime.py --state-dir data/runtime --mode live --from-date 2026-09-27 --days 7 --research-file data/research-live-search.jsonl
  ```
- **离线回放模式（Replay，零网络尝试）**：
  ```bash
  python3 scripts/build_verified_runtime.py --state-dir data/runtime_replay --mode replay --from-date 2026-09-27 --days 7
  ```
- **第二窗口独立运行**：
  ```bash
  python3 scripts/build_verified_runtime.py --state-dir data/runtime_oct04 --mode live --from-date 2026-10-04 --days 7 --research-file data/research-live-search.jsonl
  ```

### 当前 Partial 范围与整改进展
1. **YaleConnect 实时网络**：【已修复】此前环境因 `get_ssl_context` 忽略系统 CA / 代理 CA 导致证书链验证失败。现已升级为基于 `ssl.create_default_context()` 并按需动态补充 `certifi` 与系统证书链，经真实在线接口调用验证成功（HTTP 200，实时抓取并解析活动有效）。
2. **Eventbrite 429 限流**：Yale India Forum（Eventbrite ID 1998160586102）遇平台频率限制，保留平台公开摘要并如实记录限流状态，不强行重试或捏造票价。
3. **未定档讲座**：Nucleate New Haven Demo Day 及工学院未公布具体日期的活动保留在 `unverified_leads` 队列，待后续解析。
4. **快照与缓存架构**：`data/observations/pages/` 明确作为固定金标准测试回放快照（支持全离线 0 网络开销验证）；`data/runtime/cache/` 作为爬虫运行时的动态 HTTP 缓存。
