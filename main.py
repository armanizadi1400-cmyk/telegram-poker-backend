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


def create_deck():
    deck = [
        rank + suit
        for suit in SUITS
        for rank in RANKS
    ]
    random.shuffle(deck)
    return deck


class Player:
    def __init__(self, user_id, name):
        self.user_id = user_id
        self.name = name
        self.chips = STARTING_CHIPS
        self.cards = []
        self.folded = False
        self.bet = 0


players = {}


class Action(BaseModel):
    user_id: str
    action: str


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
        players[user_id] = Player(
            user_id=user_id,
            name=name
        )

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
            user_id=data.user_id,
            name="Player"
        )

    player = players[data.user_id]

    return {
        "success": True,
        "user_id": player.user_id,
        "action": data.action,
        "chips": player.chips
    }


@app.get("/new-deck")
def new_deck():

    return {
        "cards": create_deck()
    }
