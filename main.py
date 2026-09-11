from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from collections import Counter
import random
import asyncio
import time

app = FastAPI(title="Telegram Poker 6 Tables")

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

STARTING_CHIPS = 1000
SMALL_BLIND = 10
BIG_BLIND = 20
MAX_PLAYERS = 6
TURN_TIME = 15
TABLE_COUNT = 6

RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]

BOT_NAMES = [
    "Bot Ali",
    "Bot Reza",
    "Bot Sara",
    "Bot Amir",
    "Bot Nima"
]

# =========================
# TABLES
# =========================

tables = {}

for i in range(1, TABLE_COUNT + 1):
    tables[i] = {
        "players": {},
        "game": {
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
            "hand_number": 0,
            "turn_deadline": None,
        },
        "turn_token": 0,
        "turn_task": None,
    }


# =========================
# MODELS
# =========================

class JoinRequest(BaseModel):
    user_id: str
    name: str


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: int = 0


class LeaveRequest(BaseModel):
    user_id: str


# =========================
# HELPERS
# =========================

def get_table(table_id: int):
    if table_id not in tables:
        return None
    return tables[table_id]


def create_deck():
    return [r + s for r in RANKS for s in SUITS]


def card_value(card):
    return RANKS.index(card[0]) + 2


def is_red(card):
    return "♥" in card or "♦" in card


def active_players(table):
    return [
        p for p in table["players"].values()
        if not p["folded"]
        and p["chips"] > 0
        and not p["all_in"]
    ]


def hand_players(table):
    return [
        p for p in table["players"].values()
        if not p["folded"]
    ]


def player_order(table):
    return list(table["players"].keys())


def next_active_player(table, current_id=None):
    ids = player_order(table)

    if not ids:
        return None

    if current_id in ids:
        start = ids.index(current_id) + 1
    else:
        start = 0

    for offset in range(len(ids)):
        pid = ids[(start + offset) % len(ids)]
        p = table["players"].get(pid)

        if not p:
            continue

        if p["folded"]:
            continue

        if p["all_in"]:
            continue

        if p["chips"] <= 0:
            continue

        return pid

    return None


def player_can_act(table, pid):
    p = table["players"].get(pid)

    if not p:
        return False

    if p["folded"]:
        return False

    if p["all_in"]:
        return False

    if p["chips"] <= 0:
        return False

    return True


def call_amount(table, player):
    return max(
        0,
        table["game"]["current_bet"] - player["bet"]
    )


def put_chips(player, amount):
    amount = max(0, min(amount, player["chips"]))

    player["chips"] -= amount
    player["bet"] += amount
    player["total_bet"] += amount

    if player["chips"] == 0:
        player["all_in"] = True

    return amount


def reset_bets(table):
    for p in table["players"].values():
        p["bet"] = 0
        p["acted"] = False


def cancel_timer(table):
    table["turn_token"] += 1
    table["game"]["turn_deadline"] = None

    task = table.get("turn_task")

    if task and not task.done():
        task.cancel()

    table["turn_task"] = None


def remaining_time(table):
    deadline = table["game"].get("turn_deadline")

    if not deadline:
        return 0

    return max(
        0,
        int(deadline - time.time())
    )


def schedule_timer(table_id):
    table = get_table(table_id)

    if not table:
        return

    cancel_timer(table)

    pid = table["game"]["current_player"]

    if not pid:
        return

    token = table["turn_token"]

    table["game"]["turn_deadline"] = (
        time.time() + TURN_TIME
    )

    try:
        loop = asyncio.get_running_loop()

        table["turn_task"] = loop.create_task(
            turn_worker(
                table_id,
                pid,
                token
            )
        )
    except RuntimeError:
        pass


# =========================
# HAND EVALUATION
# =========================

