# 部署与运维

适用单台 Windows / Linux 主机上的个人或小团队。SQLite 与进程锁放在本地磁盘，
不要让多台主机通过网络共享目录共同运行。多个独立简报使用独立配置/数据目录。

## 定时

```bash
python main.py --config /absolute/path/config.toml schedule --send
```

每 30 秒检查一次，按 briefing.timezone 和 send_at 判定。
程序重启后只在当天 catch_up_hours 窗口内补发；不在深夜补发一周积压内容。
夏令时重复时段使用同一期号去重；不存在的时刻在跳时后窗口内补发。
生成失败和明确可重试的投递失败按 retry_minutes 间隔、max_attempts 上限重试。
不确定投递、认证错误、超过上限的任务需要检查 status 并处理。
主机睡眠/离线时无法准点发送；用于稳定业务时放在持续在线主机并监控失败状态。

也可以让系统每 5 分钟执行一次 schedule --send --once；与常驻进程二选一。
Windows 任务计划程序应使用虚拟环境 python.exe 的完整路径及 config.toml 的完整路径，
设置无用户登录也可运行、失败后重试、禁止同一任务并发。
Linux 可用 systemd 管理常驻进程，或 cron 运行单次检查。

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

仓库包含 Dockerfile 和 compose.yaml，默认以非 root 用户运行，根文件系统只读。
先在宿主机执行 init，编辑配置并确保 data / output 目录由 UID 10001 可写：

```bash
mkdir -p data output
sudo chown 10001:10001 data output
docker compose build
docker compose run --rm daily-news doctor
docker compose run --rm daily-news preview --no-ai
docker compose up -d
```

配置默认路径 state_dir="data"、output_dir="output" 与 Compose 挂载一致。
不要把 .env 打包进镜像。容器构建在 CI 校验；本地未安装 Docker 时不要声称已做本机容器运行测试。

## 投递状态与恢复

```bash
python main.py status
python main.py retry --send --edition daily-2026-10-25
```

- pending：待投递。
- sending：正在提交。若进程中断，下次恢复为 uncertain。
- sent：SMTP/HTTP 接收端已接受，不会在同一期再次发送。
- failed：明确失败。可重试错误自动按间隔重试；修复配置后可显式 retry。
- uncertain：可能已经收到，不自动重复发送。retry 也不会绕过这一状态。

核对接收端后记录结果：

```bash
python main.py resolve --edition daily-2026-10-25 --channel webhook --action delivered --note "接收端日志确认已保存"
python main.py resolve --edition daily-2026-10-25 --channel webhook --action retry --note "接收端确认未收到"
python main.py retry --send --edition daily-2026-10-25
```

实际使用时在 delivered / retry 两种动作中选一个。resolve 本身不发送。
如果接收端无法核对，重新投递可能产生重复，不能宣称端到端 exactly-once。
SMTP 部分收件人拒绝也归入 uncertain，核对后自行处理已收到的人。

一期在发送前保存固定快照；重试复用原文和引用。内容配置或目标改变时，
拒绝把旧快照默默投递到新目标。恢复旧配置重试，或显式指定新的 --edition。
重新生成预览会刷新当前材料，但不会覆盖待补发的数据库快照。
预览不占用投递次数；启用 AI 的预览会产生一次模型调用。

## 备份、观察与升级

```bash
python main.py backup --output backups/daily-news-2026-10-25.sqlite3
```

使用 SQLite 备份 API，包含快照、状态、来源缓存和操作记录。文件已存在时不覆盖。
恢复时先停止调度，再把备份放回 state_dir/daily-news.sqlite3；同时恢复匹配的 config.toml 和 .env。
不要在程序运行中直接替换数据库，也不要只复制正在使用的 WAL 数据库主文件。

state_dir/run.log 滚动保存日志；status 输出最近任务与人工处理记录。
监控生成 failed、投递 failed/uncertain、超过期望时间无 complete 的情况。
保留周期由运营者决定；定期备份并清理不再需要的导出文件，避免长期无限积累。

升级先备份，然后在虚拟环境安装锁定依赖、跑测试和 preview，再重启调度。
默认分支上的代码变化不会自动修改已运行服务。
正式对外服务前应完成真实模型、实际邮箱/Webhook 接收端和持续运行验收；模拟测试不能代替这些验证。
