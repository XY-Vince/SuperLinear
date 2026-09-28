# Yale Free Food & Campus Events: Source Discovery & Research Guide

## 1. 目的与发现原则

本文档为智能体（Agent）及自动化流程提供耶鲁大学与纽黑文地区活动、免费餐饮及科技创业讲座的检索与发现规范。

### 核心原则
1. **扩大召回，先存后判**：搜索与日历抓取阶段，不以“是否含 food 关键词”决定是否丢弃。只要符合时间、地域（耶鲁纽黑文）或社群关联，先保存基础实体，抓取正文与报名页后再行分类。
2. **两跳递归主办方发现**：
   - 路径：`活动 → 主办方/合作方日历 → 同期其他活动`
   - 提取 host、co-host、registration_url，去重后加入待抓队列（Frontier）。
3. **真实网络工具桥接**：
   - 使用真实的搜索工具（如 `search_web`）与页面读取工具（`read_url_content`）。
   - 结果按 JSONL 格式通过 `discovery.py ingest-research` 汇入统一数据库，禁止凭空捏造。
4. **分类解耦**：
   - 供餐事实（`provided / likely / unclear / none`）与费用（`free / paid / byo / unknown`）及准入费用独立评估。
   - 创业聚会、学术前沿讲座如无餐饮，标记为 `user_interest`，不作为食物漏抓删除。

---

## 2. 六大公开搜索查询族 (Query Families)

执行规划时，自动根据目标日期区间（如 `2026-09-27` 至 `2026-10-04`）展开具体时间词。**至少 50% 查询不带食物词**。

| 族编号 | 族名称 | 英文模板 | 中文 / 备用模板 | 重点目标平台 |
|---|---|---|---|---|
| **F1** | 校园官方官方公告 | `site:yale.edu (events OR calendar) (reception OR refreshments OR lunch OR welcome)` | `site:yale.edu/events [target_date]` | 耶鲁各院系、研究中心 |
| **F2** | 创业创新专场 (无食物词) | `(Yale OR "New Haven") (workshop OR demo OR founder OR networking OR symposium) [month/day]` | `(耶鲁 OR 纽黑文) (创投 OR 创业 OR 讲座 OR 聚会)` | Tsai CITY, Yale Ventures, SEAS |
| **F3** | Lu.ma 纽黑文活动 | `site:luma.com (Yale OR "New Haven") [target_month]` | `site:lu.ma (Yale OR "New Haven")` | Lu.ma 社区与学生创客 |
| **F4** | Eventbrite 开放活动 | `site:eventbrite.com (Yale OR "New Haven") [target_month]` | `site:eventbrite.com/e/ Yale New Haven` | Eventbrite 公开活动 |
| **F5** | 餐饮与社交招待会 | `(Yale OR "New Haven") ("free snacks" OR "lunch provided" OR complimentary OR reception) [target_date]` | `(耶鲁 OR 纽黑文) (迎新 OR 茶歇 OR 披萨 OR 招待会)` | 校园社团、文化中心、校友聚会 |
| **F6** | 中文社群与学联 | `site:mp.weixin.qq.com (耶鲁 OR Yale) (活动 OR 创投 OR 学联) [year/month]` | `ACSSYale OR "耶鲁学联" 活动` | ACSSYale, YVC, 纽黑文中文社群 |

---

## 3. AG 搜索桥接契约 (Research Ingestion Contract)

Agent 使用网络工具完成搜索或页面抓取后，将提取结果写入 JSONL 临时文件，并通过以下命令灌入主存储：

```bash
python3 scripts/discovery.py ingest-research --input /path/to/research-results.jsonl --state-dir /path/to/runtime
```

### JSONL 每行标准 Schema：
```json
{
  "task_id": "search_f2_founder_01",
  "query_or_parent_url": "https://city.yale.edu/events",
  "result_url": "https://luma.com/tsai-city-lunch-1002",
  "fetched_at": "2026-09-27T14:30:00Z",
  "title": "Tsai CITY Founder Lunch & Learn: Identify What Matters",
  "starts_at": "2026-10-02T12:30:00-04:00",
  "ends_at": "2026-10-02T13:30:00-04:00",
  "location": "Tsai CITY, 17 Prospect St, New Haven, CT",
  "host": "Tsai Center for Innovative Thinking at Yale",
  "food_status": "provided",
  "food_cost": "free",
  "admission_cost": "free",
  "confidence": "confirmed_free",
  "rsvp_status": "RSVP required via Luma",
  "eligibility": "Yale Students, Postdocs, Faculty & Staff",
  "participation_note": "Free lunch provided. Register on Luma.",
  "evidence_text": "Join us for lunch and discussion. Lunch will be provided for registered attendees.",
  "extraction_method": "organizer_expansion",
  "user_interest": true
}
```

---

## 4. 2-Hop 主办方递归发现机制

1. **第一跳（Direct Event）**：
   - 从种子或搜索引擎发现活动 URL。
   - 解析详情，抽取 `host`（主办方）与外链（如 Lu.ma、Eventbrite、主办方日历主页）。
2. **第二跳（Host Expansion）**：
   - 提取主办方链接（如 `https://luma.com/TheYaleTable` 或 `https://city.yale.edu/events`）。
   - 将该链接以 `depth=1` 放入 `frontier` 表。
   - 访问该主办方主页，提取同期或未来 7 日内发布的其他活动。
3. **收敛与防死循环**：
   - 最大深度控制在 2 跳。
   - URL 规范化过滤跟踪参数（`utm_*`, `ref`, `tk`）。
   - 域名黑名单与通配排除：防止爬取通用外链（Google Forms、外部社交主页等）。

---

## 5. 渲染与多模态海报兜底 (Render & Poster Fallback)

1. **静态 HTML 结构解析**：
   - 首选 JSON-LD (`<script type="application/ld+json">`)。
   - 其次解析 OpenGraph 与 Twitter Card meta 标签。
2. **页面正文与附件**：
   - 搜索正文中的活动日期与餐饮线索。
   - 若正文包含海报链接或 PDF 议程，优先读取并提取文本。
3. **状态留痕**：
   - 若活动信息明确存在但时间未知，归入 `unverified_food_leads` 队列，保留在日报末尾，绝不丢弃。
