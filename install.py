#!/usr/bin/env python3
"""
Smart Daily News - 静默安装向导
直接生成默认配置，无交互式问答
"""

import os

def create_default_env():
    """
    创建默认 .env 配置文件
    """
    env_content = """# Smart Daily News 配置文件
# 由 install.py 自动生成（静默安装）

# 邮箱配置（推送方式为 email 时需要配置）
EMAIL_RECEIVER=your_email@qq.com
EMAIL_AUTH_CODE=your_auth_code
SMTP_SERVER=smtp.qq.com
SMTP_PORT=465

# 显示模式 (1=纯文本, 2=HTML)
DISPLAY_MODE=1

# 发送时间（24小时制，如 07:00）
SEND_TIME=07:00

# 新闻关键词（逗号分隔，如：科技,财经,重庆本地新闻）
NEWS_KEYWORDS=重庆本地新闻

# 推送方式：dialog/email/qq/feishu/wechat/dingtalk/telegram/webhook
PUSH_METHOD=dialog

# Webhook URL（当 PUSH_METHOD=webhook 时需要配置）
PUSH_WEBHOOK_URL=

# 安装标记（请勿修改）
SKILL_READY=true
"""
    
    with open('.env', 'w', encoding='utf-8') as f:
        f.write(env_content)
    
    return True

def main():
    """
    主函数：静默安装
    """
    print("=" * 50)
    print("   Smart Daily News - 静默安装")
    print("=" * 50)
    print()
    
    # 检查是否已存在 .env 文件
    if os.path.exists('.env'):
        print("⚠️  检测到已存在 .env 文件，将覆盖现有配置。")
        # 静默安装模式下直接覆盖，不询问
    
    print("📝 正在生成默认配置文件...")
    
    # 创建默认配置
    if create_default_env():
        print("✅ .env 文件已生成（默认配置）")
        print()
        print("=" * 50)
        print("   安装完成！Smart Daily News 已就绪。")
        print("=" * 50)
        print()
        print("当前配置如下：")
        print("显示模式：纯文本")
        print("发送时间：07:00")
        print("新闻关键词：重庆本地新闻")
        print("推送方式：当前对话框")
        print()
        print("您可以修改以下配置：")
        print("1️⃣ 显示模式：输入 1 纯文本 / 2 精美排版")
        print("2️⃣ 发送时间：输入您希望的时间（如 07:30）")
        print("3️⃣ 新闻关键词：输入感兴趣的话题")
        print("4️⃣ 推送方式：输入 dialog / email / qq / feishu / wechat / dingtalk / telegram / webhook")
        print()
        print("直接告诉我需要修改哪些，或者回复"确认"开始运行。")
    else:
        print("❌ 安装失败！")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
