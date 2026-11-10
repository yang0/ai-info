# 参考仓库调研与选型

核查时间：2026-11-10（上海时间）。Star 数为 GitHub 元数据查询时的快照，仅作为项目关注度参考，不代表质量保证。

| 项目 | Star 快照 | 许可证 | 借鉴点 | 本项目处理 |
| --- | ---: | --- | --- | --- |
| [AIHOT](https://github.com/KKKKhazix/AIHOT) | 10,171 | MIT | 资料预筛、独立评分、中文摘要、事件聚合 | 借鉴事件中心思路，趋势分由程序计算 |
| [IntelBureau-v2](https://github.com/yang0/IntelBureau-v2) | 私有参考 | 本轮未确认可复用授权 | 带出处的阶段流水线、作战大屏、离线模式 | 仅参考产品思路，未搬运代码或私有资源 |
| [Agent Reach](https://github.com/Panniantong/Agent-Reach) | 110,712 | MIT | 免费多平台工具、统一 JSON、登录与错误状态 | 作为可选采集进程，保留采样排序边界 |
| [TrendRadar](https://github.com/sansan0/TrendRadar) | 63,563 | GPL-3.0 | 多源聚合、关键词筛选、轻部署 | 参考配置体验，未复制代码 |
| [BettaFish](https://github.com/666ghj/BettaFish) | 42,614 | GPL-2.0 | 不同职责 Agent、交叉分析与报告 | 采用机会 / 质疑双视角，本地模型可选，未复制代码 |
| [OpenCLI](https://github.com/jackwener/opencli) | 未记录 | 上游 README 标识 MIT | 免费使用已登录浏览器，Twitter 最新搜索 | 推荐只读采集入口，用户单独安装 |

## 为什么推荐 OpenCLI Latest

阅读 Agent Reach 当前的 [`twitter.py`](https://github.com/Panniantong/Agent-Reach/blob/main/agent_reach/channels/twitter.py) 与 CLI 参数定义后发现：其 Twitter search 只包装 `query` 和 `limit`。阅读 OpenCLI [`clis/twitter/search.js`](https://github.com/jackwener/opencli/blob/main/clis/twitter/search.js) 后确认：

- `--product live` 才映射到 X 的 Latest；默认 `top` 映射到 Top。
- `created_at` 是 X 日期字符串，可能采用 RFC 格式。
- 返回 URL 可为 `https://x.com/i/status/<id>`，实际作者在单独的 author 字段。

因此直接假设 Agent Reach 结果是“最新时间线”、用 URL 中的 `i` 统计独立作者或仅支持 ISO 日期，都会影响趋势质量。首版明确处理这些差异，推荐通过 OpenCLI 的已登录浏览器读取 Latest；Agent Reach 单独标注热门排序抽样，不自动降级切源。

这是依据当前上游代码做的工程选择，尚未用用户的已登录 X 账号验证网站现场行为。网页搜索覆盖和上游适配器可能变化，后续应补长时间实测。

## 免费与实现边界

- 不使用 X 官方付费 API、商业搜索服务或付费代理。
- 不依赖付费云端模型；默认规则流程，可选本机 Ollama。
- 不复制参考项目的实现代码、Logo 或运营信源名单。
- 暂不引入通用大 Agent 框架：当前 DAG 固定，轻量异步编排足以支持并发、取消、状态和本地模型角色。需要动态研究工具与多轮任务时再评估框架。
- SQLite 与无需前端构建的界面适合单机首版，暂不增加 PostgreSQL、Redis、Docker 的部署成本。
