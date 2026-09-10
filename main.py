from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random

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

players = {}

game = {
    "started": False,
    "deck": [],
    "community_cards": [],
    "pot": 0,
}


class Player:
    def __init__(self, user_id, name):
        self.user_id = user_id
        self.name = name
        self.chips = STARTING_CHIPS
        self.cards = []
        self.folded = False
        self.bet = 0


class Action(BaseModel):
    user_id: str
    action: str


def create_deck():
    deck = [
        rank + suit
        for suit in SUITS
        for rank in RANKS
    ]

    random.shuffle(deck)
    return deck


@app.get("/")
def home():
    return {
        "status": "online",
        "message": "Poker backend is running"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/join")
def join_game(user_id: str, name: str = "Player"):

    if user_id not in players:
        players[user_id] = Player(user_id, name)

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
        "players": [
            {
                "user_id": p.user_id,
                "name": p.name,
                "chips": p.chips
            }
            for p in players.values()
        ]
    }


@app.post("/start")
def start_game():

    if len(players) < 1:
        return {
            "success": False,
            "message": "No players"
        }

    game["deck"] = create_deck()
    game["community_cards"] = []
    game["pot"] = 0
    game["started"] = True

    for player in players.values():

        player.cards = [
            game["deck"].pop(),
            game["deck"].pop()
        ]

        player.folded = False
        player.bet = 0

    return {
        "success": True,
        "message": "Game started"
    }


@app.get("/game")
def get_game():

    return {
        "started": game["started"],
        "community_cards": game["community_cards"],
        "pot": game["pot"]
    }


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


@app.post("/action")
def poker_action(data: Action):

    allowed = [
        "check",
        "call",
        "raise",
        "allin",
        "fold"
    ]

    if data.action not in allowed:
        return {
            "success": False,
            "message": "Invalid action"
        }

    if data.user_id not in players:
        players[data.user_id] = Player(
            data.user_id,
            "Player"
        )

    player = players[data.user_id]

    return {
        "success": True,
        "user_id": player.user_id,
        "action": data.action,
        "chips": player.chips
    }


@app.post("/next-card")
def next_card():

    if not game["started"]:
        return {
            "success": False,
            "message": "Game has not started"
        }

    current = len(game["community_cards"])

    # FLOP — 3 کارت همزمان
    if current == 0:

        flop = [
            game["deck"].pop(),
            game["deck"].pop(),
            game["deck"].pop()
        ]

        game["community_cards"].extend(flop)

        return {
            "success": True,
            "stage": "flop",
            "new_cards": flop,
            "community_cards": game["community_cards"],
            "pot": game["pot"]
        }

    # TURN — یک کارت
    elif current == 3:

        turn = game["deck"].pop()

        game["community_cards"].append(turn)

        return {
            "success": True,
            "stage": "turn",
            "new_cards": [turn],
            "community_cards": game["community_cards"],
            "pot": game["pot"]
        }

    # RIVER — یک کارت
    elif current == 4:

        river = game["deck"].pop()

        game["community_cards"].append(river)

        return {
            "success": True,
            "stage": "river",
            "new_cards": [river],
            "community_cards": game["community_cards"],
            "pot": game["pot"]
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

    for player in players.values():
        player.cards = []
        player.folded = False
        player.bet = 0

    return {
        "success": True,
        "message": "Game reset"
    }
