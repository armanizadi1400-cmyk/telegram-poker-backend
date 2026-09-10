from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random
from collections import Counter
from typing import Optional


app = FastAPI(title="Telegram Poker Backend")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# SETTINGS
# =========================

RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]

STARTING_CHIPS = 1000
SMALL_BLIND = 10
BIG_BLIND = 20


# =========================
# PLAYERS
# =========================

players = {}


class Player:
    def __init__(self, user_id, name):
        self.user_id = user_id
        self.name = name

        self.chips = STARTING_CHIPS

        self.cards = []

        self.folded = False
        self.all_in = False

        # Bet in current betting round
        self.bet = 0

        # Total amount invested in this hand
        self.total_bet = 0


# =========================
# GAME
# =========================

game = {
    "started": False,

    "deck": [],

    "community_cards": [],

    "pot": 0,

    "stage": "waiting",

    "current_player": None,

    "current_bet": 0,

    "dealer_index": 0,

    "acted_players": set(),

    "winner": None,

    "message": ""
}


# =========================
# REQUEST MODELS
# =========================

class Action(BaseModel):
    user_id: str
    action: str
    amount: int = 0


# =========================
# DECK
# =========================

def create_deck():
    deck = []

    for suit in SUITS:
        for rank in RANKS:
            deck.append(rank + suit)

    random.shuffle(deck)

    return deck


# =========================
# PLAYER HELPERS
# =========================

def active_players():
    return [
        p for p in players.values()
        if not p.folded
    ]


def players_can_act():
    return [
        p for p in players.values()
        if not p.folded and not p.all_in
    ]


def get_players_data():

    result = []

    for p in players.values():

        result.append({
            "user_id": p.user_id,
            "name": p.name,
            "chips": p.chips,
            "cards": p.cards,
            "folded": p.folded,
            "all_in": p.all_in,
            "bet": p.bet,
            "total_bet": p.total_bet,
            "is_turn": p.user_id == game["current_player"]
        })

    return result


# =========================
# TURN SYSTEM
# =========================

def next_active_player(current_id: Optional[str] = None):

    ids = list(players.keys())

    if not ids:
        return None

    if current_id in ids:
        start = ids.index(current_id) + 1
    else:
        start = 0

    for i in range(len(ids)):

        index = (start + i) % len(ids)

        player = players[ids[index]]

        if not player.folded and not player.all_in:
            return player.user_id

    return None


def set_first_turn():

    can_act = players_can_act()

    if can_act:
        game["current_player"] = can_act[0].user_id
    else:
        game["current_player"] = None


# =========================
# BETTING
# =========================

def collect_current_bets():

    for p in players.values():

        if p.bet > 0:
            game["pot"] += p.bet
            p.bet = 0


def betting_round_finished():

    active = active_players()

    if len(active) <= 1:
        return True

    for p in active:

        if p.all_in:
            continue

        if p.user_id not in game["acted_players"]:
            return False

        if p.bet != game["current_bet"]:
            return False

    return True


def prepare_new_betting_round():

    for p in players.values():
        p.bet = 0

    game["current_bet"] = 0
    game["acted_players"] = set()
    game["current_player"] = None

    set_first_turn()


# =========================
# COMMUNITY CARDS
# =========================

def deal_flop():

    cards = [
        game["deck"].pop(),
        game["deck"].pop(),
        game["deck"].pop()
    ]

    game["community_cards"].extend(cards)

    game["stage"] = "flop"

    return cards


def deal_turn():

    card = game["deck"].pop()

    game["community_cards"].append(card)

    game["stage"] = "turn"

    return [card]


def deal_river():

    card = game["deck"].pop()

    game["community_cards"].append(card)

    game["stage"] = "river"

    return [card]


# =========================
# CARD EVALUATION
# =========================

def rank_value(rank):

    return RANKS.index(rank) + 2


def parse_card(card):

    return card[0], card[1]


