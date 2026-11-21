"""AI-calling API. Mount with app.include_router(router) (prefix /ai-calling)."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from . import store

# LiveKit built-in clips ("" = none). Same options as the AlgoTalks context form.
SOUNDS = {"", "office_ambience", "city_ambience", "crowded_room", "forest_ambience",
          "keyboard_typing", "keyboard_typing2", "hold_music"}
VOICE_LANGS = {"hi": {"hi", "enhi"}, "en": {"en"}}  # which catalog languages fit each use-case language

router = APIRouter(prefix="/ai-calling", tags=["ai-calling"])


@lru_cache
def _voices() -> list[dict]:
    """bulbul:v4-flash Hindi/English/Hinglish voices. Gender is not published by Sarvam for v4: it is taken from
    the v3 persona-name mapping in the LiveKit Sarvam plugin plus Sarvam's recommended-voice tables; the rest are 'unknown'."""
    return json.loads((Path(__file__).parent / "voices.json").read_text(encoding="utf-8"))


class UseCase(BaseModel):
    usecaseId: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    name: str
    spokenName: str = ""  # how the voice agent says it in the opening, e.g. "ऑर्डर देने"
    type: str = "custom"
    intentDescription: str
    examples: list[str] = []
    prompt: str
    greeting: str = ""
    closing: str = ""
    language: Literal["hi", "en"] = "hi"
    gender: Literal["female", "male"] = "female"
    voice: str = ""  # bulbul:v4-flash speaker id; "" = agent default
    backgroundSound: str = ""  # plays through the whole call
    toolSound: str = ""  # plays while a tool runs
    knowledgeBaseId: str = ""
    slots: list[str] = []
    tools: list[dict] = []
    channels: list[str] = ["chat", "voice"]
    priority: int = 99
    canPreempt: bool = False
    enabled: bool = True


    @model_validator(mode="after")
    def _check(self):
        if self.backgroundSound not in SOUNDS or self.toolSound not in SOUNDS:
            raise ValueError("unknown sound")
        if self.voice:
            v = next((x for x in _voices() if x["id"] == self.voice), None)
            if not v or v["language"] not in VOICE_LANGS[self.language]:
                raise ValueError(f"voice {self.voice!r} is not a bulbul:v4-flash voice for language {self.language!r}")
        if not self.channels or set(self.channels) - {"chat", "voice"}:
            raise ValueError("channels must be a non-empty subset of chat, voice")
        return self


class Settings(BaseModel):
    companyName: str = Field("Rekon Sales", min_length=1, max_length=60)  # who the assistant says it answers for
    purposeGreeting: str  # first words when the call connects, e.g. "हेलो।"
    defaultUsecaseId: str = ""  # "" = none: an unclear request gets a polite list of what the agent can do
    classifier: str = "prism"
    T_high: float = Field(0.9, ge=0, le=1)
    margin: float = Field(0.3, ge=0, le=1)
    T_multi: float = Field(0.7, ge=0, le=1)
    maxIntentsPerCall: int = Field(2, ge=1, le=5)
    maxRoutingTurns: int = Field(2, ge=1, le=5)


@router.get("/voices")
def voices():
    return _voices()


@router.get("/usecases")
def list_usecases():
    return store.list_usecases()


@router.get("/usecases/{uid}")
def get_usecase(uid: str):
    uc = store.get_usecase(uid)
    if not uc:
        raise HTTPException(404, "use case not found")
    return uc


@router.post("/usecases", status_code=201)
def create_usecase(uc: UseCase):
    if store.get_usecase(uc.usecaseId):
        raise HTTPException(409, "usecaseId already exists")
    return store.put_usecase(uc.model_dump())


@router.put("/usecases/{uid}")
def update_usecase(uid: str, uc: UseCase):
    if uid != uc.usecaseId or not store.get_usecase(uid):
        raise HTTPException(404, "use case not found")
    return store.put_usecase(uc.model_dump())


@router.delete("/usecases/{uid}", status_code=204)
def delete_usecase(uid: str):
    if uid == store.get_settings().get("defaultUsecaseId"):
        raise HTTPException(409, "cannot delete the default use case")
    if not store.delete_usecase(uid):
        raise HTTPException(404, "use case not found")


@router.put("/usecases-order")
def reorder(ids: list[str]):
    known = {u["usecaseId"] for u in store.list_usecases()}
    if set(ids) - known:
        raise HTTPException(422, "unknown usecaseId in order")
    return store.reorder(ids)


@router.get("/settings")
def get_settings():
    return store.get_settings()


@router.put("/settings")
def put_settings(s: Settings):
    if s.defaultUsecaseId and not store.get_usecase(s.defaultUsecaseId):
        raise HTTPException(422, "defaultUsecaseId does not exist")
    return store.put_settings(s.model_dump())


@router.get("/conversations")
def conversations():
    return store.list_conversations()
