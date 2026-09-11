from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from itertools import combinations
import random
import asyncio

app = FastAPI(
    title="Telegram Poker Backend",
    version="5.0-BOT"
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

BOT_NAMES = [
    "Bot Ali",
    "Bot Reza",
    "Bot Sara",
    "Bot Amir",
    "Bot Nima"
]

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

bot_task = None


class JoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: Optional[int] = 0


def make_deck():
    deck = [
        rank + suit
        for rank in RANKS
        for suit in SUITS
    ]

    random.shuffle(deck)
    return deck


def card_value(card):
    return RANKS.index(card[0]) + 2


def evaluate_five(cards):

    values = sorted(
        [card_value(c) for c in cards],
        reverse=True
    )

    suits = [c[1] for c in cards]

    counts = {}

    for value in values:
        counts[value] = counts.get(value, 0) + 1

    unique = sorted(
        set(values),
        reverse=True
    )

    straight_high = None

    if len(unique) == 5:

        if unique[0] - unique[4] == 4:
            straight_high = unique[0]

        elif unique == [14, 5, 4, 3, 2]:
            straight_high = 5

    flush = len(set(suits)) == 1

    if flush and straight_high:
        return (8, straight_high)

    four = [
        v for v, c in counts.items()
        if c == 4
    ]

    if four:

        kicker = max(
            v for v in values
            if v != four[0]
        )

        return (7, four[0], kicker)

    triples = sorted(
        [
            v for v, c in counts.items()
            if c == 3
        ],
        reverse=True
    )

    pairs = sorted(
        [
            v for v, c in counts.items()
            if c == 2
        ],
        reverse=True
    )

    if triples and pairs:
        return (
            6,
            triples[0],
            pairs[0]
        )

    if len(triples) >= 2:
        return (
            6,
            triples[0],
            triples[1]
        )

    if flush:
        return (5, *values)

    if straight_high:
        return (4, straight_high)

    if triples:

        kickers = sorted(
            [
                v for v in values
                if v != triples[0]
            ],
            reverse=True
        )

        return (
            3,
            triples[0],
            *kickers
        )

    if len(pairs) >= 2:

        kicker = max(
            v for v in values
            if v not in pairs[:2]
        )

        return (
            2,
            pairs[0],
            pairs[1],
            kicker
        )

    if len(pairs) == 1:

        kickers = sorted(
            [
                v for v in values
                if v != pairs[0]
            ],
            reverse=True
        )

        return (
            1,
            pairs[0],
            *kickers
        )

    return (0, *values)


def best_hand(cards):

    if len(cards) < 5:
        return (0, *sorted(
            [card_value(c) for c in cards],
            reverse=True
        ))

    best = None

    for combo in combinations(cards, 5):

        score = evaluate_five(combo)

        if best is None or score > best:
            best = score

    return best


def find_player(user_id):

    for p in players:

        if p["user_id"] == user_id:
            return p

    return None


def active_players():

    return [
        p for p in players
        if not p["folded"]
    ]


def can_act(p):

    return (
        p is not None
        and not p["folded"]
        and not p["all_in"]
    )


def update_pot():

    game["pot"] = sum(
        p["total_bet"]
        for p in players
    )


def next_active_index(start):

    if not players:
        return None

    total = len(players)

    for offset in range(1, total + 1):

        idx = (
            start + offset
        ) % total

        p = players[idx]

        if can_act(p):
            return idx

    return None


def set_next_player():

    current_index = 0

    for i, p in enumerate(players):

        if p["user_id"] == game["current_player"]:
            current_index = i
            break

    nxt = next_active_index(
        current_index
    )

    if nxt is None:
        game["current_player"] = None
    else:
        game["current_player"] = players[nxt]["user_id"]


def reset_street():

    for p in players:

        p["bet"] = 0
        p["acted"] = False

    game["current_bet"] = 0


def all_bets_complete():

    active = [
        p for p in players
        if not p["folded"]
        and not p["all_in"]
    ]

    if len(active) <= 1:
        return True

    for p in active:

        if not p["acted"]:
            return False

        if p["bet"] != game["current_bet"]:
            return False

    return True


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

    game["deck"].pop()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "turn"


def deal_river():

    game["deck"].pop()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "river"


def start_street():

    reset_street()

    if not players:
        return

    start = game["dealer_index"]

    nxt = next_active_index(
        start
    )

    if nxt is not None:

        game["current_player"] = (
            players[nxt]["user_id"]
        )

    else:
        game["current_player"] = None


def advance_stage():

    alive = active_players()

    if len(alive) <= 1:

        finish_hand()

        return

    if game["stage"] == "preflop":

        deal_flop()

        start_street()

        game["message"] = (
            "Flop dealt"
        )

    elif game["stage"] == "flop":

        deal_turn()

        start_street()

        game["message"] = (
            "Turn dealt"
        )

    elif game["stage"] == "turn":

        deal_river()

        start_street()

        game["message"] = (
            "River dealt"
        )

    elif game["stage"] == "river":

        finish_hand()

        return

    update_pot()


def finish_hand():

    global bot_task

    alive = [
        p for p in players
        if not p["folded"]
    ]

    if not alive:
        game["started"] = False
        game["message"] = "No winner"
        return

    if len(alive) == 1:

        winner = alive[0]

        winner["chips"] += game["pot"]

        game["winner"] = winner["name"]

        game["message"] = (
            f"{winner['name']} wins "
            f"{game['pot']} chips"
        )

        game["pot"] = 0
        game["started"] = False
        game["current_player"] = None

        return

    results = []

    for p in alive:

        score = best_hand(
            p["cards"] +
            game["community_cards"]
        )

        results.append(
            (score, p)
        )

    best_score = max(
        score
        for score, p in results
    )

    winners = [
        p
        for score, p in results
        if score == best_score
    ]

    pot = game["pot"]

    share = pot // len(winners)
    remainder = pot % len(winners)

    for i, winner in enumerate(winners):

        winner["chips"] += share

        if i < remainder:
            winner["chips"] += 1

    if len(winners) == 1:

        game["winner"] = (
            winners[0]["name"]
        )

        game["message"] = (
            f"{winners[0]['name']} wins "
            f"{pot} chips"
        )

    else:

        names = ", ".join(
            w["name"]
            for w in winners
        )

        game["winner"] = names

        game["message"] = (
            f"Tie! {names} split "
            f"{pot} chips"
        )

    game["pot"] = 0
    game["started"] = False
    game["current_player"] = None


def bot_strength(bot):

    if len(bot["cards"]) < 2:
        return 0.3

    values = sorted(
        [
            card_value(c)
            for c in bot["cards"]
        ],
        reverse=True
    )

    a = values[0]
    b = values[1]

    score = 0.25

    if a == b:
        score += 0.35

        if a >= 10:
            score += 0.15

    if a >= 14:
        score += 0.10

    if a >= 11 and b >= 10:
        score += 0.10

    if abs(a - b) <= 2:
        score += 0.05

    suits = [
        c[1]
        for c in bot["cards"]
    ]

    if suits[0] == suits[1]:
        score += 0.07

    community = game["community_cards"]

    if community:

        score += min(
            0.35,
            len(community) * 0.05
        )

    return min(score, 0.98)


def bot_decision(bot):

    strength = bot_strength(bot)

    current_bet = game["current_bet"]

    needed = max(
        0,
        current_bet - bot["bet"]
    )

    chips = bot["chips"]

    if chips <= 0:
        return (
            "allin",
            0
        )

    if strength < 0.28:

        if needed == 0:

            if random.random() < 0.65:
                return ("check", 0)

            return (
                "fold",
                0
            )

        if random.random() < 0.55:
            return (
                "fold",
                0
            )

        return (
            "call",
            0
        )

    if strength < 0.48:

        if needed == 0:

            if random.random() < 0.18:

                amount = min(
                    BIG_BLIND,
                    chips
                )

                return (
                    "raise",
                    amount
                )

            return (
                "check",
                0
            )

        if needed <= chips:

            return (
                "call",
                0
            )

        return (
            "fold",
            0
        )

    if strength < 0.68:

        if random.random() < 0.30:

            amount = min(
                max(
                    BIG_BLIND,
                    current_bet
                ),
                chips
            )

            return (
                "raise",
                amount
            )

        if needed > 0:

            return (
                "call",
                0
            )

        return (
            "check",
            0
        )

    if strength < 0.85:

        if random.random() < 0.65:

            amount = min(
                max(
                    BIG_BLIND * 2,
                    current_bet
                ),
                chips
            )

            return (
                "raise",
                amount
            )

        if needed > 0:

            return (
                "call",
                0
            )

        return (
            "check",
            0
        )

    if chips <= max(
        BIG_BLIND * 3,
        current_bet * 2
    ):

        return (
            "allin",
            0
        )

    amount = min(
        max(
            BIG_BLIND * 3,
            current_bet * 2
        ),
        chips
    )

    return (
        "raise",
        amount
    )


async def run_bots():

    global bot_task

    await asyncio.sleep(0.8)

    while game["started"]:

        current = find_player(
            game["current_player"]
        )

        if not current:
            break

        if not current.get(
            "is_bot",
            False
        ):
            break

        await asyncio.sleep(
            random.uniform(
                0.8,
                1.8
            )
        )

        if not game["started"]:
            break

        current = find_player(
            game["current_player"]
        )

        if not current:
            break

        if not current.get(
            "is_bot",
            False
        ):
            break

        decision, amount = bot_decision(
            current
        )

        try:

            process_action(
                current,
                decision,
                amount
            )

        except Exception as e:

            print(
                "BOT ERROR:",
                e
            )

            try:

                current["acted"] = True
                set_next_player()

            except:
                pass

        update_pot()

        await asyncio.sleep(
            0.3
        )

    bot_task = None


def start_bot_loop():

    global bot_task

    if bot_task is None or bot_task.done():

        bot_task = asyncio.create_task(
            run_bots()
        )


def process_action(
    p,
    action_name,
    amount=0
):

    if not can_act(p):
        return

    if (
        game["current_player"]
        != p["user_id"]
    ):
        return

    if action_name == "fold":

        p["folded"] = True
        p["acted"] = True

        game["last_action"] = (
            f"{p['name']} folded"
        )

    elif action_name == "check":

        if p["bet"] != game["current_bet"]:
            return

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

            pay = min(
                needed,
                p["chips"]
            )

            p["chips"] -= pay
            p["bet"] += pay
            p["total_bet"] += pay

            p["acted"] = True

            if p["chips"] == 0:
                p["all_in"] = True

        game["last_action"] = (
            f"{p['name']} called"
        )

    elif action_name == "raise":

        target = int(amount)

        if target <= game["current_bet"]:
            return

        needed = (
            target - p["bet"]
        )

        if needed > p["chips"]:
            target = (
                p["bet"]
                + p["chips"]
            )

            needed = (
                target - p["bet"]
            )

        if needed <= 0:
            return

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
            f"{p['name']} raised "
            f"to {target}"
        )

    elif action_name == "allin":

        amount = p["chips"]

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

    update_pot()

    alive = active_players()

    if len(alive) <= 1:

        finish_hand()
        return

    if all_bets_complete():

        advance_stage()

    else:

        set_next_player()

    update_pot()


