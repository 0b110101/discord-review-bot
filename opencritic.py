import json
import os
import sys
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

# 新游戏时间窗口：
# 发行日前 14 天 ～ 发行后 3 天
PRE_RELEASE_DAYS = 14
POST_RELEASE_DAYS = 3


# ============================================================
# API
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


def get_reviewed_today():
    return api_get("/game/reviewed-today")


def get_game(game_id):
    return api_get(f"/game/{game_id}")


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

            return json.load(f)

    except Exception:

        print(
            "WARNING: State file is invalid."
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

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# Release Date
# ============================================================

def get_release_date(game):

    possible_fields = [
        "firstReleaseDate",
        "releaseDate",
    ]

    for field in possible_fields:

        value = game.get(field)

        if not value:
            continue

        if isinstance(value, str):

            try:

                return datetime.strptime(
                    value[:10],
                    "%Y-%m-%d"
                ).date()

            except ValueError:
                pass

    return None


def is_in_new_game_window(game):

    release_date = get_release_date(game)

    if release_date is None:

        print(
            "  Release date unavailable."
        )

        return False

    today = datetime.now(
        timezone.utc
    ).date()

    start_date = (
        release_date
        - timedelta(days=PRE_RELEASE_DAYS)
    )

    end_date = (
        release_date
        + timedelta(days=POST_RELEASE_DAYS)
    )

    in_window = (
        start_date
        <= today
        <= end_date
    )

    print(
        f"  Release date: {release_date}"
    )

    print(
        f"  Monitoring window: "
        f"{start_date} ~ {end_date}"
    )

    print(
        f"  In window: {in_window}"
    )

    return in_window


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
                    f"OpenCritic Score"
                ),

                "description": description,

                "url": opencritic_url,
            }
        ]
    }

    response = requests.post(
        DISCORD_WEBHOOK,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()


# ============================================================
# Main
# ============================================================

def main():

    # --------------------------------------------------------
    # Check configuration
    # --------------------------------------------------------

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
        f"Initialized: {initialized}"
    )

    # --------------------------------------------------------
    # Get today's reviewed games
    # --------------------------------------------------------

    print(
        "\nGetting games reviewed today..."
    )

    games = get_reviewed_today()

    if not isinstance(games, list):

        print(
            "ERROR: Unexpected API response:"
        )

        print(games)

        sys.exit(1)

    print(
        f"Found {len(games)} games."
    )

    state_changed = False

    # --------------------------------------------------------
    # Process games
    # --------------------------------------------------------

    for item in games:

        game_id = item.get("id")

        if not game_id:
            continue

        game_id = str(game_id)

        try:

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
                f"\n{name}"
            )

            print(
                f"  ID: {game_id}"
            )

            print(
                f"  Score: {score}"
            )

            # ------------------------------------------------
            # 没有评分
            # ------------------------------------------------

            if score is None:

                print(
                    "  No score. Skip."
                )

                continue

            # ------------------------------------------------
            # 新游戏时间窗口
            # ------------------------------------------------

            if not is_in_new_game_window(game):

                print(
                    "  Outside new-game window."
                )

                continue

            # ------------------------------------------------
            # 第一次运行
            # ------------------------------------------------

            if not initialized:

                print(
                    "  FIRST RUN:"
                )

                print(
                    "  Recording baseline "
                    "without Discord notification."
                )

                games_state[game_id] = {
                    "name": name,
                    "score": score,
                    "last_notified_at": None,
                }

                state_changed = True

                continue

            # ------------------------------------------------
            # 后续运行
            # ------------------------------------------------

            game_state = games_state.get(
                game_id
            )

            # ------------------------------------------------
            # 第一次看到这个游戏
            # ------------------------------------------------

            if game_state is None:

                print(
                    "  NEW GAME DETECTED."
                )

                send_discord(game)

                games_state[game_id] = {
                    "name": name,
                    "score": score,
                    "last_notified_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }

                state_changed = True

                continue

            # ------------------------------------------------
            # 已经推送过
            # ------------------------------------------------

            last_notified = game_state.get(
                "last_notified_at"
            )

            # ------------------------------------------------
            # 同一个游戏允许重复推送
            # ------------------------------------------------

            print(
                "  Game already notified."
            )

            print(
                "  Sending again because "
                "game is still inside "
                "the release window."
            )

            send_discord(game)

            game_state["score"] = score

            game_state[
                "last_notified_at"
            ] = datetime.now(
                timezone.utc
            ).isoformat()

            state_changed = True

        except requests.HTTPError as e:

            print(
                f"  HTTP ERROR: {e}"
            )

        except Exception as e:

            print(
                f"  ERROR: {e}"
            )

    # --------------------------------------------------------
    # Mark initialized
    # --------------------------------------------------------

    if not initialized:

        state["initialized"] = True

        state_changed = True

        print(
            "\nInitial baseline created."
        )

        print(
            "No Discord notifications were sent."
        )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

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
