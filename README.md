# Smart Daily News

面向个人与小团队的自部署新闻简报 Skill。把真实 RSS/Atom 材料筛选、去重并整合成可追溯的日报，通过邮箱或通用 Webhook 投递。

当前版本：0.2.0。提供可部署的单机工作流；正式业务投用前仍需配置自己的来源与凭据，并完成实际投递和持续运行验收。

## 已实现

- **真实来源**：RSS/Atom、ETag 缓存、发布时间过滤、关键词筛选、来源优先级和来源数量限制。
- **内容整合**：链接去重、保守的同事件合并、DeepSeek / 千问可选 AI 整合；逐条保留出处和发布时间。
- **透明降级**：不配置 AI 时使用来源摘要；模型失败可停止本期，或带标记降级。没有内容时不会制造示例新闻。
- **多格式**：同一份数据输出 HTML、Markdown、纯文本和 JSON；邮件为纯文本/HTML 双格式。
- **可靠投递**：SMTP SSL/STARTTLS、通用 Webhook、固定快照、渠道独立状态、有限重试、重复启动保护。
- **运行维护**：时区与当天补发窗口、日志轮转、状态查询、人工处理不确定投递、SQLite 一致性备份。
- **部署与验证**：Windows/Linux CLI、Docker/Compose、跨系统 CI、独立 Skill 打包脚本。

## 快速开始

需要 Python 3.11+。以下操作只生成本地配置和预览，默认不发送消息。

```bash
git clone https://github.com/FBxiaomo/smart-daily-news.git
cd smart-daily-news
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe main.py init
.\.venv\Scripts\python.exe main.py doctor --online
.\.venv\Scripts\python.exe main.py preview --no-ai
```

Linux/macOS：

```bash
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python main.py init
.venv/bin/python main.py doctor --online
.venv/bin/python main.py preview --no-ai
```

输出位于 output/；打开 HTML 即可查看。默认提供三个公开订阅源作为采集示例。
init 与兼容入口 install.py 都保留已有 config.toml 和 .env。

## 配置自己的日报

编辑 config.toml：

- briefing：标题、时区、发送时间、时间范围、兴趣词、数量与目录。
- sources：自己选定的新闻源、分类、优先级。
- ai：DeepSeek、千问或其他兼容接口，默认关闭。
- email / webhook：按需启用；凭据放入 .env 或进程环境。

质量优先可启用 AI 并设置 require_success=true，避免模型失效时自动发送未整合的版本。
程序不自动选用付费服务、不调用其他项目的密钥。

完整说明：

- [来源、筛选、邮箱与 Webhook](references/configuration.md)
- [DeepSeek 与千问 AI 配置](references/ai-providers.md)
- [定时、Docker、失败恢复与备份](references/operations.md)

## 预览、发送和定时

以下 python 指已安装依赖的虚拟环境解释器。使用自定义配置时，--config 放在子命令前。

```bash
python main.py --config /path/to/config.toml preview
python main.py --config /path/to/config.toml run --send
python main.py --config /path/to/config.toml schedule --send
```

run/retry/schedule 必须带 --send 才能发送。常驻 schedule 需要主机持续在线。
同一期号重复执行不会重发已成功的渠道；重试复用已保存的内容，不重新编写日报。

```bash
python main.py status
python main.py retry --send --edition daily-2026-10-25
python main.py backup --output backups/daily-news-2026-10-25.sqlite3
```

SMTP/HTTP 超时可能发生在对方已经接受之后，此时状态为 uncertain，不自动重发。
[运维说明](references/operations.md)介绍如何核对、记录和继续处理。

## 作为 Skill 使用

仓库根目录是技能入口，包含 SKILL.md 与配套 Python 包。
在支持 Agent Skills 的宿主中安装整个目录，并让宿主能够执行 Python。
运行数据建议放在独立目录，通过 --config 指定；不要把密钥写入技能说明。

```bash
python scripts/package_skill.py
```

生成 dist/smart-daily-news.skill，包含代码、必要文档和测试，不包含 .env、私人配置或运行记录。
只复制 SKILL.md 无法获得配套脚本功能。

## 验证

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q -W error
```

离线测试覆盖 RSS/Atom、筛选/去重、引用、配置保护、快照重试、进程中断、时区、
SMTP 和 Webhook 协议分支。设置 RUN_LOCAL_HTTP_TEST=1 还会运行本机 HTTP 闭环测试，
使用临时 RSS、模型响应服务和接收端，不访问真实邮箱或付费模型。
GitHub Actions 对 Windows/Linux 的 Python 3.11–3.13 执行检查，并构建容器。

## 边界

- 内容来自订阅源提供的摘要；不穿透付费墙，也不自动抓取整篇文章。
- 同事件合并采用保守的标题相似度，无法保证跨语言事件合并。引用校验不等于事实核查。
- 通用 Webhook 不是各聊天平台专用 Bot 协议；本版本不宣称已支持那些适配器。
- 适用于受信任运营者维护的单机部署，没有多租户账号、计费或公共管理后台。
- 公开 RSS 地址不代表商业再分发授权。对外分发应使用适用的内容许可；本次升级没有变更仓库的代码授权安排。
