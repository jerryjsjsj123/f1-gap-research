# 04: 端到端 Research

**What to build:** 对 `POST /chat` 提一个写明 Race 和 Driver Pair 的问题，Lead 选出要查的 Gap Phase，在同一轮里并行派出 Researcher，必要时补查一轮，Writer 写出报告。

**Blocked by:** 02, 03

**Status:** done

- [x] 响应结构与 Project1 一致（response、session_id、tool_calls），每条 tool_calls 带 agent 字段，包含子 agent 的调用
- [x] 首轮的多个 Researcher 并行运行，各自使用全新的上下文
- [x] 代码强制上限：最多 4 个 Researcher、补查 1 轮、每个 agent 有轮数上限
- [x] 模型经 Vertex AI 调用 Gemini
- [x] 响应结构有测试（Research 替换为固定结果）
- [x] 用一个真实问题在本机跑通，耗时远低于 5 分钟
