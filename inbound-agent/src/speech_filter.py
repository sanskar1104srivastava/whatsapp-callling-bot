"""Clean the LLM's streamed reply before it is spoken or stored in the chat history.

gpt-oss on Cerebras (reasoning low/medium) sometimes, mostly right after a tool call, adds junk after a good first
sentence: the same question again, English notes to itself ("We need to wait for user reply."), stage directions in
brackets, or tool-call JSON written out as text. A voice would read all of it aloud. The first sentence has been right
in every case we captured, so it is streamed word by word (no wait for the full stop, which cost 0.15-0.4 s a turn);
the sentences after it are checked whole: drop mostly-Latin sentences and near repeats, stop after the first question,
never more than two sentences. Bracketed text is always dropped, and non-breaking hyphens (which made the voice pause
oddly in "पिक‑अप") are removed.
"""
import re
from collections.abc import Callable

_END = re.compile(r"[।?!.]+[\"'”’)]*\s*")
_HYPHENS = str.maketrans({"‑": "", "‐": ""})
MAX_SENTENCES = 2


def _mostly_latin(s: str) -> bool:
    latin = sum(1 for c in s if "a" <= c.lower() <= "z")
    deva = sum(1 for c in s if "ऀ" <= c <= "ॿ")
    if deva == 0:
        return latin >= 4  # no Hindi at all: an English note to itself ("We wait."), not something to say
    return latin >= 8 and latin > deva  # brand names inside a Hindi sentence stay: Devanagari outweighs them


def _words(s: str) -> set[str]:
    return set(re.findall(r"[^\s।?!.,;:]+", s))  # split on spaces/punctuation: \w would cut Hindi words at vowel signs


class SpeechCleaner:
    def __init__(self, reject: Callable[[str], bool] | None = None) -> None:
        self._reject = reject or (lambda s: False)  # caller-specific: e.g. a reference number nobody issued
        self.rejected = False
        self._depth = 0
        self._buf = ""  # pending text of the current sentence (brackets already removed)
        self._early = ""  # part of the first sentence already released word by word
        self._emitted = 0
        self._stopped = False
        self._last_words: set[str] = set()

    def feed(self, text: str) -> str:
        """Add streamed text, return what may be spoken now."""
        for ch in text.translate(_HYPHENS):
            if ch in "([{":
                self._depth += 1
            elif ch in ")]}":
                self._depth = max(0, self._depth - 1)
            elif self._depth == 0:
                self._buf += ch
        out = []
        while not self._stopped and (m := _END.search(self._buf)):
            sentence, self._buf = self._buf[: m.end()], self._buf[m.end():]
            out.append(self._take(sentence))
        if self._stopped:
            self._buf = ""
        elif self._emitted == 0 and (cut := self._buf.rfind(" ")) > 0:
            head = self._buf[: cut + 1]  # whole words of the still-open first sentence
            so_far = self._early + head
            if any("ऀ" <= ch <= "ॿ" for ch in so_far) and not _mostly_latin(so_far) and not self._reject(so_far):  # some Hindi seen: not a leak
                self._early += head
                self._buf = self._buf[cut + 1:]
                out.append(head)
        return "".join(out)

    def flush(self) -> str:
        """End of stream: whatever is left, if it is still allowed."""
        tail, self._buf = self._buf, ""
        return "" if self._stopped else self._take(tail)

    def _take(self, rest: str) -> str:
        full, early = self._early + rest, self._early
        self._early = ""
        if not full.strip():
            return ""
        if early:  # first sentence, partly spoken already: finish it, unless the rest must not be said
            if self._reject(full):
                self.rejected = self._stopped = True
                return ""
            return self._accept(full, rest)
        if _mostly_latin(full):
            return ""
        if self._reject(full):
            self.rejected = True
            return ""
        words = _words(full)
        if self._last_words and words and len(words & self._last_words) / min(len(words), len(self._last_words)) >= 0.75:
            self._stopped = True  # it is the previous sentence said again
            return ""
        return self._accept(full, rest)

    def _accept(self, full: str, rest: str) -> str:
        self._last_words = _words(full)
        self._emitted += 1
        if full.rstrip().rstrip("\"'”’)").endswith("?") or self._emitted >= MAX_SENTENCES:
            self._stopped = True
        return rest if rest[-1:].isspace() or self._stopped else rest + " "  # keep sentences apart: "है। धन्यवाद"
