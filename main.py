from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random
import itertools
import asyncio
import time
from typing import Optional


# =========================================================
# APP
# =========================================================

app = FastAPI(title="Telegram Poker Backend")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# SETTINGS
# =========================================================

STARTING_CHIPS = 1000
SMALL_BLIND = 10
BIG_BLIND = 20
MAX_PLAYERS = 6

TURN_TIME = 15


BOT_NAMES = [
    "Bot Ali",
    "Bot Reza",
    "Bot Sara",
    "Bot Amir",
    "Bot Nima",
]


RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]


# =========================================================
# DATA
# =========================================================

players = {}

game = {
    "started": False,
    "deck": [],
    "community_cards": [],
    "pot": 0,
    "stage": "waiting",
    "current_player": None,
    "current_bet": 0,
    "dealer_index": 0,
    "acted_players": [],
    "winner": None,
    "message": "",
}


# =========================================================
# TIMER DATA
# =========================================================

turn_deadline = None
turn_token = 0
turn_task = None


# =========================================================
# MODELS
# =========================================================

class JoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: Optional[int] = 0


# =========================================================
# TIMER HELPERS
# =========================================================

def cancel_turn_timer():

    global turn_task
    global turn_token
    global turn_deadline

    turn_token += 1

    turn_deadline = None

    if turn_task and not turn_task.done():

        turn_task.cancel()

    turn_task = None


def get_remaining_time():

    if not game["started"]:
        return 0

    if not game["current_player"]:
        return 0

    if turn_deadline is None:
        return 0

    remaining = turn_deadline - time.monotonic()

    if remaining <= 0:
        return 0

    return int(remaining + 0.999)


async def turn_timer_worker(
    player_id,
    token
):

    global turn_deadline

    try:

        await asyncio.sleep(TURN_TIME)

    except asyncio.CancelledError:

        return

    # Make sure this is still the same timer.
    if token != turn_token:
        return

    # Game may have ended.
    if not game["started"]:
        return

    # Turn may have changed.
    if game["current_player"] != player_id:
        return

    player = players.get(player_id)

    if not player:
        return

    # -----------------------------------------------------
    # TIME IS UP
    # -----------------------------------------------------

    turn_deadline = None

    # If player somehow folded/all-in, skip safely.
    if player["folded"] or player["all_in"]:

        nxt = next_active_player(player_id)

        if nxt:

            game["current_player"] = nxt

            schedule_turn_timer(nxt)

        else:

            advance_street_if_needed()

        return

    # -----------------------------------------------------
    # BOT TIMEOUT
    # -----------------------------------------------------

    if player.get("is_bot", False):

        bot_action, bot_amount = bot_decision(player)

        result = process_action(
            player_id,
            bot_action,
            bot_amount
        )

    # -----------------------------------------------------
    # HUMAN TIMEOUT
    # -----------------------------------------------------

    else:

        current_bet = game["current_bet"]

        call_amount = max(
            0,
            current_bet - player["bet"]
        )

        # If check is possible:
        # automatically check.
        if call_amount == 0:

            result = process_action(
                player_id,
                "check",
                0
            )

        # Otherwise automatically fold.
        else:

            result = process_action(
                player_id,
                "fold",
                0
            )

    # -----------------------------------------------------
    # START NEXT PLAYER TIMER
    # -----------------------------------------------------

    if result.get("success"):

        if (
            game["started"]
            and game["current_player"]
        ):

            schedule_turn_timer(
                game["current_player"]
            )


def schedule_turn_timer(player_id):

    global turn_task
    global turn_token
    global turn_deadline

    cancel_turn_timer()

    if not game["started"]:
        return

    if not player_id:
        return

    if player_id not in players:
        return

    player = players[player_id]

    if player["folded"]:
        return

    if player["all_in"]:
        return

    turn_token += 1

    current_token = turn_token

    turn_deadline = (
        time.monotonic() + TURN_TIME
    )

    try:

        loop = asyncio.get_running_loop()

        turn_task = loop.create_task(
            turn_timer_worker(
                player_id,
                current_token
            )
        )

    except RuntimeError:

        turn_task = None


# =========================================================
# CARD FUNCTIONS
# =========================================================

def create_deck():

    return [
        rank + suit
        for rank in RANKS
        for suit in SUITS
    ]


