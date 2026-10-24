# DeepSeek / 千问配置

程序只发送本期入选的标题、发布时间、来源名称和 RSS 摘要。不会发送邮箱配置、历史聊天、密钥或本地文件。
高质量内容首先依赖可信来源和材料完整性。模型只能整合输入材料，不能替代事实核查。
每期一次批量调用，不自动重复失败的付费请求。重新生成失败的一期可能再次产生调用成本；
已保存快照的补发不会再次调用模型。预览会单独调用模型，需要免费预览时使用 preview --no-ai。

## DeepSeek

将 config.toml 的 [ai] 部分对应字段设为：

```toml
[ai]
enabled = true
provider = "deepseek"
base_url = "https://api.deepseek.com"
model = "deepseek-v4-pro"
api_key_env = "DEEPSEEK_API_KEY"
json_mode = true
require_success = true
```

在同目录 .env 中设置 DEEPSEEK_API_KEY，或通过进程环境注入。
也可以选择服务商控制台当前提供的其他文本模型。默认禁用思考模式，使用非流式结构化输出。
模型名称按服务商实际可用列表调整。

## 千问 AI 平台 / 阿里云百炼

```toml
[ai]
enabled = true
provider = "qwen"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
model = "qwen-plus"
api_key_env = "DASHSCOPE_API_KEY"
json_mode = true
require_success = true
```

把 DASHSCOPE_API_KEY 放入 .env。BASE_URL 与密钥地域必须匹配。
如果控制台提供业务空间专属域名，请直接使用控制台给出的 BASE_URL，保留 /compatible-mode/v1。
程序对千问请求设置 enable_thinking=false，使非流式 JSON 模式行为明确。
可替换为账户已开通、支持非思考 JSON 输出的更高质量模型。

同一份运行配置选择一家。需要对比两家效果时，使用两个配置目录，各自有独立的 .env、state_dir 和 output_dir，
分别运行 preview；不要同时启用两份定时投递，除非你确实需要两份日报。
不会未经配置自动切换到另一家付费服务。

## 质量与费用开关

- require_success=true：AI 不可用、引用无效或输出截断时停止本期投递。
- require_success=false：改用来源摘要，并在日报中明确标注降级原因。
- max_input_chars：模型输入 JSON 的字符预算，超限拒绝调用，不截断 JSON。
- max_output_tokens：回复预算；不足时拒绝使用被截断的内容。
- max_stories：减少数量有助于把预算用在更充分的整合上。
- 每条要点必须引用同一事件的输入文章 ID；校验只能保证引用存在，不能证明表述语义必然正确。
- 报告记录服务商返回的 token 使用量，不估算或承诺价格。

## 官方接口依据

于 2026-10-25 核对：

- [DeepSeek 接口](https://api-docs.deepseek.com/)
- [DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)
- [DeepSeek 思考模式开关](https://api-docs.deepseek.com/guides/thinking_mode/)
- [千问兼容接口与地域](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [千问结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
