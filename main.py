from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random
from collections import Counter
from itertools import combinations
from typing import Optional

app = FastAPI(title="Telegram Poker Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]

STARTING_CHIPS = 1000
SMALL_BLIND = 10
BIG_BLIND = 20

players = {}


class Player:
    def __init__(self, user_id, name):
        self.user_id = str(user_id)
        self.name = name
        self.chips = STARTING_CHIPS
        self.cards = []
        self.folded = False
        self.all_in = False
        self.bet = 0
        self.total_bet = 0


class Action(BaseModel):
    user_id: str
    action: str
    amount: int = 0


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
    "message": "",
}


def create_deck():
    deck = []

    for suit in SUITS:
        for rank in RANKS:
            deck.append(rank + suit)

    random.shuffle(deck)

    return deck


def active_players():
    return [
        p for p in players.values()
        if not p.folded and len(p.cards) == 2
    ]


def players_can_act():
    return [
        p for p in active_players()
        if not p.all_in and p.chips > 0
    ]


def public_players():
    result = []

    for p in players.values():
        result.append({
            "user_id": p.user_id,
            "name": p.name,
            "chips": p.chips,
            "folded": p.folded,
            "all_in": p.all_in,
            "bet": p.bet,
            "total_bet": p.total_bet,
            "is_turn": p.user_id == game["current_player"],
        })

    return result


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

        if (
            not player.folded
            and not player.all_in
            and player.chips > 0
            and len(player.cards) == 2
        ):
            return player.user_id

    return None


def reset_player_for_hand(player):
    player.cards = []
    player.folded = False
    player.all_in = False
    player.bet = 0
    player.total_bet = 0


def prepare_new_betting_round():
    for player in players.values():
        player.bet = 0

    game["current_bet"] = 0
    game["acted_players"] = set()
    game["current_player"] = next_active_player()


def collect_bets():
    for player in players.values():
        if player.bet > 0:
            game["pot"] += player.bet
            player.bet = 0


def betting_round_finished():
    active = active_players()

    if len(active) <= 1:
        return True

    can_act = players_can_act()

    if not can_act:
        return True

    for player in can_act:
        if player.user_id not in game["acted_players"]:
            return False

        if player.bet != game["current_bet"]:
            return False

    return True


