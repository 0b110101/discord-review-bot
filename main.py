import os
import requests
import feedparser

# 1. 配置 RSS 源
RSS_FEEDS = [
    # 1. OpenCritic 官方 API 原生 XML 源（不会被防爬虫误杀）
    "https://api.opencritic.com/api/feed/rss",
    
    # 2. Metacritic 官方游戏评测 Feed（备用 OpenCritic，稳定解禁）
    "https://www.metacritic.com/rss/game",
    
    # 3. GameSpot 官方评测 Feed（IGN 被截胡时的完美大媒体替代）
    "https://www.gamespot.com/feeds/reviews/"
]

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
HISTORY_FILE = "sent_history.txt"

if not WEBHOOK_URL:
    print("错误: 未找到 DISCORD_WEBHOOK 环境变量，请检查 GitHub Secrets 设置!")
    exit(1)

# 加载已发送的历史记录
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        sent_ids = set(line.strip() for line in f if line.strip())
else:
    sent_ids = set()

new_sent_ids = set(sent_ids)

# 伪装成完整的 Chrome 浏览器，防止被防爬虫机制拦下返回 HTML
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'application/rss+xml, application/xml, text/xml, text/html, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Cache-Control': 'no-cache'
}

for feed_url in RSS_FEEDS:
    print(f"\n正在抓取源: {feed_url}")
    try:
        # 先用 requests 下载源码，解决 HTTP 拦截问题
        response = requests.get(feed_url, headers=HEADERS, timeout=15)
        
        if response.status_code != 200:
            print(f"❌ 请求失败，HTTP 状态码: {response.status_code}")
            continue

        # 将下载的文本喂给 feedparser
        feed = feedparser.parse(response.content)

        # 如果有 bozo 标记但解析出了内容，只打印警告，不中断流程
        if feed.bozo:
            print(f"⚠️ RSS 语法存在瑕疵 (已忽略): {feed.bozo_exception}")

        entries = feed.entries
        print(f"成功解析到 {len(entries)} 条内容。")

        if not entries:
            print("⚠️ 未能提取到任何条目，跳过此源。")
            continue

        # 从旧到新遍历条目
        for entry in reversed(entries):
            title = getattr(entry, 'title', None)
            link = getattr(entry, 'link', None)
            guid = getattr(entry, 'id', link)

            if not guid or not title:
                continue

            if guid in sent_ids:
                continue

            print(f"发现新内容: {title}")

            # 构造 Discord 卡片
            payload = {
                "username": "评分解禁播报",
                "avatar_url": "https://i.imgur.com/8N48xJ3.png",
                "embeds": [
                    {
                        "title": title,
                        "url": link,
                        "color": 15258703,  # 金黄色
                        "footer": {
                            "text": "OpenCritic / IGN Reviews • 媒体评分解禁"
                        }
                    }
                ]
            }

            # 发送到 Discord
            res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
            if res.status_code in [200, 204]:
                print(f"✅ 成功发送到 Discord: {title}")
                new_sent_ids.add(guid)
            else:
                print(f"❌ 发送到 Discord 失败, 状态码: {res.status_code}, 返回: {res.text}")

    except Exception as e:
        print(f"抓取 {feed_url} 发生异常: {e}")

# 保存已发送的历史记录
with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    for item_id in new_sent_ids:
        f.write(f"{item_id}\n")

print("\n任务完成，历史记录已更新。")