def card_rank(card):

    return RANKS.index(card[0]) + 2


def card_suit(card):

    return card[1]


def deal_card():

    if not game["deck"]:
        return None

    return game["deck"].pop()


# =========================================================
# HAND EVALUATION
# =========================================================

def evaluate_five(cards):

    values = sorted(
        [card_rank(c) for c in cards],
        reverse=True
    )

    counts = {}

    for v in values:

        counts[v] = (
            counts.get(v, 0) + 1
        )

    unique = sorted(
        set(values),
        reverse=True
    )

    straight_high = None

    if 14 in unique:

        unique.append(1)

    for i in range(
        len(unique) - 4
    ):

        group = unique[i:i + 5]

        if group[0] - group[4] == 4:

            straight_high = group[0]

            break

    flush = (
        len(
            set(
                card_suit(c)
                for c in cards
            )
        ) == 1
    )

    if flush and straight_high:

        return (
            8,
            straight_high
        )

    quads = sorted(
        [
            v
            for v, c in counts.items()
            if c == 4
        ],
        reverse=True
    )

    if quads:

        kicker = max(
            v
            for v in values
            if v != quads[0]
        )

        return (
            7,
            quads[0],
            kicker
        )

    trips = sorted(
        [
            v
            for v, c in counts.items()
            if c == 3
        ],
        reverse=True
    )

    pairs = sorted(
        [
            v
            for v, c in counts.items()
            if c == 2
        ],
        reverse=True
    )

    if trips and pairs:

        return (
            6,
            trips[0],
            pairs[0]
        )

    if len(trips) >= 2:

        return (
            6,
            trips[0],
            trips[1]
        )

    if flush:

        return (
            5,
            *values
        )

    if straight_high:

        return (
            4,
            straight_high
        )

    if trips:

        kickers = sorted(
            [
                v
                for v in values
                if v != trips[0]
            ],
            reverse=True
        )[:2]

        return (
            3,
            trips[0],
            *kickers
        )

    if len(pairs) >= 2:

        high_pair = pairs[0]
        low_pair = pairs[1]

        kicker = max(
            v
            for v in values
            if v != high_pair
            and v != low_pair
        )

        return (
            2,
            high_pair,
            low_pair,
            kicker
        )

    if len(pairs) == 1:

        pair = pairs[0]

        kickers = sorted(
            [
                v
                for v in values
                if v != pair
            ],
            reverse=True
        )[:3]

        return (
            1,
            pair,
            *kickers
        )

    return (
        0,
        *values
    )


def evaluate_hand(cards):

    if len(cards) < 5:

        values = sorted(
            [
                card_rank(c)
                for c in cards
            ],
            reverse=True
        )

        return (
            0,
            *values
        )

    best = None

    for combo in itertools.combinations(
        cards,
        5
    ):

        score = evaluate_five(
            combo
        )

        if (
            best is None
            or score > best
        ):

            best = score

    return best


# =========================================================
# ACTIVE PLAYERS
# =========================================================

def active_players():

    return [
        p
        for p in players.values()
        if not p["folded"]
        and p["chips"] > 0
    ]


def players_in_hand():

    return [
        p
        for p in players.values()
        if not p["folded"]
    ]


# =========================================================
# PLAYER ORDER
# =========================================================

def ordered_player_ids():

    return list(players.keys())


def next_active_player(
    current_id
):

    ids = ordered_player_ids()

    if not ids:

        return None

    try:

        index = ids.index(
            current_id
        )

    except ValueError:

        index = -1

    for step in range(
        1,
        len(ids) + 1
    ):

        candidate_id = ids[
            (index + step) % len(ids)
        ]

        p = players.get(
            candidate_id
        )

        if not p:
            continue

        # CRITICAL:
        # Folded players are skipped.
        if p["folded"]:
            continue

        # All-in players are skipped.
        if p["all_in"]:
            continue

        if p["chips"] <= 0:
            continue

        return candidate_id

    return None


def first_player_postflop():

    ids = ordered_player_ids()

    if not ids:
        return None

    dealer_id = ids[
        game["dealer_index"] % len(ids)
    ]

    return next_active_player(
        dealer_id
    )


# =========================================================
# BETS
# =========================================================