def deal_flop():
    cards = [
        game["deck"].pop(),
        game["deck"].pop(),
        game["deck"].pop(),
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

    if 14 in unique:
        unique.append(1)

    straight_high = None

    for i in range(len(unique) - 4):
        group = unique[i:i + 5]

        if group[0] - group[4] == 4:
            straight_high = group[0]
            break

    flush = len(set(suits)) == 1

    if flush and straight_high:
        return (8, straight_high)

    four_cards = [
        rank
        for rank, count in counts.items()
        if count == 4
    ]

    if four_cards:
        four = max(four_cards)

        kicker = max(
            rank
            for rank in ranks
            if rank != four
        )

        return (7, four, kicker)

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

        other_pairs = [
            rank
            for rank in pairs
            if rank != triple
        ]

        if other_pairs:
            return (6, triple, other_pairs[0])

    if flush:
        return (
            5,
            *sorted(ranks, reverse=True)
        )

    if straight_high:
        return (4, straight_high)

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

    return (
        0,
        *sorted(ranks, reverse=True)
    )


def best_hand(cards):
    if len(cards) < 5:
        return (0,)

    best = None

    for combo in combinations(cards, 5):
        score = evaluate_five(combo)

        if best is None or score > best:
            best = score

    return best


def finish_showdown():
    active = active_players()

    if len(active) == 0:
        game["started"] = False
        game["stage"] = "finished"
        game["current_player"] = None
        return

    if len(active) == 1:
        winner = active[0]
        amount = game["pot"]

        winner.chips += amount

        game["pot"] = 0

        game["winner"] = {
            "user_id": winner.user_id,
            "name": winner.name,
            "amount": amount,
        }

        game["message"] = (
            f"{winner.name} wins {amount} chips!"
        )

        game["started"] = False
        game["stage"] = "finished"
        game["current_player"] = None

        return

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

    pot = game["pot"]

    share = pot // len(winners)
    remainder = pot % len(winners)

    for i, winner in enumerate(winners):
        winner.chips += share

        if i < remainder:
            winner.chips += 1

    if len(winners) == 1:
        winner = winners[0]

        game["winner"] = {
            "user_id": winner.user_id,
            "name": winner.name,
            "amount": pot,
            "hand_score": list(best_score),
        }

        game["message"] = (
            f"{winner.name} wins {pot} chips!"
        )

    else:
        names = ", ".join(
            winner.name
            for winner in winners
        )

        game["winner"] = {
            "user_id": winners[0].user_id,
            "name": names,
            "amount": pot,
        }

        game["message"] = (
            f"Split pot between {names}"
        )

    game["pot"] = 0
    game["started"] = False
    game["stage"] = "finished"
    game["current_player"] = None


def advance_stage():
    collect_bets()

    active = active_players()

    if len(active) <= 1:
        finish_showdown()
        return

    if len(game["community_cards"]) == 0:
        deal_flop()
        prepare_new_betting_round()
        return

    if len(game["community_cards"]) == 3:
        deal_turn()
        prepare_new_betting_round()
        return

    if len(game["community_cards"]) == 4:
        deal_river()
        prepare_new_betting_round()
        return

    if len(game["community_cards"]) == 5:
        game["stage"] = "showdown"
        finish_showdown()


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


@app.post("/join")
def join_game(
    user_id: str,
    name: str = "Player"
):
    user_id = str(user_id)

    if user_id not in players:
        players[user_id] = Player(
            user_id,
            name
        )
    else:
        players[user_id].name = name

    player = players[user_id]

    return {
        "success": True,
        "user_id": player.user_id,
        "name": player.name,
        "chips": player.chips
    }


@app.get("/players")
def get_players():
    return {
        "players": public_players()
    }


@app.post("/start")
def start_game():
    if game["started"]:
        return {
            "success": False,
            "message": "Game already running"
        }

    active = [
        player
        for player in players.values()
        if player.chips > 0
    ]

    if len(active) < 2:
        return {
            "success": False,
            "message": "At least 2 players are required"
        }

    game["deck"] = create_deck()
    game["community_cards"] = []
    game["pot"] = 0
    game["started"] = True
    game["stage"] = "preflop"
    game["current_player"] = None
    game["current_bet"] = 0
    game["acted_players"] = set()
    game["winner"] = None
    game["message"] = ""

    for player in players.values():
        if player.chips > 0:
            reset_player_for_hand(player)

            player.cards = [
                game["deck"].pop(),
                game["deck"].pop()
            ]

    active = [
        player
        for player in players.values()
        if len(player.cards) == 2
        and player.chips > 0
    ]

    dealer_index = (
        game["dealer_index"] % len(active)
    )

    small_blind = active[
        (dealer_index + 1) % len(active)
    ]

    big_blind = active[
        (dealer_index + 2) % len(active)
    ]

    sb = min(
        SMALL_BLIND,
        small_blind.chips
    )

    small_blind.chips -= sb
    small_blind.bet += sb
    small_blind.total_bet += sb
    game["pot"] += sb

    if small_blind.chips == 0:
        small_blind.all_in = True

    bb = min(
        BIG_BLIND,
        big_blind.chips
    )

    big_blind.chips -= bb
    big_blind.bet += bb
    big_blind.total_bet += bb
    game["pot"] += bb

    if big_blind.chips == 0:
        big_blind.all_in = True

    game["current_bet"] = bb

    game["current_player"] = next_active_player(
        big_blind.user_id
    )

    return {
        "success": True,
        "message": "Game started",
        "stage": "preflop",
        "pot": game["pot"],
        "current_bet": game["current_bet"],
        "current_player": game["current_player"],
        "players": public_players()
    }


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
        "players": public_players()
    }


