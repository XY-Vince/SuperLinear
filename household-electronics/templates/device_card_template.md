# [品牌] [产品型号] 资产档案卡

---
id: "DEV-TIER-YYYY-001"            # 资产唯一编号，如 DEV-COMP-2024-001 或 DEV-KTCH-2023-002
record_kind: "real"                # real (真实资产) | example (演示/样例)
verification_status: "unverified"  # unverified (待核实) | verified (凭证已核验)
source_refs: ["user_declaration_unverified"] # 证据引用 (发票哈希/购机凭证/用户口述)
observed_at: "2026-10-08"          # 数据采集或最后核实日期
category: "kitchen_appliances"     # computing_and_storage | smart_home_iot | kitchen_appliances | general_home
brand: "Breville"                  # 品牌
model: "BES870XL"                  # 完整型号
name: "Barista Express 意式咖啡机"   # 设备通俗名称
serial_number: "SN123456789"       # 设备序列号 S/N
status: "active"                   # active | maintenance | stored | sold | recycled
location: "厨房水槽右侧"             # 物理存放/使用位置
purchase_date: "2023-11-24"        # 购买日期 YYYY-MM-DD
purchase_price_usd: 699.95         # 购买价格
vendor: "Amazon US"                # 购买渠道 / 零售商
warranty_expiry: "2025-11-24"      # 质保到期日 YYYY-MM-DD
power_rating_w: 1600               # 额定功率（瓦特，主要针对家电）
---

## 1. 硬件规格与技术参数
- **颜色/材质**: 不锈钢拉丝
- **电压规格**: 120V / 60Hz
- **关键技术特性**: 内置锥形研磨器、15巴意大利高压水泵、PID温控系统

## 2. 电子单证与归档附件
- **发票收据**: `receipts_warranties/Breville_BES870XL_Invoice_2023-11-24.pdf`
- **官方说明书**: `manuals/Breville_BES870XL_Manual.pdf`
- **快速指南/代码表**: `manuals/Breville_BES870XL_QuickStart.pdf`

## 3. 耗材规格与备件信息
- **水滤芯**: Breville ClaroSwiss 滤芯 (每 90 天更换)
- **除垢剂**: 官方无水柠檬酸除垢粉或通用浓缩咖啡机除垢液
- **冲煮头胶圈**: 54mm 硅胶密封垫圈 (备件型号: SP0001474)

## 4. 维护周期配置 (Maintenance Schedule)
- **除垢 (Descaling)**: 周期 60 天 | 上次维护: `2024-08-10` | 下次预计: `2024-10-09`
- **反冲洗 (Backflush)**: 周期 30 天 | 上次维护: `2024-09-01` | 下次预计: `2024-10-01`
- **滤芯更换**: 周期 90 天 | 上次维护: `2024-07-15` | 下次预计: `2024-10-13`

## 5. 数据与网络配置 (计算/IoT专用，厨电可填N/A)
- **IP / MAC 地址**: N/A
- **绑定的管理 App**: N/A
- **本地备份方案**: N/A

## 6. 备注与特殊保养技巧
- 冬季室内温度较低时，建议开机预热 15 分钟并放空水加热手柄再行萃取。