def evaluate_five(cards):
    values = sorted(
        [card_value(c) for c in cards],
        reverse=True
    )

    counts = Counter(values)

    unique = sorted(set(values), reverse=True)

    if 14 in unique:
        unique.append(1)

    straight_high = None

    for i in range(len(unique) - 4):
        seq = unique[i:i + 5]

        if seq[0] - seq[4] == 4:
            straight_high = seq[0]
            break

    flush = len({
        c[1] for c in cards
    }) == 1

    if flush and straight_high:
        return (8, straight_high)

    groups = sorted(
        counts.items(),
        key=lambda x: (x[1], x[0]),
        reverse=True
    )

    if groups[0][1] == 4:
        quad = groups[0][0]
        kicker = max(
            v for v in values if v != quad
        )
        return (7, quad, kicker)

    trips = sorted(
        [v for v, n in counts.items() if n == 3],
        reverse=True
    )

    pairs = sorted(
        [v for v, n in counts.items() if n >= 2],
        reverse=True
    )

    if trips:
        trip = trips[0]
        remaining_pairs = [
            v for v in pairs if v != trip
        ]

        if remaining_pairs:
            return (6, trip, remaining_pairs[0])

    if flush:
        return (5, *values)

    if straight_high:
        return (4, straight_high)

    if trips:
        trip = trips[0]
        kickers = sorted(
            [v for v in values if v != trip],
            reverse=True
        )[:2]
        return (3, trip, *kickers)

    if len(pairs) >= 2:
        pair1 = pairs[0]
        pair2 = pairs[1]

        kicker = max(
            v for v in values
            if v != pair1 and v != pair2
        )

        return (2, pair1, pair2, kicker)

    if len(pairs) == 1:
        pair = pairs[0]

        kickers = sorted(
            [v for v in values if v != pair],
            reverse=True
        )[:3]

        return (1, pair, *kickers)

    return (0, *values)


def best_hand(cards):
    if len(cards) < 5:
        return (0,)

    best = None

    from itertools import combinations

    for combo in combinations(cards, 5):
        score = evaluate_five(list(combo))

        if best is None or score > best:
            best = score

    return best


# =========================
# WINNER
# =========================

def finish_hand(table_id):
    table = get_table(table_id)

    if not table:
        return

    cancel_timer(table)

    players = hand_players(table)

    if not players:
        table["game"]["winner"] = None
        table["game"]["message"] = "No winner"
        table["game"]["started"] = False
        return

    if len(players) == 1:
        winner = players[0]

    else:
        community = table["game"]["community_cards"]

        results = []

        for p in players:
            score = best_hand(
                p["cards"] + community
            )

            results.append(
                (score, p)
            )

        results.sort(
            key=lambda x: x[0],
            reverse=True
        )

        best_score = results[0][0]

        winners = [
            p for score, p in results
            if score == best_score
        ]

        if len(winners) == 1:
            winner = winners[0]
        else:
            winner = winners[0]

    pot = table["game"]["pot"]

    winner["chips"] += pot

    table["game"]["winner"] = winner["user_id"]
    table["game"]["message"] = (
        f"{winner['name']} wins {pot} chips"
    )

    table["game"]["started"] = False
    table["game"]["current_player"] = None
    table["game"]["turn_deadline"] = None

    for p in table["players"].values():
        p["bet"] = 0
        p["total_bet"] = 0
        p["acted"] = False


# =========================
# STREET
# =========================

def deal_flop(table):
    table["game"]["community_cards"] = [
        table["game"]["deck"].pop(),
        table["game"]["deck"].pop(),
        table["game"]["deck"].pop(),
    ]

    table["game"]["stage"] = "flop"


def deal_turn(table):
    table["game"]["community_cards"].append(
        table["game"]["deck"].pop()
    )

    table["game"]["stage"] = "turn"


def deal_river(table):
    table["game"]["community_cards"].append(
        table["game"]["deck"].pop()
    )

    table["game"]["stage"] = "river"


def start_street(table_id, first_player=None):
    table = get_table(table_id)

    if not table:
        return

    reset_bets(table)

    table["game"]["current_bet"] = 0
    table["game"]["acted_players"] = set()

    if table["game"]["stage"] == "preflop":
        pass

    elif table["game"]["stage"] == "flop":
        deal_flop(table)

    elif table["game"]["stage"] == "turn":
        deal_turn(table)

    elif table["game"]["stage"] == "river":
        deal_river(table)

    if first_player:
        table["game"]["current_player"] = first_player
    else:
        table["game"]["current_player"] = next_active_player(
            table
        )

    if table["game"]["current_player"]:
        schedule_timer(table_id)


def all_players_acted_or_allin(table):
    for p in active_players(table):

        if p["user_id"] not in table["game"]["acted_players"]:
            return False

    return True