@app.get("/my-cards")
def my_cards(user_id: str):
    user_id = str(user_id)

    if user_id not in players:
        return {
            "success": False,
            "message": "Player not found",
            "cards": []
        }

    return {
        "success": True,
        "user_id": user_id,
        "cards": players[user_id].cards
    }


@app.post("/action")
def poker_action(data: Action):
    action = data.action.lower().strip()

    if action not in [
        "check",
        "call",
        "raise",
        "allin",
        "fold"
    ]:
        return {
            "success": False,
            "message": "Invalid action"
        }

    if not game["started"]:
        return {
            "success": False,
            "message": "Game is not running"
        }

    user_id = str(data.user_id)

    if user_id not in players:
        return {
            "success": False,
            "message": "Player not found"
        }

    player = players[user_id]

    if game["current_player"] != user_id:
        return {
            "success": False,
            "message": "Not your turn",
            "current_player": game["current_player"]
        }

    if player.folded:
        return {
            "success": False,
            "message": "Player folded"
        }

    if player.all_in:
        return {
            "success": False,
            "message": "Already all-in"
        }

    if action == "check":

        if player.bet != game["current_bet"]:
            return {
                "success": False,
                "message": "Cannot check. Call required."
            }

        game["acted_players"].add(user_id)

    elif action == "call":

        needed = (
            game["current_bet"]
            - player.bet
        )

        if needed <= 0:
            return {
                "success": False,
                "message": "Nothing to call. Check instead."
            }

        amount = min(
            needed,
            player.chips
        )

        player.chips -= amount
        player.bet += amount
        player.total_bet += amount
        game["pot"] += amount

        game["acted_players"].add(user_id)

        if player.chips == 0:
            player.all_in = True

    elif action == "raise":

        target = int(data.amount)

        if target <= game["current_bet"]:
            return {
                "success": False,
                "message": "Raise must be higher than current bet."
            }

        needed = target - player.bet

        if needed > player.chips:
            return {
                "success": False,
                "message": "Not enough chips."
            }

        player.chips -= needed
        player.bet += needed
        player.total_bet += needed
        game["pot"] += needed

        game["current_bet"] = target

        game["acted_players"] = {user_id}

        if player.chips == 0:
            player.all_in = True

    elif action == "allin":

        amount = player.chips

        player.chips = 0
        player.bet += amount
        player.total_bet += amount
        game["pot"] += amount
        player.all_in = True

        if player.bet > game["current_bet"]:
            game["current_bet"] = player.bet
            game["acted_players"] = {user_id}
        else:
            game["acted_players"].add(user_id)

    elif action == "fold":

        player.folded = True
        game["acted_players"].add(user_id)

    if len(active_players()) <= 1:

        finish_showdown()

    elif betting_round_finished():

        advance_stage()

    else:

        game["current_player"] = next_active_player(
            user_id
        )

    return {
        "success": True,
        "action": action,
        "user_id": user_id,
        "chips": player.chips,
        "pot": game["pot"],
        "stage": game["stage"],
        "current_bet": game["current_bet"],
        "current_player": game["current_player"],
        "community_cards": game["community_cards"],
        "winner": game["winner"],
        "players": public_players()
    }


@app.post("/next-card")
def next_card():

    if not game["started"]:
        return {
            "success": False,
            "message": "Game has not started"
        }

    if len(game["community_cards"]) == 0:

        cards = deal_flop()

        prepare_new_betting_round()

        return {
            "success": True,
            "stage": "flop",
            "new_cards": cards,
            "community_cards": game["community_cards"],
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
            "current_player": game["current_player"]
        }

    return {
        "success": False,
        "message": "All community cards dealt"
    }


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
        "players": public_players()
    }
