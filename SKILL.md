---
name: smart-daily-news
description: 配置并生成多源新闻简报，调整 27 种展示组合、主题和投递计划，管理历史、收藏与七日回顾。用于日报预览、明确授权的邮件或机器人推送、定时和故障恢复。
---

# Smart Daily News

使用真实 RSS/Atom 来源生成可追溯日报。完整技能目录包含 Python 包、本机设置页面和 references；宿主需要 Python 3.11+ 与执行脚本的能力。BASE 为本文件所在目录，CONFIG 为用户运行目录中的 config.toml。

## 准备与设置

确认用户关心的内容、样式、时间和接收渠道，沿用已给出的偏好。运行 `python BASE/main.py --config CONFIG init` 只创建缺失文件；需要时在所选虚拟环境安装 BASE/requirements.lock。

可运行 `python BASE/main.py --config CONFIG studio` 打开仅监听本机的设置页面。页面支持 3 种排版、3 种风格、3 种内容详略，29 个主题、自定义来源与关键词，以及星期、多时间和接收渠道。新配置默认选择所有主题并尽量均衡；旧配置保留原来源模式。只保存时间不会启动定时。

终端用 `python BASE/main.py --config CONFIG topics` 列出新闻类型。凭据由用户在本机页面、同目录 .env 或进程环境中提供，不写入聊天、日志或技能文件。详见 [主题与来源](references/topics.md)、[渠道配置](references/notifications.md)、[邮箱及 Webhook](references/configuration.md) 和 [AI 配置](references/ai-providers.md)。

## 预览与整理

页面中的样式预览使用标明的示例内容；“采集真实新闻预览”使用已保存的 RSS 设置，不调用 AI、不推送。命令行可用 `preview --no-ai`。保持出处及降级标记，不把示例或来源摘要说成模型生成的真实结论；无内容时报告原因。

用户可在页面搜索历史、收藏或手动清理，也可从近 7 天本地记录生成免费回顾。仅在用户主动选择时使用 AI 整合；这可能产生 API 费用。回顾不代表全网热度，见 [历史与回顾](references/archive.md)。

“AI 整合预览”使用已配置的服务，尽量翻译标题和摘要并保留原文。默认 AI 不可用时使用原文摘要，明确标注降级原因；仅在 require_success=true 时停止生成或投递。收藏不改变选题。

## 发送与定时

用户明确要求向指定且已配置的目标发送时，才执行 `run --send`、启动调度或发送周报；已有授权无需重复索取。邮件、企微群机器人、飞书、Server酱 Turbo 和 Webhook 可同时启用；机器人可选完整、摘要或提醒，超平台长度会标注省略。

确定时区、星期、时间和持续在线的主机。页面“启动定时”只在工作台服务进程运行期间生效；另可选独立 `schedule --send` 或系统周期任务，使用一种调度方式。修改设置前先停止当前调度并等待正在执行的任务结束。停止工作台前也先停止其调度。

## 检查与恢复

`doctor --online` 检查来源，不发送消息或调用模型；`status` 检查任务与各渠道。已保存的期次使用固定快照，仅重试已知失败渠道。不要把平台接受说成最终送达。

`uncertain` 表示可能已收到：先核对接收端，再用 `resolve` 记录处理决定；不得清空数据库、改期号或盲目重发来绕过去重。密钥或完整鉴权 URL 不得输出。备份、暂停及补发窗口见 [运维说明](references/operations.md)。
