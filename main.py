from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import random
from collections import Counter
from itertools import combinations

app = FastAPI(
    title="Telegram Poker Backend",
    version="3.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# CONFIG
# =========================================================

STARTING_CHIPS = 1000
MAX_PLAYERS = 6
SMALL_BLIND = 10
BIG_BLIND = 20

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = [
    "2", "3", "4", "5", "6", "7",
    "8", "9", "10", "J", "Q", "K", "A"
]

HAND_NAMES = {
    8: "Straight Flush",
    7: "Four of a Kind",
    6: "Full House",
    5: "Flush",
    4: "Straight",
    3: "Three of a Kind",
    2: "Two Pair",
    1: "One Pair",
    0: "High Card"
}


# =========================================================
# MODELS
# =========================================================

class JoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: Optional[int] = None


# =========================================================
# STATE
# =========================================================

players = []

game = {
    "started": False,
    "stage": "waiting",
    "pot": 0,
    "community_cards": [],
    "deck": [],
    "current_player": None,
    "current_bet": 0,
    "winner": None,
    "message": "Waiting for game",
    "dealer_index": 0,
    "round_bet": 0,
    "acted_players": [],
}


# =========================================================
# DECK
# =========================================================

def create_deck():
    return [
        rank + suit
        for suit in SUITS
        for rank in RANKS
    ]


def reset_deck():
    game["deck"] = create_deck()
    random.shuffle(game["deck"])


def deal_card():
    if not game["deck"]:
        return None
    return game["deck"].pop()


# =========================================================
# PLAYER HELPERS
# =========================================================

def find_player(user_id):
    uid = str(user_id)

    for player in players:
        if str(player["user_id"]) == uid:
            return player

    return None


def serialize_player(player):
    return {
        "user_id": str(player["user_id"]),
        "name": player.get("name", "Player"),
        "chips": int(player.get("chips", 0)),
        "bet": int(player.get("bet", 0)),
        "folded": bool(player.get("folded", False)),
        "all_in": bool(player.get("all_in", False)),
        "is_turn": (
            str(game["current_player"])
            == str(player["user_id"])
        )
    }


def eligible_players():
    return [
        p for p in players
        if not p.get("folded", False)
    ]


def action_players():
    return [
        p for p in players
        if not p.get("folded", False)
        and not p.get("all_in", False)
        and p.get("chips", 0) > 0
    ]


def collect_pot():
    total = 0

    for p in players:
        total += int(p.get("bet", 0))

    game["pot"] = total


def total_bet(player):
    return int(player.get("bet", 0))


def current_max_bet():
    return max(
        [total_bet(p) for p in players],
        default=0
    )


# =========================================================
# PUBLIC GAME
# =========================================================

def public_game():
    return {
        "started": game["started"],
        "stage": game["stage"],
        "pot": game["pot"],
        "community_cards": list(
            game["community_cards"]
        ),
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": game["winner"],
        "message": game["message"]
    }


def update_message():
    if not game["started"]:
        return

    if game["current_player"]:
        p = find_player(
            game["current_player"]
        )

        if p:
            game["message"] = (
                f"It's {p['name']}'s turn"
            )


# =========================================================
# TURN MANAGEMENT
# =========================================================

def next_player_after(user_id):
    if not players:
        return None

    start_index = 0

    for i, p in enumerate(players):
        if str(p["user_id"]) == str(user_id):
            start_index = i
            break

    for offset in range(1, len(players) + 1):
        index = (
            start_index + offset
        ) % len(players)

        p = players[index]

        if (
            not p.get("folded", False)
            and not p.get("all_in", False)
            and p.get("chips", 0) > 0
        ):
            return str(p["user_id"])

    return None


def first_action_player():
    """
    Preflop:
    first player after big blind.

    Postflop:
    first active player after dealer.
    """

    if not players:
        return None

    if len(players) == 2:
        if game["stage"] == "preflop":
            return str(
                players[
                    (game["dealer_index"] + 1)
                    % len(players)
                ]["user_id"]
            )

    if game["stage"] == "preflop":

        bb_index = (
            game["dealer_index"] + 2
        ) % len(players)

        for offset in range(
            len(players)
        ):
            index = (
                bb_index + offset
            ) % len(players)

            p = players[index]

            if (
                not p.get("folded", False)
                and not p.get("all_in", False)
                and p.get("chips", 0) > 0
            ):
                return str(
                    p["user_id"]
                )

    for offset in range(
        1,
        len(players) + 1
    ):
        index = (
            game["dealer_index"]
            + offset
        ) % len(players)

        p = players[index]

        if (
            not p.get("folded", False)
            and not p.get("all_in", False)
            and p.get("chips", 0) > 0
        ):
            return str(
                p["user_id"]
            )

    return None


# =========================================================
# ROUND MANAGEMENT
# =========================================================

def reset_round_bets():
    for p in players:
        p["bet"] = 0

    game["current_bet"] = 0
    game["acted_players"] = []


def reset_hand_data():
    for p in players:
        p["bet"] = 0
        p["folded"] = False
        p["all_in"] = False
        p["cards"] = []

    game["community_cards"] = []
    game["pot"] = 0
    game["current_bet"] = 0
    game["acted_players"] = []
    game["winner"] = None


def active_non_allin_count():
    return len([
        p for p in players
        if not p.get("folded", False)
        and not p.get("all_in", False)
    ])


def betting_round_complete():
    active = [
        p for p in players
        if not p.get("folded", False)
        and not p.get("all_in", False)
    ]

    if not active:
        return True

    max_bet = current_max_bet()

    for p in active:

        if p.get("chips", 0) > 0:
            if total_bet(p) != max_bet:
                return False

            if str(p["user_id"]) not in [
                str(x)
                for x in game["acted_players"]
            ]:
                return False

    return True


# =========================================================
# COMMUNITY CARDS
# =========================================================

def deal_flop():
    for _ in range(3):
        card = deal_card()

        if card:
            game["community_cards"].append(
                card
            )

    game["stage"] = "flop"


def deal_turn():
    card = deal_card()

    if card:
        game["community_cards"].append(
            card
        )

    game["stage"] = "turn"


def deal_river():
    card = deal_card()

    if card:
        game["community_cards"].append(
            card
        )

    game["stage"] = "river"


def advance_stage():
    """
    Called when betting round is complete.
    """

    if game["stage"] == "preflop":

        deal_flop()

    elif game["stage"] == "flop":

        deal_turn()

    elif game["stage"] == "turn":

        deal_river()

    elif game["stage"] == "river":

        finish_game()
        return

    game["acted_players"] = []
    game["current_bet"] = 0

    for p in players:
        p["bet"] = 0

    if game["started"]:

        game["current_player"] = (
            first_action_player()
        )

        if game["current_player"]:
            p = find_player(
                game["current_player"]
            )

            if p:
                game["message"] = (
                    f"{game['stage'].upper()} - "
                    f"{p['name']}'s turn"
                )


# =========================================================
# HAND EVALUATION
# =========================================================

def card_value(card):

    rank = card[:-1]

    values = {
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "7": 7,
        "8": 8,
        "9": 9,
        "10": 10,
        "J": 11,
        "Q": 12,
        "K": 13,
        "A": 14
    }

    return values.get(rank, 0)


def evaluate_five(cards):

    values = sorted(
        [card_value(c) for c in cards],
        reverse=True
    )

    counts = Counter(values)

    # Flush
    flush = len({
        c[-1]
        for c in cards
    }) == 1

    # Straight
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
        seq = unique[i:i + 5]

        if seq[0] - seq[4] == 4:
            straight_high = seq[0]
            break

    # Straight flush
    if flush and straight_high:
        return (
            8,
            straight_high
        )

    # Four
    four = sorted(
        [
            v for v, c in counts.items()
            if c == 4
        ],
        reverse=True
    )

    if four:
        kicker = max(
            v for v in values
            if v != four[0]
        )

        return (
            7,
            four[0],
            kicker
        )

    # Full house
    trips = sorted(
        [
            v for v, c in counts.items()
            if c >= 3
        ],
        reverse=True
    )

    pairs = sorted(
        [
            v for v, c in counts.items()
            if c >= 2
        ],
        reverse=True
    )

    if trips:

        remaining_pairs = [
            v for v in pairs
            if v != trips[0]
        ]

        if remaining_pairs:
            return (
                6,
                trips[0],
                remaining_pairs[0]
            )

    # Flush
    if flush:
        return (
            5,
            *values
        )

    # Straight
    if straight_high:
        return (
            4,
            straight_high
        )

    # Three
    if trips:
        trip = trips[0]

        kickers = sorted(
            [
                v for v in values
                if v != trip
            ],
            reverse=True
        )

        return (
            3,
            trip,
            *kickers[:2]
        )

    # Two pair
    pair_values = sorted(
        [
            v for v, c in counts.items()
            if c >= 2
        ],
        reverse=True
    )

    if len(pair_values) >= 2:

        high_pair = pair_values[0]
        low_pair = pair_values[1]

        kicker = max(
            v for v in values
            if v != high_pair
            and v != low_pair
        )

        return (
            2,
            high_pair,
            low_pair,
            kicker
        )

    # One pair
    if len(pair_values) == 1:

        pair = pair_values[0]

        kickers = sorted(
            [
                v for v in values
                if v != pair
            ],
            reverse=True
        )

        return (
            1,
            pair,
            *kickers[:3]
        )

    # High card
    return (
        0,
        *values
    )


def evaluate_hand(cards):

    if len(cards) < 5:
        return (
            0,
            *sorted(
                [
                    card_value(c)
                    for c in cards
                ],
                reverse=True
            )
        )

    best = None

    for combo in combinations(
        cards,
        5
    ):

        score = evaluate_five(
            list(combo)
        )

        if best is None or score > best:
            best = score

    return best


# =========================================================
# WINNER
# =========================================================

def determine_winners():

    active = [
        p for p in players
        if not p.get("folded", False)
    ]

    if not active:
        return []

    if len(active) == 1:
        return [active[0]]

    scored = []

    for p in active:

        cards = (
            p.get("cards", [])
            + game["community_cards"]
        )

        score = evaluate_hand(cards)

        scored.append(
            (
                score,
                p
            )
        )

    best_score = max(
        score
        for score, _ in scored
    )

    return [
        p
        for score, p in scored
        if score == best_score
    ]


def finish_game():

    collect_pot()

    winners = determine_winners()

    if not winners:

        game["winner"] = None
        game["message"] = "No winner"
        game["started"] = False
        game["stage"] = "finished"
        game["current_player"] = None

        return

    pot = int(game["pot"])

    share = pot // len(winners)

    remainder = pot % len(winners)

    names = []

    for i, winner in enumerate(winners):

        amount = share

        if i == 0:
            amount += remainder

        winner["chips"] += amount

        names.append(
            winner["name"]
        )

    cards = (
        winners[0].get("cards", [])
        + game["community_cards"]
    )

    score = evaluate_hand(cards)

    hand_name = HAND_NAMES.get(
        score[0],
        "Poker Hand"
    )

    game["winner"] = (
        "🏆 "
        + ", ".join(names)
        + f" wins {pot} chips"
        + f" with {hand_name}"
    )

    game["message"] = game["winner"]
    game["current_player"] = None
    game["started"] = False
    game["stage"] = "finished"


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "message": "Poker backend is running"
    }