def evaluate_five(cards):

    ranks = []
    suits = []

    for card in cards:

        rank, suit = parse_card(card)

        ranks.append(rank_value(rank))
        suits.append(suit)

    counts = Counter(ranks)

    unique = sorted(set(ranks), reverse=True)

    # Straight
    straight_high = None

    if 14 in unique:
        unique.append(1)

    for i in range(len(unique) - 4):

        group = unique[i:i + 5]

        if group[0] - group[4] == 4:
            straight_high = group[0]
            break

    flush = len(set(suits)) == 1

    # Straight flush
    if flush and straight_high:

        return (
            8,
            straight_high
        )

    count_values = sorted(
        counts.values(),
        reverse=True
    )

    # Four of a kind
    if 4 in count_values:

        four = max(
            rank for rank, count in counts.items()
            if count == 4
        )

        kicker = max(
            rank for rank, count in counts.items()
            if count != 4
        )

        return (
            7,
            four,
            kicker
        )

    # Full house
    triples = sorted(
        [
            rank
            for rank, count in counts.items()
            if count >= 3
        ],
        reverse=True
    )

    pairs = sorted(
        [
            rank
            for rank, count in counts.items()
            if count >= 2
        ],
        reverse=True
    )

    if triples:

        triple = triples[0]

        remaining_pairs = [
            rank
            for rank in pairs
            if rank != triple
        ]

        if remaining_pairs:

            return (
                6,
                triple,
                remaining_pairs[0]
            )

    # Flush
    if flush:

        return (
            5,
            *sorted(ranks, reverse=True)
        )

    # Straight
    if straight_high:

        return (
            4,
            straight_high
        )

    # Three of a kind
    if triples:

        triple = triples[0]

        kickers = sorted(
            [
                rank
                for rank in ranks
                if rank != triple
            ],
            reverse=True
        )[:2]

        return (
            3,
            triple,
            *kickers
        )

    # Two pair
    pair_ranks = sorted(
        [
            rank
            for rank, count in counts.items()
            if count >= 2
        ],
        reverse=True
    )

    if len(pair_ranks) >= 2:

        high_pair = pair_ranks[0]
        low_pair = pair_ranks[1]

        kicker = max(
            rank
            for rank in ranks
            if rank != high_pair
            and rank != low_pair
        )

        return (
            2,
            high_pair,
            low_pair,
            kicker
        )

    # One pair
    if len(pair_ranks) == 1:

        pair = pair_ranks[0]

        kickers = sorted(
            [
                rank
                for rank in ranks
                if rank != pair
            ],
            reverse=True
        )[:3]

        return (
            1,
            pair,
            *kickers
        )

    # High card
    return (
        0,
        *sorted(ranks, reverse=True)
    )


def best_hand(cards):

    if len(cards) < 5:
        return (0,)

    best = None

    from itertools import combinations

    for combo in combinations(cards, 5):

        score = evaluate_five(combo)

        if best is None or score > best:
            best = score

    return best


# =========================
# SHOWDOWN
# =========================

def finish_showdown():

    active = active_players()

    if not active:
        return None

    # One player left
    if len(active) == 1:

        winner = active[0]

        amount = game["pot"]

        winner.chips += amount

        game["winner"] = {
            "user_id": winner.user_id,
            "name": winner.name,
            "amount": amount
        }

        game["pot"] = 0

        game["started"] = False
        game["stage"] = "finished"
        game["current_player"] = None

        game["message"] = (
            f"{winner.name} wins {amount} chips!"
        )

        return winner

    # Evaluate hands
    rankings = []

    for player in active:

        cards = (
            player.cards
            + game["community_cards"]
        )

        score = best_hand(cards)

        rankings.append(
            (score, player)
        )

    rankings.sort(
        key=lambda x: x[0],
        reverse=True
    )

    best_score = rankings[0][0]

    winners = [
        player
        for score, player in rankings
        if score == best_score
    ]

    share = game["pot"] // len(winners)

    remainder = game["pot"] % len(winners)

    for i, winner in enumerate(winners):

        winner.chips += share

        if i < remainder:
            winner.chips += 1

    if len(winners) == 1:

        winner = winners[0]

        amount = share + remainder

        game["winner"] = {
            "user_id": winner.user_id,
            "name": winner.name,
            "amount": amount,
            "hand_score": list(best_score)
        }

        game["message"] = (
            f"{winner.name} wins {amount} chips!"
        )

    else:

        names = ", ".join(
            winner.name
            for winner in winners
        )

        game["winner"] = {
            "user_id": winners[0].user_id,
            "name": names,
            "amount": game["pot"]
        }

        game["message"] = (
            f"Split pot between {names}"
        )

    game["pot"] = 0

    game["started"] = False
    game["stage"] = "finished"
    game["current_player"] = None

    return winners


# =========================
# ADVANCE GAME
# =========================

def advance_stage():

    # Move current bets into pot
    collect_current_bets()

    active = active_players()

    if len(active) <= 1:

        finish_showdown()
        return

    # PREFLOP -> FLOP
    if len(game["community_cards"]) == 0:

        deal_flop()

        prepare_new_betting_round()

        return

    # FLOP -> TURN
    if len(game["community_cards"]) == 3:

        deal_turn()

        prepare_new_betting_round()

        return

    # TURN -> RIVER
    if len(game["community_cards"]) == 4:

        deal_river()

        prepare_new_betting_round()

        return

    # RIVER -> SHOWDOWN
    if len(game["community_cards"]) == 5:

        game["stage"] = "showdown"

        finish_showdown()


