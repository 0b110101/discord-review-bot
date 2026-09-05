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

PRE_RELEASE_DAYS = 14
POST_RELEASE_DAYS = 3

HEADERS = {
    "X-RapidAPI-Key": API_KEY,
    "X-RapidAPI-Host": "opencritic-api.p.rapidapi.com",
}

REQUEST_DELAY = 0.3


# ============================================================
# API request
# ============================================================

def api_get(path):

    url = API_BASE + path

    print(f"GET {url}")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    print(f"HTTP {response.status_code}")

    response.raise_for_status()

    try:
        return response.json()
    except ValueError:
        print("ERROR: API did not return JSON.")
        print(response.text[:1000])
        sys.exit(1)


# ============================================================
# Normalize API result
# ============================================================

def normalize_games(data):

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        for key in (
            "data",
            "games",
            "results",
            "items",
        ):

            value = data.get(key)

            if isinstance(value, list):
                return value

    print(
        "ERROR: Could not find a game list "
        "in API response."
    )

    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        )[:3000]
    )

    sys.exit(1)


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

            state = json.load(f)

        state.setdefault(
            "initialized",
            False
        )

        state.setdefault(
            "games",
            {}
        )

        return state

    except Exception as e:

        print(
            f"ERROR reading state.json: {e}"
        )

        sys.exit(1)


def save_state(state):

    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

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
# Date handling
# ============================================================

def parse_datetime(value):

    if not value:
        return None

    if not isinstance(value, str):
        return None

    try:

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

    for field in (
        "firstReleaseDate",
        "releaseDate",
    ):

        value = game.get(field)

        parsed = parse_datetime(
            value
        )

        if parsed:
            return parsed

    return None


def is_in_monitor_window(game):

    release_date = get_release_date(
        game
    )

    if release_date is None:

        print(
            "  No release date."
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
        f"  Release: {release_date.isoformat()}"
    )

    print(
        f"  Monitor: "
        f"{start.isoformat()} "
        f"-> "
        f"{end.isoformat()}"
    )

    print(
        f"  In window: {result}"
    )

    return result


# ============================================================
# OpenCritic discovery
# ============================================================

def get_candidate_games():

    candidates = {}

    endpoints = [
        "/game/upcoming",
        "/game/recently-released",
    ]

    for endpoint in endpoints:

        print(
            "\n=========================================="
        )

        print(
            f"Discovering games: {endpoint}"
        )

        data = api_get(
            endpoint
        )

        games = normalize_games(
            data
        )

        print(
            f"Returned games: {len(games)}"
        )

        for game in games:

            game_id = game.get(
                "id"
            )

            if game_id is None:
                continue

            candidates[
                str(game_id)
            ] = game

        time.sleep(
            REQUEST_DELAY
        )

    print(
        "\n=========================================="
    )

    print(
        f"Unique candidate games: "
        f"{len(candidates)}"
    )

    return list(
        candidates.values()
    )


# ============================================================
# Game details
# ============================================================

def get_game(game_id):

    return api_get(
        f"/game/{game_id}"
    )


# ============================================================
# Discord
# ============================================================

def send_discord(
    game,
    old_score=None
):

    name = game.get(
        "name",
        "Unknown Game"
    )

    score = game.get(
        "topCriticScore"
    )

    game_id = game.get(
        "id"
    )

    reviews = game.get(
        "numTopCriticReviews"
    )

    if reviews is None:
        reviews = 0

    recommended = game.get(
        "percentRecommended"
    )

    opencritic_url = (
        f"https://opencritic.com/game/"
        f"{game_id}"
    )

    if old_score is None:

        score_line = (
            f"🟢 **OpenCritic Score: "
            f"{score:g}**"
        )

    else:

        score_line = (
            f"📈 **OpenCritic Score: "
            f"{old_score:g} → {score:g}**"
        )

    description = (
        f"{score_line}\n\n"
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
        f"({opencritic_url})"
    )

    payload = {
        "username": "OpenCritic",
        "embeds": [
            {
                "title": f"🎮 {name}",
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

    if response.status_code >= 400:

        print(
            "ERROR sending Discord message:"
        )

        print(
            response.text
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
    # Check secrets
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

    initialized = state[
        "initialized"
    ]

    games_state = state[
        "games"
    ]

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
    # Discover candidates
    # --------------------------------------------------------

    candidates = get_candidate_games()

    state_changed = False

    # --------------------------------------------------------
    # Process
    # --------------------------------------------------------

    for summary in candidates:

        game_id = summary.get(
            "id"
        )

        if game_id is None:
            continue

        game_id = str(
            game_id
        )

        # ----------------------------------------------------
        # First filter using summary data
        # ----------------------------------------------------

        print(
            "\n------------------------------------------"
        )

        print(
            f"Game ID: {game_id}"
        )

        print(
            f"Name: "
            f"{summary.get('name', 'Unknown')}"
        )

        if not is_in_monitor_window(
            summary
        ):

            print(
                "Outside monitoring window."
            )

            continue

        # ----------------------------------------------------
        # Get full game data
        # ----------------------------------------------------

        try:

            time.sleep(
                REQUEST_DELAY
            )

            game = get_game(
                game_id
            )

        except Exception as e:

            print(
                f"ERROR getting game "
                f"{game_id}: {e}"
            )

            continue

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
            f"Full game: {name}"
        )

        print(
            f"Top Critic Score: {score}"
        )

        # ----------------------------------------------------
        # No score yet
        # ----------------------------------------------------

        if score is None:

            print(
                "No score yet."
            )

            # Save the game as score=None
            # so that a later score unlock can be detected.

            if game_id not in games_state:

                games_state[
                    game_id
                ] = {
                    "name": name,
                    "score": None,
                    "last_notified_at": None
                }

                state_changed = True

            continue

        # ----------------------------------------------------
        # FIRST RUN
        # ----------------------------------------------------

        if not initialized:

            print(
                "FIRST RUN:"
            )

            print(
                "Saving current score as baseline."
            )

            print(
                "No Discord notification."
            )

            games_state[
                game_id
            ] = {
                "name": name,
                "score": score,
                "last_notified_at": None
            }

            state_changed = True

            continue

        # ----------------------------------------------------
        # Existing game
        # ----------------------------------------------------

        previous = games_state.get(
            game_id
        )

        # ----------------------------------------------------
        # Completely new game
        # ----------------------------------------------------

        if previous is None:

            print(
                "NEW GAME + SCORE."
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
                    ).isoformat()
            }

            state_changed = True

            continue

        # ----------------------------------------------------
        # Score was previously unavailable
        # ----------------------------------------------------

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
                "name"
            ] = name

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

        # ----------------------------------------------------
        # Score unchanged
        # ----------------------------------------------------

        if float(old_score) == float(score):

            print(
                f"Score unchanged: "
                f"{old_score}"
            )

            continue

        # ----------------------------------------------------
        # Score changed
        # ----------------------------------------------------

        print(
            f"SCORE CHANGED: "
            f"{old_score} -> {score}"
        )

        send_discord(
            game,
            old_score=old_score
        )

        previous[
            "name"
        ] = name

        previous[
            "score"
        ] = score

        previous[
            "last_notified_at"
        ] = datetime.now(
            timezone.utc
        ).isoformat()

        state_changed = True

    # --------------------------------------------------------
    # Finish first run
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
            "No Discord notifications were sent."
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