def advance_street_if_needed(table_id):
    table = get_table(table_id)

    if not table:
        return False

    active = active_players(table)

    if len(active) <= 1:
        finish_hand(table_id)
        return True

    if not all_players_acted_or_allin(table):
        return False

    stage = table["game"]["stage"]

    first = None

    if stage == "preflop":
        table["game"]["stage"] = "flop"

        ids = player_order(table)

        dealer = table["game"]["dealer_index"]

        if ids:
            first = ids[
                (dealer + 1) % len(ids)
            ]

        start_street(
            table_id,
            first
        )

        return True

    if stage == "flop":
        table["game"]["stage"] = "turn"

        ids = player_order(table)
        dealer = table["game"]["dealer_index"]

        if ids:
            first = ids[
                (dealer + 1) % len(ids)
            ]

        start_street(
            table_id,
            first
        )

        return True

    if stage == "turn":
        table["game"]["stage"] = "river"

        ids = player_order(table)
        dealer = table["game"]["dealer_index"]

        if ids:
            first = ids[
                (dealer + 1) % len(ids)
            ]

        start_street(
            table_id,
            first
        )

        return True

    if stage == "river":
        finish_hand(table_id)
        return True

    return False


# =========================
# ACTION
# =========================

def process_action(
    table_id,
    user_id,
    action,
    amount=0
):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    game = table["game"]

    if not game["started"]:
        return {
            "success": False,
            "message": "Game is not running"
        }

    if str(game["current_player"]) != str(user_id):
        return {
            "success": False,
            "message": "Not your turn"
        }

    player = table["players"].get(user_id)

    if not player:
        return {
            "success": False,
            "message": "Player not found"
        }

    if not player_can_act(table, user_id):
        return {
            "success": False,
            "message": "You cannot act"
        }

    cancel_timer(table)

    action = action.lower().strip()

    if action == "fold":

        player["folded"] = True

    elif action == "check":

        if call_amount(table, player) != 0:
            schedule_timer(table_id)

            return {
                "success": False,
                "message": "Cannot check. You must call."
            }

    elif action == "call":

        needed = call_amount(
            table,
            player
        )

        if needed <= 0:
            pass
        else:
            put_chips(
                player,
                needed
            )

    elif action == "raise":

        try:
            amount = int(amount)
        except:
            amount = 0

        if amount <= game["current_bet"]:
            schedule_timer(table_id)

            return {
                "success": False,
                "message": "Raise must be higher"
            }

        needed = amount - player["bet"]

        if needed <= 0:
            schedule_timer(table_id)

            return {
                "success": False,
                "message": "Invalid raise"
            }

        put_chips(
            player,
            needed
        )

        game["current_bet"] = player["bet"]

    elif action == "allin":

        old_bet = player["bet"]

        put_chips(
            player,
            player["chips"]
        )

        if player["bet"] > game["current_bet"]:
            game["current_bet"] = player["bet"]

    else:

        schedule_timer(table_id)

        return {
            "success": False,
            "message": "Unknown action"
        }

    game["acted_players"].add(
        user_id
    )

    # collect pot
    total = 0

    for p in table["players"].values():
        total += p["total_bet"]

    game["pot"] = total

    # check only one remaining
    alive = hand_players(table)

    if len(alive) <= 1:
        finish_hand(table_id)

        return {
            "success": True,
            "message": f"{player['name']} acted",
            "game": game
        }

    # next street
    if advance_street_if_needed(table_id):
        return {
            "success": True,
            "message": f"{player['name']} acted",
            "game": game
        }

    nxt = next_active_player(
        table,
        user_id
    )

    game["current_player"] = nxt

    if nxt:
        schedule_timer(table_id)
    else:
        advance_street_if_needed(table_id)

    return {
        "success": True,
        "message": f"{player['name']} acted",
        "game": game
    }


# =========================
# BOT AI
# =========================

def bot_decision(table, player):
    needed = call_amount(
        table,
        player
    )

    roll = random.random()

    if needed == 0:
        if roll < 0.12:
            return "fold", 0

        if roll < 0.28:
            return "raise", max(
                table["game"]["current_bet"] + BIG_BLIND,
                player["bet"] + BIG_BLIND * 2
            )

        return "check", 0

    if roll < 0.12:
        return "fold", 0

    if roll < 0.78:
        return "call", 0

    return "raise", max(
        table["game"]["current_bet"] + BIG_BLIND,
        player["bet"] + BIG_BLIND * 2
    )


