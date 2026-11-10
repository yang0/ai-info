# 免费接入说明

## 方案选择

| 入口 | 费用 | 使用场景 | 必要条件 | 数据边界 |
| --- | --- | --- | --- | --- |
| OpenCLI（推荐） | 无 API 费用 | 最新动态，发现起势信号 | Node、Chrome、官方扩展、自行登录 X | Latest 标签的抽样搜索结果 |
| Agent Reach（可选） | 无 API 费用 | 已有 Agent Reach 环境 | 当前版本、Twitter 渠道、专用浏览器登录 | 当前上游默认热门排序抽样 |
| 演示 | 无 | 体验大屏、离线验证 | Python | 合成样本，不是真实新闻 |
| 规则研判 | 无 | 默认全部流程 | 无模型要求 | 确定性评分与模板分析 |
| Ollama（可选） | 无模型 API 费用 | 中文机会 / 质疑分析 | 本机算力、已下载本地模型 | 前 5 个话题，原帖在本机推理 |

不购买代理，不购买数据套餐，不提供付费 X API 或收费模型的接入入口。联网环境需能正常访问 X；免费浏览器采集的覆盖与稳定性取决于网站和当前会话。

## OpenCLI

按 [官方 README](https://github.com/jackwener/opencli#quick-start) 安装。Windows 也可选官方 OpenCLIApp；CLI-only 方式使用 `npm install -g @jackwener/opencli`。

- 在 Chrome 安装官方 Browser Bridge 扩展。
- 自行登录 X，保持 Chrome 打开。
- 多配置环境使用 `opencli profile list` 与 `opencli profile use <配置>`。
- `opencli doctor` 检查浏览器桥。
- `opencli twitter search "AI agent" --product live --exclude retweets --limit 10 -f json` 检查 X 搜索。

本服务调用固定只读命令，参数通过 argv 数组传递，不通过 shell。Windows 的 npm `.cmd` 启动器会解析到该包的 Node 入口后直接运行，查询词不会交给 `cmd.exe`。

## 已安装 Agent Reach 的用户

按 [官方安装指南](https://github.com/Panniantong/Agent-Reach/blob/main/docs/install.md) 安装当前版本。**PyPI 上同名 `agent-reach` 不是这个项目**。

官方目前的安装入口：

```bash
uv tool install https://github.com/Panniantong/Agent-Reach/releases/latest/download/agent-reach.zip
agent-reach setup
agent-reach add twitter
agent-reach login twitter
agent-reach doctor
agent-reach twitter search "AI agent" --limit 10 --backend opencli
```

登录由你在专用浏览器里完成。本项目不自动安装/升级这些外部工具，不代你登录。

选择工作台的 Agent Reach 模式后，要求统一 JSON `schema_version=1`，读取 `items[].url / author / text / published_at`。兼容 X 的 RFC 日期格式与 `/i/status/<id>` 链接，保留实际作者。缺少有效发布时间、作者或原帖链接的记录不参与趋势计算；不会用采集时间冒充发布时间。

当前 Agent Reach 的 Twitter 包装未暴露 `product=live` 参数，所以该入口为热门排序抽样。需要最新动态请选择 OpenCLI 模式。两种来源不在失败后自动切换，防止悄悄改变样本范围。

## 监控查询示例

```text
("AI agent" OR "coding agent" OR "agentic") -filter:nativeretweets
("open source" OR "model release") (AI OR LLM) -filter:nativeretweets
(OpenAI OR Anthropic OR DeepSeek OR Qwen OR Gemini) -filter:nativeretweets
lang:zh (大模型 OR 智能体 OR 开源模型)
```

网页搜索使用 `-filter:nativeretweets`。推荐从 2–3 条查询、每条 25–50 条样本、5–10 分钟间隔开始。系统最多允许 8 条查询，每条最多 100 条；采集最多 2 个查询并发。OpenCLI Latest 默认追加覆盖两小时起点日期的 `since:YYYY-MM-DD`；最终由真实发布时间裁剪到 2 小时。

同一个账号重复采集的帖子 ID 去重；同内容复制扩散也不重复计算。修改查询范围后使用新的数据命名空间，避免旧任务样本污染新榜单。开启自动扫描后，服务需要持续运行；浏览器也应保持运行。

## 常见异常

| 提示 | 处理 |
| --- | --- |
| 未安装工具 | 安装 OpenCLI 或 Agent Reach，重新打开终端再启动工作台 |
| 找不到 Windows Node 入口 | 用 npm 重装 `@jackwener/opencli`，或安装官方桌面版本 |
| 浏览器连接 / 登录问题 | `opencli doctor` 或 `agent-reach doctor`，自行在对应浏览器检查 X 登录 |
| 需要网站验证 | 自行在正常浏览器处理；程序暂停自动监控，不轮换账号或反复重试 |
| 限流 | 等待至少 1 小时冷却；减少查询、样本数或频率，不切换入口规避 |
| 时间格式不兼容 / 全部记录无效 | 更新上游工具并检查其 JSON 输出，不会写入伪造时间 |
| 本地模型失败 | 确认 `ollama list`、`OLLAMA_MODEL` 与本机端口，或关闭模型使用规则模式 |
| 首轮为冷启动 | 累积样本后再看，前窗少于 3 条或历史少于 6 条时不认定持续崛起 |

## 配置和数据

配置通过页面保存到 SQLite。`.env.example` 只是环境变量清单，不自动加载；在启动服务的终端设置变量即可。默认数据在 `data/intel.db`；备份时停止服务后复制 `data/`。

服务仅面向本机访问，不作为公网多用户服务部署。浏览器会话由上游工具管理，模型只调用本机 Ollama；没有远程上传、外部推送或付费账单功能。
