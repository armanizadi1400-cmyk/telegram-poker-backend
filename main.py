import asyncio
import json
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =========================================================
# CONFIG
# =========================================================

MAX_SEATS = 6
STARTING_CHIPS = 1000
TURN_SECONDS = 15

SUITS = ["S", "H", "D", "C"]
RANKS = list("23456789TJQKA")

TABLE_CONFIG = {
    1: {"name": "Micro", "sb": 10, "bb": 20},
    2: {"name": "Low", "sb": 25, "bb": 50},
    3: {"name": "Regular", "sb": 50, "bb": 100},
    4: {"name": "High", "sb": 100, "bb": 200},
    5: {"name": "VIP", "sb": 250, "bb": 500},
    6: {"name": "Elite", "sb": 500, "bb": 1000},
}

BOT_NAMES = [
    "Alex",
    "Daniel",
    "Mason",
    "Sophia",
    "Emma",
    "Ryan",
    "Liam",
    "Noah",
    "Oliver",
    "Lucas",
    "Mia",
    "Ava",
]


# =========================================================
# GLOBAL STATE
# =========================================================

players = {}
tables = {}
connections = {}
turn_tasks = {}
hand_tasks = {}

state_lock = asyncio.Lock()


# =========================================================
# MODELS
# =========================================================

class JoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: int = 0


class ChatRequest(BaseModel):
    user_id: str
    message: str


# =========================================================
# CARD ENGINE
# =========================================================

def make_card(rank, suit):
    return {
        "rank": rank,
        "suit": suit
    }


def card_text(card):
    symbols = {
        "S": "♠",
        "H": "♥",
        "D": "♦",
        "C": "♣"
    }

    return card["rank"] + symbols[card["suit"]]


def new_deck():
    deck = []

    for rank in RANKS:
        for suit in SUITS:
            deck.append(
                make_card(rank, suit)
            )

    random.shuffle(deck)

    return deck


def rank_value(rank):
    return RANKS.index(rank) + 2


# =========================================================
# HAND EVALUATION
# =========================================================

