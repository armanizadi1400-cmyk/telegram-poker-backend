from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from itertools import combinations
import random

app = FastAPI(
    title="Telegram Poker Backend",
    version="4.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STARTING_CHIPS = 1000
SMALL_BLIND = 10
BIG_BLIND = 20
MAX_PLAYERS = 6

RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]

players = []

game = {
    "started": False,
    "stage": "waiting",
    "pot": 0,
    "community_cards": [],
    "deck": [],
    "dealer_index": 0,
    "current_player": None,
    "current_bet": 0,
    "winner": None,
    "message": "Waiting for players",
    "hand_number": 0,
    "last_action": ""
}


class JoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: Optional[int] = 0


def make_deck():
    deck = []

    for rank in RANKS:
        for suit in SUITS:
            deck.append(rank + suit)

    random.shuffle(deck)
    return deck


def card_value(card):
    return RANKS.index(card[0]) + 2


def evaluate_five(cards):
    values = sorted([card_value(c) for c in cards], reverse=True)

    suits = [c[1] for c in cards]

    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1

    unique = sorted(set(values), reverse=True)

    straight_high = None

    if len(unique) == 5:
        if unique[0] - unique[4] == 4:
            straight_high = unique[0]

        if unique == [14, 5, 4, 3, 2]:
            straight_high = 5

    flush = len(set(suits)) == 1

    if flush and straight_high:
        return (8, straight_high)

    groups = sorted(
        [(count, value) for value, count in counts.items()],
        reverse=True
    )

    if groups[0][0] == 4:
        four = groups[0][1]
        kicker = max(v for v in values if v != four)
        return (7, four, kicker)

    triples = sorted(
        [v for v, c in counts.items() if c == 3],
        reverse=True
    )

    pairs = sorted(
        [v for v, c in counts.items() if c == 2],
        reverse=True
    )

    if triples and (len(pairs) >= 1 or len(triples) >= 2):
        if len(triples) >= 2:
            return (6, triples[0], triples[1])

        return (6, triples[0], pairs[0])

    if flush:
        return (5, *values)

    if straight_high:
        return (4, straight_high)

    if triples:
        kickers = sorted(
            [v for v in values if v != triples[0]],
            reverse=True
        )
        return (3, triples[0], *kickers)

    if len(pairs) >= 2:
        high_pair = pairs[0]
        low_pair = pairs[1]

        kicker = max(
            v for v in values
            if v != high_pair and v != low_pair
        )

        return (2, high_pair, low_pair, kicker)

    if len(pairs) == 1:
        pair = pairs[0]

        kickers = sorted(
            [v for v in values if v != pair],
            reverse=True
        )

        return (1, pair, *kickers)

    return (0, *values)


def best_hand(cards):
    best = None

    for combo in combinations(cards, 5):
        score = evaluate_five(combo)

        if best is None or score > best:
            best = score

    return best


def active_players():
    return [
        p for p in players
        if not p["folded"] and p["chips"] > 0
    ]


def not_folded():
    return [
        p for p in players
        if not p["folded"]
    ]


def find_player(user_id):
    for p in players:
        if p["user_id"] == user_id:
            return p

    return None


def next_active_index(start):
    if not players:
        return None

    for i in range(1, len(players) + 1):
        idx = (start + i) % len(players)

        p = players[idx]

        if not p["folded"] and not p["all_in"]:
            return idx

    return None


def update_pot():
    game["pot"] = sum(p["total_bet"] for p in players)


def all_bets_complete():
    active = [
        p for p in players
        if not p["folded"] and not p["all_in"]
    ]

    if len(active) <= 1:
        return True

    for p in active:
        if p["bet"] != game["current_bet"]:
            return False

        if not p["acted"]:
            return False

    return True


def reset_street_bets():
    for p in players:
        p["bet"] = 0
        p["acted"] = False

    game["current_bet"] = 0


def advance_turn():
    if game["current_player"] is None:
        return

    idx = game["current_player"]

    nxt = next_active_index(idx)

    game["current_player"] = (
        players[nxt]["user_id"]
        if nxt is not None
        else None
    )


def deal_flop():
    if len(game["deck"]) < 4:
        return

    game["deck"].pop()

    game["community_cards"] = [
        game["deck"].pop(),
        game["deck"].pop(),
        game["deck"].pop()
    ]

    game["stage"] = "flop"


def deal_turn():
    if not game["deck"]:
        return

    game["deck"].pop()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "turn"


def deal_river():
    if not game["deck"]:
        return

    game["deck"].pop()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "river"


def start_betting_round(first_index=None):
    reset_street_bets()

    if first_index is None:
        first_index = game["dealer_index"]

    nxt = next_active_index(first_index - 1)

    game["current_player"] = (
        players[nxt]["user_id"]
        if nxt is not None
        else None
    )


