#!/usr/bin/env python3
"""
Smart Daily News - 智能日报推送
支持多种推送方式：dialog/email/qq/feishu/wechat/dingtalk/telegram/webhook
"""

import os
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def load_env(filepath=".env"):
    """
    手动加载 .env 文件（不依赖 python-dotenv）
    
    Args:
        filepath (str): .env 文件路径
    """
    if not os.path.exists(filepath):
        print(f"⚠️  未找到 .env 文件，请先运行 python install.py")
        return
    
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            # 解析 KEY=VALUE
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()


# 加载 .env 配置
load_env()

# 从 .env 读取配置
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
EMAIL_AUTH_CODE = os.getenv("EMAIL_AUTH_CODE")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.qq.com")  # 默认 QQ 邮箱
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
DISPLAY_MODE = os.getenv("DISPLAY_MODE", "1")  # 1=纯文本, 2=精美HTML

# 新增配置项
SEND_TIME = os.getenv("SEND_TIME", "07:00")
NEWS_KEYWORDS = os.getenv("NEWS_KEYWORDS", "科技,财经")
PUSH_METHOD = os.getenv("PUSH_METHOD", "dialog")  # 默认推送到当前对话框
PUSH_WEBHOOK_URL = os.getenv("PUSH_WEBHOOK_URL", "")

# API 地址
QUOTE_API = "https://api.quotable.io/random"
IPINFO_API = "https://ipinfo.io/json"