@app.get("/health")
def health():

    return {
        "status": "ok",
        "players": len(players),
        "game_started": game["started"]
    }


# =========================================================
# JOIN
# =========================================================

@app.post("/join")
def join(request: JoinRequest):

    existing = find_player(
        request.user_id
    )

    if existing:

        existing["name"] = (
            request.name
            or existing["name"]
        )

        return {
            "success": True,
            "message": "Already joined",
            "player": serialize_player(
                existing
            )
        }

    if len(players) >= MAX_PLAYERS:

        return {
            "success": False,
            "detail": "Table is full"
        }

    player = {
        "user_id": str(
            request.user_id
        ),
        "name": (
            request.name
            or "Player"
        ),
        "chips": STARTING_CHIPS,
        "bet": 0,
        "folded": False,
        "all_in": False,
        "cards": []
    }

    players.append(player)

    return {
        "success": True,
        "message": "Joined poker table",
        "player": serialize_player(
            player
        )
    }


# =========================================================
# PLAYERS
# =========================================================

@app.get("/players")
def get_players():

    return {
        "success": True,
        "players": [
            serialize_player(p)
            for p in players
        ]
    }


# =========================================================
# GAME
# =========================================================

@app.get("/game")
def get_game():

    update_message()

    return public_game()


# =========================================================
# MY CARDS
# =========================================================

