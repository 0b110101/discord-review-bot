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

# Fixed Discord image size
CARD_WIDTH = 1200
CARD_HEIGHT = 675


# ============================================================
# Fonts
# ============================================================

def get_font(size, bold=False):

    candidates = []

    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]

    for path in candidates:

        if os.path.exists(path):

            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


# ============================================================
# Score normalization
# ============================================================

def normalize_score(score):

    if score is None:
        return None

    try:

        return int(
            round(
                float(score)
            )
        )

    except (
        TypeError,
        ValueError
    ):

        return None


# ============================================================
# Slug
# ============================================================

def make_slug(name):

    if not name:
        return ""

    name = unicodedata.normalize(
        "NFKD",
        str(name)
    )

    name = "".join(
        char
        for char in name
        if not unicodedata.combining(char)
    )

    name = name.lower()

    name = name.replace(
        "'",
        ""
    )

    name = name.replace(
        "’",
        ""
    )

    name = re.sub(
        r"[^a-z0-9]+",
        "-",
        name
    )

    name = re.sub(
        r"-+",
        "-",
        name
    )

    return name.strip("-")


def get_opencritic_url(game):

    game_id = game.get("id")

    slug = make_slug(
        game.get(
            "name",
            "game"
        )
    )

    return (
        f"https://opencritic.com/game/"
        f"{game_id}/"
        f"{slug}"
    )


# ============================================================
# API
# ============================================================

def api_get(path):

    url = API_BASE + path

    print(
        f"GET {url}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    print(
        f"HTTP {response.status_code}"
    )

    response.raise_for_status()

    try:

        return response.json()

    except ValueError:

        print(
            "ERROR: API did not return JSON."
        )

        print(
            response.text[:1000]
        )

        sys.exit(1)


# ============================================================
# Game list normalization
# ============================================================

