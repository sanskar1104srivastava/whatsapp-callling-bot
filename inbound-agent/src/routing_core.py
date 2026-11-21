"""Use-case routing: ask Sarvam Prism what the caller wants, turn its probabilities into an ordered queue.

Pure Python (aiohttp only), no LiveKit imports, so it is unit-testable and reusable by the chat bot.

One Prism call per utterance (the text is billed once per request, extra questions are cheap):
  - `main`      : choice, "which ONE use case does the caller mainly want" (+ an `unclear` option)
  - `yes_<id>`  : noul (independent yes/no) per use case, "is the caller also asking for this"
"""
import asyncio
import os
import random
from dataclasses import dataclass, field

import aiohttp

PRISM_URL = os.getenv("PRISM_URL", "https://api.sarvam.ai/v2/systemone")
PRISM_MODEL = "sarvam-prism"
UNCLEAR = "unclear"  # greeting / too vague to tell: ask again
OUT_OF_SCOPE = "out_of_scope"  # a clear request we do not handle: politely decline
OUT_OF_SCOPE_MIN = 0.5
_RETRY_STATUS = {429, 502, 503}


class PrismError(Exception):
    pass


@dataclass
class Decision:
    kind: str  # "route" | "unclear" | "out_of_scope"
    queue: list[str] = field(default_factory=list)  # use case ids, priority order
    scores: dict = field(default_factory=dict)


def enabled_voice(usecases: list[dict]) -> list[dict]:
    return sorted(
        (u for u in usecases if u.get("enabled") and "voice" in u.get("channels", [])),
        key=lambda u: u["priority"],
    )


def build_opening(settings: dict, usecases: list[dict], limit: int = 3) -> str:
    """The first words of the call. A {options} placeholder, if present, becomes the top use cases."""
    names = [u.get("spokenName") or u["name"] for u in enabled_voice(usecases)[:limit]]
    options = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " और " + names[-1] if names else ""
    template = settings["purposeGreeting"]
    return template.format(options=options)  # without {options} the opening is used as written, e.g. just "हेलो।"


def build_request(text: str, usecases: list[dict]) -> dict:
    ucs = enabled_voice(usecases)
    criteria = {u["usecaseId"]: _describe(u) for u in ucs}
    criteria[UNCLEAR] = "The caller only greeted, was cut off, or said something too short or vague to tell what they want."
    criteria[OUT_OF_SCOPE] = (
        "The caller clearly wants something, but it is none of the other options "
        "(another product or service, a loan, a recharge, general knowledge, medical advice, anything else)."
    )
    questions = {"main": {"type": "choice", "instructions": "Which one thing does the caller mainly want?", "criteria": criteria}}
    for u in ucs:
        questions[f"yes_{u['usecaseId']}"] = {
            "type": "noul",
            "instructions": f"Is the caller asking for this: {_describe(u)}",
        }
    return {"model": PRISM_MODEL, "state": f"Caller on a phone call (Hindi/Hinglish) said: {text}", "questions": questions}


def _describe(u: dict) -> str:
    ex = "; ".join(u.get("examples") or [])
    return f"{u['intentDescription']}" + (f" (e.g. {ex})" if ex else "")


def parse_scores(resp: dict, usecases: list[dict]) -> dict:
    """-> {"choice": {id: p, "unclear": p}, "yes": {id: p}}. Raises PrismError on any unexpected shape."""
    try:
        answers = resp["answers"]
        choice = {k: float(v) for k, v in answers["main"]["probabilities"].items()}
        yes = {u["usecaseId"]: float(answers[f"yes_{u['usecaseId']}"]["noul"]) for u in enabled_voice(usecases)}
    except (KeyError, TypeError, ValueError) as e:
        raise PrismError(f"unexpected Prism response: {e!r}") from e
    return {"choice": choice, "yes": yes}


async def classify(http: aiohttp.ClientSession, text: str, usecases: list[dict], api_key: str, timeout: float = 1.5) -> dict:
    """One Prism call, retried once on 429/502/503. Raises PrismError on any failure."""
    body = build_request(text, usecases)
    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
    for attempt in (1, 2):
        try:
            async with http.post(PRISM_URL, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status in _RETRY_STATUS and attempt == 1:
                    await asyncio.sleep(0.2 + random.random() * 0.2)
                    continue
                if r.status != 200:
                    raise PrismError(f"Prism HTTP {r.status}")
                return parse_scores(await r.json(), usecases)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt == 2:
                raise PrismError(f"Prism unreachable: {e!r}") from e
    raise PrismError("unreachable")  # pragma: no cover


async def warm_up(http: aiohttp.ClientSession) -> None:
    """Open the TLS connection to Prism before the first utterance (cold connect can take seconds). Never raises."""
    try:
        async with http.get(PRISM_URL.rsplit("/v2/", 1)[0], timeout=aiohttp.ClientTimeout(total=5)):
            pass
    except Exception:
        pass


def decide(scores: dict, usecases: list[dict], settings: dict) -> Decision:
    """Probabilities -> ordered queue. Never queues everything:
    candidates = use cases whose independent yes >= T_multi, plus the top choice if it is decisive
    (>= T_high and ahead of the runner-up by `margin`). Keep the strongest `maxIntentsPerCall`,
    then order by the vendor's priority (lower first)."""
    ucs = {u["usecaseId"]: u for u in enabled_voice(usecases)}
    choice = {i: scores["choice"].get(i, 0.0) for i in ucs}
    cands = {i: p for i, p in scores["yes"].items() if i in ucs and p >= settings["T_multi"]}
    if choice:
        ranked = sorted(choice.values(), reverse=True)
        top = max(choice, key=choice.get)
        second = ranked[1] if len(ranked) > 1 else 0.0
        if choice[top] >= settings["T_high"] and choice[top] - second >= settings["margin"]:
            cands.setdefault(top, choice[top])
    if not cands:
        oos, unc = scores["choice"].get(OUT_OF_SCOPE, 0.0), scores["choice"].get(UNCLEAR, 0.0)
        return Decision("out_of_scope" if oos >= OUT_OF_SCOPE_MIN and oos >= unc else "unclear", scores=scores)
    strongest = sorted(cands, key=lambda i: -cands[i])[: settings["maxIntentsPerCall"]]
    queue = sorted(strongest, key=lambda i: (ucs[i]["priority"], -cands[i]))
    return Decision("route", queue, scores)
