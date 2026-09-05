import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


API_BASE = "https://opencritic-api.p.rapidapi.com"

API_KEY = os.environ.get("OPENCRITIC_API_KEY")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

STATE_FILE = Path("data/games_state.json")

HEADERS = {
    "X-RapidAPI-Key": API_KEY,
    "X-RapidAPI-Host": "opencritic-api.p.rapidapi.com",
}


# ============================================================
# 基础工具
# ============================================================

def api_get(path, params=None):
    url = API_BASE + path

    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        print("WARNING: State file is invalid. Starting with empty state.")
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# OpenCritic
# ============================================================

def get_reviewed_today():
    """
    获取今天有媒体 Review 的游戏。
    """

    return api_get("/game/reviewed-today")


def get_game(game_id):
    """
    获取游戏详细信息。
    """

    return api_get(f"/game/{game_id}")


# ============================================================
# 日期处理
# ============================================================

def parse_release_date(game):
    """
    尝试读取 OpenCritic 的游戏发行日期。

    不同版本/API 返回字段可能略有不同，
    因此这里依次尝试几个常见字段。
    """

    possible_fields = [
        "firstReleaseDate",
        "releaseDate",
        "releaseDateString",
    ]

    for field in possible_fields:
        value = game.get(field)

        if not value:
            continue

        if isinstance(value, str):
            value = value[:10]

            try:
                return datetime.strptime(
                    value,
                    "%Y-%m-%d"
                ).date()

            except ValueError:
                pass

    return None


def is_new_game(game):
    """
    判断是不是近期的新游戏。

    默认：
    当前日期前后 30 天以内视为新游戏。

    如果无法读取发行日期，为了避免误报，
    默认认为不是新游戏。
    """

    release_date = parse_release_date(game)

    if release_date is None:
        print("  Release date unavailable -> skip.")
        return False

    today = datetime.now(timezone.utc).date()

    difference = abs(
        (today - release_date).days
    )

    if difference <= 30:
        return True

    print(
        f"  Release date: {release_date} "
        f"({difference} days from today) -> old game."
    )

    return False


# ============================================================
# Discord
# ============================================================