def normalize_games(data):

    if isinstance(
        data,
        list
    ):

        return data

    if isinstance(
        data,
        dict
    ):

        for key in (
            "data",
            "games",
            "results",
            "items"
        ):

            value = data.get(
                key
            )

            if isinstance(
                value,
                list
            ):

                return value

    print(
        "ERROR: Could not find "
        "game list in API response."
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

        migrated = False

        for game_id, game_data in state["games"].items():

            if not isinstance(
                game_data,
                dict
            ):

                continue

            old_score = game_data.get(
                "score"
            )

            if old_score is None:
                continue

            new_score = normalize_score(
                old_score
            )

            if new_score is not None:

                if old_score != new_score:

                    print(
                        f"Migrating score "
                        f"{game_id}: "
                        f"{old_score} -> "
                        f"{new_score}"
                    )

                    game_data[
                        "score"
                    ] = new_score

                    migrated = True

        if migrated:

            save_state(
                state
            )

            print(
                "Old scores migrated."
            )

        return state

    except Exception as e:

        print(
            f"ERROR reading state.json: {e}"
        )

        sys.exit(1)


# ============================================================
# Dates
# ============================================================

def parse_datetime(value):

    if not value:
        return None

    if not isinstance(
        value,
        str
    ):

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
        "releaseDate"
    ):

        parsed = parse_datetime(
            game.get(field)
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
        f"  Release: "
        f"{release_date.isoformat()}"
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
# Discovery
# ============================================================

def get_candidate_games():

    candidates = {}

    endpoints = [
        "/game/upcoming",
        "/game/recently-released"
    ]

    for endpoint in endpoints:

        print(
            "\n=========================================="
        )

        print(
            f"Discovering games: "
            f"{endpoint}"
        )

        data = api_get(
            endpoint
        )

        games = normalize_games(
            data
        )

        print(
            f"Returned games: "
            f"{len(games)}"
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
# Game detail
# ============================================================

def get_game(game_id):

    return api_get(
        f"/game/{game_id}"
    )


# ============================================================
# Review data
# ============================================================

def get_reviews(game_id):

    try:

        data = api_get(
            f"/review/game/{game_id}"
        )

        if isinstance(
            data,
            list
        ):

            return data

        if isinstance(
            data,
            dict
        ):

            for key in (
                "data",
                "reviews",
                "results",
                "items"
            ):

                value = data.get(
                    key
                )

                if isinstance(
                    value,
                    list
                ):

                    return value

        return []

    except Exception as e:

        print(
            f"WARNING: Could not get "
            f"reviews: {e}"
        )

        return []


# ============================================================
# Review parsing
# ============================================================

def get_review_publication(review):

    publication = (
        review.get(
            "publication"
        )
        or review.get(
            "critic"
        )
        or review.get(
            "outlet"
        )
        or review.get(
            "source"
        )
    )

    if isinstance(
        publication,
        dict
    ):

        return (
            publication.get("name")
            or publication.get("displayName")
            or publication.get("title")
        )

    if isinstance(
        publication,
        str
    ):

        return publication

    return None


def get_review_score(review):

    for key in (
        "score",
        "rating",
        "ratingValue"
    ):

        value = review.get(
            key
        )

        if value is not None:

            return value

    return None


def get_review_score_display(review):

    score = get_review_score(
        review
    )

    if score is None:

        return None

    # Some API versions return:
    # score = 83
    # score = 4.5
    # score = {"value": 4.5, "max": 5}

    if isinstance(
        score,
        dict
    ):

        value = (
            score.get("value")
            or score.get("score")
        )

        maximum = (
            score.get("max")
            or score.get("outOf")
            or score.get("maximum")
        )

        if value is None:

            return None

        if maximum is not None:

            return (
                f"{format_number(value)}"
                f" / "
                f"{format_number(maximum)}"
            )

        return format_number(
            value
        )

    # Direct numeric score
    return format_number(
        score
    )


def format_number(value):

    try:

        number = float(
            value
        )

        if number.is_integer():

            return str(
                int(number)
            )

        return (
            f"{number:.2f}"
            .rstrip("0")
            .rstrip(".")
        )

    except (
        TypeError,
        ValueError
    ):

        return str(value)


# ============================================================
# Select top 5 critics
# ============================================================

def get_top_reviews(reviews):

    usable = []

    seen = set()

    for review in reviews:

        if not isinstance(
            review,
            dict
        ):

            continue

        publication = (
            get_review_publication(
                review
            )
        )

        score = (
            get_review_score_display(
                review
            )
        )

        if not publication or not score:

            continue

        key = publication.lower()

        if key in seen:

            continue

        seen.add(key)

        usable.append(
            {
                "publication": publication,
                "score": score
            }
        )

    return usable[:5]


# ============================================================
# Image helpers
# ============================================================

def get_image_url(game):

    images = game.get(
        "images"
    )

    if not isinstance(
        images,
        dict
    ):

        return None

    # Prefer wide banner
    candidates = [
        images.get("banner"),
        images.get("masthead"),
        images.get("box"),
        images.get("square")
    ]

    for value in candidates:

        if isinstance(
            value,
            str
        ):

            return value

    return None


def download_image(url):

    if not url:

        return None

    try:

        response = requests.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        return Image.open(
            BytesIO(
                response.content
            )
        ).convert(
            "RGB"
        )

    except Exception as e:

        print(
            f"WARNING: Could not "
            f"download image: {e}"
        )

        return None


def crop_cover(image):

    if image is None:

        return Image.new(
            "RGB",
            (
                700,
                430
            ),
            (
                35,
                38,
                45
            )
        )

    target_ratio = 700 / 430

    width, height = image.size

    ratio = width / height

    if ratio > target_ratio:

        new_width = int(
            height
            * target_ratio
        )

        left = (
            width
            - new_width
        ) // 2

        image = image.crop(
            (
                left,
                0,
                left + new_width,
                height
            )
        )

    else:

        new_height = int(
            width
            / target_ratio
        )

        top = (
            height
            - new_height
        ) // 2

        image = image.crop(
            (
                0,
                top,
                width,
                top + new_height
            )
        )

    return image.resize(
        (
            700,
            430
        ),
        Image.Resampling.LANCZOS
    )


# ============================================================
# Drawing helpers
# ============================================================

def rounded_rectangle(
    draw,
    xy,
    radius,
    fill,
    outline=None,
    width=1
):

    draw.rounded_rectangle(
        xy,
        radius=radius,
        fill=fill,
        outline=outline,
        width=width
    )


def draw_score_ring(
    draw,
    center,
    radius,
    score,
    label,
    font_score,
    font_label
):

    x, y = center

    # Background ring
    draw.ellipse(
        (
            x - radius,
            y - radius,
            x + radius,
            y + radius
        ),
        outline=(
            65,
            72,
            84
        ),
        width=12
    )

    # Orange/red ring
    angle = (
        360
        * max(
            0,
            min(
                100,
                score
            )
        )
        / 100
    )

    draw.arc(
        (
            x - radius,
            y - radius,
            x + radius,
            y + radius
        ),
        start=-90,
        end=-90 + angle,
        fill=(
            255,
            85,
            40
        ),
        width=12
    )

    score_text = str(
        score
    )

    bbox = draw.textbbox(
        (0, 0),
        score_text,
        font=font_score
    )

    text_width = (
        bbox[2]
        - bbox[0]
    )

    text_height = (
        bbox[3]
        - bbox[1]
    )

    draw.text(
        (
            x - text_width / 2,
            y - text_height / 2 - 3
        ),
        score_text,
        fill=(
            245,
            247,
            250
        ),
        font=font_score
    )

    bbox = draw.textbbox(
        (0, 0),
        label,
        font=font_label
    )

    label_width = (
        bbox[2]
        - bbox[0]
    )

    draw.text(
        (
            x - label_width / 2,
            y + radius + 12
        ),
        label,
        fill=(
            170,
            180,
            195
        ),
        font=font_label
    )


# ============================================================
# Create card
# ============================================================

def create_card(
    game,
    top_reviews,
    output_path,
    old_score=None
):

    width = CARD_WIDTH
    height = CARD_HEIGHT

    # --------------------------------------------------------
    # Colors
    # --------------------------------------------------------

    bg = (
        23,
        28,
        38
    )

    panel = (
        29,
        35,
        47
    )

    white = (
        245,
        247,
        250
    )

    muted = (
        170,
        180,
        195
    )

    orange = (
        255,
        82,
        35
    )

    blue = (
        55,
        160,
        255
    )

    line = (
        55,
        64,
        80
    )

    # --------------------------------------------------------
    # Canvas
    # --------------------------------------------------------

    image = Image.new(
        "RGB",
        (
            width,
            height
        ),
        bg
    )

    draw = ImageDraw.Draw(
        image
    )

    # --------------------------------------------------------
    # Fonts
    # --------------------------------------------------------

    font_title = get_font(
        38,
        True
    )

    font_score = get_font(
        48,
        True
    )

    font_big = get_font(
        42,
        True
    )

    font_label = get_font(
        20,
        False
    )

    font_media = get_font(
        22,
        False
    )

    font_media_score = get_font(
        22,
        True
    )

    font_small = get_font(
        18,
        False
    )

    font_small_bold = get_font(
        18,
        True
    )

    # --------------------------------------------------------
    # Outer border
    # --------------------------------------------------------

    rounded_rectangle(
        draw,
        (
            18,
            18,
            width - 18,
            height - 18
        ),
        18,
        fill=bg,
        outline=line,
        width=3
    )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    name = game.get(
        "name",
        "Unknown Game"
    )

    title = name

    # Prevent extremely long title
    while (
        draw.textbbox(
            (0, 0),
            title,
            font=font_title
        )[2]
        > 760
        and len(title) > 10
    ):

        title = title[:-4].rstrip() + "..."

    draw.text(
        (
            48,
            45
        ),
        title,
        fill=white,
        font=font_title
    )

    # OpenCritic text
    draw.text(
        (
            1010,
            54
        ),
        "OpenCritic",
        fill=white,
        font=font_small_bold
    )

    # --------------------------------------------------------
    # Game image
    # --------------------------------------------------------

    image_url = get_image_url(
        game
    )

    cover = download_image(
        image_url
    )

    cover = crop_cover(
        cover
    )

    cover_x = 48
    cover_y = 115

    image.paste(
        cover,
        (
            cover_x,
            cover_y
        )
    )

    # Image border
    draw.rounded_rectangle(
        (
            cover_x,
            cover_y,
            cover_x + 700,
            cover_y + 430
        ),
        radius=12,
        outline=line,
        width=3
    )

    # --------------------------------------------------------
    # Main score
    # --------------------------------------------------------

    score = normalize_score(
        game.get(
            "topCriticScore"
        )
    )

    if score is None:
        score = 0

    draw_score_ring(
        draw,
        (
            870,
            190
        ),
        58,
        score,
        "Top Critic Average",
        font_score,
        font_label
    )

    # --------------------------------------------------------
    # Score change
    # --------------------------------------------------------

    if old_score is not None:

        change = (
            score
            - old_score
        )

        if change > 0:

            change_text = (
                f"+{change}"
            )

        else:

            change_text = str(
                change
            )

        draw.text(
            (
                820,
                290
            ),
            f"{old_score} → {score} "
            f"({change_text})",
            fill=orange,
            font=font_small_bold
        )

    # --------------------------------------------------------
    # Top 5 media
    # --------------------------------------------------------

    media_y = 330

    for review in top_reviews:

        publication = review[
            "publication"
        ]

        review_score = review[
            "score"
        ]

        # Keep long publication names readable
        if len(publication) > 22:

            publication = (
                publication[:20]
                + "..."
            )

        draw.text(
            (
                800,
                media_y
            ),
            publication,
            fill=white,
            font=font_media
        )

        bbox = draw.textbbox(
            (
                0,
                0
            ),
            review_score,
            font=font_media_score
        )

        score_width = (
            bbox[2]
            - bbox[0]
        )

        draw.text(
            (
                1125 - score_width,
                media_y
            ),
            review_score,
            fill=white,
            font=font_media_score
        )

        media_y += 48

    # --------------------------------------------------------
    # Critics recommend
    # --------------------------------------------------------

    recommended = game.get(
        "percentRecommended"
    )

    if recommended is not None:

        try:

            recommended = round(
                float(
                    recommended
                )
            )

        except (
            TypeError,
            ValueError
        ):

            recommended = None

    if recommended is not None:

        draw_score_ring(
            draw,
            (
                145,
                595
            ),
            45,
            recommended,
            "Critics Recommend",
            get_font(
                30,
                True
            ),
            font_small
        )

    # --------------------------------------------------------
    # Review count
    # --------------------------------------------------------

    review_count = game.get(
        "numTopCriticReviews"
    )

    if review_count is None:

        review_count = 0

    draw.text(
        (
            275,
            565
        ),
        str(
            review_count
        ),
        fill=white,
        font=font_big
    )

    draw.text(
        (
            275,
            615
        ),
        "Critic Reviews",
        fill=muted,
        font=font_small
    )

    # --------------------------------------------------------
    # Footer
    # --------------------------------------------------------

    draw.text(
        (
            915,
            620
        ),
        "View on OpenCritic ↗",
        fill=blue,
        font=font_small_bold
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    image.save(
        output_path,
        "PNG",
        optimize=True
    )


# ============================================================
# Discord upload
# ============================================================

def send_discord(
    game,
    top_reviews,
    old_score=None
):

    output_path = Path(
        "opencritic_card.png"
    )

    create_card(
        game,
        top_reviews,
        output_path,
        old_score
    )

    opencritic_url = (
        get_opencritic_url(
            game
        )
    )

    name = game.get(
        "name",
        "Unknown Game"
    )

    payload = {
        "username": "OpenCritic",
        "embeds": [
            {
                "title": name,
                "url": opencritic_url,
                "image": {
                    "url":
                        "attachment://"
                        "opencritic_card.png"
                }
            }
        ]
    }

    print(
        f"OpenCritic URL: "
        f"{opencritic_url}"
    )

    with open(
        output_path,
        "rb"
    ) as image_file:

        response = requests.post(
            DISCORD_WEBHOOK,
            data={
                "payload_json":
                    json.dumps(
                        payload,
                        ensure_ascii=False
                    )
            },
            files={
                "file":
                    (
                        "opencritic_card.png",
                        image_file,
                        "image/png"
                    )
            },
            timeout=60
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
        "Discord notification sent."
    )


# ============================================================
# Main
# ============================================================

def main():

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

    candidates = get_candidate_games()

    state_changed = False

    for summary in candidates:

        game_id = summary.get(
            "id"
        )

        if game_id is None:
            continue

        game_id = str(
            game_id
        )

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

        raw_score = game.get(
            "topCriticScore"
        )

        score = normalize_score(
            raw_score
        )

        print(
            f"Full game: {name}"
        )

        print(
            f"Raw Score: {raw_score}"
        )

        print(
            f"Normalized Score: {score}"
        )

        # ----------------------------------------------------
        # No score
        # ----------------------------------------------------

        if score is None:

            print(
                "No score yet."
            )

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
        # First run
        # ----------------------------------------------------

        if not initialized:

            print(
                "FIRST RUN:"
            )

            print(
                "Saving current integer "
                "score as baseline."
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
        # New game
        # ----------------------------------------------------

        if previous is None:

            print(
                "NEW GAME + SCORE."
            )

            print(
                "Getting critic reviews..."
            )

            reviews = get_reviews(
                game_id
            )

            top_reviews = get_top_reviews(
                reviews
            )

            print(
                f"Selected "
                f"{len(top_reviews)} "
                f"critic outlets."
            )

            send_discord(
                game,
                top_reviews
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
        # Normalize previous score
        # ----------------------------------------------------

        old_score = normalize_score(
            previous.get(
                "score"
            )
        )

        if previous.get(
            "score"
        ) != old_score:

            previous[
                "score"
            ] = old_score

            state_changed = True

        # ----------------------------------------------------
        # Score unlocked
        # ----------------------------------------------------

        if old_score is None:

            print(
                f"SCORE UNLOCKED: "
                f"None -> {score}"
            )

            print(
                "Getting critic reviews..."
            )

            reviews = get_reviews(
                game_id
            )

            top_reviews = get_top_reviews(
                reviews
            )

            send_discord(
                game,
                top_reviews
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
        # No change
        # ----------------------------------------------------

        if old_score == score:

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

        print(
            "Getting critic reviews..."
        )

        reviews = get_reviews(
            game_id
        )

        top_reviews = get_top_reviews(
            reviews
        )

        send_discord(
            game,
            top_reviews,
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
    # Save
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
