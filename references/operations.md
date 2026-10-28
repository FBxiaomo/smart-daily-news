# 部署与运维

适用单台 Windows / Linux 主机上的个人或小团队。SQLite 与进程锁放在本地磁盘，不要让多台主机通过网络共享目录共同运行。多个独立简报使用独立配置和数据目录。

## 工作台与定时

```bash
python main.py --config /absolute/path/config.toml studio
```

页面只监听本机地址。按界面选择星期、每天一个或多个时间，保存设置后点击“启动定时”。仅保存时间不会启动调度。工作台中的调度运行在该工作台服务进程内，主机和服务都需要保持在线；关闭浏览器标签不代表停止服务。需要修改设置或结束服务时，先点击“停止”，等待当前任务结束并显示已停止，再在终端按 `Ctrl+C`。环境变量会优先于 .env，修改后如有覆盖请检查进程环境。

同一状态目录有调度锁，避免多个进程同时运行调度；也可以不用工作台中的调度，而选择独立进程：

```bash
python main.py --config /absolute/path/config.toml schedule --send
```

或让系统每 5 分钟执行一次到期检查：

```bash
python main.py --config /absolute/path/config.toml schedule --send --once
```

三种方式选择一种。系统任务需使用虚拟环境 python 与配置文件的完整路径，并安排常驻主机。默认每 30 秒检查，按 `briefing.timezone`、所选星期和时间判断；如有多个时间，每个时间有独立期次。程序重启后只在当天 `catch_up_hours` 窗口内处理最新一个符合条件的时间，避免补发大量积压消息。

生成失败和明确可重试的投递失败按 `retry_minutes` 与 `max_attempts` 控制；无符合条件的新文章时不会生成空日报。认证失败、重试次数耗尽或结果不确定需要检查状态并处理。

示例 systemd 单元（替换路径及用户）：

```ini
[Unit]
Description=Smart Daily News
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=dailynews
WorkingDirectory=/opt/smart-daily-news
ExecStart=/opt/smart-daily-news/.venv/bin/python /opt/smart-daily-news/main.py --config /var/lib/smart-daily-news/config.toml schedule --send
Restart=on-failure
RestartSec=30
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=multi-user.target
```

## Docker

仓库提供 Dockerfile 与 compose.yaml，使用非 root 用户和只读根文件系统。先初始化并配置、确认 data 和 output 目录由 UID 10001 可写：

```bash
mkdir -p data output
sudo chown 10001:10001 data output
docker compose build
docker compose run --rm daily-news doctor
docker compose run --rm daily-news preview --no-ai
docker compose up -d
```

Compose 默认运行独立调度器，配置默认的 `state_dir="data"`、`output_dir="output"` 与挂载一致。不要将 .env 打包进镜像；工作台与该常驻调度选择其一。实际 Docker 及目标平台的运行情况以部署环境验证为准。

## 渠道和投递状态

邮件、企微群机器人、飞书群机器人、Server酱 Turbo 和通用 Webhook 可独立启用，失败和结果分开记录。机器人内容模式及平台限制见 [通知说明](notifications.md)。

```bash
python main.py status
python main.py retry --send --edition daily-2026-10-25
```

- pending：待投递。
- sending：正在提交；若进程中断，下次恢复为 uncertain。
- sent：平台或服务器已接受；不等于最终用户一定收到。同一期成功渠道不重复发送。
- failed：明确失败，可重试错误按间隔有限重试；修复后可显式 retry。
- uncertain：可能已收到，不自动重发；retry 不会绕过该状态。

核对接收端后选择其中一种处理：

```bash
python main.py resolve --edition daily-2026-10-25 --channel webhook --action delivered --note "接收端日志确认已保存"
python main.py resolve --edition daily-2026-10-25 --channel webhook --action retry --note "接收端确认未收到"
python main.py retry --send --edition daily-2026-10-25
```

`resolve` 本身不发送。SMTP 部分收件人被拒绝也标为 uncertain；如果不能核实接收结果，重发可能产生重复。期次在投递前保存固定快照；重试复用原内容。内容配置或目标变化时不能静默把旧快照投递到新目标：恢复原配置重试，或有意创建新的期次。

## 历史、回顾与备份

本地历史、收藏默认长期保留，不会自动过期。工作台支持手动搜索、收藏和清理；未完成的期次不能删除。七日回顾仅依据本地近 168 小时的记录，默认不调用 AI；用户选择 AI 可能产生 API 费用，发送需要手动确认。详见 [历史与回顾](archive.md)。

```bash
python main.py backup --output backups/daily-news-2026-10-25.sqlite3
```

使用 SQLite 备份 API 保存数据库，包括快照、投递状态、历史、收藏、来源缓存和操作记录；已有目标文件不会覆盖。另行备份对应 config.toml、.env 和需要保留的导出文件，并妥善保管。恢复前先停止所有调度，再恢复完整数据；不要在运行中直接替换数据库或只复制有活动 WAL 的主文件。

运行日志保存在 state_dir/run.log 并轮转。升级前先备份，安装锁定依赖并预览，再启动调度；旧配置中缺失的新增字段使用兼容默认值，新配置默认全选类型。正式使用前，以自己的模型、邮箱、机器人和网络环境完成投递与持续运行验收。
