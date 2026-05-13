---
name: smart-daily-news
description: 智能日报推送 Skill，支持定时发送、智能定位（中国/美国）、个性化亮点（每日一言、双模式切换）。当用户需要设置每日新闻推送、配置邮箱和发送时间时使用。
---

# Smart Daily News Skill

智能日报推送 Skill，帮您每天自动获取并推送新闻到邮箱。

## 功能亮点

- **定时发送**：支持用户设置每天几点发送
- **智能定位**：自动检测用户 IP 或根据配置判断国家（中国推国内头条，美国推国际头条）
- **个性化**：
  - 每日一言：每次邮件底部包含一句随机的名人名言或金句
  - 双模式切换：极简纯文本 or 精美 HTML 排版

## 使用方法

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 交互式配置

运行安装脚本，按提示输入信息：

```bash
bash setup.sh
# 或在 Windows 上：
# Git Bash：bash setup.sh
# PowerShell：.\setup.ps1
```

脚本会询问：
- 接收邮箱？
- 邮箱授权码？
- 发送时间（如 08:00）？
- 订阅关键词（回车默认为"综合新闻"）？
- 显示模式（输入 1 为纯文本，输入 2 为精美排版）？

配置会写入 `.env` 文件。

### 3. 运行主程序

```bash
python main.py
```

### 4. 设置定时任务

使用 OpenClaw 的定时任务功能，或者系统的 crontab（Linux/Mac）或任务计划程序（Windows）。

## 文件说明

- `main.py`：主程序，包含获取名言、获取新闻、发送邮件等功能
- `setup.sh`：交互式安装脚本（bash）
- `setup.ps1`：交互式安装脚本（PowerShell，Windows）
- `requirements.txt`：Python 依赖
- `.env`：配置文件（由 setup 脚本生成，包含邮箱、授权码、发送时间等）

## 技术说明

- 使用 `requests` 库获取新闻和名言
- 使用 `smtplib` 发送邮件
- 使用 `python-dotenv` 读取 `.env` 配置
- 智能定位：通过 IP 地址判断国家（使用 ipinfo.io API）
- 双模式：根据 `.env` 中的 `DISPLAY_MODE` 配置生成不同格式的邮件内容