async def turn_worker(
    table_id,
    player_id,
    token
):
    try:
        await asyncio.sleep(TURN_TIME)

        table = get_table(table_id)

        if not table:
            return

        if token != table["turn_token"]:
            return

        if table["game"]["current_player"] != player_id:
            return

        player = table["players"].get(player_id)

        if not player:
            return

        # BOT
        if player["is_bot"]:

            action, amount = bot_decision(
                table,
                player
            )

            process_action(
                table_id,
                player_id,
                action,
                amount
            )

        # HUMAN
        else:

            needed = call_amount(
                table,
                player
            )

            if needed == 0:
                process_action(
                    table_id,
                    player_id,
                    "check",
                    0
                )
            else:
                process_action(
                    table_id,
                    player_id,
                    "fold",
                    0
                )

    except asyncio.CancelledError:
        pass

    except Exception as e:
        print(
            "TURN WORKER ERROR:",
            e
        )


# =========================
# START HAND
# =========================

def start_hand(table_id):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    players = list(
        table["players"].values()
    )

    if len(players) < 2:
        return {
            "success": False,
            "message": "Need at least 2 players"
        }

    cancel_timer(table)

    game = table["game"]

    game["started"] = True
    game["deck"] = create_deck()
    random.shuffle(game["deck"])

    game["community_cards"] = []
    game["pot"] = 0
    game["stage"] = "preflop"
    game["current_bet"] = BIG_BLIND
    game["acted_players"] = set()
    game["winner"] = None
    game["message"] = ""
    game["hand_number"] += 1

    # remove old bots if needed
    for p in players:
        p["cards"] = []
        p["folded"] = False
        p["all_in"] = False
        p["bet"] = 0
        p["total_bet"] = 0
        p["acted"] = False

    # dealer
    game["dealer_index"] %= len(players)

    # cards
    for _ in range(2):
        for p in players:
            if p["chips"] > 0:
                p["cards"].append(
                    game["deck"].pop()
                )

    # blinds
    dealer_pos = game["dealer_index"]

    sb_pos = (
        dealer_pos + 1
    ) % len(players)

    bb_pos = (
        dealer_pos + 2
    ) % len(players)

    sb = players[sb_pos]
    bb = players[bb_pos]

    sb["small_blind"] = True
    bb["big_blind"] = True

    put_chips(
        sb,
        SMALL_BLIND
    )

    put_chips(
        bb,
        BIG_BLIND
    )

    game["pot"] = (
        SMALL_BLIND +
        BIG_BLIND
    )

    # first player after BB
    first_pos = (
        bb_pos + 1
    ) % len(players)

    game["current_player"] = players[
        first_pos
    ]["user_id"]

    schedule_timer(table_id)

    return {
        "success": True,
        "message": "Game started"
    }


# =========================
# JOIN / LEAVE
# =========================

@app.post("/join/{table_id}")
def join_table(
    table_id: int,
    req: JoinRequest
):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    if req.user_id in table["players"]:
        return {
            "success": True,
            "message": "Already seated",
            "table_id": table_id
        }

    if len(table["players"]) >= MAX_PLAYERS:
        return {
            "success": False,
            "message": "Table is full"
        }

    # remove player from other tables
    for tid, other in tables.items():
        if req.user_id in other["players"]:
            del other["players"][req.user_id]

    table["players"][req.user_id] = {
        "user_id": req.user_id,
        "name": req.name[:20],
        "chips": STARTING_CHIPS,
        "cards": [],
        "folded": False,
        "all_in": False,
        "bet": 0,
        "total_bet": 0,
        "acted": False,
        "is_bot": False,
        "dealer": False,
        "small_blind": False,
        "big_blind": False,
    }

    return {
        "success": True,
        "message": "Joined table",
        "table_id": table_id
    }


@app.post("/leave/{table_id}")
def leave_table(
    table_id: int,
    req: LeaveRequest
):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    if req.user_id in table["players"]:
        del table["players"][req.user_id]

    if not table["players"]:
        cancel_timer(table)
        table["game"]["started"] = False

    return {
        "success": True,
        "message": "Left table"
    }


# =========================
# ADD BOTS
# =========================

