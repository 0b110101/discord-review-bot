import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


# ============================================================
# Configuration
# ============================================================

API_BASE = "https://opencritic-api.p.rapidapi.com"

API_KEY = os.environ.get("OPENCRITIC_API_KEY")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

STATE_FILE = Path("data/state.json")

HEADERS = {
    "X-RapidAPI-Key": API_KEY,
    "X-RapidAPI-Host": "opencritic-api.p.rapidapi.com",
}

# ------------------------------------------------------------
# 新游戏监控时间范围
# ------------------------------------------------------------

PRE_RELEASE_DAYS = 14
POST_RELEASE_DAYS = 3

# ------------------------------------------------------------
# OpenCritic /game 分页
#
# 每页通常包含 20 个游戏。
# 我们检查足够多的近期页面，避免只看到少数游戏。
# ------------------------------------------------------------

MAX_PAGES = 5

# API 请求之间稍微间隔一下
REQUEST_DELAY = 0.3


# ============================================================
# HTTP
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


# ============================================================
# State
# ============================================================

def load_state():

    if not STATE_FILE.exists():

        return {
            "initialized": False,
            "games": {}
        }

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        data.setdefault(
            "initialized",
            False
        )

        data.setdefault(
            "games",
            {}
        )

        return data

    except Exception as e:

        print(
            f"WARNING: Failed to read state file: {e}"
        )

        return {
            "initialized": False,
            "games": {}
        }


def save_state(state):

    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # 先写临时文件，再替换。
    # 防止程序中途失败导致 JSON 损坏。

    temp_file = STATE_FILE.with_suffix(
        ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    temp_file.replace(
        STATE_FILE
    )


# ============================================================
# Date
# ============================================================

def parse_date(value):

    if not value:
        return None

    if not isinstance(value, str):
        return None

    try:

        # OpenCritic 通常返回：
        # 2026-09-10T00:00:00.000Z

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        )

    except ValueError:

        try:

            return datetime.strptime(
                value[:10],
                "%Y-%m-%d"
            ).replace(
                tzinfo=timezone.utc
            )

        except ValueError:

            return None


def get_release_date(game):

    possible_fields = [
        "firstReleaseDate",
        "releaseDate",
    ]

    for field in possible_fields:

        release_date = parse_date(
            game.get(field)
        )

        if release_date:

            return release_date

    return None


def is_in_monitor_window(game):

    release_date = get_release_date(
        game
    )

    if release_date is None:

        print(
            "  Release date unavailable."
        )

        return False

    now = datetime.now(
        timezone.utc
    )

    start = (
        release_date
        - timedelta(
            days=PRE_RELEASE_DAYS
        )
    )

    end = (
        release_date
        + timedelta(
            days=POST_RELEASE_DAYS
        )
    )

    result = (
        start
        <= now
        <= end
    )

    print(
        f"  Release: "
        f"{release_date.isoformat()}"
    )

    print(
        f"  Window: "
        f"{start.isoformat()} "
        f"-> "
        f"{end.isoformat()}"
    )

    print(
        f"  In window: {result}"
    )

    return result


# ============================================================
# OpenCritic
# ============================================================

def get_recent_games():

    """
    获取近期游戏。

    OpenCritic 的 /game 接口是分页游戏列表。
    我们按发行日期排序，并读取多个页面。

    不依赖 reviewed-today 的 10 个结果，
    避免同一天游戏过多时漏掉目标游戏。
    """

    all_games = {}

    for page in range(
        1,
        MAX_PAGES + 1
    ):

        print(
            f"Getting game list page {page}..."
        )

        try:

            data = api_get(
                "/game",
                params={
                    "page": page,
                    "sort": "date",
                }
            )

        except Exception as e:

            print(
                f"ERROR getting page {page}: {e}"
            )

            continue

        # ----------------------------------------------------
        # 兼容不同 API 返回结构
        # ----------------------------------------------------

        items = []

        if isinstance(data, list):

            items = data

        elif isinstance(data, dict):

            if isinstance(
                data.get("items"),
                list
            ):

                items = data["items"]

            elif isinstance(
                data.get("data"),
                list
            ):

                items = data["data"]

            elif isinstance(
                data.get("data"),
                dict
            ):

                if isinstance(
                    data["data"].get("items"),
                    list
                ):

                    items = data[
                        "data"
                    ]["items"]

        print(
            f"  Found {len(items)} games."
        )

        for game in items:

            game_id = game.get("id")

            if game_id is not None:

                all_games[
                    str(game_id)
                ] = game

        time.sleep(
            REQUEST_DELAY
        )

    return list(
        all_games.values()
    )