def put_bet(
    player,
    amount
):

    amount = min(
        amount,
        player["chips"]
    )

    player["chips"] -= amount

    player["bet"] += amount

    player["total_bet"] += amount

    if player["chips"] == 0:

        player["all_in"] = True

    game["pot"] += amount

    return amount


def post_blind(
    player,
    amount
):

    return put_bet(
        player,
        amount
    )


# =========================================================
# NEW STREET
# =========================================================

def reset_street():

    for p in players.values():

        p["bet"] = 0
        p["acted"] = False

    game["current_bet"] = 0


def deal_flop():

    if len(
        game["community_cards"]
    ) >= 3:

        return

    if game["deck"]:

        game["deck"].pop()

    for _ in range(3):

        card = deal_card()

        if card:

            game[
                "community_cards"
            ].append(card)


def deal_turn():

    if game["deck"]:

        game["deck"].pop()

    card = deal_card()

    if card:

        game[
            "community_cards"
        ].append(card)


def deal_river():

    if game["deck"]:

        game["deck"].pop()

    card = deal_card()

    if card:

        game[
            "community_cards"
        ].append(card)


def start_street(
    stage
):

    cancel_turn_timer()

    reset_street()

    game["stage"] = stage

    if stage == "flop":

        deal_flop()

    elif stage == "turn":

        deal_turn()

    elif stage == "river":

        deal_river()

    first = first_player_postflop()

    if first:

        game["current_player"] = first

        schedule_turn_timer(
            first
        )


# =========================================================
# STREET CHECK
# =========================================================

def betting_finished():

    alive = [
        p
        for p in players.values()
        if not p["folded"]
    ]

    playable = [
        p
        for p in alive
        if not p["all_in"]
    ]

    if len(alive) <= 1:

        return True

    if not playable:

        return True

    for p in playable:

        if not p["acted"]:

            return False

        if (
            p["bet"]
            != game["current_bet"]
        ):

            return False

    return True


def advance_street_if_needed():

    if len(
        players_in_hand()
    ) <= 1:

        finish_hand()

        return True

    if not betting_finished():

        return False

    if game["stage"] == "preflop":

        start_street(
            "flop"
        )

        return True

    if game["stage"] == "flop":

        start_street(
            "turn"
        )

        return True

    if game["stage"] == "turn":

        start_street(
            "river"
        )

        return True

    if game["stage"] == "river":

        finish_hand()

        return True

    return False


# =========================================================
# BOT AI
# =========================================================

def bot_strength(
    player
):

    cards = player["cards"]

    if len(cards) < 2:

        return 0.20

    a = card_rank(
        cards[0]
    )

    b = card_rank(
        cards[1]
    )

    strength = 0.20

    if a == b:

        strength += 0.45

    if a >= 12:

        strength += 0.15

    if b >= 12:

        strength += 0.15

    if (
        card_suit(cards[0])
        ==
        card_suit(cards[1])
    ):

        strength += 0.10

    if abs(a - b) <= 2:

        strength += 0.05

    community = game[
        "community_cards"
    ]

    if community:

        score = evaluate_hand(
            cards + community
        )

        category = score[0]

        if category >= 5:

            strength = 0.95

        elif category == 4:

            strength = 0.90

        elif category == 3:

            strength = 0.85

        elif category == 2:

            strength = 0.75

        elif category == 1:

            strength = max(
                strength,
                0.55
            )

    return min(
        strength,
        0.99
    )


def bot_decision(
    player
):

    strength = bot_strength(
        player
    )

    current = game[
        "current_bet"
    ]

    my_bet = player["bet"]

    to_call = max(
        0,
        current - my_bet
    )

    if strength < 0.30:

        if to_call == 0:

            return (
                "check",
                0
            )

        if to_call <= max(
            10,
            player["chips"] * 0.03
        ):

            return (
                "call",
                0
            )

        if random.random() < 0.20:

            return (
                "call",
                0
            )

        return (
            "fold",
            0
        )

    if strength < 0.60:

        if to_call == 0:

            if random.random() < 0.25:

                target = (
                    current
                    + BIG_BLIND
                )

                return (
                    "raise",
                    target
                )

            return (
                "check",
                0
            )

        if (
            to_call
            <= player["chips"] * 0.20
        ):

            return (
                "call",
                0
            )

        return (
            "fold",
            0
        )

    if strength < 0.80:

        if to_call == 0:

            target = max(
                BIG_BLIND * 2,
                current
                + BIG_BLIND * 2
            )

            return (
                "raise",
                target
            )

        if (
            to_call
            <= player["chips"] * 0.40
        ):

            return (
                "call",
                0
            )

        if random.random() < 0.25:

            return (
                "allin",
                0
            )

        return (
            "fold",
            0
        )

    if random.random() < 0.35:

        return (
            "allin",
            0
        )

    target = max(
        current + BIG_BLIND * 3,
        int(
            player["bet"]
            + player["chips"] * 0.40
        )
    )

    return (
        "raise",
        target
    )


