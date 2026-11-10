# F1 Gap Research

一个 Deep Research 助手：针对一场 2026 赛季正赛里的两名车手，用比赛数据重建他们之间的差距如何形成，并判断哪些解释有数据依据。

## Language

### 比赛与差距

**Race**:
2026 赛季的一场正赛。排位赛、冲刺赛、练习赛不在范围内。
_Avoid_: session, Grand Prix, event

**Driver Pair**:
一次研究所比较的两名车手，有固定的先后顺序（A 和 B）。
_Avoid_: matchup, rivals

**Gap**:
Driver Pair 在某一圈结束时过线时刻之差，每圈一个值；正数表示 A 落后于 B。只定义到两人都完成的最后一圈。
_Avoid_: interval, delta, margin

**Gap Phase**:
一段连续的圈数范围，其间 Gap 的变化方式大致一致（例如持续扩大、基本持平、突然跳变）。
_Avoid_: stint, segment, window

### 研究与证据

**Research**:
针对一个用户问题的一次完整调查：Lead 选出要查的 Gap Phase 并派出 Researcher，必要时补查，最后由 Writer 写成报告。同一对话里的追问会开启一次更聚焦的 Research，并沿用已有的 Race、Driver Pair 和 Evidence。
_Avoid_: run, job, query

**Evidence**:
一次数据查询返回的结果，带唯一编号和来源（数据表、Race、车手、圈数范围）。报告里的每个事实都引用 Evidence 编号。
_Avoid_: source, citation, finding

**Explanation**:
对某个 Gap Phase 里 Gap 变化原因的一种说法（例如进站时间差、轮胎胎龄、安全车）。
_Avoid_: reason, cause, factor

**Support Level**:
一个 Explanation 的证据强度，三档之一：Supported（数据支持）、Consistent（与数据一致但无法证实）、Unanswerable（数据无法回答）。车手失误、赛车损伤、车队指令、燃油和引擎模式不在数据里，属于 Unanswerable。
_Avoid_: confidence, certainty
