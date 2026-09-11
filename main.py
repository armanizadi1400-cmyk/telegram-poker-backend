from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import Optional
import random
import asyncio
import uuid
import time


# =========================================================
# CONFIG
# =========================================================

STARTING_CHIPS = 1000
TURN_TIME = 15
MAX_PLAYERS = 6

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = list("23456789TJQKA")

BOT_NAMES = [
    "Bot Ali",
    "Bot Reza",
    "Bot Sara",
    "Bot Amir",
    "Bot Nima",
    "Bot Hossein",
    "Bot Mehdi",
    "Bot Arash",
    "Bot Sina",
    "Bot Milad",
]

TABLES = [
    {
        "id": 1,
        "name": "Table 1",
        "small_blind": 10,
        "big_blind": 20,
    },
    {
        "id": 2,
        "name": "Table 2",
        "small_blind": 10,
        "big_blind": 20,
    },
    {
        "id": 3,
        "name": "Table 3",
        "small_blind": 25,
        "big_blind": 50,
    },
    {
        "id": 4,
        "name": "Table 4",
        "small_blind": 50,
        "big_blind": 100,
    },
    {
        "id": 5,
        "name": "Table 5",
        "small_blind": 100,
        "big_blind": 200,
    },
    {
        "id": 6,
        "name": "Table 6",
        "small_blind": 250,
        "big_blind": 500,
    },
]


# =========================================================
# DATA
# =========================================================

players = {}

games = {}

bot_tasks = {}

turn_tasks = {}

turn_deadlines = {}

turn_tokens = {}


# =========================================================
# APP
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    initialize_tables()

    asyncio.create_task(bot_manager())

    yield

    for task in bot_tasks.values():
        if not task.done():
            task.cancel()

    for task in turn_tasks.values():
        if not task.done():
            task.cancel()


app = FastAPI(
    title="Telegram Poker Backend",
    lifespan=lifespan
)


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
    table_id: int = 1


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: int = 0


class TableJoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


# =========================================================
# UTILS
# =========================================================

def create_deck():
    return [
        f"{rank}{suit}"
        for suit in SUITS
        for rank in RANKS
    ]


def card_rank(card):
    return RANKS.index(card[0]) + 2


def card_suit(card):
    return card[1]


def is_bot(user_id):
    return str(user_id).startswith("bot_")


def get_table(table_id):

    if table_id not in games:
        raise HTTPException(
            status_code=404,
            detail="Table not found"
        )

    return games[table_id]


def active_players(table_id):

    game = get_table(table_id)

    return [
        p for p in game["players"]
        if not p["folded"]
        and not p["all_in"]
        and p["chips"] > 0
    ]


def alive_players(table_id):

    game = get_table(table_id)

    return [
        p for p in game["players"]
        if not p["folded"]
    ]


def find_player(table_id, user_id):

    game = get_table(table_id)

    for p in game["players"]:
        if str(p["user_id"]) == str(user_id):
            return p

    return None


def player_index(table_id, user_id):

    game = get_table(table_id)

    for i, p in enumerate(game["players"]):

        if str(p["user_id"]) == str(user_id):
            return i

    return -1


def broadcast_state(table_id):
    return


# =========================================================
# TABLE INITIALIZATION
# =========================================================

def initialize_tables():

    for table in TABLES:

        table_id = table["id"]

        games[table_id] = {
            "id": table_id,
            "name": table["name"],
            "small_blind": table["small_blind"],
            "big_blind": table["big_blind"],

            "players": [],

            "started": False,
            "stage": "waiting",

            "deck": [],
            "community_cards": [],

            "pot": 0,
            "current_bet": 0,

            "current_player": None,
            "dealer_index": 0,

            "acted_players": [],

            "winner": None,
            "message": "Waiting for players",

            "hand_number": 0,
        }


    # Distribute 10 bots between tables
    for i, name in enumerate(BOT_NAMES):

        table_id = (i % 6) + 1

        add_bot(
            table_id,
            name,
            i
        )