# =========================
# ROOT
# =========================

@app.get("/")
def home():

    return {
        "status": "online",
        "message": "Poker backend is running"
    }


@app.get("/health")
def health():

    return {
        "status": "ok"
    }


# =========================
# JOIN
# =========================

@app.post("/join")
def join_game(
    user_id: str,
    name: str = "Player"
):

    if user_id not in players:

        players[user_id] = Player(
            user_id,
            name
        )

    player = players[user_id]

    return {
        "success": True,
        "user_id": player.user_id,
        "name": player.name,
        "chips": player.chips
    }


# =========================
# PLAYERS
# =========================

@app.get("/players")
def get_players():

    return {
        "players": get_players_data()
    }


# =========================
# START GAME
# =========================

@app.post("/start")
def start_game():

    if len(players) < 1:

        return {
            "success": False,
            "message": "No players"
        }

    # New deck
    game["deck"] = create_deck()

    game["community_cards"] = []

    game["pot"] = 0

    game["started"] = True

    game["stage"] = "preflop"

    game["winner"] = None

    game["message"] = ""

    game["current_bet"] = 0

    game["acted_players"] = set()

    # Deal two cards to everyone
    for player in players.values():

        # If player has no chips, don't start them
        if player.chips <= 0:
            continue

        player.cards = [
            game["deck"].pop(),
            game["deck"].pop()
        ]

        player.folded = False
        player.all_in = False
        player.bet = 0
        player.total_bet = 0

    # =========================
    # BLINDS
    # =========================

    active = [
        p for p in players.values()
        if p.chips > 0
    ]

    if len(active) >= 2:

        dealer_index = game["dealer_index"] % len(active)

        small_blind_player = active[
            (dealer_index + 1) % len(active)
        ]

        big_blind_player = active[
            (dealer_index + 2) % len(active)
        ]

        # Small blind
        sb_amount = min(
            SMALL_BLIND,
            small_blind_player.chips
        )

        small_blind_player.chips -= sb_amount

        small_blind_player.bet += sb_amount

        small_blind_player.total_bet += sb_amount

        game["pot"] += sb_amount

        if small_blind_player.chips == 0:
            small_blind_player.all_in = True

        # Big blind
        bb_amount = min(
            BIG_BLIND,
            big_blind_player.chips
        )

        big_blind_player.chips -= bb_amount

        big_blind_player.bet += bb_amount

        big_blind_player.total_bet += bb_amount

        game["pot"] += bb_amount

        game["current_bet"] = bb_amount

        if big_blind_player.chips == 0:
            big_blind_player.all_in = True

        # First player after big blind
        game["current_player"] = next_active_player(
            big_blind_player.user_id
        )

    else:

        # Heads-up / single player
        game["current_player"] = active[0].user_id

    return {
        "success": True,
        "message": "Game started",
        "stage": game["stage"],
        "pot": game["pot"],
        "current_bet": game["current_bet"],
        "current_player": game["current_player"],
        "players": get_players_data()
    }


# =========================
# GAME STATE
# =========================

@app.get("/game")
def get_game():

    return {
        "started": game["started"],
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_bet": game["current_bet"],
        "current_player": game["current_player"],
        "winner": game["winner"],
        "message": game["message"],
        "players": get_players_data()
    }


# =========================
# MY CARDS
# =========================

@app.get("/my-cards")
def my_cards(user_id: str):

    if user_id not in players:

        return {
            "success": False,
            "cards": []
        }

    return {
        "success": True,
        "cards": players[user_id].cards
    }


# =========================
# ACTION
# =========================

