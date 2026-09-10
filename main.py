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


def create_deck():
    deck = [
        rank + suit
        for suit in SUITS
        for rank in RANKS
    ]

    random.shuffle(deck)
    return deck


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

    return {
        "success": True,
        "user_id": data.user_id,
        "action": data.action
    }


@app.get("/new-deck")
def new_deck():

    deck = create_deck()

    return {
        "cards": deck
    }
