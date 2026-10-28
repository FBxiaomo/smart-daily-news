# Smart Daily News

面向个人与小团队的自部署新闻简报 Skill：选择感兴趣的内容、调整日报样式、预览结果，再按自己的时间投递。项目包含本机浏览器设置页面、命令行和适用于 Agent 的技能说明。

目前升级代码位于 `codex/daily-news-studio-20261028` 独立分支。下面的克隆命令指定该分支；默认 `main` 可能仍是旧版本。

## 使用浏览器设置

需要 Python 3.11+，联网安装依赖并访问你选择的新闻源。首次启动不发送消息。

```bash
git clone --branch codex/daily-news-studio-20261028 https://github.com/FBxiaomo/smart-daily-news.git
cd smart-daily-news
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe main.py studio
```

Linux/macOS：

```bash
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python main.py studio
```

程序会在本机 `127.0.0.1` 启动设置页面并尝试打开浏览器；如未自动打开，请使用终端输出的本地地址。首次运行会生成 `config.toml` 和 `.env`，保留已存在的文件。

在页面中可按以下顺序操作：

1. **内容**：选择新闻类型、关键词、时间范围与每期条目数，也可以添加自己的 RSS/Atom 来源。
2. **外观**：切换排版、配色和内容详略，右侧会更新标为“示例预览”的样稿。
3. **真实预览**：先保存设置，再点击“采集真实新闻预览”。该预览访问 RSS，不调用 AI 或发送消息。
4. **投递**：启用需要的渠道并填写凭据，设置星期和一个或多个时间，保存后点击“启动定时”。
5. **收藏与回顾**：查看历史日报、收藏文章，或用已有记录生成近 7 天回顾。

填写并启用 AI 后，可以点击“AI 整合预览”查看整合和翻译效果，调用前会提示可能产生费用。默认尽量将标题和摘要翻译成中文，并保留原文；AI 不可用时发送原文摘要并明确标注原因。可开启“模型失败时停止本期投递”改为停止发送。收藏只用于保存和搜索，不影响以后选题。

仅保存时间并不会开始发送；必须在页面点击“启动定时”，并保持工作台服务和主机在线。关闭浏览器标签不等于停止服务；建议先点击“停止”，等待当前任务结束后再在终端按 `Ctrl+C`。需要修改设置时，也先停止定时、保存，再重新启动。可选择独立调度服务代替工作台中的调度，详见[运维说明](references/operations.md)。

## 27 种组合

三种选择相互独立：

| 维度 | 选项 |
| --- | --- |
| 排版 | 纵向要闻、卡片精选、双栏报纸 |
| 风格 | 清晨纸白、夜读深色、暖调杂志 |
| 内容详略 | 精简速览、标准阅读、深度阅读 |

共 27 种组合，用于 HTML 预览和邮件。文字、Markdown 和 JSON 也可用于读取或集成；企业微信、飞书等群机器人以平台支持的文字格式展示，不保证复现邮件配色和版式。

## 内容与整合

内置 29 种主题，覆盖国内与国际、社会、科技、AI、汽车、商业财经、科学、健康、文化、体育等。新的配置默认选择全部主题，并尽量均衡覆盖已选领域；每期数量有限、某些来源暂时无稿或不可用时，不能保证每个领域都出现。可按需取消主题、设置关键词，或添加可信的 RSS/Atom。终端运行 `python main.py topics` 可列出类型。

分类根据来源及标题、摘要中的关键词判断，可能漏判或误判；保守的标题相似度合并也不能保证跨语言新闻都能合并。正文基于来源提供的摘要，不穿透付费墙。默认不调用模型，可选择配置 DeepSeek、千问或兼容接口整合材料；模型服务可能收费，引用可追溯但不能代替事实核查。

[新闻类型与来源说明](references/topics.md) · [DeepSeek／千问配置](references/ai-providers.md)

## 接收与定时

支持同时启用邮件、企业微信群机器人、飞书群自定义机器人、Server酱 Turbo 微信通知和通用 Webhook。个人微信通过第三方 Server酱转发，需要用户自行注册并绑定微信；企业微信与飞书的机器人发到配置的群聊。密钥和地址由用户在本机页面填写，保存于本地配置环境，不放进技能包。

机器人和微信通知可选 `full`（尽量完整）、`summary`（精简要点）、`notice`（仅提示事件数）。平台有消息大小限制；内容超限会标注省略并尽量保留已展示内容的出处。服务商接受请求不等于最终用户一定已收到。

每周可选择发送日期，每天可配置多个时间；按配置时区运行。掉线恢复只处理当天补发窗口内最新的可执行时间，不补发长时间积压。渠道各自记录状态；明确失败可有限重试，不确定是否送达的结果需要先核对。只启用一个调度方式，避免多个调度进程争用。

[微信、企微与飞书配置](references/notifications.md) · [来源、邮箱与 Webhook](references/configuration.md) · [定时与故障恢复](references/operations.md)

## 历史、收藏与七日回顾

预览、已生成的日报和收藏保存在本地，默认长期保留，可搜索或手动清理；未完成的投递不能直接删除。七日回顾根据最近 168 小时的本地历史生成，免费模式整理标题、来源和摘要，不代表全网热度。用户也可以主动勾选 AI 整合；预览不会自动推送，发送需要手动确认。[历史与回顾说明](references/archive.md)

## 命令行与 Skill

```bash
python main.py init                 # 仅创建缺失配置
python main.py topics               # 查看新闻类型
python main.py doctor --online      # 检查配置和来源，不调用 AI、不发送
python main.py preview --no-ai      # 真实来源预览，不发送
python main.py run --send           # 向已启用渠道发送
python main.py schedule --send      # 常驻调度
python main.py status                # 查看状态
```

`python` 应指向已安装锁定依赖的虚拟环境。自定义配置路径可在子命令之前传入 `--config /path/to/config.toml`。发送、重试和常驻调度必须显式加 `--send`。

作为 Agent Skill 使用时，安装包含 `SKILL.md`、Python 包、页面资源和 references 的完整目录；宿主需要 Python 及执行脚本的能力。使用 `python scripts/package_skill.py` 创建 `dist/smart-daily-news.skill`；技能包不包含本机配置、密钥和历史数据。

旧配置无需重新初始化：没有 `[topics]` 区块的旧文件继续使用原有来源；缺省的新版选项按兼容默认值加载。更新依赖并备份配置、`.env` 和数据后重启服务。

## 验证与使用边界

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q -W error
```

自动测试包含来源筛选、样式、调度、归档和模拟投递；真实邮箱、群机器人、Server酱和模型调用仍需用自己的账号端到端验收。设置 `RUN_LOCAL_HTTP_TEST=1` 可运行本机 HTTP 闭环测试。

本项目为受信任运营者维护的单机自部署工具，不提供多租户账号、计费或公共订阅后台。公开 RSS 地址不自动授予商业再分发许可，商业使用请确认所选来源的适用许可并保留出处。
