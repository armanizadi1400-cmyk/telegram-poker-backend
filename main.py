from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import random
from collections import Counter

app = FastAPI(title="Telegram Poker Backend", version="2.0")


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
# GAME STATE
# =========================================================

STARTING_CHIPS = 1000
MAX_PLAYERS = 6
SMALL_BLIND = 10
BIG_BLIND = 20

players = []
game = {
    "started": False,
    "stage": "waiting",
    "pot": 0,
    "community_cards": [],
    "deck": [],
    "current_player": None,
    "current_bet": BIG_BLIND,
    "winner": None,
    "message": "Waiting for game"
}


# =========================================================
# DECK
# =========================================================

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = [
    "2", "3", "4", "5", "6", "7", "8", "9",
    "10", "J", "Q", "K", "A"
]


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
# HELPERS
# =========================================================

def find_player(user_id):
    user_id = str(user_id)

    for player in players:
        if str(player["user_id"]) == user_id:
            return player

    return None


def active_players():
    return [
        p for p in players
        if not p.get("folded", False)
    ]


def serialize_player(player):
    return {
        "user_id": str(player["user_id"]),
        "name": player.get("name", "Player"),
        "chips": int(player.get("chips", 0)),
        "bet": int(player.get("bet", 0)),
        "folded": bool(player.get("folded", False)),
        "all_in": bool(player.get("all_in", False))
    }


def public_game():
    return {
        "started": game["started"],
        "stage": game["stage"],
        "pot": game["pot"],
        "community_cards": list(game["community_cards"]),
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": game["winner"],
        "message": game["message"]
    }


def update_message():
    if not game["started"]:
        game["message"] = "Waiting for game"
        return

    if game["current_player"]:
        p = find_player(game["current_player"])

        if p:
            game["message"] = (
                f"It's {p['name']}'s turn"
            )


def next_active_player(current_id=None):
    active = [
        p for p in players
        if not p.get("folded", False)
        and not p.get("all_in", False)
        and p.get("chips", 0) > 0
    ]

    if not active:
        return None

    if current_id is None:
        return str(active[0]["user_id"])

    ids = [str(p["user_id"]) for p in active]

    if str(current_id) not in ids:
        return str(active[0]["user_id"])

    index = ids.index(str(current_id))

    for i in range(1, len(active) + 1):
        candidate = active[(index + i) % len(active)]

        if (
            not candidate.get("folded", False)
            and not candidate.get("all_in", False)
            and candidate.get("chips", 0) > 0
        ):
            return str(candidate["user_id"])

    return None


def collect_bets():
    total = 0

    for player in players:
        total += int(player.get("bet", 0))

    game["pot"] = total


def reset_player_round_data():
    for player in players:
        player["bet"] = 0
        player["folded"] = False
        player["all_in"] = False
        player["cards"] = []


# =========================================================
# HAND EVALUATION
# =========================================================

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