def advance_stage():
    active = not_folded()

    if len(active) <= 1:
        finish_hand()
        return

    if game["stage"] == "preflop":
        deal_flop()
        start_betting_round(game["dealer_index"])

        game["message"] = "Flop dealt"

    elif game["stage"] == "flop":
        deal_turn()
        start_betting_round(game["dealer_index"])

        game["message"] = "Turn dealt"

    elif game["stage"] == "turn":
        deal_river()
        start_betting_round(game["dealer_index"])

        game["message"] = "River dealt"

    elif game["stage"] == "river":
        finish_hand()
        return

    update_pot()


def player_can_act(p):
    return (
        game["started"]
        and p is not None
        and not p["folded"]
        and not p["all_in"]
        and game["current_player"] == p["user_id"]
    )


def finish_hand():
    remaining = [
        p for p in players
        if not p["folded"]
    ]

    if not remaining:
        game["started"] = False
        game["winner"] = None
        game["message"] = "No winner"
        return

    if len(remaining) == 1:
        winner = remaining[0]

        winner["chips"] += game["pot"]

        game["winner"] = winner["name"]
        game["message"] = (
            f"{winner['name']} wins {game['pot']} chips"
        )

        game["pot"] = 0
        game["started"] = False
        return

    results = []

    for p in remaining:
        score = best_hand(
            p["cards"] + game["community_cards"]
        )

        results.append((score, p))

    results.sort(
        key=lambda x: x[0],
        reverse=True
    )

    best_score = results[0][0]

    winners = [
        p
        for score, p in results
        if score == best_score
    ]

    if winners:
        share = game["pot"] // len(winners)
        remainder = game["pot"] % len(winners)

        for i, winner in enumerate(winners):
            winner["chips"] += share

            if i < remainder:
                winner["chips"] += 1

        if len(winners) == 1:
            game["winner"] = winners[0]["name"]

            game["message"] = (
                f"{winners[0]['name']} wins {game['pot']} chips"
            )

        else:
            names = ", ".join(
                w["name"] for w in winners
            )

            game["winner"] = names

            game["message"] = (
                f"Tie! {names} split {game['pot']} chips"
            )

    game["pot"] = 0
    game["started"] = False


@app.get("/")
def root():
    return {
        "status": "online",
        "message": "Poker backend is running",
        "version": "4.0"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "players": len(players),
        "started": game["started"]
    }


@app.post("/join")
def join(req: JoinRequest):

    existing = find_player(req.user_id)

    if existing:
        return {
            "success": True,
            "player": existing
        }

    if len(players) >= MAX_PLAYERS:
        raise HTTPException(
            status_code=400,
            detail="Table is full"
        )

    player = {
        "user_id": req.user_id,
        "name": req.name[:18] or "Player",
        "chips": STARTING_CHIPS,
        "cards": [],
        "folded": False,
        "all_in": False,
        "bet": 0,
        "total_bet": 0,
        "acted": False
    }

    players.append(player)

    return {
        "success": True,
        "player": player,
        "players_count": len(players)
    }


@app.get("/players")
def get_players():

    result = []

    for i, p in enumerate(players):

        result.append({
            "seat": i + 1,
            "user_id": p["user_id"],
            "name": p["name"],
            "chips": p["chips"],
            "bet": p["bet"],
            "folded": p["folded"],
            "all_in": p["all_in"],
            "is_turn": (
                game["current_player"]
                == p["user_id"]
            ),
            "dealer": (
                game["started"]
                and game["dealer_index"] == i
            ),
            "small_blind": (
                game["started"]
                and len(players) >= 2
                and i == (
                    game["dealer_index"] + 1
                ) % len(players)
            ),
            "big_blind": (
                game["started"]
                and len(players) >= 2
                and i == (
                    game["dealer_index"] + 2
                ) % len(players)
            )
        })

    return result


@app.get("/game")
def get_game():

    return {
        "started": game["started"],
        "stage": game["stage"],
        "pot": game["pot"],
        "community_cards": game["community_cards"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": game["winner"],
        "message": game["message"],
        "hand_number": game["hand_number"],
        "last_action": game["last_action"]
    }


@app.get("/my-cards")
def my_cards(user_id: str):

    p = find_player(user_id)

    if not p:
        return {
            "success": False,
            "cards": []
        }

    return {
        "success": True,
        "cards": p["cards"]
    }


@app.post("/start")
def start_game():

    if len(players) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 players are required"
        )

    if game["started"]:
        raise HTTPException(
            status_code=400,
            detail="Game already started"
        )

    game["started"] = True
    game["stage"] = "preflop"
    game["pot"] = 0
    game["community_cards"] = []
    game["deck"] = make_deck()
    game["winner"] = None
    game["message"] = "New hand started"
    game["last_action"] = ""
    game["hand_number"] += 1

    for p in players:

        p["cards"] = [
            game["deck"].pop(),
            game["deck"].pop()
        ]

        p["folded"] = False
        p["all_in"] = False
        p["bet"] = 0
        p["total_bet"] = 0
        p["acted"] = False

    n = len(players)

    dealer = game["dealer_index"] % n

    sb_index = (
        dealer + 1
    ) % n

    bb_index = (
        dealer + 2
    ) % n

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
    sb["bet"] = sb_amount
    sb["total_bet"] = sb_amount

    if sb["chips"] == 0:
        sb["all_in"] = True

    bb["chips"] -= bb_amount
    bb["bet"] = bb_amount
    bb["total_bet"] = bb_amount

    if bb["chips"] == 0:
        bb["all_in"] = True

    game["current_bet"] = bb_amount

    first = next_active_index(bb_index)

    game["current_player"] = (
        players[first]["user_id"]
        if first is not None
        else None
    )

    update_pot()

    return {
        "success": True,
        "message": "Game started"
    }