@app.get("/my-cards")
def my_cards(user_id: str):

    player = find_player(user_id)

    if not player:

        return {
            "success": False,
            "cards": []
        }

    return {
        "success": True,
        "cards": list(
            player.get("cards", [])
        )
    }


# =========================================================
# START GAME
# =========================================================

@app.post("/start")
def start_game():

    if len(players) < 2:

        return {
            "success": False,
            "detail": "At least 2 players are required"
        }

    reset_deck()
    reset_hand_data()

    game["started"] = True
    game["stage"] = "preflop"
    game["dealer_index"] = (
        game["dealer_index"]
        % len(players)
    )

    # Deal 2 cards to each player
    for _ in range(2):

        for p in players:

            card = deal_card()

            if card:
                p["cards"].append(card)

    # -----------------------------------------------------
    # BLINDS
    # -----------------------------------------------------

    if len(players) == 2:

        sb_index = game["dealer_index"]

        bb_index = (
            game["dealer_index"] + 1
        ) % len(players)

    else:

        sb_index = (
            game["dealer_index"] + 1
        ) % len(players)

        bb_index = (
            game["dealer_index"] + 2
        ) % len(players)

    sb = players[sb_index]
    bb = players[bb_index]

    sb_amount = min(
        SMALL_BLIND,
        sb["chips"]
    )

    bb_amount = min(
        BIG_BLIND,
        bb["chips"]
    )

    sb["chips"] -= sb_amount
    sb["bet"] += sb_amount

    bb["chips"] -= bb_amount
    bb["bet"] += bb_amount

    if sb["chips"] == 0:
        sb["all_in"] = True

    if bb["chips"] == 0:
        bb["all_in"] = True

    collect_pot()

    game["current_bet"] = BIG_BLIND

    game["current_player"] = (
        first_action_player()
    )

    game["message"] = (
        "Poker game started"
    )

    # If everyone somehow all-in
    if active_non_allin_count() == 0:

        while len(
            game["community_cards"]
        ) < 5:

            card = deal_card()

            if card:
                game["community_cards"].append(
                    card
                )

        game["stage"] = "river"

        finish_game()

    return {
        "success": True,
        "message": "Poker game started",
        "game": public_game()
    }


