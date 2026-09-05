import xml.etree.ElementTree as ET
import json
import os
import requests

# 1. 配置你的 RSS 源 (可以添加多个)
RSS_FEEDS = [
    "https://opencritic.com/rss",                           # OpenCritic 解禁
    "https://feeds.feedburner.com/ign/all-reviews"         # IGN 评测
]

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
HISTORY_FILE = "sent_history.txt"

# 加载已发送的历史记录，避免重复推送
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
        sent_ids = set(line.strip() for line in f)
else:
    sent_ids = set()

new_sent_ids = set(sent_ids)

for feed_url in RSS_FEEDS:
    try:
        resp = requests.get(feed_url, timeout=10)
        root = ET.fromstring(resp.content)
        
        # 解析 RSS 中的 Item
        items = root.findall(".//item")
        for item in reversed(items):  # 从旧到新遍历
            title = item.findtext("title")
            link = item.findtext("link")
            guid = item.findtext("guid") or link
            
            if guid in sent_ids:
                continue
                
            # 构造 Discohook 风格的卡片 Embed 消息
            payload = {
                "username": "评分解禁播报",
                "avatar_url": "https://i.imgur.com/8N48xJ3.png",
                "embeds": [
                    {
                        "title": title,
                        "url": link,
                        "color": 15258703,  # OpenCritic 经典金黄色
                        "footer": {
                            "text": "OpenCritic / IGN Reviews • 媒体评分解禁"
                        }
                    }
                ]
            }
            
            # 发送到 Discord
            requests.post(WEBHOOK_URL, json=payload)
            new_sent_ids.add(guid)
            
    except Exception as e:
        print(f"抓取 {feed_url} 失败: {e}")

# 保存推送历史
with open(HISTORY_FILE, "w") as f:
    for item_id in new_sent_ids:
        f.write(f"{item_id}\n")