# =========================================================
# BOTS
# =========================================================

def add_bot(table_id, name, number):

    game = games[table_id]

    if len(game["players"]) >= MAX_PLAYERS:
        return

    bot_id = f"bot_{name.lower().replace(' ', '_')}"

    player = {
        "user_id": bot_id,
        "name": name,
        "chips": STARTING_CHIPS,

        "cards": [],

        "folded": False,
        "all_in": False,

        "bet": 0,
        "total_bet": 0,

        "is_bot": True,
        "connected": True,

        "table_id": table_id,
    }

    game["players"].append(player)

    players[bot_id] = player


# =========================================================
# BOT MANAGER
# =========================================================

async def bot_manager():

    await asyncio.sleep(2)

    while True:

        try:

            for table_id in games:

                game = games[table_id]

                # Start table automatically
                if not game["started"]:

                    usable = [
                        p for p in game["players"]
                        if p["chips"] > 0
                    ]

                    if len(usable) >= 2:

                        await start_game_internal(table_id)


                # If game ended, start next hand
                elif game["winner"] is not None:

                    await asyncio.sleep(2)

                    await new_hand_internal(table_id)


                # Bot turn
                else:

                    current_id = game["current_player"]

                    if current_id:

                        current = find_player(
                            table_id,
                            current_id
                        )

                        if current and current["is_bot"]:

                            if table_id not in bot_tasks:

                                bot_tasks[table_id] = asyncio.create_task(
                                    bot_play(
                                        table_id,
                                        current_id
                                    )
                                )

                            elif bot_tasks[table_id].done():

                                bot_tasks.pop(
                                    table_id,
                                    None
                                )

        except Exception as e:

            print(
                "BOT MANAGER ERROR:",
                e
            )

        await asyncio.sleep(1)


# =========================================================
# BOT AI
# =========================================================

async def bot_play(table_id, user_id):

    try:

        await asyncio.sleep(
            random.uniform(
                1.5,
                4.0
            )
        )

        game = get_table(table_id)

        player = find_player(
            table_id,
            user_id
        )

        if not player:
            return

        if game["current_player"] != user_id:
            return

        if player["folded"]:
            return

        # Simple poker AI
        strength = random.random()

        to_call = max(
            0,
            game["current_bet"] - player["bet"]
        )

        # Very weak hand
        if strength < 0.20:

            if to_call == 0:

                await process_action(
                    table_id,
                    user_id,
                    "check",
                    0
                )

            else:

                await process_action(
                    table_id,
                    user_id,
                    "fold",
                    0
                )

        # Medium
        elif strength < 0.75:

            if to_call == 0:

                await process_action(
                    table_id,
                    user_id,
                    "check",
                    0
                )

            else:

                await process_action(
                    table_id,
                    user_id,
                    "call",
                    0
                )

        # Strong
        else:

            if player["chips"] <= 0:

                await process_action(
                    table_id,
                    user_id,
                    "allin",
                    0
                )

            else:

                raise_amount = max(
                    game["big_blind"],
                    game["current_bet"] * 2
                )

                await process_action(
                    table_id,
                    user_id,
                    "raise",
                    raise_amount
                )

    except Exception as e:

        print(
            "BOT PLAY ERROR:",
            e
        )

    finally:

        bot_tasks.pop(
            table_id,
            None
        )


# =========================================================
# TURN TIMER
# =========================================================

def schedule_turn_timer(table_id):

    old_task = turn_tasks.get(table_id)

    if old_task and not old_task.done():

        old_task.cancel()

    token = str(uuid.uuid4())

    turn_tokens[table_id] = token

    deadline = time.time() + TURN_TIME

    turn_deadlines[table_id] = deadline

    turn_tasks[table_id] = asyncio.create_task(
        turn_timer_worker(
            table_id,
            token
        )
    )


