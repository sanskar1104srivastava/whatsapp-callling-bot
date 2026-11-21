"""Dummy data only. No real customers.

Three use cases: order medicine, raise a complaint, book an appointment. Anything else is politely declined by the
agent (Prism's out-of-scope option), so there is no catch-all use case.
"""


def _uc(uid, name, typ, desc, examples, prompt, priority, preempt=False, slots=(), spoken=""):
    return {
        "usecaseId": uid, "name": name, "spokenName": spoken, "type": typ,
        "intentDescription": desc, "examples": examples,
        "prompt": prompt, "greeting": "", "closing": "", "language": "hi", "gender": "female",
        "voice": "simran_hi_conversation", "backgroundSound": "office_ambience", "toolSound": "",
        "knowledgeBaseId": "", "slots": list(slots), "tools": [],
        "channels": ["chat", "voice"], "priority": priority, "canPreempt": preempt, "enabled": True,
    }


SEED = {
    "usecases": [
        _uc(
            "complaint", "Raise a complaint", "complaint",
            "Caller has a problem with an order or the service: late or missing delivery, wrong or damaged medicine, "
            "a billing mistake, or bad behaviour by staff",
            ["मेरा ऑर्डर अभी तक नहीं आया", "गलत दवाई आ गई है", "bill mein zyada paise lag gaye"],
            "Listen to the complaint and say sorry once, sincerely. Find out what went wrong and the order number if "
            "there is one. Do not argue, do not blame anyone, and do not promise a refund, replacement or timeline. "
            "Log it with submit_request and tell them the team will call them back.",
            1, preempt=True, slots=["what went wrong", "order number"], spoken="शिकायत",
        ),
        _uc(
            "order", "Order medicine", "order",
            "Caller wants to order or buy medicines (names, strips, bottles, boxes, quantities) for delivery or pickup",
            ["मुझे डोलो 650 की दो पत्ती चाहिए", "crocin ka order dena hai", "दवाई मँगवानी है"],
            "Take the caller's medicine order. For each medicine get the name, the strength if they know it (like 650 mg) "
            "and the quantity (strips, bottles or boxes)."
            "Words callers use: पत्ती or पत्ता means one strip of tablets (दो पत्ती = two strips), डिब्बा means a box, शीशी means a bottle. A quantity said like that IS the quantity: never ask for it again. Read the list back once, briefly, to confirm. Then ask for the "
            "delivery address, or whether they will pick it up. Never suggest a medicine, a dose or a substitute and never "
            "give medical advice: if asked, say a doctor or pharmacist should advise on that. Then place it with submit_request.",
            2, slots=["medicine name", "strength", "quantity", "delivery address or pickup"], spoken="दवाई का ऑर्डर",
        ),
        _uc(
            "appointment", "Book appointment", "appointment",
            "Caller wants to book, change or cancel an appointment or a visit",
            ["कल शाम का अपॉइंटमेंट चाहिए", "appointment book karna hai", "मिलने का टाइम चाहिए"],
            "Book the appointment. Ask what it is for, the preferred date and time, and the caller's name. Confirm it back "
            "in one short sentence. You cannot see a live calendar, so say the team will confirm the exact time. "
            "Then save it with submit_request.",
            3, slots=["purpose", "date", "time", "name"], spoken="अपॉइंटमेंट",
        ),
    ],
    "settings": {
        "companyName": "Rekon Sales",
        "purposeGreeting": "हेलो।",
        "defaultUsecaseId": "",  # none: an unclear request gets a polite "this is what I can help with"
        "classifier": "prism",
        "T_high": 0.9, "margin": 0.3, "T_multi": 0.7,
        "maxIntentsPerCall": 2, "maxRoutingTurns": 2,
    },
    "conversations": [
        {"id": "c1", "channel": "voice", "customer": "Demo Caller 1", "startedAt": "2026-11-18T10:02:00Z",
         "durationSec": 74, "routed": ["order"], "queue": [], "transcript": [
             {"role": "agent", "text": "हेलो।"},
             {"role": "user", "text": "मुझे डोलो 650 की दो पत्ती चाहिए"}]},
        {"id": "c2", "channel": "chat", "customer": "Demo Customer 2", "startedAt": "2026-11-18T10:20:00Z",
         "durationSec": 0, "routed": ["complaint", "order"], "queue": ["order"], "transcript": [
             {"role": "user", "text": "mera order late hai aur ek nayi dawai bhi mangwani hai"}]},
    ],
}