def get_game(game_id):

    return api_get(
        f"/game/{game_id}"
    )


# ============================================================
# Discord
# ============================================================

def send_discord(game, old_score=None):

    name = game.get(
        "name",
        "Unknown Game"
    )

    score = game.get(
        "topCriticScore"
    )

    reviews = game.get(
        "numTopCriticReviews"
    )

    if reviews is None:

        reviews = game.get(
            "numReviews",
            0
        )

    recommended = game.get(
        "percentRecommended"
    )

    game_id = game.get(
        "id"
    )

    url = (
        f"https://opencritic.com/game/{game_id}"
    )

    # --------------------------------------------------------
    # Score display
    # --------------------------------------------------------

    if old_score is None:

        score_text = (
            f"🟢 **OpenCritic Score: "
            f"{score:g}**"
        )

    else:

        score_text = (
            f"📈 **OpenCritic Score: "
            f"{old_score:g} → {score:g}**"
        )

    description = (
        f"{score_text}\n\n"
        f"⭐ Top Critic Reviews: "
        f"{reviews}\n"
    )

    if recommended is not None:

        description += (
            f"👍 Recommended: "
            f"{recommended:.1f}%\n"
        )

    description += (
        f"\n[View on OpenCritic]"
        f"({url})"
    )

    payload = {

        "username": "OpenCritic",

        "embeds": [
            {
                "title": (
                    f"🎮 {name}"
                ),

                "description": description,

                "url": url,
            }
        ]
    }

    response = requests.post(
        DISCORD_WEBHOOK,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    print(
        "  Discord notification sent."
    )


# ============================================================
# Main
# ============================================================

def main():

    # --------------------------------------------------------
    # Configuration check
    # --------------------------------------------------------

    if not API_KEY:

        print(
            "ERROR: OPENCRITIC_API_KEY "
            "is missing."
        )

        sys.exit(1)

    if not DISCORD_WEBHOOK:

        print(
            "ERROR: DISCORD_WEBHOOK "
            "is missing."
        )

        sys.exit(1)

    # --------------------------------------------------------
    # Load state
    # --------------------------------------------------------

    state = load_state()

    initialized = state.get(
        "initialized",
        False
    )

    games_state = state.setdefault(
        "games",
        {}
    )

    print(
        "=================================================="
    )

    print(
        "OpenCritic Score Monitor"
    )

    print(
        "=================================================="
    )

    print(
        f"Initialized: {initialized}"
    )

    # --------------------------------------------------------
    # Get recent games
    # --------------------------------------------------------

    games = get_recent_games()

    print(
        f"\nTotal unique games found: "
        f"{len(games)}"
    )

    state_changed = False

    # --------------------------------------------------------
    # Process games
    # --------------------------------------------------------

    for summary in games:

        game_id = summary.get(
            "id"
        )

        if game_id is None:
            continue

        game_id = str(
            game_id
        )

        # ----------------------------------------------------
        # 先利用列表数据过滤日期
        # ----------------------------------------------------

        if not is_in_monitor_window(
            summary
        ):

            continue

        try:

            print(
                "\n------------------------------------------"
            )

            print(
                f"Checking ID: {game_id}"
            )

            # ------------------------------------------------
            # 获取完整游戏数据
            # ------------------------------------------------

            game = get_game(
                game_id
            )

            name = game.get(
                "name",
                summary.get(
                    "name",
                    "Unknown Game"
                )
            )

            score = game.get(
                "topCriticScore"
            )

            print(
                f"Name: {name}"
            )

            print(
                f"Score: {score}"
            )

            # ------------------------------------------------
            # 没有评分
            # ------------------------------------------------

            if score is None:

                print(
                    "No Top Critic Score."
                )

                # 如果还不知道这个游戏，
                # 记录它目前没有分数。

                if game_id not in games_state:

                    games_state[
                        game_id
                    ] = {

                        "name": name,

                        "score": None,

                        "last_notified_at": None,

                    }

                    state_changed = True

                continue

            # ------------------------------------------------
            # 第一次运行
            # ------------------------------------------------

            if not initialized:

                print(
                    "FIRST RUN:"
                )

                print(
                    "Recording current score "
                    "without notification."
                )

                games_state[
                    game_id
                ] = {

                    "name": name,

                    "score": score,

                    "last_notified_at": None,

                }

                state_changed = True

                continue

            # ------------------------------------------------
            # 后续运行
            # ------------------------------------------------

            previous = games_state.get(
                game_id
            )

            # ------------------------------------------------
            # 从未见过这个游戏
            # ------------------------------------------------

            if previous is None:

                print(
                    "NEW GAME WITH SCORE."
                )

                print(
                    "Sending Discord notification."
                )

                send_discord(
                    game
                )

                games_state[
                    game_id
                ] = {

                    "name": name,

                    "score": score,

                    "last_notified_at":
                        datetime.now(
                            timezone.utc
                        ).isoformat(),

                }

                state_changed = True

                continue

            # ------------------------------------------------
            # 以前没有分数，现在有分数
            # ------------------------------------------------

            old_score = previous.get(
                "score"
            )

            if old_score is None:

                print(
                    f"SCORE UNLOCKED: "
                    f"None -> {score}"
                )

                send_discord(
                    game
                )

                previous[
                    "score"
                ] = score

                previous[
                    "last_notified_at"
                ] = datetime.now(
                    timezone.utc
                ).isoformat()

                state_changed = True

                continue

            # ------------------------------------------------
            # 分数没有变化
            # ------------------------------------------------

            if float(old_score) == float(score):

                print(
                    f"Score unchanged: "
                    f"{old_score}"
                )

                continue

            # ------------------------------------------------
            # 分数发生变化
            # ------------------------------------------------

            print(
                f"SCORE CHANGED: "
                f"{old_score} -> {score}"
            )

            send_discord(
                game,
                old_score=old_score
            )

            previous[
                "score"
            ] = score

            previous[
                "last_notified_at"
            ] = datetime.now(
                timezone.utc
            ).isoformat()

            state_changed = True

            time.sleep(
                REQUEST_DELAY
            )

        except requests.HTTPError as e:

            print(
                f"HTTP ERROR for "
                f"{game_id}: {e}"
            )

        except Exception as e:

            print(
                f"ERROR for "
                f"{game_id}: {e}"
            )

    # --------------------------------------------------------
    # First-run baseline
    # --------------------------------------------------------

    if not initialized:

        state[
            "initialized"
        ] = True

        state_changed = True

        print(
            "\n=================================================="
        )

        print(
            "FIRST RUN COMPLETE"
        )

        print(
            "Baseline created."
        )

        print(
            "NO Discord notifications were sent."
        )

        print(
            "=================================================="
        )

    # --------------------------------------------------------
    # Save state
    # --------------------------------------------------------

    if state_changed:

        save_state(
            state
        )

        print(
            "\nState saved."
        )

    else:

        print(
            "\nNo state changes."
        )

    print(
        "\nDone."
    )


if __name__ == "__main__":

    main()