async def turn_timer_worker(
    table_id,
    token
):

    try:

        await asyncio.sleep(
            TURN_TIME
        )

        if turn_tokens.get(table_id) != token:
            return

        game = get_table(table_id)

        if not game["started"]:
            return

        current_id = game["current_player"]

        if not current_id:
            return

        player = find_player(
            table_id,
            current_id
        )

        if not player:
            return

        # Auto action
        to_call = max(
            0,
            game["current_bet"] - player["bet"]
        )

        if to_call == 0:

            await process_action(
                table_id,
                current_id,
                "check",
                0
            )

        else:

            await process_action(
                table_id,
                current_id,
                "fold",
                0
            )

    except asyncio.CancelledError:
        pass

    except Exception as e:

        print(
            "TIMER ERROR:",
            e
        )


# =========================================================
# NEXT PLAYER
# =========================================================

def next_active_player(
    table_id,
    current_index
):

    game = get_table(table_id)

    count = len(game["players"])

    if count == 0:
        return None

    for step in range(
        1,
        count + 1
    ):

        index = (
            current_index + step
        ) % count

        player = game["players"][index]

        if (
            not player["folded"]
            and not player["all_in"]
            and player["chips"] > 0
        ):

            return player

    return None


def set_next_player(table_id):

    game = get_table(table_id)

    current_id = game["current_player"]

    if current_id is None:

        return

    current_index = player_index(
        table_id,
        current_id
    )

    player = next_active_player(
        table_id,
        current_index
    )

    if player:

        game["current_player"] = player["user_id"]

        schedule_turn_timer(
            table_id
        )

    else:

        game["current_player"] = None

        asyncio.create_task(
            finish_round(
                table_id
            )
        )


# =========================================================
# START GAME
# =========================================================

async def start_game_internal(table_id):

    game = get_table(table_id)

    usable = [
        p for p in game["players"]
        if p["chips"] > 0
    ]

    if len(usable) < 2:
        return False

    game["hand_number"] += 1

    game["started"] = True
    game["stage"] = "preflop"

    game["deck"] = create_deck()

    random.shuffle(
        game["deck"]
    )

    game["community_cards"] = []

    game["pot"] = 0

    game["current_bet"] = game["big_blind"]

    game["acted_players"] = []

    game["winner"] = None

    game["message"] = ""

    # Reset players
    for p in game["players"]:

        if p["chips"] <= 0:

            p["chips"] = STARTING_CHIPS

        p["cards"] = []

        p["folded"] = False

        p["all_in"] = False

        p["bet"] = 0

        p["total_bet"] = 0


    # Deal cards
    for _ in range(2):

        for p in game["players"]:

            if p["chips"] > 0:

                p["cards"].append(
                    game["deck"].pop()
                )


    # Dealer
    if game["dealer_index"] >= len(
        game["players"]
    ):

        game["dealer_index"] = 0


    # Small blind
    sb_index = (
        game["dealer_index"] + 1
    ) % len(game["players"])

    # Big blind
    bb_index = (
        game["dealer_index"] + 2
    ) % len(game["players"])


    sb_player = game["players"][sb_index]

    bb_player = game["players"][bb_index]


    pay_blind(
        sb_player,
        game["small_blind"]
    )

    pay_blind(
        bb_player,
        game["big_blind"]
    )


    # First player after big blind
    first_index = (
        bb_index + 1
    ) % len(game["players"])


    first_player = next_active_player(
        table_id,
        first_index - 1
    )

    if first_player:

        game["current_player"] = first_player["user_id"]

        schedule_turn_timer(
            table_id
        )

    return True


def pay_blind(
    player,
    amount
):

    actual = min(
        amount,
        player["chips"]
    )

    player["chips"] -= actual

    player["bet"] += actual

    player["total_bet"] += actual

    if player["chips"] == 0:

        player["all_in"] = True


    # Pot belongs to table
    for table_id, game in games.items():

        if player in game["players"]:

            game["pot"] += actual

            break