def evaluate_five(cards):

    values = sorted(
        [rank_value(c["rank"]) for c in cards],
        reverse=True
    )

    suits = [
        c["suit"]
        for c in cards
    ]

    counts = {}

    for value in values:
        counts[value] = counts.get(value, 0) + 1

    unique = sorted(
        set(values),
        reverse=True
    )

    if 14 in unique:
        unique.append(1)

    straight_high = None

    for i in range(len(unique) - 4):

        five = unique[i:i + 5]

        if five[0] - five[4] == 4:
            straight_high = five[0]
            break

    flush = len(set(suits)) == 1

    if flush and straight_high:
        return (
            8,
            straight_high
        )

    quads = sorted(
        [v for v, c in counts.items() if c == 4],
        reverse=True
    )

    if quads:
        q = quads[0]

        kicker = max(
            v for v in values
            if v != q
        )

        return (
            7,
            q,
            kicker
        )

    trips = sorted(
        [v for v, c in counts.items() if c == 3],
        reverse=True
    )

    pairs = sorted(
        [v for v, c in counts.items() if c == 2],
        reverse=True
    )

    if trips and (
        len(trips) >= 2 or pairs
    ):

        trip = trips[0]

        pair = (
            trips[1]
            if len(trips) >= 2
            else pairs[0]
        )

        return (
            6,
            trip,
            pair
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

        trip = trips[0]

        kickers = sorted(
            [
                v for v in values
                if v != trip
            ],
            reverse=True
        )[:2]

        return (
            3,
            trip,
            *kickers
        )

    if len(pairs) >= 2:

        p1 = pairs[0]
        p2 = pairs[1]

        kicker = max(
            v for v in values
            if v != p1 and v != p2
        )

        return (
            2,
            p1,
            p2,
            kicker
        )

    if len(pairs) == 1:

        pair = pairs[0]

        kickers = sorted(
            [
                v for v in values
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


def best_hand(cards):

    if len(cards) < 5:
        return (0,)

    from itertools import combinations

    best = None

    for combo in combinations(cards, 5):

        score = evaluate_five(
            list(combo)
        )

        if best is None or score > best:
            best = score

    return best


# =========================================================
# PLAYER
# =========================================================

def create_player(
    user_id,
    name,
    is_bot=False
):

    return {
        "id": str(user_id),
        "name": name,
        "chips": STARTING_CHIPS,
        "table_id": None,
        "seat": None,
        "cards": [],
        "folded": False,
        "all_in": False,
        "bet": 0,
        "total_bet": 0,
        "is_bot": is_bot,
        "connected": is_bot,
        "hands": 0,
        "wins": 0,
        "joined_at": time.time(),
    }


# =========================================================
# TABLE
# =========================================================

def create_table(table_id):

    config = TABLE_CONFIG[table_id]

    return {
        "id": table_id,
        "name": config["name"],
        "small_blind": config["sb"],
        "big_blind": config["bb"],

        "players": {},

        "deck": [],
        "community": [],

        "pot": 0,
        "stage": "waiting",

        "dealer_seat": -1,
        "current_seat": None,

        "current_bet": 0,

        "acted": set(),

        "hand_number": 0,

        "winner": None,
        "message": "",

        "turn_started": None,

        "chat": [],
    }


# =========================================================
# TABLE HELPERS
# =========================================================

def get_table(table_id):

    table = tables.get(
        int(table_id)
    )

    if not table:
        raise HTTPException(
            404,
            "Table not found"
        )

    return table


def table_player_list(table):

    return list(
        table["players"].values()
    )


def active_players(table):

    return [
        p for p in table_player_list(table)
        if not p["folded"]
        and p["chips"] > 0
        and p["connected"]
    ]


def live_players(table):

    return [
        p for p in table_player_list(table)
        if not p["folded"]
        and p["connected"]
    ]


def available_seat(table):

    used = {
        p["seat"]
        for p in table_player_list(table)
    }

    for seat in range(MAX_SEATS):

        if seat not in used:
            return seat

    return None


def current_player(table):

    seat = table["current_seat"]

    if seat is None:
        return None

    for p in table_player_list(table):

        if p["seat"] == seat:
            return p

    return None


def next_playable_seat(
    table,
    from_seat
):

    seats = sorted(
        [
            p["seat"]
            for p in table_player_list(table)
        ]
    )

    if not seats:
        return None

    for offset in range(
        1,
        MAX_SEATS + 1
    ):

        seat = (
            from_seat + offset
        ) % MAX_SEATS

        for p in table_player_list(table):

            if p["seat"] == seat:

                if (
                    p["connected"]
                    and not p["folded"]
                    and not p["all_in"]
                    and p["chips"] > 0
                ):
                    return seat

    return None


# =========================================================
# MONEY
# =========================================================

def take_chips(
    player,
    amount,
    table
):

    amount = max(
        0,
        min(
            amount,
            player["chips"]
        )
    )

    player["chips"] -= amount
    player["bet"] += amount
    player["total_bet"] += amount

    table["pot"] += amount

    if player["chips"] == 0:
        player["all_in"] = True

    return amount


# =========================================================
# BROADCAST
# =========================================================

async def send_to_user(
    user_id,
    payload
):

    websocket = connections.get(
        str(user_id)
    )

    if not websocket:
        return

    try:

        await websocket.send_text(
            json.dumps(
                payload,
                ensure_ascii=False
            )
        )

    except Exception:

        connections.pop(
            str(user_id),
            None
        )


async def broadcast_table(table):

    payload = make_game_state(
        table
    )

    for p in table_player_list(table):

        await send_to_user(
            p["id"],
            payload
        )


async def broadcast_lobby():

    payload = {
        "type": "lobby",
        "tables": [
            make_table_summary(table)
            for table in tables.values()
        ]
    }

    for user_id in list(
        connections.keys()
    ):

        await send_to_user(
            user_id,
            payload
        )


# =========================================================
# SERIALIZATION
# =========================================================

def make_table_summary(table):

    return {
        "id": table["id"],
        "name": table["name"],
        "small_blind": table["small_blind"],
        "big_blind": table["big_blind"],
        "players": len(
            table_player_list(table)
        ),
        "max_players": MAX_SEATS,
        "stage": table["stage"],
        "pot": table["pot"],
        "started": table["stage"] != "waiting",
    }


def make_player_public(
    player,
    table
):

    return {
        "id": player["id"],
        "name": player["name"],
        "chips": player["chips"],
        "seat": player["seat"],
        "bet": player["bet"],
        "folded": player["folded"],
        "all_in": player["all_in"],
        "is_bot": player["is_bot"],
        "connected": player["connected"],
        "is_turn": (
            table["current_seat"]
            == player["seat"]
        ),
    }


def make_game_state(table):

    public_players = [
        make_player_public(
            p,
            table
        )
        for p in table_player_list(table)
    ]

    return {
        "type": "game",

        "table": {
            "id": table["id"],
            "name": table["name"],
            "small_blind": table["small_blind"],
            "big_blind": table["big_blind"],
        },

        "stage": table["stage"],

        "community": [
            card_text(c)
            for c in table["community"]
        ],

        "pot": table["pot"],

        "current_bet":
            table["current_bet"],

        "current_seat":
            table["current_seat"],

        "dealer_seat":
            table["dealer_seat"],

        "winner":
            table["winner"],

        "message":
            table["message"],

        "turn_started":
            table["turn_started"],

        "turn_seconds":
            TURN_SECONDS,

        "players":
            public_players,

        "chat":
            table["chat"][-30:],
    }


# =========================================================
# START HAND
# =========================================================

def reset_hand_player(p):

    p["cards"] = []
    p["folded"] = False
    p["all_in"] = False
    p["bet"] = 0
    p["total_bet"] = 0


def start_hand(table):

    participants = [
        p for p in table_player_list(table)
        if p["connected"]
        and p["chips"] > 0
    ]

    if len(participants) < 2:

        table["stage"] = "waiting"
        table["current_seat"] = None

        return False

    table["hand_number"] += 1

    table["deck"] = new_deck()

    table["community"] = []

    table["pot"] = 0

    table["winner"] = None
    table["message"] = ""

    table["stage"] = "preflop"

    table["acted"] = set()

    table["current_bet"] = (
        table["big_blind"]
    )

    for p in participants:

        reset_hand_player(p)

        p["hands"] += 1

    # Dealer
    seats = sorted(
        p["seat"]
        for p in participants
    )

    if table["dealer_seat"] not in seats:

        table["dealer_seat"] = seats[0]

    else:

        next_seat = None

        for seat in seats:

            if seat > table["dealer_seat"]:

                next_seat = seat
                break

        if next_seat is None:
            next_seat = seats[0]

        table["dealer_seat"] = next_seat

    # Deal cards
    for _ in range(2):

        for p in participants:

            p["cards"].append(
                table["deck"].pop()
            )

    # Blinds
    dealer = table["dealer_seat"]

    sb_seat = next(
        (
            seat
            for seat in seats
            if seat > dealer
        ),
        seats[0]
    )

    bb_seat = next(
        (
            seat
            for seat in seats
            if seat > sb_seat
        ),
        seats[0]
    )

    sb = next(
        p for p in participants
        if p["seat"] == sb_seat
    )

    bb = next(
        p for p in participants
        if p["seat"] == bb_seat
    )

    take_chips(
        sb,
        table["small_blind"],
        table
    )

    take_chips(
        bb,
        table["big_blind"],
        table
    )

    # First player after big blind
    table["current_seat"] = next_playable_seat(
        table,
        bb_seat
    )

    table["turn_started"] = time.time()

    return True


# =========================================================
# ADVANCE STAGE
# =========================================================

def all_bets_equal(table):

    alive = [
        p for p in table_player_list(table)
        if not p["folded"]
        and not p["all_in"]
        and p["connected"]
    ]

    if len(alive) <= 1:
        return True

    for p in alive:

        if p["bet"] != table["current_bet"]:
            return False

    return all(
        p["id"] in table["acted"]
        for p in alive
    )


def advance_stage(table):

    if table["stage"] == "preflop":

        table["deck"].pop()

        table["community"] = [
            table["deck"].pop(),
            table["deck"].pop(),
            table["deck"].pop(),
        ]

        table["stage"] = "flop"

    elif table["stage"] == "flop":

        table["deck"].pop()

        table["community"].append(
            table["deck"].pop()
        )

        table["stage"] = "turn"

    elif table["stage"] == "turn":

        table["deck"].pop()

        table["community"].append(
            table["deck"].pop()
        )

        table["stage"] = "river"

    elif table["stage"] == "river":

        finish_hand(table)
        return

    for p in table_player_list(table):

        if not p["folded"]:

            p["bet"] = 0

    table["current_bet"] = 0

    table["acted"] = set()

    table["current_seat"] = (
        next_playable_seat(
            table,
            table["dealer_seat"]
        )
    )

    table["turn_started"] = time.time()


# =========================================================
# FINISH HAND
# =========================================================

def finish_hand(table):

    cancel_turn_task(
        table["id"]
    )

    alive = [
        p for p in table_player_list(table)
        if not p["folded"]
        and p["connected"]
    ]

    if not alive:

        table["stage"] = "finished"
        table["current_seat"] = None

        return

    if len(alive) == 1:

        winners = alive

    else:

        scored = []

        for p in alive:

            score = best_hand(
                p["cards"]
                + table["community"]
            )

            scored.append(
                (score, p)
            )

        best = max(
            score
            for score, _
            in scored
        )

        winners = [
            p
            for score, p
            in scored
            if score == best
        ]

    share = (
        table["pot"]
        // len(winners)
    )

    remainder = (
        table["pot"]
        - share * len(winners)
    )

    for i, winner in enumerate(winners):

        winner["chips"] += share

        if i == 0:
            winner["chips"] += remainder

        winner["wins"] += 1

    names = ", ".join(
        p["name"]
        for p in winners
    )

    table["winner"] = names

    table["message"] = (
        f"Winner: {names}"
    )

    table["stage"] = "finished"

    table["current_seat"] = None

    table["turn_started"] = None


# =========================================================
# ACTION
# =========================================================

def do_action(
    table,
    user_id,
    action,
    amount=0
):

    p = table["players"].get(
        str(user_id)
    )

    if not p:

        raise HTTPException(
            404,
            "Player not at table"
        )

    if (
        table["current_seat"]
        != p["seat"]
    ):

        raise HTTPException(
            400,
            "Not your turn"
        )

    if p["folded"] or p["all_in"]:

        raise HTTPException(
            400,
            "Invalid action"
        )

    action = action.lower()

    if action == "fold":

        p["folded"] = True

        table["acted"].add(
            p["id"]
        )

    elif action == "check":

        if p["bet"] != table["current_bet"]:

            raise HTTPException(
                400,
                "Cannot check"
            )

        table["acted"].add(
            p["id"]
        )

    elif action == "call":

        needed = (
            table["current_bet"]
            - p["bet"]
        )

        if needed > p["chips"]:
            needed = p["chips"]

        take_chips(
            p,
            needed,
            table
        )

        table["acted"].add(
            p["id"]
        )

    elif action == "raise":

        try:
            amount = int(amount)
        except Exception:

            raise HTTPException(
                400,
                "Invalid raise"
            )

        min_raise = (
            table["current_bet"]
            + table["big_blind"]
        )

        if amount < min_raise:

            raise HTTPException(
                400,
                f"Minimum raise is {min_raise}"
            )

        target = amount

        needed = (
            target - p["bet"]
        )

        if needed <= 0:

            raise HTTPException(
                400,
                "Invalid raise"
            )

        if needed > p["chips"]:

            raise HTTPException(
                400,
                "Not enough chips"
            )

        take_chips(
            p,
            needed,
            table
        )

        table["current_bet"] = target

        table["acted"] = {
            p["id"]
        }

    elif action == "allin":

        old_bet = p["bet"]

        amount = p["chips"]

        take_chips(
            p,
            amount,
            table
        )

        new_bet = p["bet"]

        if new_bet > table["current_bet"]:

            table["current_bet"] = new_bet

            table["acted"] = {
                p["id"]
            }

        else:

            table["acted"].add(
                p["id"]
            )

    else:

        raise HTTPException(
            400,
            "Unknown action"
        )

    # Check remaining players
    alive = [
        x for x in table_player_list(table)
        if not x["folded"]
        and x["connected"]
    ]

    if len(alive) <= 1:

        finish_hand(table)

        return

    # Move turn
    next_seat = next_playable_seat(
        table,
        p["seat"]
    )

    if next_seat is None:

        if all_bets_equal(table):

            advance_stage(table)

        return

    table["current_seat"] = next_seat

    if all_bets_equal(table):

        advance_stage(table)

    else:

        table["turn_started"] = time.time()


# =========================================================
# TURN TIMER
# =========================================================

def cancel_turn_task(table_id):

    task = turn_tasks.get(
        table_id
    )

    if task and not task.done():

        task.cancel()

    turn_tasks.pop(
        table_id,
        None
    )


def schedule_turn(table):

    cancel_turn_task(
        table["id"]
    )

    if table["current_seat"] is None:
        return

    async def timer():

        try:

            await asyncio.sleep(
                TURN_SECONDS
            )

            async with state_lock:

                player = current_player(
                    table
                )

                if not player:
                    return

                if player["bet"] == table["current_bet"]:

                    do_action(
                        table,
                        player["id"],
                        "check"
                    )

                else:

                    do_action(
                        table,
                        player["id"],
                        "fold"
                    )

                await broadcast_table(
                    table
                )

        except asyncio.CancelledError:
            pass

        except Exception:
            pass

    turn_tasks[
        table["id"]
    ] = asyncio.create_task(
        timer()
    )


# =========================================================
# BOT AI
# =========================================================

async def bot_loop():

    while True:

        try:

            await asyncio.sleep(1)

            async with state_lock:

                for table in tables.values():

                    if table["stage"] == "waiting":

                        bots = [
                            p for p in table_player_list(table)
                            if p["is_bot"]
                            and p["chips"] > 0
                        ]

                        humans = [
                            p for p in table_player_list(table)
                            if not p["is_bot"]
                            and p["connected"]
                            and p["chips"] > 0
                        ]

                        if len(
                            bots + humans
                        ) >= 2:

                            start_hand(
                                table
                            )

                            schedule_turn(
                                table
                            )

                            await broadcast_table(
                                table
                            )

                            continue

                    if table["stage"] in (
                        "preflop",
                        "flop",
                        "turn",
                        "river"
                    ):

                        player = current_player(
                            table
                        )

                        if (
                            player
                            and player["is_bot"]
                        ):

                            await asyncio.sleep(
                                0.7
                            )

                            roll = random.random()

                            if (
                                player["bet"]
                                < table["current_bet"]
                            ):

                                if roll < 0.12:

                                    action = "fold"
                                    amount = 0

                                elif roll < 0.82:

                                    action = "call"
                                    amount = 0

                                else:

                                    action = "raise"

                                    amount = (
                                        table["current_bet"]
                                        + table["big_blind"]
                                    )

                            else:

                                if roll < 0.18:

                                    action = "raise"

                                    amount = (
                                        table["current_bet"]
                                        + table["big_blind"]
                                    )

                                else:

                                    action = "check"
                                    amount = 0

                            cancel_turn_task(
                                table["id"]
                            )

                            try:

                                do_action(
                                    table,
                                    player["id"],
                                    action,
                                    amount
                                )

                            except Exception:
                                pass

                            schedule_turn(
                                table
                            )

                            await broadcast_table(
                                table
                            )

                    elif table["stage"] == "finished":

                        if table["id"] not in hand_tasks:

                            async def next_hand(
                                target_table
                            ):

                                try:

                                    await asyncio.sleep(3)

                                    async with state_lock:

                                        if target_table["stage"] == "finished":

                                            for p in table_player_list(
                                                target_table
                                            ):

                                                if (
                                                    p["chips"] <= 0
                                                    and p["is_bot"]
                                                ):

                                                    p["chips"] = STARTING_CHIPS

                                            if len(
                                                [
                                                    x for x in table_player_list(
                                                        target_table
                                                    )
                                                    if x["connected"]
                                                    and x["chips"] > 0
                                                ]
                                            ) >= 2:

                                                start_hand(
                                                    target_table
                                                )

                                                schedule_turn(
                                                    target_table
                                                )

                                                await broadcast_table(
                                                    target_table
                                                )

                                finally:

                                    hand_tasks.pop(
                                        target_table["id"],
                                        None
                                    )

                            hand_tasks[
                                table["id"]
                            ] = asyncio.create_task(
                                next_hand(table)
                            )

        except Exception:

            continue


# =========================================================
# APP
# =========================================================

@asynccontextmanager
async def lifespan(app):

    for table_id in TABLE_CONFIG:

        tables[table_id] = create_table(
            table_id
        )

    # Create bots
    for i, name in enumerate(
        BOT_NAMES
    ):

        bot_id = (
            f"bot_{i+1}"
        )

        p = create_player(
            bot_id,
            name,
            True
        )

        table_id = (
            (i % len(TABLE_CONFIG))
            + 1
        )

        table = tables[table_id]

        seat = available_seat(
            table
        )

        if seat is not None:

            p["table_id"] = table_id
            p["seat"] = seat

            table["players"][
                bot_id
            ] = p

        players[
            bot_id
        ] = p

    task = asyncio.create_task(
        bot_loop()
    )

    yield

    task.cancel()

    for t in turn_tasks.values():
        t.cancel()

    for t in hand_tasks.values():
        t.cancel()


app = FastAPI(
    title="Iran Poker V1",
    version="1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# BASIC API
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "game": "Iran Poker V1",
        "tables": len(tables),
        "players": len(players),
        "websocket": True,
    }


@app.get("/health")
def health():

    return {
        "status": "ok"
    }


@app.get("/tables")
def get_tables():

    return {
        "success": True,
        "tables": [
            make_table_summary(t)
            for t in tables.values()
        ]
    }


@app.get("/tables/{table_id}")
def table_info(table_id: int):

    table = get_table(
        table_id
    )

    return make_game_state(
        table
    )


# =========================================================
# JOIN
# =========================================================

@app.post("/join")
async def join(
    req: JoinRequest,
    table_id: int
):

    async with state_lock:

        table = get_table(
            table_id
        )

        uid = str(
            req.user_id
        )

        # Existing player
        if uid in players:

            p = players[uid]

            # Already at another table
            if (
                p["table_id"] is not None
                and p["table_id"] != table_id
            ):

                old_table = tables.get(
                    p["table_id"]
                )

                if old_table:

                    old_table["players"].pop(
                        uid,
                        None
                    )

            # Existing seat
            if uid in table["players"]:

                p["connected"] = True

                connections.get(uid)

                return {
                    "success": True,
                    "user_id": uid,
                    "table_id": table_id,
                    "seat": p["seat"],
                }

        # Check capacity
        if len(
            table["players"]
        ) >= MAX_SEATS:

            raise HTTPException(
                400,
                "Table is full"
            )

        seat = available_seat(
            table
        )

        if seat is None:

            raise HTTPException(
                400,
                "No seat available"
            )

        if uid not in players:

            p = create_player(
                uid,
                req.name or "Player",
                False
            )

            players[uid] = p

        else:

            p = players[uid]

            p["name"] = (
                req.name
                or p["name"]
            )

        p["table_id"] = table_id
        p["seat"] = seat
        p["connected"] = True

        table["players"][
            uid
        ] = p

        # Start when enough players
        if (
            table["stage"] == "waiting"
            and len(
                [
                    x for x in table["players"].values()
                    if x["chips"] > 0
                ]
            ) >= 2
        ):

            start_hand(
                table
            )

            schedule_turn(
                table
            )

        await broadcast_table(
            table
        )

        await broadcast_lobby()

        return {
            "success": True,
            "user_id": uid,
            "table_id": table_id,
            "seat": seat,
        }


# =========================================================
# LEAVE
# =========================================================

@app.post("/leave")
async def leave(
    user_id: str
):

    async with state_lock:

        p = players.get(
            str(user_id)
        )

        if not p:

            return {
                "success": True
            }

        table_id = p["table_id"]

        if table_id:

            table = tables.get(
                table_id
            )

            if table:

                table["players"].pop(
                    p["id"],
                    None
                )

                if (
                    table["current_seat"]
                    == p["seat"]
                ):

                    next_seat = next_playable_seat(
                        table,
                        p["seat"]
                    )

                    table["current_seat"] = next_seat

        p["table_id"] = None
        p["seat"] = None
        p["connected"] = False

        await broadcast_lobby()

        return {
            "success": True
        }


# =========================================================
# PRIVATE CARDS
# =========================================================

@app.get("/cards")
def get_cards(
    user_id: str,
    table_id: int
):

    p = players.get(
        str(user_id)
    )

    if not p:

        return {
            "success": False,
            "cards": []
        }

    if p["table_id"] != table_id:

        return {
            "success": False,
            "cards": []
        }

    return {
        "success": True,
        "cards": [
            card_text(c)
            for c in p["cards"]
        ]
    }


# =========================================================
# ACTION
# =========================================================

@app.post("/action")
async def action(
    table_id: int,
    req: ActionRequest
):

    async with state_lock:

        table = get_table(
            table_id
        )

        cancel_turn_task(
            table_id
        )

        do_action(
            table,
            str(req.user_id),
            req.action,
            req.amount
        )

        schedule_turn(
            table
        )

        await broadcast_table(
            table
        )

        return {
            "success": True
        }


# =========================================================
# CHAT
# =========================================================

@app.post("/chat")
async def chat(
    table_id: int,
    req: ChatRequest
):

    async with state_lock:

        table = get_table(
            table_id
        )

        p = players.get(
            str(req.user_id)
        )

        if not p:

            raise HTTPException(
                404,
                "Player not found"
            )

        message = (
            req.message
            .strip()
        )

        if not message:

            raise HTTPException(
                400,
                "Empty message"
            )

        message = message[:200]

        table["chat"].append({
            "user_id": p["id"],
            "name": p["name"],
            "message": message,
            "time": int(time.time()),
        })

        table["chat"] = (
            table["chat"][-30:]
        )

        await broadcast_table(
            table
        )

        return {
            "success": True
        }


# =========================================================
# LEADERBOARD
# =========================================================

@app.get("/leaderboard")
def leaderboard():

    ranking = sorted(
        players.values(),
        key=lambda p: (
            p["wins"],
            p["chips"]
        ),
        reverse=True
    )

    return {
        "success": True,
        "players": [
            {
                "name": p["name"],
                "chips": p["chips"],
                "hands": p["hands"],
                "wins": p["wins"],
            }
            for p in ranking[:50]
        ]
    }


# =========================================================
# WEBSOCKET
# =========================================================

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str
):

    await websocket.accept()

    uid = str(
        user_id
    )

    connections[uid] = websocket

    try:

        await websocket.send_text(
            json.dumps(
                {
                    "type": "connected",
                    "user_id": uid,
                },
                ensure_ascii=False
            )
        )

        p = players.get(uid)

        if p and p["table_id"]:

            table = tables.get(
                p["table_id"]
            )

            if table:

                await websocket.send_text(
                    json.dumps(
                        make_game_state(
                            table
                        ),
                        ensure_ascii=False
                    )
                )

        else:

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "lobby",
                        "tables": [
                            make_table_summary(t)
                            for t in tables.values()
                        ],
                    },
                    ensure_ascii=False
                )
            )

        while True:

            message = await websocket.receive_text()

            try:

                data = json.loads(
                    message
                )

            except Exception:

                continue

            if data.get("type") == "ping":

                await websocket.send_text(
                    json.dumps({
                        "type": "pong"
                    })
                )

    except WebSocketDisconnect:

        pass

    except Exception:

        pass

    finally:

        if connections.get(uid) is websocket:

            connections.pop(
                uid,
                None
            )

        p = players.get(uid)

        if p:

            p["connected"] = False


# =========================================================
# ADMIN RESET
# =========================================================

@app.post("/reset")
async def reset():

    async with state_lock:

        for task in turn_tasks.values():
            task.cancel()

        turn_tasks.clear()

        for table in tables.values():

            table["deck"] = []
            table["community"] = []
            table["pot"] = 0
            table["stage"] = "waiting"
            table["current_seat"] = None
            table["current_bet"] = 0
            table["acted"] = set()
            table["winner"] = None
            table["message"] = ""

            for p in table_player_list(table):

                p["cards"] = []
                p["folded"] = False
                p["all_in"] = False
                p["bet"] = 0
                p["total_bet"] = 0

        return {
            "success": True
        }