@app.get("/")
def root():

    return {
        "status": "online",
        "message": "Poker backend is running",
        "version": "5.0-BOT"
    }


@app.get("/health")
def health():

    return {
        "status": "ok",
        "players": len(players),
        "started": game["started"],
        "bots": sum(
            1 for p in players
            if p.get("is_bot", False)
        )
    }


@app.post("/join")
def join(req: JoinRequest):

    existing = find_player(
        req.user_id
    )

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
        "acted": False,
        "is_bot": False
    }

    players.append(player)

    return {
        "success": True,
        "player": player,
        "players_count": len(players)
    }


def add_bots():

    existing_bots = [
        p for p in players
        if p.get("is_bot", False)
    ]

    needed = (
        MAX_PLAYERS
        - len(players)
    )

    for name in BOT_NAMES:

        if needed <= 0:
            break

        bot_id = (
            "bot_" +
            name.lower()
            .replace(" ", "_")
        )

        if find_player(bot_id):
            continue

        players.append({
            "user_id": bot_id,
            "name": name,
            "chips": STARTING_CHIPS,
            "cards": [],
            "folded": False,
            "all_in": False,
            "bet": 0,
            "total_bet": 0,
            "acted": False,
            "is_bot": True
        })

        needed -= 1


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
                and i == (
                    game["dealer_index"] + 1
                ) % len(players)
            ),
            "big_blind": (
                game["started"]
                and i == (
                    game["dealer_index"] + 2
                ) % len(players)
            ),
            "is_bot": p.get(
                "is_bot",
                False
            )
        })

    return result