def get_quote():
    """
    获取每日一言（名人名言或金句）
    
    Returns:
        str: 一句名言或金句
    """
    try:
        response = requests.get(QUOTE_API, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return f"{data['content']} —— {data['author']}"
        else:
            # 兜底：返回默认名言
            return "生活不是等待风暴过去，而是学会在雨中跳舞。—— 佚名"
    except Exception as e:
        print(f"❌ 获取名言失败: {e}")
        return "每一天都是新的开始！"


def get_country_by_ip():
    """
    通过 IP 地址判断用户所在国家
    
    Returns:
        str: 国家代码（如 'CN' 表示中国，'US' 表示美国）
    """
    try:
        response = requests.get(IPINFO_API, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get("country", "CN")  # 默认中国
        else:
            return "CN"  # 兜底：默认中国
    except Exception as e:
        print(f"❌ 获取 IP 信息失败: {e}")
        return "CN"  # 兜底：默认中国


def fetch_news(country=None, keywords=None):
    """
    获取新闻头条（模拟数据，实际使用时可替换为真实 API）
    
    Args:
        country (str): 国家代码，如 'CN' 或 'US'
        keywords (str): 关键词，逗号分隔（暂未实现过滤）
        
    Returns:
        list: 新闻列表，每个元素是 dict (title, description, url)
    """
    # 模拟新闻数据
    mock_news = [
        {
            "title": "科技头条：AI 取得新突破",
            "description": "人工智能领域最新研究成果，性能提升显著。",
            "url": "https://example.com/news/ai-breakthrough"
        },
        {
            "title": "财经动态：全球市场回暖",
            "description": "股市今日表现强劲，投资者信心增强。",
            "url": "https://example.com/news/market-recovery"
        },
        {
            "title": "健康生活：运动有益身心健康",
            "description": "最新研究表明，规律运动可显著改善生活质量。",
            "url": "https://example.com/news/health-benefits"
        }
    ]
    
    # 实际使用时可调用真实 API，例如：
    # api_key = os.getenv("NEWS_API_KEY")
    # url = f"https://newsapi.org/v2/top-headlines?country={country.lower()}&apiKey={api_key}"
    # response = requests.get(url)
    # return response.json()["articles"][:3]
    
    return mock_news


def generate_text_content(news_list, quote):
    """
    生成纯文本内容（极简模式）
    
    Args:
        news_list (list): 新闻列表
        quote (str): 每日一言
        
    Returns:
        str: 纯文本内容
    """
    content = f"每日新闻日报 - {datetime.now().strftime('%Y-%m-%d')}\n\n"
    
    # 添加新闻
    content += "今日头条：\n"
    for i, news in enumerate(news_list, 1):
        content += f"{i}. {news['title']}\n"
        content += f"   {news['description']}\n"
        content += f"   链接：{news['url']}\n\n"
    
    # 添加每日一言
    content += f"💡 每日一言：\n{quote}\n"
    
    return content


def generate_html_content(news_list, quote):
    """
    生成 HTML 内容（精美排版模式）
    
    Args:
        news_list (list): 新闻列表
        quote (str): 每日一言
        
    Returns:
        str: HTML 内容
    """
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>每日新闻日报</title>
    <style>
        body {{
            font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
            margin-bottom: 30px;
        }}
        .news-item {{
            background: #f9f9f9;
            border-left: 4px solid #3498db;
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 4px;
        }}
        .news-item h2 {{
            margin-top: 0;
            color: #2980b9;
        }}
        .quote {{
            font-style: italic;
            color: #7f8c8d;
            border-left: 4px solid #e74c3c;
            padding-left: 15px;
            margin-top: 30px;
            padding-top: 15px;
            padding-bottom: 15px;
        }}
        a {{
            color: #3498db;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📰 每日新闻日报 - {datetime.now().strftime('%Y年%m月%d日')}</h1>
"""
    
    # 添加新闻
    for news in news_list:
        html += f"""
        <div class="news-item">
            <h2>{news['title']}</h2>
            <p>{news['description']}</p>
            <p><a href="{news['url']}" target="_blank">📖 阅读全文</a></p>
        </div>
"""
    
    # 添加每日一言
    html += f"""
        <div class="quote">
            <p>💡 每日一言：</p>
            <p>{quote}</p>
        </div>
    """
    
    html += """
    </div>
</body>
</html>
"""
    
    return html


def push_content(news_list, quote):
    """
    根据 PUSH_METHOD 配置，将日报内容推送到对应渠道
    
    Args:
        news_list (list): 新闻列表
        quote (str): 每日一言
    """
    # 根据显示模式生成内容
    if DISPLAY_MODE == "1":
        content = generate_text_content(news_list, quote)
    else:
        content = generate_html_content(news_list, quote)
    
    # 根据推送方式选择渠道
    if PUSH_METHOD == "dialog":
        # 当前对话框：直接打印到控制台
        print("=" * 60)
        print(content)
        print("=" * 60)
        print("✅ 日报已打印到当前对话框")
    
    elif PUSH_METHOD == "email":
        # 发送到邮箱
        send_email(content)
    
    elif PUSH_METHOD in ["qq", "feishu", "wechat", "dingtalk", "telegram"]:
        # 推送到各大平台 Bot（暂未实现，提示用户）
        print(f"⚠️  推送方式 '{PUSH_METHOD}' 暂未实现")
        print(f"   内容已生成，请手动配置 {PUSH_METHOD} Bot 集成")
        print("\n生成的内容：")
        print(content)
    
    elif PUSH_METHOD == "webhook":
        # 推送到自定义 Webhook 地址
        if not PUSH_WEBHOOK_URL:
            print("❌ 未配置 PUSH_WEBHOOK_URL，无法使用 webhook 推送")
            return
        try:
            response = requests.post(
                PUSH_WEBHOOK_URL,
                json={"text": content},
                timeout=10
            )
            if response.status_code == 200:
                print(f"✅ 已推送到 webhook: {PUSH_WEBHOOK_URL}")
            else:
                print(f"❌ Webhook 推送失败: HTTP {response.status_code}")
        except Exception as e:
            print(f"❌ Webhook 推送失败: {e}")
    
    else:
        print(f"❌ 不支持的推送方式: {PUSH_METHOD}")
        print("   支持的推送方式：dialog/email/qq/feishu/wechat/dingtalk/telegram/webhook")


def send_email(content):
    """
    发送邮件（支持纯文本和 HTML 两种模式）
    
    Args:
        content (str): 邮件内容（纯文本或 HTML）
    """
    # 创建邮件对象
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"每日新闻日报 - {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = EMAIL_RECEIVER
    msg["To"] = EMAIL_RECEIVER
    
    # 根据显示模式设置邮件内容类型
    if DISPLAY_MODE == "1":
        part = MIMEText(content, "plain", "utf-8")
    else:
        part = MIMEText(content, "html", "utf-8")
    
    msg.attach(part)
    
    # 发送邮件（使用配置的 SMTP 服务器）
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_RECEIVER, EMAIL_AUTH_CODE)
            server.send_message(msg)
            print("✅ 邮件发送成功！")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")


def main():
    """
    主函数：协调获取名言、获取新闻、推送日报
    """
    print("🚀 开始生成每日新闻日报...")
    
    # 1. 获取每日一言
    print("📝 正在获取每日一言...")
    quote = get_quote()
    print(f"✅ 获取到名言：{quote}")
    
    # 2. 判断国家（智能定位）
    print("🌍 正在检测您的地理位置...")
    country = get_country_by_ip()
    print(f"✅ 检测到您位于：{country}")
    
    # 3. 获取新闻
    print("📰 正在获取新闻...")
    news_list = fetch_news(country=country, keywords=NEWS_KEYWORDS)
    print(f"✅ 获取到 {len(news_list)} 条新闻")
    
    # 4. 推送日报
    print(f"📤 正在推送到：{PUSH_METHOD}")
    push_content(news_list, quote)
    
    print("🎉 每日新闻日报处理完成！")


if __name__ == "__main__":
    main()
