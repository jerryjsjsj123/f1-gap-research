# 用离线快照，而不是线上实时调用 OpenF1 / Jolpica

线上的 agent 只查随仓库部署的本地快照；快照由一个离线脚本从 OpenF1 和 Jolpica 拉取 2026 赛季全部正赛后生成。这样做是因为 OpenF1 有限流，并行的 Researcher 容易撞上，评分期间 API 一旦不可用助手就无法回答，而课程也要求 "Ship the data with the deploy"。代价是新比赛结束后要重跑脚本并 push 才会出现在助手里。

## Considered Options

- **线上实时调 API**：数据永远最新，但受限流和外部可用性影响。
- **快照为主，缺了再调 API**：覆盖最全，但要维护两条数据路径和缓存。