def evaluate_hand(cards):
    if len(cards) < 5:
        return (0, 0)

    values = sorted(
        [card_value(c) for c in cards],
        reverse=True
    )

    counts = Counter(values)

    unique_values = sorted(
        set(values),
        reverse=True
    )

    # Straight
    straight_high = None

    if 14 in unique_values:
        unique_values.append(1)

    for i in range(len(unique_values) - 4):
        seq = unique_values[i:i + 5]

        if seq[0] - seq[4] == 4:
            straight_high = seq[0]
            break

    suits = [c[-1] for c in cards]

    flush_suit = None

    for suit in SUITS:
        suited = [
            card_value(c)
            for c in cards
            if c[-1] == suit
        ]

        if len(suited) >= 5:
            flush_suit = suit
            break

    # Straight flush
    if flush_suit:
        suited_values = sorted(
            {
                card_value(c)
                for c in cards
                if c[-1] == flush_suit
            },
            reverse=True
        )

        if 14 in suited_values:
            suited_values.append(1)

        for i in range(len(suited_values) - 4):
            seq = suited_values[i:i + 5]

            if seq[0] - seq[4] == 4:
                return (8, seq[0])

    four = [
        v for v, count in counts.items()
        if count >= 4
    ]

    if four:
        return (7, max(four))

    trips = sorted(
        [
            v for v, count in counts.items()
            if count >= 3
        ],
        reverse=True
    )

    pairs = sorted(
        [
            v for v, count in counts.items()
            if count >= 2
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
                trips[0] * 100 + remaining_pairs[0]
            )

    if flush_suit:
        return (
            5,
            max(
                card_value(c)
                for c in cards
                if c[-1] == flush_suit
            )
        )

    if straight_high:
        return (4, straight_high)

    if trips:
        return (3, trips[0])

    if len(pairs) >= 2:
        return (
            2,
            pairs[0] * 100 + pairs[1]
        )

    if len(pairs) == 1:
        return (1, pairs[0])

    return (0, max(values))


def determine_winner():
    candidates = active_players()

    if len(candidates) == 1:
        return candidates[0]

    if not candidates:
        return None

    scored = []

    for player in candidates:
        cards = (
            player.get("cards", [])
            + game["community_cards"]
        )

        score = evaluate_hand(cards)

        scored.append(
            (
                score,
                player
            )
        )

    scored.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return scored[0][1]


def finish_game():
    winner = determine_winner()

    if winner is None:
        game["winner"] = None
        game["message"] = "No winner"
        return

    pot = int(game["pot"])

    winner["chips"] += pot

    hand_text = ""

    cards = (
        winner.get("cards", [])
        + game["community_cards"]
    )

    if len(cards) >= 5:
        category = evaluate_hand(cards)[0]
        hand_text = HAND_NAMES.get(
            category,
            "Poker Hand"
        )

    game["winner"] = (
        f"🏆 {winner['name']} wins "
        f"{pot} chips"
        + (
            f" with {hand_text}"
            if hand_text
            else ""
        )
    )

    game["current_player"] = None
    game["stage"] = "finished"
    game["started"] = False
    game["message"] = game["winner"]


# =========================================================
# ROOT / HEALTH
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
        existing["name"] = request.name or existing["name"]

        return {
            "success": True,
            "message": "Already joined",
            "player": serialize_player(existing)
        }

    if len(players) >= MAX_PLAYERS:
        raise HTTPException(
            status_code=400,
            detail="Table is full"
        )

    player = {
        "user_id": str(request.user_id),
        "name": request.name or "Player",
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
        "player": serialize_player(player)
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
    reset_player_round_data()

    game["started"] = True
    game["stage"] = "preflop"
    game["pot"] = 0
    game["community_cards"] = []
    game["winner"] = None
    game["current_bet"] = BIG_BLIND

    # Deal 2 cards to every player
    for _ in range(2):
        for player in players:
            card = deal_card()

            if card:
                player["cards"].append(card)

    # Blinds
    if len(players) >= 2:

        sb = players[0]
        bb = players[1]

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

    collect_bets()

    # First action after big blind
    if len(players) > 2:
        game["current_player"] = str(
            players[2]["user_id"]
        )
    else:
        game["current_player"] = str(
            players[0]["user_id"]
        )

    game["message"] = "Game started"

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

    if str(game["current_player"]) != str(
        request.user_id
    ):
        return {
            "success": False,
            "detail": "It is not your turn"
        }

    action_name = request.action.lower().strip()

    # -------------------------
    # FOLD
    # -------------------------

    if action_name == "fold":

        player["folded"] = True

        remaining = active_players()

        if len(remaining) <= 1:
            finish_game()

            return {
                "success": True,
                "message": "You folded",
                "game": public_game()
            }

    # -------------------------
    # CHECK
    # -------------------------

    elif action_name == "check":

        max_bet = max(
            [p["bet"] for p in players],
            default=0
        )

        if player["bet"] < max_bet:
            return {
                "success": False,
                "detail": "You cannot check"
            }

        game["message"] = (
            f"{player['name']} checked"
        )

    # -------------------------
    # CALL
    # -------------------------

    elif action_name == "call":

        max_bet = max(
            [p["bet"] for p in players],
            default=0
        )

        needed = max_bet - player["bet"]

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

        collect_bets()

        game["message"] = (
            f"{player['name']} called"
        )

    # -------------------------
    # RAISE
    # -------------------------

    elif action_name == "raise":

        if request.amount is None:
            return {
                "success": False,
                "detail": "Raise amount required"
            }

        amount = int(request.amount)

        if amount <= player["bet"]:
            return {
                "success": False,
                "detail": "Raise must be higher than current bet"
            }

        additional = (
            amount - player["bet"]
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

        collect_bets()

        game["message"] = (
            f"{player['name']} raised to {amount}"
        )

    # -------------------------
    # ALL IN
    # -------------------------

    elif action_name in ["all-in", "allin"]:

        amount = player["chips"]

        player["bet"] += amount
        player["chips"] = 0
        player["all_in"] = True

        if player["bet"] > game["current_bet"]:
            game["current_bet"] = player["bet"]

        collect_bets()

        game["message"] = (
            f"{player['name']} is ALL-IN"
        )

    else:

        return {
            "success": False,
            "detail": "Unknown action"
        }

    # Next player
    next_player = next_active_player(
        player["user_id"]
    )

    game["current_player"] = next_player

    # If nobody can act anymore
    available = [
        p for p in active_players()
        if not p.get("all_in", False)
        and p.get("chips", 0) > 0
    ]

    if not available:

        # Automatically complete board
        while len(game["community_cards"]) < 5:
            card = deal_card()

            if not card:
                break

            game["community_cards"].append(card)

        game["stage"] = "river"

        collect_bets()

        finish_game()

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

    if not game["started"]:
        return {
            "success": False,
            "detail": "Game has not started"
        }

    current_count = len(
        game["community_cards"]
    )

    # PREFLOP -> FLOP
    # IMPORTANT:
    # THREE CARDS ARE DEALT TOGETHER
    if current_count == 0:

        cards = []

        for _ in range(3):
            card = deal_card()

            if card:
                cards.append(card)

        game["community_cards"].extend(
            cards
        )

        game["stage"] = "flop"
        game["message"] = "Flop dealt: 3 cards"

    # FLOP -> TURN
    elif current_count == 3:

        card = deal_card()

        if not card:
            return {
                "success": False,
                "detail": "No cards left"
            }

        game["community_cards"].append(
            card
        )

        game["stage"] = "turn"
        game["message"] = "Turn dealt"

    # TURN -> RIVER
    elif current_count == 4:

        card = deal_card()

        if not card:
            return {
                "success": False,
                "detail": "No cards left"
            }

        game["community_cards"].append(
            card
        )

        game["stage"] = "river"
        game["message"] = "River dealt"

    # RIVER -> FINISH
    elif current_count == 5:

        collect_bets()
        finish_game()

    return {
        "success": True,
        "message": game["message"],
        "game": public_game()
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
    game["current_bet"] = BIG_BLIND
    game["winner"] = None
    game["message"] = "Waiting for game"

    for player in players:

        player["chips"] = STARTING_CHIPS
        player["bet"] = 0
        player["folded"] = False
        player["all_in"] = False
        player["cards"] = []

    return {
        "success": True,
        "message": "Game reset",
        "game": public_game()
    }


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
