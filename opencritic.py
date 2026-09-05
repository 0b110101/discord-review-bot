import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import requests


API_BASE = "https://opencritic-api.p.rapidapi.com"

API_KEY = os.environ.get("OPENCRITIC_API_KEY")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

STATE_FILE = Path("data/notified_games.json")

HEADERS = {
    "X-RapidAPI-Key": API_KEY,
    "X-RapidAPI-Host": "opencritic-api.p.rapidapi.com",
}


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


def api_get(path, params=None):
    url = API_BASE + path

    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    return response.json()


def get_reviewed_today():
    """
    获取今天有媒体评测的游戏。
    """
    return api_get("/game/reviewed-today")


def get_game(game_id):
    """
    获取游戏详细信息。
    """
    return api_get(f"/game/{game_id}")


def send_discord(game):
    name = game.get("name", "Unknown Game")
    score = game.get("topCriticScore")
    reviews = game.get("numTopCriticReviews", 0)
    recommended = game.get("percentRecommended")

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
        description += f"👍 Recommended: {recommended}%\n"

    description += f"\n[View on OpenCritic]({opencritic_url})"

    payload = {
        "username": "OpenCritic",
        "embeds": [
            {
                "title": f"🎮 {name} — Score Unlocked",
                "description": description,
                "url": opencritic_url,
            }
        ]
    }

    response = requests.post(
        DISCORD_WEBHOOK,
        json=payload,
        timeout=30
    )

    response.raise_for_status()


def main():
    if not API_KEY:
        print("ERROR: OPENCRITIC_API_KEY is not configured.")
        sys.exit(1)

    if not DISCORD_WEBHOOK:
        print("ERROR: DISCORD_WEBHOOK is not configured.")
        sys.exit(1)

    state = load_state()

    print("Getting games reviewed today...")

    games = get_reviewed_today()

    if not isinstance(games, list):
        print("ERROR: Unexpected API response:")
        print(games)
        sys.exit(1)

    print(f"Found {len(games)} games.")

    state_changed = False

    for item in games:

        game_id = item.get("id")

        if not game_id:
            continue

        game_id = str(game_id)

        try:
            game = get_game(game_id)

            name = game.get("name", item.get("name", "Unknown"))
            score = game.get("topCriticScore")
            reviews = game.get("numTopCriticReviews", 0)

            print(
                f"{name} | "
                f"Score={score} | "
                f"Reviews={reviews}"
            )

            # 没有 Top Critic Score
            if score is None:
                continue

            # 已经通知过
            if game_id in state:
                continue

            print(f"NEW SCORE: {name} -> {score}")

            send_discord(game)

            state[game_id] = {
                "name": name,
                "score": score,
                "notified_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            state_changed = True

        except requests.HTTPError as e:
            print(
                f"HTTP ERROR while processing game {game_id}: {e}"
            )

        except Exception as e:
            print(
                f"ERROR while processing game {game_id}: {e}"
            )

    if state_changed:
        save_state(state)
        print("State saved.")
    else:
        print("No new scores.")


if __name__ == "__main__":
    main()
