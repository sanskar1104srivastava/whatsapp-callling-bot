"""Run from inbound-agent/: uv run python tests/test_speech_filter.py  (the junk strings are real captures)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from speech_filter import SpeechCleaner  # noqa: E402


def clean(text, step=None):
    c = SpeechCleaner()
    parts = [text] if step is None else [text[i:i + step] for i in range(0, len(text), step)]
    return "".join(c.feed(p) for p in parts) + c.flush()


def norm(s):
    return " ".join(s.split())  # spacing between sentences may differ with chunking; the words may not


CASES = [
    ("ऑर्डर नंबर बताइए, जी?ऑर्डर नंबर कृपया बताइए।(Waiting for user response)कृपया ", "ऑर्डर नंबर बताइए, जी?"),
    ("कौन सा ऑर्डर नंबर है, सर?ऑर्डर नंबर बताइए।समझ गई, धन्यवाद।We need sum", "कौन सा ऑर्डर नंबर है, सर?"),
    ('हाँ जी, बताइए।{"request":"रेकॉन सेल्स से बात हो रही है?"}{"function":"x","arguments":{"a":1}}', "हाँ जी, बताइए।"),
    ("समझ गई। आपका ऑर्डर नंबर बताइए?", "समझ गई। आपका ऑर्डर नंबर बताइए?"),
    ("ठीक है। जी बिल्कुल। और कुछ?", "ठीक है। जी बिल्कुल।"),  # at most two sentences
    ("OK, ठीक है।", "OK, ठीक है।"),
    ("हाँ जी, Rekon Sales बोल रही हूँ।", "हाँ जी, Rekon Sales बोल रही हूँ।"),
    ("We wait.User hasn't responded yet.", ""),
    ("ऑर्डर नंबर बताइए, मैं देखती हूँ।We need to wait for user to give order number.कृपया ऑर्डर", "ऑर्डर नंबर बताइए, मैं देखती हूँ। कृपया ऑर्डर"),
    ("कृपया अपना ऑर्डर नंबर बताइए।कृपया बताइए।", "कृपया अपना ऑर्डर नंबर बताइए।"),  # restated
    ("कौन सा प्रोडक्ट चाहिए?कौन सा आइटम चाहिए?", "कौन सा प्रोडक्ट चाहिए?"),
    ("ठीक है।धन्यवाद, अलविदा।", "ठीक है। धन्यवाद, अलविदा।"),  # sentences kept apart
    ("डिलीवरी का पता बताइए या आप पिक‑अप करेंगे?", "डिलीवरी का पता बताइए या आप पिकअप करेंगे?"),  # no non-breaking hyphen
    ("हाँ जी", "हाँ जी"),  # no terminator: flushed at the end
    ("", ""),
    ("(कृपया ऑर्डर नंबर या विवरण बताइए)ऑर्डर नंबर बताइए।", "ऑर्डर नंबर बताइए।"),
]
for src, want in CASES:
    for step in (None, 1, 3, 7):  # whole text and arbitrary stream chunking must agree
        got = clean(src, step)
        assert norm(got) == norm(want), f"step={step}\n src ={src!r}\n got ={got!r}\n want={want!r}"

# the first sentence is released word by word, before its full stop arrives
c = SpeechCleaner()
assert c.feed("डोलो छह सौ") == "डोलो छह "  # whole words only; "सौ" may still grow
assert c.feed(" पचास की दो") == "सौ पचास की "
assert c.feed(" पत्ती, सही है?") + c.flush() == "दो पत्ती, सही है?"
# ...but an English leak at the start is held back, then dropped
c = SpeechCleaner()
assert c.feed("We need to wait ") == ""
assert c.feed("for the user.") + c.flush() == ""
# a caller-specific reject rule (used for reference numbers that were never issued)
import re  # noqa: E402
ref = lambda s: bool(re.search(r"रेफ़?रेंस", s))
for step in (None, 1, 4):
    c = SpeechCleaner(reject=ref)
    src = "ठीक है, पिकअप नोट कर लिया। आपका ऑर्डर रेफ़रेंस रेफ़12345 है, धन्यवाद।"
    parts = [src] if step is None else [src[i:i + step] for i in range(0, len(src), step)]
    got = "".join(c.feed(p) for p in parts) + c.flush()
    assert norm(got) == "ठीक है, पिकअप नोट कर लिया।" and c.rejected, (step, got)
    c = SpeechCleaner(reject=ref)  # also when it is the first sentence: never streamed early
    got = "".join(c.feed(ch) for ch in "आपका रेफ़रेंस नंबर एक दो तीन है।") + c.flush()
    assert "रेफ़" not in got and "तीन" not in got and c.rejected, got  # at most the words before it slip out
print("ok", len(CASES), "cases x 4 chunkings + streaming + reject")