def send_discord(game):
    name = game.get(
        "name",
        "Unknown Game"
    )

    score = game.get(
        "topCriticScore"
    )

    reviews = game.get(
        "numTopCriticReviews",
        0
    )

    recommended = game.get(
        "percentRecommended"
    )

    game_id = game.get("id")

    opencritic_url = (
        f"https://opencritic.com/game/{game_id}"
        if game_id
        else "https://opencritic.com/"
    )

    description = (
        f"🟢 **OpenCritic Score: {score}**\n\n"
        f"⭐ Top Critic Reviews: {reviews}\n"
    )

    if recommended is not None:
        description += (
            f"👍 Recommended: {recommended}%\n"
        )

    description += (
        f"\n[View on OpenCritic]({opencritic_url})"
    )

    payload = {
        "username": "OpenCritic",
        "embeds": [
            {
                "title": (
                    f"🎮 {name} — "
                    f"OpenCritic Score Unlocked"
                ),
                "description": description,
                "url": opencritic_url,
            }
        ],
    }

    response = requests.post(
        DISCORD_WEBHOOK,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()


# ============================================================
# 主程序
# ============================================================

def main():

    if not API_KEY:
        print(
            "ERROR: OPENCRITIC_API_KEY "
            "is not configured."
        )
        sys.exit(1)

    if not DISCORD_WEBHOOK:
        print(
            "ERROR: DISCORD_WEBHOOK "
            "is not configured."
        )
        sys.exit(1)

    state = load_state()

    print(
        "Getting games reviewed today..."
    )

    games = get_reviewed_today()

    if not isinstance(games, list):

        print(
            "ERROR: Unexpected API response:"
        )

        print(games)

        sys.exit(1)

    print(
        f"Found {len(games)} reviewed games."
    )

    state_changed = False

    # ========================================================
    # 第一次运行检测
    # ========================================================

    first_run = len(state) == 0

    if first_run:
        print(
            "\nFIRST RUN DETECTED."
        )

        print(
            "Existing games will be recorded "
            "without sending Discord notifications.\n"
        )

    # ========================================================
    # 处理游戏
    # ========================================================

    for item in games:

        game_id = item.get("id")

        if not game_id:
            continue

        game_id = str(game_id)

        try:

            print(
                f"\nChecking game ID: {game_id}"
            )

            game = get_game(game_id)

            name = game.get(
                "name",
                item.get(
                    "name",
                    "Unknown Game"
                )
            )

            score = game.get(
                "topCriticScore"
            )

            print(
                f"  Name: {name}"
            )

            print(
                f"  Score: {score}"
            )

            # ------------------------------------------------
            # 没有评分
            # ------------------------------------------------

            if score is None:

                print(
                    "  No Top Critic Score."
                )

                # 即使没有分数，也记录这个游戏。
                # 这样以后可以检测它何时第一次开分。

                if game_id not in state:

                    state[game_id] = {
                        "name": name,
                        "score": None,
                        "seen_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }

                    state_changed = True

                continue

            # ------------------------------------------------
            # 游戏以前没有记录
            # ------------------------------------------------

            if game_id not in state:

                # 第一次运行：
                # 只建立数据库，不通知。
                if first_run:

                    print(
                        "  Existing score found "
                        "during first run."
                    )

                    print(
                        "  Recording without notification."
                    )

                    state[game_id] = {
                        "name": name,
                        "score": score,
                        "seen_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }

                    state_changed = True

                    continue

                # ------------------------------------------------
                # 后续运行发现全新的游戏
                # ------------------------------------------------

                print(
                    "  NEW GAME DETECTED."
                )

                # 检查发行日期
                if not is_new_game(game):

                    print(
                        "  Not considered a new release."
                    )

                    state[game_id] = {
                        "name": name,
                        "score": score,
                        "seen_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }

                    state_changed = True

                    continue

                # ------------------------------------------------
                # 新游戏 + 第一次出现 Score
                # ------------------------------------------------

                print(
                    "  NEW SCORE UNLOCK DETECTED!"
                )

                send_discord(game)

                state[game_id] = {
                    "name": name,
                    "score": score,
                    "notified": True,
                    "notified_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }

                state_changed = True

                continue

            # ------------------------------------------------
            # 游戏以前没有评分，现在有评分
            # ------------------------------------------------

            old_score = state[game_id].get(
                "score"
            )

            if old_score is None:

                print(
                    f"  Score unlocked: "
                    f"None -> {score}"
                )

                # 必须是新游戏才通知
                if not is_new_game(game):

                    print(
                        "  Old game -> "
                        "score unlock ignored."
                    )

                    state[game_id]["score"] = score

                    state_changed = True

                    continue

                print(
                    "  NEW GAME SCORE UNLOCK!"
                )

                send_discord(game)

                state[game_id]["score"] = score

                state[game_id][
                    "notified"
                ] = True

                state[game_id][
                    "notified_at"
                ] = datetime.now(
                    timezone.utc
                ).isoformat()

                state_changed = True

                continue

            # ------------------------------------------------
            # 已经有评分
            # ------------------------------------------------

            if old_score == score:

                print(
                    "  No score change."
                )

                continue

            # ------------------------------------------------
            # Score 后续发生变化
            # ------------------------------------------------

            print(
                f"  Score changed: "
                f"{old_score} -> {score}"
            )

            # 我们的目标是“首次开分”
            # 所以后续变化不再通知。
            state[game_id]["score"] = score

            state_changed = True

        except requests.HTTPError as e:

            print(
                f"  HTTP ERROR: {e}"
            )

        except Exception as e:

            print(
                f"  ERROR: {e}"
            )

    # ========================================================
    # 保存状态
    # ========================================================

    if state_changed:

        save_state(state)

        print(
            "\nState saved."
        )

    else:

        print(
            "\nNo state changes."
        )


if __name__ == "__main__":
    main()
