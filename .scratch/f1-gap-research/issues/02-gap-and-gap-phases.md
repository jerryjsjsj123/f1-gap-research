# 02: Gap 与候选 Gap Phase

**What to build:** 给定一场 Race 和一对 Driver Pair，得到逐圈 Gap 和一组候选 Gap Phase（每段的圈数范围、Gap 变化量、变化方式）。

**Blocked by:** 01

**Status:** done

- [x] Gap 为每圈过线时刻之差，正数表示 A 落后 B，只算到两人都完成的最后一圈
- [x] 进站圈、安全车和红旗时段是 Gap Phase 的强制边界
- [x] 各 Gap Phase 的变化量之和等于首尾 Gap 之差
- [x] 退赛、被套圈的情况有明确说明
- [x] 以上行为有测试，测试用手工构造的小数据集
