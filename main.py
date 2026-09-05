import os
import requests
import feedparser

# 1. 配置 RSS 源
RSS_FEEDS = [
    "https://opencritic.com/rss",                           # OpenCritic 解禁
    "https://feeds.feedburner.com/ign/all-reviews"         # IGN 评测
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

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

for feed_url in RSS_FEEDS:
    print(f"\n正在抓取源: {feed_url}")
    try:
        # 使用 feedparser 直接解析 URL，自动容错不规范的 XML
        feed = feedparser.parse(feed_url, request_headers=headers)
        
        if feed.bozo and not feed.entries:
            print(f"⚠️ RSS 解析警报: {feed.bozo_exception}")
            continue

        print(f"成功解析到 {len(feed.entries)} 条内容。")

        # 从旧到新遍历条目
        for entry in reversed(feed.entries):
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
