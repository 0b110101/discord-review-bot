import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# Configuration
# ============================================================

API_BASE = "https://opencritic-api.p.rapidapi.com"

API_KEY = os.environ.get("OPENCRITIC_API_KEY")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

STATE_FILE = Path("data/state.json")

PRE_RELEASE_DAYS = 14
POST_RELEASE_DAYS = 3

HEADERS = {
    "X-RapidAPI-Key": API_KEY,
    "X-RapidAPI-Host": "opencritic-api.p.rapidapi.com",
}

REQUEST_DELAY = 0.3

# 卡片最终导出尺寸（16:9 标准比例）
CARD_WIDTH = 1200
CARD_HEIGHT = 675

# 权威媒体优先级列表（按业内公认影响力及官方主页常驻媒体排序）
POPULAR_OUTLETS = [
    "IGN",
    "GameSpot",
    "PC Gamer",
    "Eurogamer",
    "TheGamer",
    "GamesRadar+",
    "Polygon",
    "Kotaku",
    "Game Informer",
    "Destructoid",
    "VGC",
    "Video Games Chronicle",
    "VG247",
    "Giant Bomb",
    "Edge Magazine",
    "Rock, Paper, Shotgun",
    "Easy Allies",
    "Push Square",
    "Nintendo Life",
    "Pure Xbox",
    "Shacknews",
    "Hardcore Gamer",
    "Twinfinite",
    "Screen Rant",
    "Inverse",
    "Wccftech",
    "Metro GameCentral",
    "Digital Trends",
    "Siliconera",
    "DualShockers",
]


# ============================================================
# Fonts
# ============================================================

def get_font(size, bold=False):
    candidates = []
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "arialbd.ttf",
            "msyhbd.ttc",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "arial.ttf",
            "msyh.ttc",
        ]

    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

    return ImageFont.load_default()


# ============================================================
# Score normalization & Formatting
# ============================================================

def normalize_score(score):
    if score is None:
        return None
    try:
        val = int(round(float(score)))
        # OpenCritic API 使用 -1 表示尚未出分 (Unrated)
        if val < 0:
            return None
        return val
    except (TypeError, ValueError):
        return None


def format_number(value):
    try:
        value = float(value)
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


# ============================================================
# Slug & URL
# ============================================================

def make_slug(name):
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", str(name))
    name = "".join(char for char in name if not unicodedata.combining(char))
    name = name.lower().replace("'", "").replace("’", "")
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def get_opencritic_url(game):
    game_id = game.get("id")
    slug = make_slug(game.get("name", "game"))
    return f"https://opencritic.com/game/{game_id}/{slug}"


# ============================================================
# API
# ============================================================

def api_get(path):
    url = API_BASE + path
    print(f"GET {url}")
    response = requests.get(url, headers=HEADERS, timeout=30)
    print(f"HTTP {response.status_code}")
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        print("ERROR: API did not return JSON.")
        print(response.text[:1000])
        sys.exit(1)


