# 01: 2026 正赛快照

**What to build:** 一个离线脚本，从 OpenF1 和 Jolpica 拉取 2026 赛季全部已完成的 Race，生成随仓库部署的本地快照；应用能从快照里列出所有 Race 和每场的车手。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 运行一个命令即可生成或更新快照，遇到限流会自动重试
- [x] 快照包含每场 Race 的逐圈数据、stint、进站、race control、超车、位置、天气、成绩和发车位
- [x] 能列出快照里的全部 Race 以及每场的车手（号码、缩写、车队）
- [x] 快照体积远小于 GitHub 的 100 MB 单文件限制