# =========================================================
# ACTION
# =========================================================

@app.post("/action")
def action(request: ActionRequest):

    player = find_player(
        request.user_id
    )

    if not player:

        return {
            "success": False,
            "detail": "Player not found"
        }

    if not game["started"]:

        return {
            "success": False,
            "detail": "Game has not started"
        }

    if str(
        game["current_player"]
    ) != str(
        request.user_id
    ):

        return {
            "success": False,
            "detail": "It is not your turn"
        }

    action_name = (
        request.action
        .lower()
        .strip()
    )

    max_bet = current_max_bet()

    # =====================================================
    # FOLD
    # =====================================================

    if action_name == "fold":

        player["folded"] = True

        game["acted_players"].append(
            str(player["user_id"])
        )

        remaining = [
            p for p in players
            if not p.get("folded", False)
        ]

        if len(remaining) == 1:

            finish_game()

            return {
                "success": True,
                "message": "You folded",
                "game": public_game()
            }

        game["message"] = (
            f"{player['name']} folded"
        )

    # =====================================================
    # CHECK
    # =====================================================

    elif action_name == "check":

        if player["bet"] < max_bet:

            return {
                "success": False,
                "detail": (
                    "You cannot check. "
                    f"Call {max_bet - player['bet']} chips"
                )
            }

        game["acted_players"].append(
            str(player["user_id"])
        )

        game["message"] = (
            f"{player['name']} checked"
        )

    # =====================================================
    # CALL
    # =====================================================

    elif action_name == "call":

        needed = (
            max_bet
            - player["bet"]
        )

        if needed <= 0:

            return {
                "success": False,
                "detail": "Nothing to call"
            }

        amount = min(
            needed,
            player["chips"]
        )

        player["chips"] -= amount
        player["bet"] += amount

        if player["chips"] == 0:

            player["all_in"] = True

        game["acted_players"].append(
            str(player["user_id"])
        )

        collect_pot()

        game["message"] = (
            f"{player['name']} called"
        )

    # =====================================================
    # RAISE
    # =====================================================

    elif action_name == "raise":

        if request.amount is None:

            return {
                "success": False,
                "detail": "Raise amount required"
            }

        amount = int(
            request.amount
        )

        if amount <= max_bet:

            return {
                "success": False,
                "detail": (
                    "Raise must be higher "
                    "than current bet"
                )
            }

        if amount <= player["bet"]:

            return {
                "success": False,
                "detail": "Invalid raise amount"
            }

        additional = (
            amount
            - player["bet"]
        )

        if additional > player["chips"]:

            return {
                "success": False,
                "detail": "Not enough chips"
            }

        player["chips"] -= additional
        player["bet"] = amount

        if player["chips"] == 0:
            player["all_in"] = True

        game["current_bet"] = amount

        # Everyone else needs to act again
        game["acted_players"] = [
            str(player["user_id"])
        ]

        collect_pot()

        game["message"] = (
            f"{player['name']} "
            f"raised to {amount}"
        )

    # =====================================================
    # ALL IN
    # =====================================================

    elif action_name in [
        "all-in",
        "allin"
    ]:

        amount = player["chips"]

        if amount <= 0:

            return {
                "success": False,
                "detail": "You have no chips"
            }

        player["bet"] += amount
        player["chips"] = 0
        player["all_in"] = True

        if player["bet"] > max_bet:

            game["current_bet"] = (
                player["bet"]
            )

            game["acted_players"] = [
                str(player["user_id"])
            ]

        else:

            game["acted_players"].append(
                str(player["user_id"])
            )

        collect_pot()

        game["message"] = (
            f"{player['name']} is ALL-IN"
        )

    else:

        return {
            "success": False,
            "detail": "Unknown action"
        }

    # =====================================================
    # CHECK FOR WIN / NEXT ROUND
    # =====================================================

    remaining = [
        p for p in players
        if not p.get("folded", False)
    ]

    if len(remaining) == 1:

        finish_game()

        return {
            "success": True,
            "message": game["message"],
            "game": public_game()
        }

    # If all remaining players are all-in
    if active_non_allin_count() == 0:

        while len(
            game["community_cards"]
        ) < 5:

            card = deal_card()

            if card:
                game["community_cards"].append(
                    card
                )

        game["stage"] = "river"

        finish_game()

        return {
            "success": True,
            "message": game["message"],
            "game": public_game()
        }

    # =====================================================
    # BETTING ROUND COMPLETE
    # =====================================================

    if betting_round_complete():

        advance_stage()

    else:

        next_player = next_player_after(
            player["user_id"]
        )

        game["current_player"] = (
            next_player
        )

        if next_player:

            next_p = find_player(
                next_player
            )

            if next_p:

                game["message"] = (
                    f"It's "
                    f"{next_p['name']}'s turn"
                )

    collect_pot()

    return {
        "success": True,
        "message": game["message"],
        "game": public_game()
    }