# =========================================================
# ACTION
# =========================================================

async def process_action(
    table_id,
    user_id,
    action,
    amount=0
):

    game = get_table(table_id)

    player = find_player(
        table_id,
        user_id
    )

    if not player:

        return {
            "success": False,
            "message": "Player not found"
        }

    if game["current_player"] != user_id:

        return {
            "success": False,
            "message": "Not your turn"
        }

    if player["folded"]:

        return {
            "success": False,
            "message": "Player folded"
        }


    # Cancel timer
    task = turn_tasks.get(table_id)

    if task and not task.done():

        task.cancel()


    # ==========================================
    # FOLD
    # ==========================================

    if action == "fold":

        player["folded"] = True

        game["message"] = (
            f"{player['name']} folded"
        )


    # ==========================================
    # CHECK
    # ==========================================

    elif action == "check":

        to_call = max(
            0,
            game["current_bet"] - player["bet"]
        )

        if to_call > 0:

            return {
                "success": False,
                "message": "Cannot check"
            }

        game["message"] = (
            f"{player['name']} checked"
        )


    # ==========================================
    # CALL
    # ==========================================

    elif action == "call":

        to_call = max(
            0,
            game["current_bet"] - player["bet"]
        )

        actual = min(
            to_call,
            player["chips"]
        )

        player["chips"] -= actual

        player["bet"] += actual

        player["total_bet"] += actual

        game["pot"] += actual

        if player["chips"] == 0:

            player["all_in"] = True

        game["message"] = (
            f"{player['name']} called"
        )


    # ==========================================
    # RAISE
    # ==========================================

    elif action == "raise":

        target = int(amount)

        if target <= game["current_bet"]:

            target = game["current_bet"] + game["big_blind"]

        difference = (
            target - player["bet"]
        )

        if difference <= 0:

            return {
                "success": False,
                "message": "Invalid raise"
            }

        actual = min(
            difference,
            player["chips"]
        )

        player["chips"] -= actual

        player["bet"] += actual

        player["total_bet"] += actual

        game["pot"] += actual

        game["current_bet"] = player["bet"]

        game["acted_players"] = [
            user_id
        ]

        if player["chips"] == 0:

            player["all_in"] = True

        game["message"] = (
            f"{player['name']} raised"
        )


    # ==========================================
    # ALL IN
    # ==========================================

    elif action == "allin":

        actual = player["chips"]

        player["chips"] = 0

        player["bet"] += actual

        player["total_bet"] += actual

        game["pot"] += actual

        if player["bet"] > game["current_bet"]:

            game["current_bet"] = player["bet"]

            game["acted_players"] = [
                user_id
            ]

        player["all_in"] = True

        game["message"] = (
            f"{player['name']} is ALL IN"
        )


    else:

        return {
            "success": False,
            "message": "Invalid action"
        }


    # ==========================================
    # CHECK END OF HAND
    # ==========================================

    alive = alive_players(
        table_id
    )

    if len(alive) == 1:

        winner = alive[0]

        await award_winner(
            table_id,
            winner
        )

        return {
            "success": True,
            "winner": winner["name"]
        }


    # ==========================================
    # CHECK ROUND
    # ==========================================

    if should_advance_stage(table_id):

        await advance_stage(
            table_id
        )

        return {
            "success": True,
            "stage": game["stage"]
        }


    # ==========================================
    # NEXT PLAYER
    # ==========================================

    set_next_player(
        table_id
    )

    return {
        "success": True,
        "action": action,
        "next_player": game["current_player"]
    }


# =========================================================
# ROUND LOGIC
# =========================================================