# =========================================================
# PROCESS ACTION
# =========================================================

def process_action(
    user_id,
    action,
    amount=0
):

    player = players.get(
        user_id
    )

    if not player:

        return {
            "success": False,
            "message": "Player not found"
        }

    # -----------------------------------------------------
    # ONLY CURRENT PLAYER CAN ACT
    # -----------------------------------------------------

    if (
        game["current_player"]
        != user_id
    ):

        return {
            "success": False,
            "message": "It is not your turn"
        }

    # -----------------------------------------------------
    # FOLDED PLAYER CAN NEVER ACT
    # -----------------------------------------------------

    if player["folded"]:

        return {
            "success": False,
            "message": "Player already folded"
        }

    if player["all_in"]:

        return {
            "success": False,
            "message": "Player is all-in"
        }

    action = (
        action
        .lower()
        .strip()
    )

    current_bet = game[
        "current_bet"
    ]

    call_amount = max(
        0,
        current_bet
        - player["bet"]
    )

    # -----------------------------------------------------
    # FOLD
    # -----------------------------------------------------

    if action == "fold":

        player["folded"] = True

        player["acted"] = True

        # IMPORTANT:
        # Cancel current timer immediately.
        cancel_turn_timer()

        # If only one player remains,
        # finish immediately.
        if len(
            players_in_hand()
        ) <= 1:

            finish_hand()

            return {
                "success": True,
                "message":
                    "Folded. Hand finished."
            }

    # -----------------------------------------------------
    # CHECK
    # -----------------------------------------------------

    elif action == "check":

        if call_amount > 0:

            return {
                "success": False,
                "message":
                    "Cannot check. You must call."
            }

        player["acted"] = True

        cancel_turn_timer()

    # -----------------------------------------------------
    # CALL
    # -----------------------------------------------------

    elif action == "call":

        if call_amount > 0:

            put_bet(
                player,
                call_amount
            )

        player["acted"] = True

        cancel_turn_timer()

    # -----------------------------------------------------
    # RAISE
    # -----------------------------------------------------

    elif action == "raise":

        try:

            target_bet = int(
                amount or 0
            )

        except:

            target_bet = 0

        minimum_raise = max(
            BIG_BLIND,
            current_bet
            + BIG_BLIND
        )

        if target_bet <= current_bet:

            return {
                "success": False,
                "message":
                    f"Raise must be above {current_bet}"
            }

        if target_bet < minimum_raise:

            target_bet = minimum_raise

        additional = (
            target_bet
            - player["bet"]
        )

        if additional <= 0:

            return {
                "success": False,
                "message":
                    "Invalid raise"
            }

        if additional >= player["chips"]:

            put_bet(
                player,
                player["chips"]
            )

            game[
                "current_bet"
            ] = player["bet"]

        else:

            put_bet(
                player,
                additional
            )

            game[
                "current_bet"
            ] = player["bet"]

        # Everyone else must act again.
        for p in players.values():

            if (
                not p["folded"]
                and not p["all_in"]
            ):

                p["acted"] = False

        player["acted"] = True

        cancel_turn_timer()

    # -----------------------------------------------------
    # ALL IN
    # -----------------------------------------------------

    elif action == "allin":

        if player["chips"] <= 0:

            player["all_in"] = True

            player["acted"] = True

        else:

            put_bet(
                player,
                player["chips"]
            )

            if (
                player["bet"]
                > game["current_bet"]
            ):

                game[
                    "current_bet"
                ] = player["bet"]

                for p in players.values():

                    if (
                        not p["folded"]
                        and not p["all_in"]
                    ):

                        p["acted"] = False

            player["acted"] = True

        cancel_turn_timer()

    else:

        return {
            "success": False,
            "message":
                "Unknown action"
        }

    # -----------------------------------------------------
    # CHECK STREET
    # -----------------------------------------------------

    if advance_street_if_needed():

        return {
            "success": True,
            "message":
                "Action accepted"
        }

    # -----------------------------------------------------
    # FIND NEXT ACTIVE PLAYER
    # -----------------------------------------------------

    nxt = next_active_player(
        user_id
    )

    if nxt:

        # IMPORTANT:
        # This can NEVER be a folded player.
        game[
            "current_player"
        ] = nxt

        schedule_turn_timer(
            nxt
        )

    else:

        advance_street_if_needed()

    return {
        "success": True,
        "message":
            "Action accepted"
    }


# =========================================================
# ADD BOTS
# =========================================================

def add_bots():

    existing_bot_count = len([
        p
        for p in players.values()
        if p.get("is_bot")
    ])

    bot_number = existing_bot_count

    while len(players) < MAX_PLAYERS:

        name = BOT_NAMES[
            bot_number
            % len(BOT_NAMES)
        ]

        bot_id = (
            "bot_"
            + name.lower()
            .replace(" ", "_")
        )

        if bot_id in players:

            bot_number += 1

            continue

        players[bot_id] = {

            "user_id":
                bot_id,

            "name":
                name,

            "chips":
                STARTING_CHIPS,

            "cards":
                [],

            "folded":
                False,

            "all_in":
                False,

            "bet":
                0,

            "total_bet":
                0,

            "acted":
                False,

            "is_bot":
                True,
        }

        bot_number += 1


# =========================================================
# START HAND
# =========================================================

def start_hand():

    if len(players) < 2:

        return False

    cancel_turn_timer()

    game["started"] = True

    game["deck"] = create_deck()

    random.shuffle(
        game["deck"]
    )

    game[
        "community_cards"
    ] = []

    game["pot"] = 0

    game["stage"] = "preflop"

    game[
        "current_bet"
    ] = BIG_BLIND

    game["winner"] = None

    game["message"] = ""

    ids = ordered_player_ids()

    if not ids:

        return False

    if (
        game["dealer_index"]
        >= len(ids)
    ):

        game["dealer_index"] = 0

    for p in players.values():

        p["cards"] = []

        p["folded"] = False

        p["all_in"] = False

        p["bet"] = 0

        p["total_bet"] = 0

        p["acted"] = False

    # -----------------------------------------------------
    # DEAL TWO CARDS
    # -----------------------------------------------------

    for _ in range(2):

        for player_id in ids:

            player = players[
                player_id
            ]

            card = deal_card()

            if card:

                player[
                    "cards"
                ].append(card)

    # -----------------------------------------------------
    # BLINDS
    # -----------------------------------------------------

    n = len(ids)

    if n == 2:

        dealer_id = ids[
            game["dealer_index"]
            % n
        ]

        small_id = dealer_id

        big_id = ids[
            (
                game["dealer_index"]
                + 1
            ) % n
        ]

        post_blind(
            players[small_id],
            SMALL_BLIND
        )

        post_blind(
            players[big_id],
            BIG_BLIND
        )

        first = next_active_player(
            big_id
        )

    else:

        small_id = ids[
            (
                game["dealer_index"]
                + 1
            ) % n
        ]

        big_id = ids[
            (
                game["dealer_index"]
                + 2
            ) % n
        ]

        post_blind(
            players[small_id],
            SMALL_BLIND
        )

        post_blind(
            players[big_id],
            BIG_BLIND
        )

        first = next_active_player(
            big_id
        )

    if not first:

        game["started"] = False

        return False

    game[
        "current_player"
    ] = first

    return True


# =========================================================
# FINISH HAND
# =========================================================

def finish_hand():

    cancel_turn_timer()

    alive = [
        p
        for p in players.values()
        if not p["folded"]
    ]

    if not alive:

        game["started"] = False

        game[
            "current_player"
        ] = None

        return

    # -----------------------------------------------------
    # ONE PLAYER LEFT
    # -----------------------------------------------------

    if len(alive) == 1:

        winner = alive[0]

        winner["chips"] += (
            game["pot"]
        )

        game[
            "winner"
        ] = winner["name"]

        game[
            "message"
        ] = (
            f"{winner['name']} "
            f"wins {game['pot']} chips!"
        )

        game["pot"] = 0

        game["started"] = False

        game[
            "current_player"
        ] = None

        return

    # -----------------------------------------------------
    # SHOWDOWN
    # -----------------------------------------------------

    scores = []

    for p in alive:

        score = evaluate_hand(
            p["cards"]
            + game["community_cards"]
        )

        scores.append(
            (
                score,
                p
            )
        )

    best_score = max(
        score
        for score, p in scores
    )

    winners = [
        p
        for score, p in scores
        if score == best_score
    ]

    if not winners:

        return

    share = (
        game["pot"]
        // len(winners)
    )

    remainder = (
        game["pot"]
        % len(winners)
    )

    for index, winner in enumerate(
        winners
    ):

        amount = share

        if index == 0:

            amount += remainder

        winner["chips"] += amount

    if len(winners) == 1:

        game[
            "winner"
        ] = winners[0]["name"]

        game[
            "message"
        ] = (
            f"{winners[0]['name']} "
            f"wins {game['pot']} chips!"
        )

    else:

        names = ", ".join(
            p["name"]
            for p in winners
        )

        game[
            "winner"
        ] = names

        game[
            "message"
        ] = (
            f"Tie! {names} "
            f"split {game['pot']} chips."
        )

    game["pot"] = 0

    game["started"] = False

    game[
        "current_player"
    ] = None


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():

    return {
        "status":
            "online",

        "message":
            "Poker backend is running",

        "version":
            "7.0-15-second-turns"
    }


@app.get("/health")
def health():

    return {
        "status":
            "ok"
    }


# =========================================================
# JOIN
# =========================================================

@app.post("/join")
def join(
    req: JoinRequest
):

    user_id = str(
        req.user_id
    ).strip()

    if not user_id:

        return {
            "success":
                False,

            "message":
                "Invalid user ID"
        }

    if user_id in players:

        if not players[
            user_id
        ].get("is_bot", False):

            players[
                user_id
            ]["name"] = (
                req.name
                or "Player"
            )

        return {
            "success":
                True,

            "message":
                "Already joined",

            "user_id":
                user_id,

            "name":
                players[
                    user_id
                ]["name"]
        }

    if len(players) >= MAX_PLAYERS:

        return {
            "success":
                False,

            "message":
                "Table is full"
        }

    players[user_id] = {

        "user_id":
            user_id,

        "name":
            req.name
            or "Player",

        "chips":
            STARTING_CHIPS,

        "cards":
            [],

        "folded":
            False,

        "all_in":
            False,

        "bet":
            0,

        "total_bet":
            0,

        "acted":
            False,

        "is_bot":
            False,
    }

    return {
        "success":
            True,

        "message":
            "Joined",

        "user_id":
            user_id,

        "name":
            players[
                user_id
            ]["name"]
    }


# =========================================================
# PLAYERS
# =========================================================

@app.get("/players")
def get_players():

    result = []

    ids = ordered_player_ids()

    for p in players.values():

        result.append({

            "user_id":
                p["user_id"],

            "name":
                p["name"],

            "chips":
                p["chips"],

            "bet":
                p["bet"],

            "folded":
                p["folded"],

            "all_in":
                p["all_in"],

            "is_turn":
                (
                    game["started"]
                    and
                    game[
                        "current_player"
                    ]
                    ==
                    p["user_id"]
                ),

            "dealer":
                (
                    len(ids) > 0
                    and
                    ids[
                        game[
                            "dealer_index"
                        ]
                        % len(ids)
                    ]
                    ==
                    p["user_id"]
                ),

            "small_blind":
                False,

            "big_blind":
                False,

            "is_bot":
                p.get(
                    "is_bot",
                    False
                ),

        })

    return {
        "success":
            True,

        "players":
            result
    }


# =========================================================
# START
# =========================================================