# =========================================================
# NEXT CARD
# =========================================================

@app.post("/next-card")
def next_card():

    # Kept for compatibility with old frontend.
    # Normal game now advances automatically.

    if not game["started"]:

        return {
            "success": False,
            "detail": "Game has not started"
        }

    return {
        "success": False,
        "detail": (
            "Community cards advance "
            "automatically after betting"
        )
    }


# =========================================================
# RESET
# =========================================================

@app.post("/reset")
def reset_game():

    game["started"] = False
    game["stage"] = "waiting"
    game["pot"] = 0
    game["community_cards"] = []
    game["deck"] = []
    game["current_player"] = None
    game["current_bet"] = 0
    game["winner"] = None
    game["message"] = "Waiting for game"
    game["acted_players"] = []

    for p in players:

        p["chips"] = STARTING_CHIPS
        p["bet"] = 0
        p["folded"] = False
        p["all_in"] = False
        p["cards"] = []

    return {
        "success": True,
        "message": "Game reset",
        "game": public_game()
    }


# =========================================================
# NEW HAND
# =========================================================

@app.post("/new-hand")
def new_hand():

    if len(players) < 2:

        return {
            "success": False,
            "detail": "At least 2 players are required"
        }

    # Rotate dealer
    game["dealer_index"] = (
        game["dealer_index"] + 1
    ) % len(players)

    return start_game()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )
