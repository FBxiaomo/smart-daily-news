# 配置与内容策略

所有相对路径相对于 config.toml 所在目录解析。进程环境优先于同目录 .env。
init 不覆盖已有文件。修改配置或密钥后，重启常驻调度进程。

## 新闻源

使用 [[sources]] 配置 RSS 2.0 / Atom；每个源包含唯一 id、name、url、category、weight，可设 enabled=false。
示例中提供 IT之家、Solidot、BBC World 的公开订阅地址，方便验证采集。它们不是内容转售授权。
商业分发请配置自有、已获授权或许可适用的来源，并保留出处。程序不会抓取付费墙或复制全文。

weight 是运营者明确设置的优先级，范围 0.1–5；它不是自动测得的可信度分数。
优先选原始公告、官方博客和专业机构等材料充分的来源，再按受众添加媒体订阅。
付费提供商若支持 RSS/Atom，可直接接入其获授权订阅地址；本版本没有实现任意新闻 JSON API 的通用转换。

采集会检查发布时间、丢弃过期及明显未来文章，默认排除无日期文章。
include_keywords / exclude_keywords 对标题和摘要执行不区分大小写的包含匹配；包含列表内任一词命中即可。
多个类别需要通过各源的 category 表达，代码不根据 IP 推测用户的兴趣或位置。

同一 URL 清除常见追踪参数后去重，保留业务查询参数。近似标题采取保守合并，
不同版本号或数字的标题不会轻易合并。每个事件最多 4 个来源；跨语言同一事件不保证自动合并。
max_per_source 限制每家来源作为主条目的数量，排序结合配置权重、时效、关键词命中和报道来源数。
完整投递成功后，链接在 dedupe_days 天内不重复入选；部分投递成功的一期仍需处理未完成渠道。

## 邮箱

```toml
[email]
enabled = true
host = "smtp.qq.com"
port = 465
security = "ssl"
```

.env 中配置：

```dotenv
DAILY_NEWS_SMTP_USER=your-sender@example.com
DAILY_NEWS_SMTP_PASSWORD=your-smtp-app-password
DAILY_NEWS_EMAIL_FROM=your-sender@example.com
DAILY_NEWS_EMAIL_TO=recipient@example.com
```

使用邮箱服务商提供的 SMTP 授权码。支持 SSL 和 STARTTLS，始终验证证书。
收件人与登录账号分开；多个收件人用逗号分隔，会出现在同一封邮件的 To 中。
邮件包含纯文本和 HTML，服务端接收后记为 sent；不等于保证送达收件箱，仍需检查退信/垃圾箱。
若部分收件人被拒绝，结果记为 uncertain，需核对后处理，避免重复投递已成功的人。

## 通用 Webhook

```toml
[webhook]
enabled = true
```

设置 DAILY_NEWS_WEBHOOK_URL，可选 DAILY_NEWS_WEBHOOK_TOKEN 和 DAILY_NEWS_WEBHOOK_SECRET。
发送 POST JSON，字段：version、event、id、text、markdown、html、digest。
event 为 daily_news.digest；id 为期号；digest 保留结构化事件、来源、采集结果和 AI 使用量。
接收端任意 2xx 表示已接受；3xx 不跟随，以免把认证信息发送到其他地址。
这不是飞书、钉钉、企业微信专用机器人协议，接入这些平台需要自行转换载荷。

请求携带稳定的 Idempotency-Key。接收端应以该键实现幂等保存，重复请求返回已处理结果。
若配置签名密钥，增加 X-Daily-News-Timestamp 和 X-Daily-News-Signature。
签名算法：sha256= + HMAC-SHA256(secret, timestamp + "." + 原始请求体字节) 的十六进制值。
校验签名时比较原始字节，使用常量时间比较，并在接收端设置时间窗口及重放防护。

## 网络与数据

默认只允许 HTTPS，拒绝 URL 内嵌账号密码以及本机、内网/保留 IP；每次重定向重新检查。
只有在你控制的私有部署中，才考虑 allow_http / allow_private_hosts。
该应用面向受信任运营者编辑配置，不是接收陌生用户任意 URL 的公共抓取服务；
DNS、代理及网络出口仍由部署环境管理，不能把应用层检查视为多租户隔离边界。

RSS 使用 ETag/Last-Modified 条件请求，限制响应大小，失败重试有上限。
日志不记录密钥、Webhook URL、服务商响应正文或模型推理内容。
生成的正文、收件目标指纹和投递状态保存在本地；敏感来源的输出目录需控制访问权限。