def should_advance_stage(table_id):

    game = get_table(table_id)

    alive = [
        p for p in game["players"]
        if not p["folded"]
    ]

    active = [
        p for p in alive
        if not p["all_in"]
        and p["chips"] > 0
    ]

    if len(active) == 0:

        return True


    # Every active player must have matched bet
    for p in active:

        if p["bet"] != game["current_bet"]:

            return False


    # All active players acted
    current_ids = {
        p["user_id"]
        for p in active
    }

    if not current_ids.issubset(
        set(game["acted_players"])
    ):

        return False


    return True


async def advance_stage(table_id):

    game = get_table(table_id)

    game["acted_players"] = []

    # Move current bets into pot
    for p in game["players"]:

        p["bet"] = 0


    game["current_bet"] = 0


    if game["stage"] == "preflop":

        for _ in range(3):

            game["community_cards"].append(
                game["deck"].pop()
            )

        game["stage"] = "flop"


    elif game["stage"] == "flop":

        game["community_cards"].append(
            game["deck"].pop()
        )

        game["stage"] = "turn"


    elif game["stage"] == "turn":

        game["community_cards"].append(
            game["deck"].pop()
        )

        game["stage"] = "river"


    elif game["stage"] == "river":

        await determine_winner(
            table_id
        )

        return


    # Set first active player after dealer
    dealer = game["dealer_index"]

    player = next_active_player(
        table_id,
        dealer
    )

    if player:

        game["current_player"] = player["user_id"]

        schedule_turn_timer(
            table_id
        )


# =========================================================
# WINNER
# =========================================================