@app.post("/start")
async def start():

    if game["started"]:

        return {
            "success":
                False,

            "message":
                "Game already started"
        }

    if len(players) == 1:

        add_bots()

    if len(players) < 2:

        return {
            "success":
                False,

            "message":
                "Need at least 2 players"
        }

    ok = start_hand()

    if not ok:

        return {
            "success":
                False,

            "message":
                "Could not start game"
        }

    # Start 15-second timer.
    schedule_turn_timer(
        game["current_player"]
    )

    return {
        "success":
            True,

        "started":
            True,

        "stage":
            game["stage"],

        "community_cards":
            game[
                "community_cards"
            ],

        "pot":
            game["pot"],

        "current_player":
            game[
                "current_player"
            ],

        "current_player_name":
            players[
                game[
                    "current_player"
                ]
            ]["name"],

        "current_bet":
            game[
                "current_bet"
            ],

        "turn_time":
            TURN_TIME,

        "turn_remaining":
            TURN_TIME,

        "message":
            "Game started"
    }


# =========================================================
# GAME
# =========================================================

@app.get("/game")
def get_game():

    current_name = None

    if (
        game[
            "current_player"
        ]
        in players
    ):

        current_name = players[
            game[
                "current_player"
            ]
        ]["name"]

    remaining = get_remaining_time()

    return {

        "success":
            True,

        "started":
            game["started"],

        "stage":
            game["stage"],

        "community_cards":
            game[
                "community_cards"
            ],

        "pot":
            game["pot"],

        "current_player":
            game[
                "current_player"
            ],

        "current_player_name":
            current_name,

        "current_bet":
            game[
                "current_bet"
            ],

        "winner":
            game["winner"],

        "message":
            game["message"],

        "dealer_index":
            game["dealer_index"],

        "turn_time":
            TURN_TIME,

        "turn_remaining":
            remaining,

        "turn_deadline":
            (
                int(
                    time.time() * 1000
                    + max(
                        0,
                        (
                            turn_deadline
                            - time.monotonic()
                        )
                    ) * 1000
                )
                if turn_deadline
                and game["started"]
                else None
            )
    }


# =========================================================
# MY CARDS
# =========================================================

@app.get("/my-cards")
def my_cards(
    user_id: str
):

    user_id = str(
        user_id
    ).strip()

    player = players.get(
        user_id
    )

    if not player:

        return {
            "success":
                False,

            "message":
                "Player not found",

            "cards":
                []
        }

    return {

        "success":
            True,

        "user_id":
            user_id,

        "name":
            player["name"],

        "cards":
            player["cards"]
    }


# =========================================================
# ACTION
# =========================================================

@app.post("/action")
async def action(
    req: ActionRequest
):

    user_id = str(
        req.user_id
    ).strip()

    if user_id not in players:

        return {
            "success":
                False,

            "message":
                "Player not found"
        }

    # Humans only.
    if players[
        user_id
    ].get("is_bot"):

        return {
            "success":
                False,

            "message":
                "Bots act automatically"
        }

    result = process_action(
        user_id,
        req.action,
        req.amount or 0
    )

    # process_action already starts
    # the timer for the next player.
    return result


# =========================================================
# NEW HAND
# =========================================================

@app.post("/new-hand")
async def new_hand():

    if game["started"]:

        return {
            "success":
                False,

            "message":
                "Current hand is still running"
        }

    cancel_turn_timer()

    if players:

        game[
            "dealer_index"
        ] = (
            game[
                "dealer_index"
            ] + 1
        ) % len(players)

    for user_id in list(
        players.keys()
    ):

        p = players[
            user_id
        ]

        if p["chips"] <= 0:

            p["chips"] = (
                STARTING_CHIPS
            )

    ok = start_hand()

    if not ok:

        return {
            "success":
                False,

            "message":
                "Could not start new hand"
        }

    schedule_turn_timer(
        game["current_player"]
    )

    return {

        "success":
            True,

        "message":
            "New hand started",

        "turn_time":
            TURN_TIME,

        "turn_remaining":
            TURN_TIME
    }


# =========================================================
# RESET
# =========================================================

@app.post("/reset")
def reset():

    cancel_turn_timer()

    players.clear()

    game["started"] = False

    game["deck"] = []

    game[
        "community_cards"
    ] = []

    game["pot"] = 0

    game["stage"] = "waiting"

    game[
        "current_player"
    ] = None

    game[
        "current_bet"
    ] = 0

    game[
        "dealer_index"
    ] = 0

    game[
        "acted_players"
    ] = []

    game["winner"] = None

    game["message"] = ""

    return {

        "success":
            True,

        "message":
            "Game reset"
    }