@app.post("/action")
def poker_action(data: Action):

    allowed = [
        "check",
        "call",
        "raise",
        "allin",
        "fold"
    ]

    action = data.action.lower().strip()

    if action not in allowed:

        return {
            "success": False,
            "message": "Invalid action"
        }

    if not game["started"]:

        return {
            "success": False,
            "message": "Game is not running"
        }

    if data.user_id not in players:

        return {
            "success": False,
            "message": "Player not found"
        }

    player = players[data.user_id]

    # Turn check
    if game["current_player"] != player.user_id:

        return {
            "success": False,
            "message": "Not your turn",
            "current_player": game["current_player"]
        }

    if player.folded:

        return {
            "success": False,
            "message": "Player has folded"
        }

    if player.all_in:

        return {
            "success": False,
            "message": "Player is already all-in"
        }

    # =========================
    # CHECK
    # =========================

    if action == "check":

        if player.bet != game["current_bet"]:

            return {
                "success": False,
                "message": "Cannot check. You must call."
            }

        game["acted_players"].add(
            player.user_id
        )

    # =========================
    # CALL
    # =========================

    elif action == "call":

        needed = (
            game["current_bet"]
            - player.bet
        )

        if needed <= 0:

            return {
                "success": False,
                "message": "Nothing to call. Use check."
            }

        amount = min(
            needed,
            player.chips
        )

        player.chips -= amount

        player.bet += amount

        player.total_bet += amount

        game["pot"] += amount

        game["acted_players"].add(
            player.user_id
        )

        if player.chips == 0:

            player.all_in = True

    # =========================
    # RAISE
    # =========================

    elif action == "raise":

        target = int(data.amount)

        if target <= game["current_bet"]:

            return {
                "success": False,
                "message": (
                    "Raise amount must be higher "
                    "than current bet."
                )
            }

        needed = target - player.bet

        if needed > player.chips:

            return {
                "success": False,
                "message": "Not enough chips"
            }

        player.chips -= needed

        player.bet += needed

        player.total_bet += needed

        game["pot"] += needed

        game["current_bet"] = target

        game["acted_players"] = {
            player.user_id
        }

        if player.chips == 0:

            player.all_in = True

    # =========================
    # ALL IN
    # =========================

    elif action == "allin":

        amount = player.chips

        player.chips = 0

        player.bet += amount

        player.total_bet += amount

        game["pot"] += amount

        player.all_in = True

        # All-in becomes a raise
        # if player's bet is higher.
        if player.bet > game["current_bet"]:

            game["current_bet"] = player.bet

            game["acted_players"] = {
                player.user_id
            }

        else:

            game["acted_players"].add(
                player.user_id
            )

    # =========================
    # FOLD
    # =========================

    elif action == "fold":

        player.folded = True

        game["acted_players"].add(
            player.user_id
        )

    # =========================
    # CHECK FOR WINNER
    # =========================

    if len(active_players()) <= 1:

        finish_showdown()

        return {
            "success": True,
            "action": action,
            "game": get_game()
        }

    # =========================
    # CHECK BETTING ROUND
    # =========================

    if betting_round_finished():

        advance_stage()

    else:

        # Move to next player
        next_player = next_active_player(
            player.user_id
        )

        game["current_player"] = next_player

    return {
        "success": True,
        "action": action,
        "user_id": player.user_id,
        "chips": player.chips,
        "pot": game["pot"],
        "stage": game["stage"],
        "current_bet": game["current_bet"],
        "current_player": game["current_player"],
        "community_cards": game["community_cards"],
        "winner": game["winner"],
        "players": get_players_data()
    }


# =========================
# NEXT CARD
# =========================

@app.post("/next-card")
def next_card():

    if not game["started"]:

        return {
            "success": False,
            "message": "Game has not started"
        }

    # This endpoint is kept for compatibility
    # with the current Mini App.
    #
    # It only deals cards when the current
    # betting round has finished.

    if len(game["community_cards"]) == 0:

        cards = deal_flop()

        prepare_new_betting_round()

        return {
            "success": True,
            "stage": "flop",
            "new_cards": cards,
            "community_cards": game["community_cards"],
            "pot": game["pot"],
            "current_player": game["current_player"]
        }

    if len(game["community_cards"]) == 3:

        cards = deal_turn()

        prepare_new_betting_round()

        return {
            "success": True,
            "stage": "turn",
            "new_cards": cards,
            "community_cards": game["community_cards"],
            "pot": game["pot"],
            "current_player": game["current_player"]
        }

    if len(game["community_cards"]) == 4:

        cards = deal_river()

        prepare_new_betting_round()

        return {
            "success": True,
            "stage": "river",
            "new_cards": cards,
            "community_cards": game["community_cards"],
            "pot": game["pot"],
            "current_player": game["current_player"]
        }

    return {
        "success": False,
        "message": "All community cards dealt"
    }


# =========================
# RESET
# =========================

@app.post("/reset")
def reset_game():

    game["started"] = False

    game["deck"] = []

    game["community_cards"] = []

    game["pot"] = 0

    game["stage"] = "waiting"

    game["current_player"] = None

    game["current_bet"] = 0

    game["acted_players"] = set()

    game["winner"] = None

    game["message"] = ""

    for player in players.values():

        player.cards = []

        player.folded = False

        player.all_in = False

        player.bet = 0

        player.total_bet = 0

    return {
        "success": True,
        "message": "Game reset",
        "players": get_players_data()
    }