@app.get("/game")
def get_game():

    return {
        "started": game["started"],
        "stage": game["stage"],
        "pot": game["pot"],
        "community_cards":
            game["community_cards"],
        "current_player":
            game["current_player"],
        "current_bet":
            game["current_bet"],
        "winner":
            game["winner"],
        "message":
            game["message"],
        "hand_number":
            game["hand_number"],
        "last_action":
            game["last_action"]
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

    if game["started"]:

        raise HTTPException(
            status_code=400,
            detail="Game already started"
        )

    if len(players) < 1:

        raise HTTPException(
            status_code=400,
            detail="Join the table first"
        )

    # Add bots until table reaches 6 seats.
    add_bots()

    if len(players) < 2:

        raise HTTPException(
            status_code=400,
            detail="At least 2 players required"
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

        if p["chips"] <= 0:
            p["chips"] = STARTING_CHIPS

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

    game["dealer_index"] %= n

    dealer = game["dealer_index"]

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

    first = next_active_index(
        bb_index
    )

    if first is not None:

        game["current_player"] = (
            players[first]["user_id"]
        )

    else:

        game["current_player"] = None

    update_pot()

    start_bot_loop()

    return {
        "success": True,
        "message": "Game started",
        "bots": sum(
            1 for p in players
            if p.get("is_bot", False)
        )
    }


@app.post("/action")
async def action(req: ActionRequest):

    p = find_player(
        req.user_id
    )

    if not p:

        raise HTTPException(
            status_code=404,
            detail="Player not found"
        )

    if p.get("is_bot", False):

        raise HTTPException(
            status_code=400,
            detail="Bot controlled player"
        )

    if not game["started"]:

        raise HTTPException(
            status_code=400,
            detail="Game is not running"
        )

    if (
        game["current_player"]
        != req.user_id
    ):

        raise HTTPException(
            status_code=400,
            detail="It is not your turn"
        )

    before = game["last_action"]

    process_action(
        p,
        req.action.lower(),
        int(req.amount or 0)
    )

    update_pot()

    if game["started"]:

        start_bot_loop()

    return {
        "success": True,
        "message": game["message"],
        "last_action":
            game["last_action"],
        "finished":
            not game["started"]
    }


@app.post("/new-hand")
def new_hand():

    if game["started"]:

        raise HTTPException(
            status_code=400,
            detail="Current hand is still running"
        )

    if len(players) < 1:

        raise HTTPException(
            status_code=400,
            detail="No players"
        )

    game["dealer_index"] = (
        game["dealer_index"] + 1
    ) % len(players)

    # Remove busted bots.
    for p in players:

        if p["chips"] <= 0:

            p["chips"] = STARTING_CHIPS

    return start_game()


@app.post("/reset")
def reset_game():

    global players
    global bot_task

    players = []

    if bot_task:

        try:
            bot_task.cancel()
        except:
            pass

        bot_task = None

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
