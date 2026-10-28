# 微信、企业微信与飞书推送

这些渠道由用户自行启用，不默认发送。每个渠道使用一次独立的 HTTP 请求，
沿用日报任务的渠道状态和重试管理；只记录状态、错误类型与业务错误码，不记录密钥、Webhook 地址或响应正文。

## 企业微信群机器人

在自己的企业微信群中添加群机器人，并复制平台生成的 Webhook 地址到本机环境变量：

```dotenv
DAILY_NEWS_WECOM_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=由用户自行填写
```

配置中启用：

```toml
[wecom]
enabled = true
url_env = "DAILY_NEWS_WECOM_URL"
timeout_seconds = 20
```

该方式面向企业微信群，不等同于直接向个人微信好友或普通微信群发消息。
企业微信 Markdown 消息最多 4096 字节；内容超限时发送尽可能多的完整条目，并提示省略，
每条已展示的内容保留其对应来源链接。完整日报仍在本地及其他已配置投递渠道中。

地址必须为企业微信提供的 HTTPS 群机器人地址，不能把地址或 key 提交到代码库。
机器人在群内被移除后需要重新配置。

## 飞书群自定义机器人

将飞书自定义机器人的 Webhook 地址配置到本地：

```dotenv
DAILY_NEWS_FEISHU_URL=https://open.feishu.cn/open-apis/bot/v2/hook/由用户自行填写
# 仅当飞书机器人启用签名校验时填写。
DAILY_NEWS_FEISHU_SECRET=
```

```toml
[feishu]
enabled = true
url_env = "DAILY_NEWS_FEISHU_URL"
secret_env = "DAILY_NEWS_FEISHU_SECRET"
keyword = ""
timeout_seconds = 20
```

如果机器人在安全设置中要求关键词，在 keyword 填一个匹配词，程序将其置于消息开头。
如机器人启用了签名校验，将飞书提供的签名密钥放入上述环境变量；
程序采用平台要求的 timestamp/HMAC-SHA256/Base64 签名。
如果配置了 IP 白名单，也需要确保实际发送主机的出口 IP 已被允许。

飞书自定义机器人单次请求体不超过 20 KB；本项目把消息控制在该上限内。
消息为带原文链接的纯文本简报；邮件中的 HTML 排版、主题颜色不保证在群聊原样呈现。

官方说明：[飞书自定义机器人使用指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)。

## 个人微信：Server酱 Turbo

个人微信不提供一个通用、免配置的直接发送接口。这里可使用第三方 Server酱 Turbo：
用户访问 [Server酱官网](https://sct.ftqq.com/)，使用微信登录，按其流程绑定希望接收通知的微信通道，
然后将 **Turbo 版，以 SCT 开头**的 SendKey 填入本地环境变量：

```dotenv
DAILY_NEWS_SERVERCHAN_KEY=由用户自行填写的Turbo版SendKey
```

```toml
[serverchan]
enabled = true
sendkey_env = "DAILY_NEWS_SERVERCHAN_KEY"
timeout_seconds = 20
```

Server酱收到 Markdown 正文后，再通过用户在其后台绑定的渠道转发。
程序发送前检查 SendKey 格式，支持 Turbo；SC3（sctp）为另一产品和地址，当前未实现。
平台额度、费用、消息保留时长、渠道可用性可能改变，应以服务商当前页面为准。
此途径依赖第三方，简报内容会发送给第三方服务；不要向不可信的第三方转发敏感日报。
官方操作：[获取 SendKey 与接收微信通知](https://sct.ftqq.com/docs/getting-started/sendkey/)。

## 推送内容详略

`[push].content` 控制以上三个渠道的内容：

- `full`：尽量显示全部要点和对应来源；超过平台容量时明确标注省略。
- `summary`：每个事件显示一个较短要点及相应出处。
- `notice`：仅发送日报生成提示和事件总数。

这项选择与邮件 HTML 的布局和风格独立。平台的消息卡片和文本阅读器不会呈现全部邮件样式。

## 结果确认

企微检查返回 JSON 的 errcode、飞书与 Server酱检查 code；HTTP 200 并不自动表示业务成功。
API 返回明确失败时记录失败；明确限流可按配置有限重试。
如果连接已发出但响应超时、返回无法解析或服务器 5xx，状态为 uncertain，不自动重复发送。
应先核对群聊或 Server酱后台，再使用运维文档的 resolve 流程处理。

HTTP/平台返回成功表示平台接受请求，不能保证成员已查看、微信号已绑定或最终通知一定送达。
真实账号与最终消息展示需要使用你自己的机器人和 SendKey 验收；测试用虚拟接收端，不会向真实群或微信发送消息。
