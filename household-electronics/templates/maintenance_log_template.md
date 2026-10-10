# 维护与耗材更换记录单 (Maintenance Log)

---
log_id: "MAINT-YYYYMMDD-001"
device_id: "DEV-KTCH-2023-001"     # 对应的设备编号
date: "YYYY-MM-DD"                 # 执行日期
operator: "User"                   # 操作人员
type: "routine_service"            # routine_service | consumable_replace | repair | inspection
cost_usd: 0.00                     # 产生耗材/维修费用
---

## 1. 维护操作摘要
- **执行项目**: [例如：咖啡机官方药剂全流程除垢 / 扫地机边刷滚刷清理与滤网换新]
- **耗材使用**: 
  - 耗材名称及型号: [例如：专用除垢粉 1包]
  - 耗材批次/来源: [例如：官方原装 / 可靠第三方]

## 2. 检查与实测指标
- [ ] 水箱及管路冲洗完成 (跑空 2 次清水水箱)
- [ ] 蒸汽喷嘴通针疏通及清洁完毕
- [ ] 密封圈弹性测试良好，无漏水压降现象
- [ ] 萃取与各项基础功能测试正常

## 3. 下次计划维护节点
- **下次维护类型**: [例如：水滤芯更换]
- **推荐执行日期**: `YYYY-MM-DD` (根据预设周期推算)