def hand_score(cards):

    values = sorted(
        [card_rank(c) for c in cards],
        reverse=True
    )

    suits = [
        card_suit(c)
        for c in cards
    ]

    counts = {}

    for v in values:

        counts[v] = counts.get(v, 0) + 1


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

        window = unique[i:i + 5]

        if window[0] - window[4] == 4:

            straight_high = window[0]

            break


    flush = len(set(suits)) == 1


    if flush and straight_high:

        return (
            8,
            straight_high
        )


    quads = [
        v for v, c in counts.items()
        if c == 4
    ]

    if quads:

        return (
            7,
            max(quads)
        )


    trips = sorted(
        [
            v for v, c in counts.items()
            if c == 3
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

    if trips and len(pairs) >= 2:

        pair_values = [
            v for v in pairs
            if v != trips[0]
        ]

        if pair_values:

            return (
                6,
                trips[0],
                max(pair_values)
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

        kickers = [
            v for v in values
            if v != trips[0]
        ]

        return (
            3,
            trips[0],
            *kickers[:2]
        )


    pair_list = [
        v for v, c in counts.items()
        if c >= 2
    ]

    pair_list = sorted(
        pair_list,
        reverse=True
    )

    if len(pair_list) >= 2:

        kickers = [
            v for v in values
            if v not in pair_list[:2]
        ]

        return (
            2,
            pair_list[0],
            pair_list[1],
            kickers[0] if kickers else 0
        )


    if len(pair_list) == 1:

        kickers = [
            v for v in values
            if v != pair_list[0]
        ]

        return (
            1,
            pair_list[0],
            *kickers[:3]
        )


    return (
        0,
        *values[:5]
    )


def best_hand(cards):

    if len(cards) <= 5:

        return hand_score(cards)

    best = None

    from itertools import combinations

    for combo in combinations(
        cards,
        5
    ):

        score = hand_score(
            list(combo)
        )

        if best is None or score > best:

            best = score

    return best


async def determine_winner(table_id):

    game = get_table(table_id)

    alive = [
        p for p in game["players"]
        if not p["folded"]
    ]

    if not alive:

        return

    if len(alive) == 1:

        await award_winner(
            table_id,
            alive[0]
        )

        return


    best_player = None
    best_score = None

    for p in alive:

        score = best_hand(
            p["cards"] +
            game["community_cards"]
        )

        if (
            best_score is None
            or score > best_score
        ):

            best_score = score
            best_player = p


    await award_winner(
        table_id,
        best_player
    )


async def award_winner(
    table_id,
    winner
):

    game = get_table(table_id)

    winner["chips"] += game["pot"]

    game["winner"] = winner["user_id"]

    game["message"] = (
        f"{winner['name']} wins {game['pot']} chips"
    )

    game["started"] = False

    game["current_player"] = None

    game["stage"] = "finished"

    task = turn_tasks.get(table_id)

    if task and not task.done():

        task.cancel()

    turn_deadlines.pop(
        table_id,
        None
    )


# =========================================================
# NEW HAND
# =========================================================

async def new_hand_internal(table_id):

    game = get_table(table_id)

    # Remove human players who left
    game["players"] = [
        p for p in game["players"]
        if p.get("connected", True)
        or p["is_bot"]
    ]


    # Bots with no chips rebuy
    for p in game["players"]:

        if p["is_bot"] and p["chips"] <= 0:

            p["chips"] = STARTING_CHIPS


    if len(game["players"]) < 2:

        game["winner"] = None
        game["started"] = False
        game["stage"] = "waiting"

        return


    # Rotate dealer
    game["dealer_index"] = (
        game["dealer_index"] + 1
    ) % len(game["players"])


    await start_game_internal(
        table_id
    )


# =========================================================
# API ROOT
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "message": "Poker backend is running",
        "tables": 6,
        "bots": 10
    }


@app.get("/health")
def health():

    return {
        "status": "ok"
    }


# =========================================================
# TABLES
# =========================================================

@app.get("/tables")
def get_tables():

    result = []

    for table_id, game in games.items():

        result.append({
            "id": table_id,
            "name": game["name"],
            "small_blind": game["small_blind"],
            "big_blind": game["big_blind"],
            "players": len(game["players"]),
            "max_players": MAX_PLAYERS,
            "started": game["started"],
            "stage": game["stage"],
            "pot": game["pot"],
        })

    return {
        "success": True,
        "tables": result
    }


@app.post("/tables/{table_id}/join")
async def join_table(
    table_id: int,
    data: TableJoinRequest
):

    game = get_table(table_id)

    existing = find_player(
        table_id,
        data.user_id
    )

    if existing:

        existing["connected"] = True

        return {
            "success": True,
            "table_id": table_id,
            "player": existing
        }


    if len(game["players"]) >= MAX_PLAYERS:

        return {
            "success": False,
            "message": "Table is full"
        }


    player = {
        "user_id": data.user_id,
        "name": data.name,
        "chips": STARTING_CHIPS,

        "cards": [],

        "folded": False,
        "all_in": False,

        "bet": 0,
        "total_bet": 0,

        "is_bot": False,
        "connected": True,

        "table_id": table_id,
    }

    game["players"].append(
        player
    )

    players[data.user_id] = player

    # Automatically start if waiting
    if not game["started"]:

        usable = [
            p for p in game["players"]
            if p["chips"] > 0
        ]

        if len(usable) >= 2:

            await start_game_internal(
                table_id
            )

    return {
        "success": True,
        "table_id": table_id,
        "player": player
    }


@app.post("/tables/{table_id}/leave")
async def leave_table(
    table_id: int,
    user_id: str
):

    game = get_table(table_id)

    player = find_player(
        table_id,
        user_id
    )

    if not player:

        return {
            "success": False,
            "message": "Player not found"
        }

    if player["is_bot"]:

        return {
            "success": False,
            "message": "Bot cannot leave"
        }

    player["connected"] = False
    player["folded"] = True

    if game["current_player"] == user_id:

        set_next_player(
            table_id
        )

    return {
        "success": True
    }


# =========================================================
# OLD JOIN API
# =========================================================

@app.post("/join")
async def join_game(
    data: JoinRequest
):

    return await join_table(
        data.table_id,
        TableJoinRequest(
            user_id=data.user_id,
            name=data.name
        )
    )


# =========================================================
# PLAYERS
# =========================================================

@app.get("/players")
def get_players(
    table_id: int = 1
):

    game = get_table(table_id)

    result = []

    remaining = 0

    current_id = game["current_player"]

    if current_id:

        remaining = max(
            0,
            int(
                turn_deadlines.get(
                    table_id,
                    0
                ) - time.time()
            )
        )


    for p in game["players"]:

        result.append({
            "user_id": p["user_id"],
            "name": p["name"],
            "chips": p["chips"],
            "bet": p["bet"],
            "folded": p["folded"],
            "all_in": p["all_in"],
            "is_turn": (
                p["user_id"]
                == current_id
            ),
            "dealer": (
                game["players"].index(p)
                == game["dealer_index"]
            ),
            "small_blind": False,
            "big_blind": False,
            "is_bot": p["is_bot"],
            "turn_time": (
                remaining
                if p["user_id"] == current_id
                else 0
            ),
        })


    return {
        "success": True,
        "players": result
    }


# =========================================================
# START
# =========================================================

@app.post("/start")
async def start_game(
    table_id: int = 1
):

    result = await start_game_internal(
        table_id
    )

    if not result:

        return {
            "success": False,
            "message": "Need at least 2 players"
        }

    game = get_table(table_id)

    return {
        "success": True,
        "started": True,
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": game["winner"],
        "message": game["message"],
        "dealer_index": game["dealer_index"]
    }


# =========================================================
# GAME
# =========================================================

@app.get("/game")
def get_game(
    table_id: int = 1
):

    game = get_table(table_id)

    remaining = 0

    if game["current_player"]:

        remaining = max(
            0,
            int(
                turn_deadlines.get(
                    table_id,
                    0
                ) - time.time()
            )
        )


    return {
        "success": True,

        "table_id": table_id,

        "started": game["started"],

        "stage": game["stage"],

        "community_cards": game["community_cards"],

        "pot": game["pot"],

        "current_player": game["current_player"],

        "current_bet": game["current_bet"],

        "winner": game["winner"],

        "message": game["message"],

        "dealer_index": game["dealer_index"],

        "hand_number": game["hand_number"],

        "turn_time": TURN_TIME,

        "turn_remaining": remaining,

        "turn_deadline": turn_deadlines.get(
            table_id
        ),
    }


# =========================================================
# MY CARDS
# =========================================================

@app.get("/my-cards")
def my_cards(
    user_id: str,
    table_id: int = 1
):

    player = find_player(
        table_id,
        user_id
    )

    if not player:

        return {
            "success": False,
            "cards": []
        }

    return {
        "success": True,
        "cards": player["cards"]
    }


# =========================================================
# ACTION
# =========================================================

@app.post("/action")
async def action(
    data: ActionRequest,
    table_id: int = 1
):

    result = await process_action(
        table_id,
        data.user_id,
        data.action.lower(),
        data.amount
    )

    return result


# =========================================================
# NEXT HAND
# =========================================================

@app.post("/new-hand")
async def new_hand(
    table_id: int = 1
):

    await new_hand_internal(
        table_id
    )

    return {
        "success": True
    }


# =========================================================
# RESET
# =========================================================

@app.post("/reset")
async def reset(
    table_id: int = 1
):

    game = get_table(table_id)

    game["started"] = False

    game["stage"] = "waiting"

    game["deck"] = []

    game["community_cards"] = []

    game["pot"] = 0

    game["current_bet"] = 0

    game["current_player"] = None

    game["winner"] = None

    game["message"] = "Table reset"

    for p in game["players"]:

        p["cards"] = []

        p["folded"] = False

        p["all_in"] = False

        p["bet"] = 0

        p["total_bet"] = 0

        if p["chips"] <= 0:

            p["chips"] = STARTING_CHIPS


    task = turn_tasks.get(table_id)

    if task and not task.done():

        task.cancel()


    return {
        "success": True
    }


# =========================================================
# RUN
# =========================================================

# Render uses:
# uvicorn main:app --host 0.0.0.0 --port $PORT