def normalize_games(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "games", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    print("ERROR: Could not find game list in API response.")
    sys.exit(1)


# ============================================================
# State
# ============================================================

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = STATE_FILE.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    temp_file.replace(STATE_FILE)


def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "games": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("initialized", False)
        state.setdefault("games", {})
        return state
    except Exception as e:
        print(f"ERROR reading state.json: {e}")
        sys.exit(1)


# ============================================================
# Dates & Window
# ============================================================

def parse_datetime(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def get_release_date(game):
    for field in ("firstReleaseDate", "releaseDate"):
        parsed = parse_datetime(game.get(field))
        if parsed:
            return parsed
    return None


def is_in_monitor_window(game):
    release_date = get_release_date(game)
    if release_date is None:
        return False
    now = datetime.now(timezone.utc)
    start = release_date - timedelta(days=PRE_RELEASE_DAYS)
    end = release_date + timedelta(days=POST_RELEASE_DAYS)
    return start <= now <= end


# ============================================================
# Discovery & Detail
# ============================================================

def get_candidate_games():
    candidates = {}
    endpoints = ["/game/upcoming", "/game/recently-released"]
    for endpoint in endpoints:
        print(f"Discovering games: {endpoint}")
        data = api_get(endpoint)
        games = normalize_games(data)
        for game in games:
            game_id = game.get("id")
            if game_id is not None:
                candidates[str(game_id)] = game
        time.sleep(REQUEST_DELAY)
    print(f"Unique candidate games: {len(candidates)}")
    return list(candidates.values())


def get_game(game_id):
    return api_get(f"/game/{game_id}")


# ============================================================
# Review data & 权威媒体加权处理
# ============================================================

def unwrap_reviews(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "reviews", "results", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return val
    return []


def get_reviews(game_id):
    try:
        data = api_get(f"/review/game/{game_id}")
        return unwrap_reviews(data)
    except Exception as e:
        print(f"WARNING: Could not get reviews: {e}")
        return []


def get_review_publication(review):
    outlet = review.get("Outlet") or review.get("outlet")
    if isinstance(outlet, dict):
        return outlet.get("name")
    if isinstance(outlet, str):
        return outlet.strip()
    return None


def get_review_score_display(review):
    score = review.get("score") if review.get("score") is not None else review.get("rating")
    if score is None:
        return None

    score_format = review.get("ScoreFormat")
    if isinstance(score_format, dict):
        base = score_format.get("base")
        if base is not None:
            try:
                num_score = float(score)
                num_base = float(base)
                if num_base != 100 and num_score > num_base:
                    display_score = (num_score / 100.0) * num_base
                else:
                    display_score = num_score
                return f"{format_number(display_score)} / {format_number(num_base)}"
            except (TypeError, ValueError):
                pass

    return format_number(score)


def get_outlet_rank(outlet_name):
    """计算媒体知名度优先级，越小优先级越高"""
    if not outlet_name:
        return 999
    name_clean = outlet_name.strip().lower()
    for rank, target in enumerate(POPULAR_OUTLETS):
        target_clean = target.lower()
        if target_clean == name_clean:
            return rank
        if target_clean in name_clean:
            return rank + 40
    return 999


def get_top_reviews(reviews):
    """
    按媒体影响力权重筛选出前 5 家媒体评分
    优先展现 IGN, GameSpot, PC Gamer, Eurogamer 等一线媒体
    """
    seen = set()
    candidates = []

    for review in reviews:
        if not isinstance(review, dict):
            continue

        publication = get_review_publication(review)
        score = get_review_score_display(review)

        if not publication or not score:
            continue

        norm_name = publication.strip().lower()
        if norm_name in seen:
            continue
        seen.add(norm_name)

        outlet = review.get("Outlet") or {}
        is_top = outlet.get("isTopCritic", False) or review.get("isTopCritic", False)
        rank = get_outlet_rank(publication)

        candidates.append({
            "publication": publication,
            "score": score,
            "rank": rank,
            "is_top": 1 if is_top else 0
        })

    # 优先排序：白名单权重 -> 是否为 Top Critic
    candidates.sort(key=lambda x: (x["rank"], -x["is_top"]))

    usable = []
    for item in candidates[:5]:
        usable.append({
            "publication": item["publication"],
            "score": item["score"]
        })

    return usable


# ============================================================
# Image helpers
# ============================================================

def get_image_url(game):
    images = game.get("images")
    if not isinstance(images, dict):
        return None

    image_objects = [
        images.get("banner"),
        images.get("masthead"),
        images.get("box"),
        images.get("square"),
    ]

    for image_data in image_objects:
        if not isinstance(image_data, dict):
            continue
        path = image_data.get("og") or image_data.get("sm")
        if not path:
            continue
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return "https://img.opencritic.com/" + path.lstrip("/")

    return None


def download_image(url):
    if not url:
        return None
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return Image.open(BytesIO(response.content)).convert("RGB")
    except Exception as e:
        print(f"WARNING: Could not download image: {e}")
        return None


def crop_and_round_cover(image, target_size, radius):
    """等比居中裁切并添加圆角蒙版，防止直角穿透"""
    tw, th = target_size
    if image is None:
        base = Image.new("RGBA", (tw, th), (30, 36, 48, 255))
    else:
        target_ratio = tw / th
        width, height = image.size
        ratio = width / height

        if ratio > target_ratio:
            new_width = int(height * target_ratio)
            left = (width - new_width) // 2
            image = image.crop((left, 0, left + new_width, height))
        else:
            new_height = int(width / target_ratio)
            top = (height - new_height) // 2
            image = image.crop((0, top, width, top + new_height))

        base = image.resize((tw, th), Image.Resampling.LANCZOS).convert("RGBA")

    # 创建圆角遮罩
    mask = Image.new("L", (tw, th), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (tw, th)], radius=radius, fill=255)
    base.putalpha(mask)
    return base


# ============================================================
# Vector Drawing helpers
# ============================================================

def draw_ring(draw, center, radius, width, percent, ring_bg, ring_fill):
    """绘制平滑圆环进度条"""
    x, y = center
    box = [x - radius, y - radius, x + radius, y + radius]
    draw.arc(box, start=0, end=360, fill=ring_bg, width=width)
    if percent > 0:
        angle = (360.0 * max(0.0, min(100.0, percent))) / 100.0
        draw.arc(box, start=-90, end=-90 + angle, fill=ring_fill, width=width)


def draw_speech_bubble(draw, x, y, width, height, color, line_w=2):
    """绘制左下角评论气泡图标"""
    r = int(height * 0.2)
    # 主体圆角矩形
    draw.rounded_rectangle([x, y, x + width, y + height], radius=r, outline=color, width=line_w)
    # 小尾巴
    tail_top = y + height - line_w
    tail_pts = [
        (x + int(width * 0.25), tail_top),
        (x + int(width * 0.45), tail_top),
        (x + int(width * 0.2), y + height + int(height * 0.3)),
    ]
    draw.polygon(tail_pts, fill=color)


def draw_opencritic_logo(draw, x, y, radius, color):
    """矢量绘制 OpenCritic 经典标志"""
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)
    # 内部镂空圆点与切槽
    inner_r = radius * 0.32
    draw.ellipse([x - inner_r, y - inner_r, x + inner_r, y + inner_r], fill=(19, 24, 34))
    line_w = max(2, int(radius * 0.18))
    draw.line([(x, y - radius), (x, y + radius)], fill=(19, 24, 34), width=line_w)
    draw.line([(x - radius, y), (x + radius, y)], fill=(19, 24, 34), width=line_w)


# ============================================================
# Create Card (2x 超采样渲染)
# ============================================================

def create_card(game, top_reviews, output_path, old_score=None):
    scale = 2
    w = CARD_WIDTH * scale
    h = CARD_HEIGHT * scale

    # 色彩方案（高保真还原）
    c_bg = (19, 24, 34)
    c_border = (38, 46, 62)
    c_white = (255, 255, 255)
    c_muted = (142, 154, 175)
    c_orange = (255, 87, 34)
    c_ring_bg = (42, 49, 66)

    canvas = Image.new("RGBA", (w, h), c_bg)
    draw = ImageDraw.Draw(canvas)

    # 1. 外边框
    draw.rounded_rectangle(
        [20 * scale, 20 * scale, w - 20 * scale, h - 20 * scale],
        radius=20 * scale,
        outline=c_border,
        width=2 * scale
    )

    # 2. 顶部 Title
    name = game.get("name", "Unknown Game")
    font_title = get_font(38 * scale, bold=True)
    draw.text((55 * scale, 45 * scale), name, fill=c_white, font=font_title)

    # 右上角 OpenCritic Logo
    logo_center = (w - 180 * scale, 68 * scale)
    draw_opencritic_logo(draw, logo_center[0], logo_center[1], radius=15 * scale, color=c_orange)
    font_brand = get_font(22 * scale, bold=True)
    draw.text((w - 152 * scale, 55 * scale), "OpenCritic", fill=c_white, font=font_brand)

    # 3. 左侧游戏海报（标准 16:9 裁剪，带圆角，杜绝直角穿模）
    cover_w = 640 * scale
    cover_h = 360 * scale
    cover_x = 55 * scale
    cover_y = 120 * scale

    cover_raw = download_image(get_image_url(game))
    cover_rounded = crop_and_round_cover(cover_raw, (cover_w, cover_h), radius=16 * scale)
    canvas.paste(cover_rounded, (cover_x, cover_y), cover_rounded)

    # 4. 右上 Top Critic Average（并排圆环）
    score = normalize_score(game.get("topCriticScore")) or 0
    ring_score_center = (800 * scale, 180 * scale)
    draw_ring(draw, ring_score_center, radius=48 * scale, width=9 * scale, percent=score, ring_bg=c_ring_bg, ring_fill=c_orange)

    font_main_score = get_font(36 * scale, bold=True)
    draw.text(ring_score_center, str(score), fill=c_white, font=font_main_score, anchor="mm")

    font_avg_label = get_font(20 * scale, bold=False)
    draw.text((865 * scale, 160 * scale), "Top Critic\nAverage", fill=c_white, font=font_avg_label, spacing=4 * scale)

    # 分数变动提示
    if old_score is not None:
        diff = score - old_score
        sign = f"+{diff}" if diff > 0 else str(diff)
        draw.text((865 * scale, 215 * scale), f"{old_score} → {score} ({sign})", fill=c_orange, font=get_font(15 * scale, bold=True))

    # 5. 右侧权威媒体评分列表（严格右对齐）
    media_start_y = 295 * scale
    media_row_gap = 52 * scale
    right_align_x = w - 60 * scale

    font_media = get_font(20 * scale, bold=False)
    font_media_score = get_font(21 * scale, bold=True)

    for i, item in enumerate(top_reviews):
        cur_y = media_start_y + i * media_row_gap
        # 媒体名（左对齐）
        pub_name = item["publication"]
        if len(pub_name) > 22:
            pub_name = pub_name[:20] + "..."
        draw.text((750 * scale, cur_y), pub_name, fill=c_white, font=font_media)
        # 分数（右对齐）
        draw.text((right_align_x, cur_y), item["score"], fill=c_white, font=font_media_score, anchor="ra")

    # 6. 左下方指标：Critics Recommend & Critic Reviews
    # 6.1 Critics Recommend
    recommended = normalize_score(game.get("percentRecommended")) or 0
    rec_center = (115 * scale, 560 * scale)
    draw_ring(draw, rec_center, radius=38 * scale, width=7 * scale, percent=recommended, ring_bg=c_ring_bg, ring_fill=c_orange)
    draw.text(rec_center, f"{recommended}%", fill=c_white, font=get_font(20 * scale, bold=True), anchor="mm")

    draw.text((170 * scale, 542 * scale), "Critics\nRecommend", fill=c_white, font=get_font(18 * scale), spacing=4 * scale)

    # 6.2 细竖线分割
    draw.line([(340 * scale, 535 * scale), (340 * scale, 585 * scale)], fill=c_ring_bg, width=2 * scale)

    # 6.3 Critic Reviews（气泡图标 + 评论数量）
    draw_speech_bubble(draw, x=375 * scale, y=547 * scale, width=24 * scale, height=18 * scale, color=c_muted, line_w=2 * scale)
    rev_count = game.get("numReviews") or game.get("numTopCriticReviews") or 0
    draw.text((415 * scale, 535 * scale), str(rev_count), fill=c_white, font=get_font(28 * scale, bold=True))
    draw.text((415 * scale, 568 * scale), "Critic Reviews", fill=c_muted, font=get_font(14 * scale))

    # 超采样下采样为目标尺寸（获得极其柔和无锯齿的边缘效果）
    final_card = canvas.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
    final_card.convert("RGB").save(output_path, "PNG", optimize=True)


# ============================================================
# Discord upload
# ============================================================

def send_discord(game, top_reviews, old_score=None):
    output_path = Path("opencritic_card.png")
    create_card(game, top_reviews, output_path, old_score)

    opencritic_url = get_opencritic_url(game)
    name = game.get("name", "Unknown Game")

    payload = {
        "username": "OpenCritic",
        "embeds": [
            {
                "title": name,
                "url": opencritic_url,
                "color": 0xFF5722,
                "image": {
                    "url": "attachment://opencritic_card.png"
                }
            }
        ]
    }

    print(f"OpenCritic URL: {opencritic_url}")

    with open(output_path, "rb") as image_file:
        response = requests.post(
            DISCORD_WEBHOOK,
            data={"payload_json": json.dumps(payload, ensure_ascii=False)},
            files={"file": ("opencritic_card.png", image_file, "image/png")},
            timeout=60
        )

    if response.status_code >= 400:
        print("ERROR sending Discord message:")
        print(response.text)
        response.raise_for_status()

    print("Discord notification sent.")


# ============================================================
# Main
# ============================================================

def main():
    if not API_KEY:
        print("ERROR: OPENCRITIC_API_KEY is missing.")
        sys.exit(1)

    if not DISCORD_WEBHOOK:
        print("ERROR: DISCORD_WEBHOOK is missing.")
        sys.exit(1)

    state = load_state()
    initialized = state["initialized"]
    games_state = state["games"]

    print("==================================================")
    print("OpenCritic Score Monitor")
    print("==================================================")
    print(f"Initialized: {initialized}")

    candidates = get_candidate_games()
    state_changed = False

    for summary in candidates:
        game_id = summary.get("id")
        if game_id is None:
            continue
        game_id = str(game_id)

        print("\n------------------------------------------")
        print(f"Game ID: {game_id}")
        print(f"Name: {summary.get('name', 'Unknown')}")

        if not is_in_monitor_window(summary):
            print("Outside monitoring window.")
            continue

        try:
            time.sleep(REQUEST_DELAY)
            game = get_game(game_id)
        except Exception as e:
            print(f"ERROR getting game {game_id}: {e}")
            continue

        name = game.get("name", summary.get("name", "Unknown Game"))
        raw_score = game.get("topCriticScore")
        score = normalize_score(raw_score)

        print(f"Full game: {name}")
        print(f"Normalized Score: {score}")

        # 获取评论数双重核验
        rev_count = game.get("numReviews") or game.get("numTopCriticReviews") or 0

        # ----------------------------------------------------
        # No score (未出分、负数占位符或评测数为 0)
        # ----------------------------------------------------
        if score is None or rev_count <= 0:
            print(f"No valid score yet (Score: {raw_score}, Reviews: {rev_count}).")

            if game_id not in games_state:
                games_state[game_id] = {
                    "name": name,
                    "score": None,
                    "last_notified_at": None
                }
                state_changed = True
            # 若历史状态中被误写入了 -1，自动清洗为 None
            elif games_state[game_id].get("score") is not None and games_state[game_id]["score"] < 0:
                games_state[game_id]["score"] = None
                state_changed = True

            continue

        # 首次运行基准初始化
        if not initialized:
            print("FIRST RUN: Saving baseline.")
            games_state[game_id] = {
                "name": name,
                "score": score,
                "last_notified_at": None
            }
            state_changed = True
            continue

        previous = games_state.get(game_id)

        # 新收录出分游戏
        if previous is None:
            print("NEW GAME + SCORE.")
            reviews = get_reviews(game_id)
            top_reviews = get_top_reviews(reviews)
            send_discord(game, top_reviews)
            games_state[game_id] = {
                "name": name,
                "score": score,
                "last_notified_at": datetime.now(timezone.utc).isoformat()
            }
            state_changed = True
            continue

        old_score = normalize_score(previous.get("score"))

        # 解禁首次开分
        if old_score is None:
            print(f"SCORE UNLOCKED: None -> {score}")
            reviews = get_reviews(game_id)
            top_reviews = get_top_reviews(reviews)
            send_discord(game, top_reviews)
            previous["name"] = name
            previous["score"] = score
            previous["last_notified_at"] = datetime.now(timezone.utc).isoformat()
            state_changed = True
            continue

        # 分数无变动
        if old_score == score:
            print(f"Score unchanged: {old_score}")
            continue

        # 分数发生变化
        print(f"SCORE CHANGED: {old_score} -> {score}")
        reviews = get_reviews(game_id)
        top_reviews = get_top_reviews(reviews)
        send_discord(game, top_reviews, old_score=old_score)
        previous["name"] = name
        previous["score"] = score
        previous["last_notified_at"] = datetime.now(timezone.utc).isoformat()
        state_changed = True

    if not initialized:
        state["initialized"] = True
        state_changed = True
        print("\nFIRST RUN COMPLETE: Baseline created.")

    if state_changed:
        save_state(state)
        print("\nState saved.")
    else:
        print("\nNo state changes.")

    print("\nDone.")


if __name__ == "__main__":
    main()