def add_bots(table):
    existing_bots = [
        p for p in table["players"].values()
        if p["is_bot"]
    ]

    human_count = len([
        p for p in table["players"].values()
        if not p["is_bot"]
    ])

    if human_count != 1:
        return

    needed = MAX_PLAYERS - len(
        table["players"]
    )

    used_names = {
        p["name"]
        for p in existing_bots
    }

    for bot_name in BOT_NAMES:

        if needed <= 0:
            break

        if bot_name in used_names:
            continue

        bot_id = (
            "bot_" +
            bot_name.lower()
                .replace(" ", "_")
        )

        table["players"][bot_id] = {
            "user_id": bot_id,
            "name": bot_name,
            "chips": STARTING_CHIPS,
            "cards": [],
            "folded": False,
            "all_in": False,
            "bet": 0,
            "total_bet": 0,
            "acted": False,
            "is_bot": True,
            "dealer": False,
            "small_blind": False,
            "big_blind": False,
        }

        needed -= 1


# =========================
# LOBBY
# =========================

@app.get("/tables")
def get_tables():
    result = []

    for tid, table in tables.items():

        players = list(
            table["players"].values()
        )

        result.append({
            "table_id": tid,
            "name": f"Poker Table {tid}",
            "players": len(players),
            "max_players": MAX_PLAYERS,
            "blinds": f"{SMALL_BLIND}/{BIG_BLIND}",
            "status": (
                "PLAYING"
                if table["game"]["started"]
                else "OPEN"
            ),
            "pot": table["game"]["pot"],
        })

    return {
        "success": True,
        "tables": result
    }


# =========================
# PLAYERS
# =========================

@app.get("/players/{table_id}")
def get_players(table_id: int):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    result = []

    for p in table["players"].values():

        item = {
            "user_id": p["user_id"],
            "name": p["name"],
            "chips": p["chips"],
            "bet": p["bet"],
            "folded": p["folded"],
            "all_in": p["all_in"],
            "is_turn": (
                table["game"]["current_player"]
                == p["user_id"]
            ),
            "dealer": p["dealer"],
            "small_blind": p["small_blind"],
            "big_blind": p["big_blind"],
            "is_bot": p["is_bot"],
        }

        result.append(item)

    return {
        "success": True,
        "players": result
    }


# =========================
# GAME
# =========================

@app.get("/game/{table_id}")
def get_game(table_id: int):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    game = table["game"]

    current_name = None

    if game["current_player"]:
        p = table["players"].get(
            game["current_player"]
        )

        if p:
            current_name = p["name"]

    return {
        "success": True,
        "started": game["started"],
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_player_name": current_name,
        "current_bet": game["current_bet"],
        "winner": game["winner"],
        "message": game["message"],
        "dealer_index": game["dealer_index"],
        "turn_time": TURN_TIME,
        "turn_remaining": remaining_time(table),
    }


# =========================
# MY CARDS
# =========================

@app.get("/my-cards/{table_id}")
def my_cards(
    table_id: int,
    user_id: str
):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    p = table["players"].get(user_id)

    if not p:
        return {
            "success": False,
            "message": "Player not found"
        }

    return {
        "success": True,
        "cards": p["cards"]
    }


# =========================
# START
# =========================

@app.post("/start/{table_id}")
def start_game(table_id: int):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    if len(table["players"]) == 1:
        add_bots(table)

    return start_hand(table_id)


# =========================
# ACTION
# =========================

@app.post("/action/{table_id}")
def action(
    table_id: int,
    req: ActionRequest
):
    return process_action(
        table_id,
        req.user_id,
        req.action,
        req.amount
    )


# =========================
# NEW HAND
# =========================

@app.post("/new-hand/{table_id}")
def new_hand(table_id: int):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    # reset blinds
    for p in table["players"].values():
        p["dealer"] = False
        p["small_blind"] = False
        p["big_blind"] = False

    count = len(table["players"])

    if count < 2:
        return {
            "success": False,
            "message": "Need at least 2 players"
        }

    table["game"]["dealer_index"] = (
        table["game"]["dealer_index"] + 1
    ) % count

    return start_hand(table_id)


# =========================
# RESET
# =========================

@app.post("/reset/{table_id}")
def reset_table(table_id: int):
    table = get_table(table_id)

    if not table:
        return {
            "success": False,
            "message": "Table not found"
        }

    cancel_timer(table)

    table["players"] = {}

    table["game"] = {
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
        "hand_number": 0,
        "turn_deadline": None,
    }

    return {
        "success": True,
        "message": "Table reset"
    }


# =========================
# ROOT
# =========================

@app.get("/")
def root():
    return {
        "status": "online",
        "message": "Telegram Poker 6 Tables Backend",
        "tables": TABLE_COUNT
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }
