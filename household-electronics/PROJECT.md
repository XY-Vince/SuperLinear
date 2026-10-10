# Household Electronics & Appliances Lifecycle Intelligence (HEM)

**Project Name:** Household Electronics & Appliances Lifecycle Intelligence (HEM / 家庭电子与电器全生命周期管理系统)  
**Version:** 1.0.0  
**Date:** 2026-10-08  
**Scope:** Private local-first asset tracking, digital data hygiene, maintenance scheduling, and decommission safety for household electronics and appliances.

---

## 1. Project Mission & Thesis

家庭中的硬件设备（计算设备、手机、厨房电器、智能家居）往往面临“双重孤岛”问题：
1. **实物资产孤岛**：纸质发票丢失、保修期与售后政策模糊、纸质说明书散落难查、易损耗材（水垢滤芯、HEPA滤网、高压密封圈、破壁机刀头）型号不明确、维护周期全靠记忆。
2. **数字数据风险孤岛**：换机与设备清理时，严重缺乏数据安全边界。2026-06 发生的“桌面误删事件”证明，自动化工具或用户误操作极易落入**云端双向同步占位符陷阱（Cloud-sync placeholder trap）**，导致级联删除；而在二手转让、以旧换新（Trade-in）时，又常因未彻底执行加密擦除（Crypto-erase）而发生隐私泄漏。

本项目的核心目标是构建一个**Local-First、结构化、可审计的家庭电子与家电全生命周期管理框架**，将**物理资产台账（Physical Asset Tracking）**与**数字数据卫生（Digital Data Hygiene & Disaster Prevention）**紧密融合。

---

## 2. Four-Tier Classification Architecture (四层分类体系)

项目统一覆盖以下四大类目：

```
                    Household Electronics & Appliances
                                   │
      ┌─────────────────┬──────────┴──────────┬─────────────────┐
      ▼                 ▼                     ▼                 ▼
   Tier A            Tier B                Tier C            Tier D
Computing &       Smart Home &            Kitchen           General Home
  Storage             IoT               Appliances          Electronics
(Laptop, Phone,  (Mesh Router,        (Air Fryer, Oven,    (Vacuum, Dyson,
 SSD, Tablet)    Robot, Smart TV)     Instant Pot, Coffee)  Monitor, Audio)
```

| 层级 | 类目范围 | 核心关注点 | 典型设备 |
|---|---|---|---|
| **Tier A** | 核心计算与高密度存储设备 | 数据完整性、云同步双向防线、离线硬核备份、FileVault/BitLocker 加密、换机数据擦除 | Laptop (MacBook Pro/Air, ThinkPad), 手机 (iPhone, Android), iPad, 移动SSD/HDD, NAS, 高速SD卡 |
| **Tier B** | 智能互联与家庭 IoT 设备 | 网络分配（IoT 隔离 VLAN/访客网络）、配套 App 与家庭共享账号、固件更新维护、重置配置文件 | Wi-Fi Mesh 路由器、扫地机器人 (Roborock)、智能电视 / Apple TV、智能摄像头、智能音箱、网关 |
| **Tier C** | 厨房电器与高功耗厨电硬件 | 电气安全与功耗、耗材型号与采购渠道、除垢（Descaling）/深度清洗/密封圈更新周期、说明书与菜谱归档 | 空气炸锅、多功能电饭煲/压力锅 (Instant Pot)、微波炉/烤箱、意式/滴滤咖啡机、破壁机、净水器、洗碗机 |
| **Tier D** | 通用生活与个人数码家电 | 电池健康度衰减、易损件配件（刷头、滤网）、充电规格与线缆匹配、收纳定位与附件管理 | 无线吸尘器 (Dyson)、空气净化器、降噪耳机、显示器、电动牙刷、挂烫机、GaN 充电站 |

---

## 3. Lifecycle State Machine (设备全生命周期状态机)

每件设备在系统中均处于明确的状态生命周期流转中：

```
[Acquisition / Intake]
         │ (Gate 1: S/N, Receipt, Manual, Warranty Registered)
         ▼
      [Active] ◄────────┐
         │              │ (Inspection Passed / Serviced)
         ├──────────────┼────────────────────────┐
         ▼              │                        ▼
   [Maintenance] ───────┘                    [Stored]
  (Filter / Descaling / Repair)           (Spare / Idle / Off-season)
         │
         │ (Gate 3: Backup Verified, Crypto-Erase, Factory Reset)
         ▼
  [Decommissioned]
         ├───────────────┬───────────────┐
         ▼               ▼               ▼
      [Resale]      [Trade-in]      [Recycled]
(FB Marketplace/     (Official       (E-waste /
    WeChat)           Apple/BestBuy)   Eco-drop)
```

---

## 4. Directory Structure (目录组织规范)

```text
work/household-electronics/
├── PROJECT.md                      # 本架构说明文件
├── GUIDELINES.md                   # 全生命周期管理与操作准则（核心指南）
├── inventory/                      # 结构化设备主档案清单
│   ├── computing_and_storage.yaml  # Tier A 设备台账
│   ├── smart_home_iot.yaml         # Tier B 设备台账
│   ├── kitchen_appliances.yaml     # Tier C 设备台账
│   └── general_home.yaml           # Tier D 设备台账
├── manuals/                        # 电子版官方使用手册 (PDF / Markdown / 外部可信链接)
├── receipts_warranties/            # 购买凭证、发票与保修卡归档
├── maintenance_logs/               # 维护记录与耗材替换日志
├── templates/                      # 标准化录入与操作模板
│   ├── device_card_template.md     # 单机建档卡模板
│   ├── maintenance_log_template.md # 维护与耗材替换日志模板
│   └── decommission_checklist.md   # 退役与安全抹除检查清单
└── scripts/                        # 自动化审计与监控脚本
    ├── validate_inventory.py       # 台账完整性与字段校验脚本
    └── status_report.py            # 质保临期预警与耗材健康报告脚本
```

---

## 5. Security & Privacy Boundaries

1. **Local-First & Non-Public**：设备 S/N、MAC 地址、家庭 Wi-Fi 分配、发票明细均保存在本地私有空间，绝不推送到公共仓库。
2. **财务与敏感 PII 加密**：包含个人真实姓名、身份证/SSN、付款信用卡号的发票原件，强制进入加密存储容器（例如 `/Volumes/Secure_WXY`），主台账仅保留去敏感化的引用。
3. **退役处置记录**：包含存储介质的设备在转让或报废前，依据硬件和系统特性执行重置或抹除，并记录执行方法与结果存入归档。