@app.post("/action")
def action(req: ActionRequest):

    p = find_player(req.user_id)

    if not p:
        raise HTTPException(
            status_code=404,
            detail="Player not found"
        )

    if not player_can_act(p):
        raise HTTPException(
            status_code=400,
            detail="It is not your turn"
        )

    action_name = req.action.lower()

    if action_name == "fold":

        p["folded"] = True
        p["acted"] = True

        game["last_action"] = (
            f"{p['name']} folded"
        )

    elif action_name == "check":

        if p["bet"] != game["current_bet"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot check"
            )

        p["acted"] = True

        game["last_action"] = (
            f"{p['name']} checked"
        )

    elif action_name == "call":

        needed = (
            game["current_bet"]
            - p["bet"]
        )

        if needed <= 0:
            p["acted"] = True

        else:

            amount = min(
                needed,
                p["chips"]
            )

            p["chips"] -= amount
            p["bet"] += amount
            p["total_bet"] += amount

            p["acted"] = True

            if p["chips"] == 0:
                p["all_in"] = True

        game["last_action"] = (
            f"{p['name']} called"
        )

    elif action_name == "raise":

        amount = int(req.amount or 0)

        if amount <= 0:
            raise HTTPException(
                status_code=400,
                detail="Invalid raise amount"
            )

        target = game["current_bet"] + amount

        needed = target - p["bet"]

        if needed <= 0:
            raise HTTPException(
                status_code=400,
                detail="Raise must increase the bet"
            )

        if needed > p["chips"]:
            raise HTTPException(
                status_code=400,
                detail="Not enough chips"
            )

        p["chips"] -= needed
        p["bet"] = target
        p["total_bet"] += needed

        game["current_bet"] = target

        for other in players:
            if (
                other["user_id"]
                != p["user_id"]
                and not other["folded"]
                and not other["all_in"]
            ):
                other["acted"] = False

        p["acted"] = True

        if p["chips"] == 0:
            p["all_in"] = True

        game["last_action"] = (
            f"{p['name']} raised to {target}"
        )

    elif action_name == "allin":

        amount = p["chips"]

        if amount <= 0:
            p["all_in"] = True
            p["acted"] = True

        else:

            p["chips"] = 0
            p["bet"] += amount
            p["total_bet"] += amount
            p["all_in"] = True
            p["acted"] = True

            if p["bet"] > game["current_bet"]:
                game["current_bet"] = p["bet"]

                for other in players:
                    if (
                        other["user_id"]
                        != p["user_id"]
                        and not other["folded"]
                        and not other["all_in"]
                    ):
                        other["acted"] = False

        game["last_action"] = (
            f"{p['name']} went ALL-IN"
        )

    else:
        raise HTTPException(
            status_code=400,
            detail="Unknown action"
        )

    update_pot()

    remaining = [
        x for x in players
        if not x["folded"]
    ]

    if len(remaining) == 1:
        finish_hand()

        return {
            "success": True,
            "finished": True
        }

    if all_bets_complete():

        advance_stage()

    else:

        advance_turn()

    update_pot()

    return {
        "success": True,
        "finished": not game["started"],
        "message": game["message"]
    }


@app.post("/new-hand")
def new_hand():

    if game["started"]:
        raise HTTPException(
            status_code=400,
            detail="Finish current hand first"
        )

    if len(players) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 players are required"
        )

    game["dealer_index"] = (
        game["dealer_index"] + 1
    ) % len(players)

    game["winner"] = None
    game["message"] = "Starting new hand"

    return start_game()


@app.post("/reset")
def reset_game():

    global players

    players = []

    game["started"] = False
    game["stage"] = "waiting"
    game["pot"] = 0
    game["community_cards"] = []
    game["deck"] = []
    game["current_player"] = None
    game["current_bet"] = 0
    game["winner"] = None
    game["message"] = "Table reset"
    game["hand_number"] = 0
    game["last_action"] = ""
    game["dealer_index"] = 0

    return {
        "success": True,
        "message": "Table reset"
    }
