"""
The deterministic core: plain Python rules, no AI.

Golden rule #1: "AI reads and writes. Code decides money."
Everything that touches money, deadlines or permissions lives here, so it is
exact, testable and the same every time.
"""

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone  # noqa: F401 (re-used by main.py)

from .config import PROJECT_ROOT

# ---------------------------------------------------------------------------
# Money: always integer minor units (paise for INR, 100 paise = 1 rupee). Floats make rounding
# errors (0.1 + 0.2 != 0.3). The names say "cents" because the maths is the same for any 2-decimal currency.
# ---------------------------------------------------------------------------


def to_cents(value: str | int | float) -> int:
    s = str(value).strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise ValueError(f"Not a money amount: {value!r}")
    whole, _, frac = s.partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def from_cents(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def dispute_amount(dispute: dict) -> tuple[int, str]:
    amt = dispute.get("dispute_amount") or {}
    return to_cents(amt.get("value", "0")), amt.get("currency_code", "INR")


def rupees_text(cents: int, currency: str = "INR") -> str:
    """149999 paise -> "₹1,499.99", with Indian digit grouping (₹1,49,999)."""
    whole, frac = divmod(cents, 100)
    digits = str(whole)
    head, tail = digits[:-3], digits[-3:]
    while len(head) > 2:
        tail, head = head[-2:] + "," + tail, head[:-2]
    grouped = (head + "," + tail) if head else tail
    symbol = "₹" if currency == "INR" else f"{currency} "
    return f"{symbol}{grouped}" + (f".{frac:02d}" if frac else "")


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    # Some APIs send "+0000" without a colon
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


RESPONSE_DAYS = 7   # used only if a case arrives without Razorpay's respond_by


def deadline_info(dispute: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    created = parse_time(dispute.get("create_time"))
    due, estimated = parse_time(dispute.get("seller_response_due_date")), False
    if not due and created and not seller_submissions(dispute):
        due, estimated = created + timedelta(days=RESPONSE_DAYS), True
    if not due:
        return {"due": None, "hours_left": None, "urgency": "unknown", "estimated": False, "created": None}
    hours = (due - now).total_seconds() / 3600
    urgency = "overdue" if hours < 0 else "critical" if hours < 48 else "soon" if hours < 120 else "ok"
    return {"due": due.isoformat(), "hours_left": round(hours, 1), "urgency": urgency, "estimated": estimated,
            "created": created.isoformat() if created else None}


# ---------------------------------------------------------------------------
# What the case says, what the seller has filed, and what the seller may do now
# ---------------------------------------------------------------------------

def dispute_statements(dispute: dict) -> list[dict]:
    """Customer messages on the case itself. Razorpay disputes carry none (the bank relays the complaint as a
    reason code), so customer words come from shop chats and calls, which the Collector scans the same way."""
    return list(dispute.get("messages") or [])


def requested_evidence(dispute: dict) -> list[str]:
    """Razorpay doesn't say which documents the bank wants; the rule checker works it out from the reason."""
    return []


def seller_submissions(dispute: dict) -> list[dict]:
    """Razorpay's own record of a filed contest: our receipt, and the guard against filing twice."""
    ev = dispute.get("evidence") or {}
    if not ev.get("submitted_at"):
        return []
    when = datetime.fromtimestamp(int(ev["submitted_at"]), timezone.utc).isoformat()
    files = (sum(len(v) for k, v in ev.items() if isinstance(v, list) and k != "others")
             + sum(len(o.get("document_ids") or []) for o in ev.get("others") or []))
    return [{"date": when, "notes": ev.get("summary") or "", "amount": ev.get("amount"), "files": files}]


CLOSED_STATUSES = ("won", "lost", "closed")


def is_closed(dispute: dict) -> bool:
    return dispute.get("status") in CLOSED_STATUSES


# ---------------------------------------------------------------------------
# Permissions, from Razorpay's own rules: contest and accept work only while the dispute is open
# and before respond_by, and Razorpay refuses both after that. We never attempt anything else.
# ---------------------------------------------------------------------------

ACTION_RELS = {"contest", "accept"}


def allowed_actions(dispute: dict, now: datetime | None = None) -> set[str]:
    if dispute.get("status") != "open" or seller_submissions(dispute):
        return set()
    due = parse_time(dispute.get("seller_response_due_date"))
    if due and due < (now or datetime.now(timezone.utc)):
        return set()
    return set(ACTION_RELS)


# ---------------------------------------------------------------------------
# Policy knowledge base (the "R" in RAG: Retrieval)
# ---------------------------------------------------------------------------

POLICY_FILE = os.path.join(PROJECT_ROOT, "policy", "dispute_rules.md")


def load_policy_sections(path: str = POLICY_FILE) -> dict[str, str]:
    sections, current, buf = {}, None, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                if current:
                    sections[current] = "".join(buf).strip()
                current, buf = line[3:].strip(), []
            elif current:
                buf.append(line)
    if current:
        sections[current] = "".join(buf).strip()
    return sections


def retrieve_policy(reason: str) -> str:
    """Return the rules for this dispute reason + the general rules.
    Simple and exact: the reason code IS the lookup key. (Real RAG with vector
    search is useful when you have hundreds of pages; we have one page.)"""
    sections = load_policy_sections()
    specific = sections.get(reason) or sections.get("OTHER", "")
    return f"Rules for {reason}:\n{specific}\n\nGeneral rules:\n{sections.get('General rules for every reply', '')}"


# ---------------------------------------------------------------------------
# Safety: untrusted text (buyer messages) may contain prompt injection
# ---------------------------------------------------------------------------

INJECTION_PATTERNS = [
    # overriding the AI's instructions
    r"(ignore|disregard|forget)\b.{0,25}\b(previous|prior|earlier|above|everything|instructions?|rules)\b",
    # the same in Hindi word order, where the verb comes last ("saare instructions bhool jao")
    r"\b(instructions?|rules?|niyam)\b.{0,30}\b(bhool|bhul|chhod|nazarandaz)\w*",
    r"\b(new|updated) (policy|instructions?|rules?)\s*:",
    # talking to the AI directly
    r"\b(note|message|attention|instructions?) (to|for) (the |any )?(ai|assistant|bot|model|agent|llm)\b",
    r"\b(the |any )?(ai|assistant|bot|model|llm)\b[^.!?]{0,40}\b(must|should|shall|needs? to|has to|have to)\b",
    r"(^|[.!?\n] ?)assistant\s*[,:]",
    r"\bas an ai\b",
    # fake authority
    r"\b(admin|administrator|system|compliance|root)\b.{0,12}\boverride\b",
    r"\boverride\s*(code|token|mode)\b",
    # fake system markup or conversation turns
    r"</?\s*(system|assistant|user|instructions?)\s*>",
    r"\[/?\s*(system|inst|instructions?)\s*\]",
    r"#{2,}\s*(instructions?|system)\b",
    r"\b(system|user|human)\s*:",
    # role play and jailbreaks
    r"\byou are now\b", r"\bpretend (that )?(you are|to be)\b", r"\bact as (an?|the|my)\b",
    r"\bdeveloper mode\b", r"\bjailbreak\b",
    # fake approval, or asking to skip a safety step
    r"\b(seller|merchant|owner) (has )?already (approved|agreed|accepted|authori[sz]ed)\b",
    r"\b(skip|bypass|disable|turn off)\b.{0,20}\b(checks?|verifier|verification|review|approval|rules)\b",
    r"\bno (human )?(approval|review|verification)\b.{0,15}\bneeded\b",
    # reading out the prompt
    r"\bsystem prompt\b",
    r"\b(print|reveal|show|repeat|output)\b.{0,10}\b(your|the)\b.{0,20}\b(prompt|instructions|rules)\b",
    # steering tools, evidence or the decision
    r"\b(call|use|run|invoke)\b.{0,15}\btool\b",
    r"\btreat (this|the|my)\b.{0,20}\bas\b.{0,15}\b(verified|evidence|proof|fact)\b",
    r"\b(do not|don'?t|never) (cite|use|mention)\b.{0,30}\b(tracking|evidence|record)\b",
    r"\boutput\b.{0,20}\b(decision|recommendation|verdict)\b",
    r"\b(decision|recommendation|verdict)\s*=",
    r"\bmust (accept|approve|refund)\b",
    r"\b(issue|approve|send) (a |the )?(full )?refund (now|immediately|right away)\b",
]


def _normalise_for_scan(text: str) -> str:
    """Attackers hide words with invisible characters, odd unicode or extra spaces."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")   # zero-width and other format characters
    return re.sub(r"\s+", " ", text).strip().lower()


def injection_flags(text: str) -> list[str]:
    """A warning for the seller, not the defence itself. Even if an attack slips past this, the AI
    has read-only tools, code decides money, and nothing reaches Razorpay without a human click."""
    t = _normalise_for_scan(text)
    return [p for p in INJECTION_PATTERNS if re.search(p, t)]


# ---------------------------------------------------------------------------
# Verifier, code layer: every claim must cite real evidence, and every
# date / amount / tracking number in the claim must appear in that evidence.
# ---------------------------------------------------------------------------

FACT_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",                 # dates like 2026-10-20
    r"(?:₹|\$|\bRs\.?)\s?\d[\d,]*(?:\.\d{2})?",   # ₹1,499 or Rs. 1499.00 or $40
    r"(?<![\d,.])\d+\.\d{2}\b",                     # 40.00 (not the tail of 2,499.00)
    r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b",         # tracking numbers / ids (must contain a digit)
]


QUOTE_PATTERN = re.compile(r'["\u201c]([^"\u201d]+)["\u201d]')


def _norm_quote(text: str) -> str:
    text = text.replace("\\", "").replace("\u2019", "'")
    return " ".join(re.sub(r"[^\w' ]+", " ", text).lower().split())


def extract_facts(text: str) -> set[str]:
    facts = set()
    for p in FACT_PATTERNS:
        for m in re.findall(p, text):
            facts.add(re.sub(r"^(?:₹|\$|Rs\.?)", "", m.replace(" ", "")).replace(",", ""))
    return facts


# The only words in a reply that aren't a checked claim. Code owns them, because a sentence the verifier
# never sees is a sentence that could say anything, such as agreeing to a refund.
REPLY_OPENING = "Thank you for reviewing this case. These are the facts, each with its evidence."
REPLY_CLOSING = "Please see the evidence listed below."


def check_claim(claim: dict, evidence_by_id: dict[str, dict]) -> tuple[bool, str]:
    ids = claim.get("evidence_ids") or []
    if not ids:
        return False, "No evidence cited."
    missing = [i for i in ids if i not in evidence_by_id]
    if missing:
        return False, f"Cites evidence that does not exist: {', '.join(missing)}."
    if any(evidence_by_id[i]["content"].get("injection_suspected") for i in ids):
        return False, "Cites a message flagged as trying to manipulate the AI."
    cited_text = " ".join(str(evidence_by_id[i]["content"]) + " " + evidence_by_id[i]["summary"] for i in ids)
    cited_facts = extract_facts(cited_text)
    for quote in QUOTE_PATTERN.findall(claim.get("text", "")):
        if len(quote.split()) >= 3 and _norm_quote(quote) not in _norm_quote(cited_text):
            return False, f"Quotes \"{quote}\", which is not in the cited evidence."
    # A fact in the claim must appear verbatim or as a number in the cited evidence
    for fact in extract_facts(claim.get("text", "")):
        normalized = fact.split(".")[0] if re.fullmatch(r"\d+\.00", fact) else fact
        if fact not in cited_facts and normalized not in cited_text:
            return False, f"Mentions '{fact}', which is not in the cited evidence."
    ok, why = check_call_claim(claim, evidence_by_id)
    if not ok:
        return False, why
    return True, "Cited evidence exists and contains every date, amount and ID mentioned."


# ---------------------------------------------------------------------------
# Call transcripts prove what someone said, never that it happened. A driver saying "I delivered it"
# is not the courier's delivery record a bank asks for, and speech-to-text can mishear. So a transcript can
# only be quoted word for word, from the other party, and it never satisfies an evidence requirement.
# ---------------------------------------------------------------------------

CALL_KIND = "call_transcript"
NOT_RECEIVED_REASON, NOT_AS_DESCRIBED_REASON, UNAUTHORISED_REASON = ("MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                                                                  "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", "UNAUTHORISED")
SPEAKER_ROLES = {"buyer": "buyer", "customer": "buyer", "carrier": "carrier", "driver": "carrier", "courier": "carrier",
                 "delivery agent": "carrier",
                 "seller": "staff", "agent": "staff", "staff": "staff", "support": "staff"}
CALL_ASSERTION = re.compile(r"\b(delivered|delivery|received|arrived|confirm\w*|prov\w*|admit\w*|verif\w*|mil gaya|aa gaya)\b", re.I)
# English, plus Hinglish as Gnani writes it in Latin letters ("nahi mila" = didn't get it)
NEGATIONS = {"not", "never", "no", "without", "didn't", "don't", "doesn't", "wasn't", "isn't", "hasn't", "haven't", "won't", "can't",
             "nahi", "nahin", "nai", "na", "mat", "kabhi"}


def parse_call_turns(transcript: str) -> list[dict]:
    """Every line must start with who spoke ("Buyer: ..."). Speech-to-text can't reliably tell
    speakers apart, so the seller labels them, and a quote can be traced to the person who said it."""
    turns = []
    for line in transcript.splitlines():
        if not line.strip():
            continue
        label, sep, text = line.partition(":")
        role = SPEAKER_ROLES.get(label.strip().lower())
        if not sep or not role or not text.strip():
            raise ValueError(f"Start each line with who spoke, like \"Buyer: ...\" or \"Agent: ...\". Check: {line.strip()[:60]}")
        turns.append({"speaker": role, "text": text.strip()})
    if not turns:
        raise ValueError("The transcript is empty.")
    if all(t["speaker"] == "staff" for t in turns):
        raise ValueError("The transcript needs the other person's words, not only your own team's.")
    return turns


def _quote_in_turn(quote: str, turn_text: str) -> bool:
    """True if the words are in this turn and aren't flipped by a negation just before them."""
    turn = f" {_norm_quote(turn_text)} "
    for m in re.finditer(re.escape(f" {_norm_quote(quote)} "), turn):
        if not NEGATIONS & set(turn[:m.start()].split()[-4:]):
            return True
    return False


def check_call_claim(claim: dict, evidence_by_id: dict[str, dict]) -> tuple[bool, str]:
    cited = [evidence_by_id[i] for i in claim.get("evidence_ids") or []]
    calls = [e for e in cited if e.get("kind") == CALL_KIND]
    if not calls:
        return True, ""
    text = claim.get("text", "")
    quotes = [q for q in QUOTE_PATTERN.findall(text) if len(q.split()) >= 3]
    if not quotes:
        return False, "A call can only be quoted word for word. It shows what was said, not what happened."
    outside = QUOTE_PATTERN.sub(" ", text).lower()
    if len(calls) == len(cited) and CALL_ASSERTION.search(outside):
        return False, "States an event as fact from a call. Only the quote stands, and only as what was said."
    wanted = "buyer" if re.search(r"\b(buyer|customer)\b", outside) else "carrier" if re.search(r"\b(carrier|driver|courier)\b", outside) else None
    turns = [t for e in calls for t in e["content"]["turns"] if t["speaker"] != "staff" and (not wanted or t["speaker"] == wanted)]
    for quote in quotes:
        if not any(_quote_in_turn(quote, t["text"]) for t in turns):
            return False, f"\"{quote}\" isn't what {('the ' + wanted) if wanted else 'the other party'} said on the call."
    return True, ""


def call_only(claim: dict, evidence_by_id: dict[str, dict]) -> bool:
    ids = claim.get("evidence_ids") or []
    return bool(ids) and all(evidence_by_id.get(i, {}).get("kind") == CALL_KIND for i in ids)


def call_quote_ok(quote: str, turn_text: str) -> bool:
    return len(quote.split()) >= 3 and _quote_in_turn(quote, turn_text)


def find_reference(text: str, refs: list[tuple[str, str]]) -> str | None:
    """Which case reference does this recording mention? Speech-to-text may write DD-1003 as "DD 1003",
    so separators are optional, but DD-1003 must never match DD-10035: a wrong match attaches a stranger's call."""
    for label, value in refs:
        chars = re.sub(r"[^a-z0-9]", "", (value or "").lower())
        if len(chars) >= 5 and re.search(r"(?<![a-z0-9])" + r"[\s\-_.]*".join(chars) + r"(?![a-z0-9])", text.lower()):
            return label
    return None


# What a buyer would have to say to undercut their own claim. A flag for the seller: it decides nothing.
CONTRADICTING_TOPICS = {NOT_RECEIVED_REASON: ("receipt", "delivery"), NOT_AS_DESCRIBED_REASON: ("item_condition",), UNAUTHORISED_REASON: ("purchase",)}


def call_contradicts_claim(reason: str, finding: dict) -> bool:
    return (finding["speaker"] == "buyer" and finding["stance"] == "helps"
            and finding["topic"] in CONTRADICTING_TOPICS.get(reason, ()))


def call_party(transcript: str) -> str:
    return "carrier" if re.search(r"(?im)^\s*(carrier|driver|courier)\s*:", transcript) else "buyer"


def call_next_steps(missing: list[str], findings: list[dict], needs_check: bool = False) -> list[str]:
    """What the seller should do next, from what the calls said and what is still missing. Plain rules."""
    steps = ["Check the transcript and who said what. Quotes join your reply only after you confirm."] if needs_check else []
    if any(f["contradicts"] for f in findings):
        steps.append("Ask the customer to confirm this in writing, on WhatsApp or email. Their written words carry more weight than a transcript.")
    if missing and any(f["speaker"] == "carrier" for f in findings):
        steps.append("A delivery agent's word isn't courier proof. Download the proof of delivery (POD) from the courier, then add it to the case.")
    if any(f["stance"] == "hurts" for f in findings):
        steps.append("Read the statements that may hurt your case before you approve. They're left out of your reply, so make sure the reply is still fair and true.")
    return steps


# ---------------------------------------------------------------------------
# Buyer risk: some buyers dispute almost every order ("friendly fraud").
# Computed by code from shop records, so it works even if the AI skips the lookup.
# ---------------------------------------------------------------------------

def buyer_risk(history: dict | None) -> dict:
    h = history or {}
    orders, disputes = int(h.get("previous_orders", 0)), int(h.get("previous_disputes", 0))
    if disputes >= 2 and disputes * 2 >= orders:
        level = "high"
        why = f"Buyer has disputed {disputes} of {orders} previous orders. A settlement would reward the pattern."
    elif disputes >= 1:
        level = "medium"
        why = f"Buyer has disputed {disputes} of {orders} previous orders."
    else:
        level = "low"
        why = f"No previous disputes ({orders} previous orders)." if orders else "New buyer, no history."
    return {"level": level, "why": why, "previous_orders": orders, "previous_disputes": disputes}


# ---------------------------------------------------------------------------
# Decide: fight, settle, or accept. Pure rules, explained in plain words.
# ---------------------------------------------------------------------------

def recommend(dispute: dict, required_met: list[str], required_missing: list[str],
              supported_claims: int, settle_max: float, max_offer_pct: int,
              risk: str = "low") -> dict:
    cents, currency = dispute_amount(dispute)
    actions = allowed_actions(dispute)
    strong = supported_claims >= 2 and not required_missing

    if strong:
        rule, decision = "Enough verified claims and nothing the bank expects is missing", "fight"
        why = "Strong case: the key evidence the bank asks for is present and verified."
        offer = None
    elif risk == "high" and required_met:
        rule, decision = "Serial disputer, some evidence: never offer a settlement", "fight"
        why = "Some evidence is missing, but this buyer disputes most orders, so don't pay them off. Answer with what you have."
        offer = None
    elif risk == "high":
        rule, decision = "Serial disputer, little evidence: never offer a settlement", "review"
        why = "Little supporting evidence, and this buyer disputes most orders. Review manually; no settlement suggested."
        offer = None
    elif cents <= to_cents(f"{settle_max:.2f}") and "contest" in actions:
        rule, decision = "Weak case, within your concession limit, and Razorpay allows a partial contest", "settle"
        pct = max_offer_pct if not required_met else max(max_offer_pct // 2, 10)
        offer = cents * pct // 100
        why = (f"Weak or incomplete case for a small amount ({rupees_text(cents, currency)}). Concede {pct}% and contest the rest: "
               "losing part likely costs less than losing all of it.")
    elif not required_met:
        rule, decision = "Nothing the bank expects was found", "review"
        why = "Little supporting evidence. Review manually before deciding; do not auto-accept."
        offer = None
    else:
        rule, decision = "Some evidence found, but too large to settle or settling not allowed", "fight"
        why = "Some key evidence is missing, but the case is still worth answering."
        offer = None

    return {
        "decision": decision,
        "why": why,
        "offer_amount": from_cents(offer) if offer is not None else None,
        "currency": currency,
        "disputed_amount": from_cents(cents),
        "required_met": required_met,
        "required_missing": required_missing,
        "receipt": {"rule": rule, "verified_claims": supported_claims, "expected_found": len(required_met),
                    "expected_missing": len(required_missing), "disputed": from_cents(cents), "currency": currency,
                    "settle_limit": f"{settle_max:.2f}", "max_offer_pct": max_offer_pct, "buyer_risk": risk,
                    "partial_allowed": "contest" in actions},
    }


# ---------------------------------------------------------------------------
# Contest checklist: what a bank looks for on this kind of chargeback, run before the seller spends effort.
# The issuing bank decides, so this never says "you will win".
# ---------------------------------------------------------------------------

NOT_RECEIVED, NOT_AS_DESCRIBED, UNAUTHORISED = ("MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                                                "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", "UNAUTHORISED")
CREDIT_NOT_PROCESSED = "CREDIT_NOT_PROCESSED"


def contest_checklist(dispute: dict, evidence: list[dict]) -> dict:
    reason, phase = dispute.get("reason"), dispute.get("phase")
    checks: list[dict] = []

    def check(label, ok, why):
        checks.append({"label": label, "state": "unknown" if ok is None else "pass" if ok else "fail", "why": why})

    known = reason in (NOT_RECEIVED, NOT_AS_DESCRIBED, UNAUTHORISED, CREDIT_NOT_PROCESSED)
    check("Reason code is understood", known or None,
          f"Mapped {dispute.get('reason_code')} to a known complaint." if known
          else f"Reason code {dispute.get('reason_code')} isn't in our table. Read Razorpay's reason code guide.")
    check("Still in a phase you can answer", phase != "arbitration",
          "Arbitration goes to the card network; Razorpay's support team handles it." if phase == "arbitration"
          else f"Phase: {phase or 'unknown'}.")
    if reason in (NOT_RECEIVED, UNAUTHORISED):
        track = [e["content"] for e in evidence if e["kind"] == "tracking"]
        delivered = [c for c in track if c.get("carrier_status") == "DELIVERED"]
        check("Courier shows delivered", bool(delivered),
              "From a courier record you added. The bank will judge it." if delivered and all(c.get("seller_added") for c in delivered)
              else "Banks expect the courier's proof of delivery (POD) with a delivered status.")
        check("Delivered to the order's address", any(e["kind"] == "address_check" and e["content"].get("match") for e in evidence),
              "The delivery address must match the shipping address on the order, including the PIN code.")
    if reason == NOT_AS_DESCRIBED:
        check("Listing and packing photo on file", any(e["kind"] == "listing" for e in evidence) and any(e["kind"] == "photo" for e in evidence),
              "Banks compare the listing with what was sent. A packing photo shows what left your shop.")
    states = {c["state"] for c in checks}
    verdict = "excluded" if phase == "arbitration" else "missing_proof" if "fail" in states else "check_terms" if "unknown" in states else "may_qualify"
    summary = {"excluded": "This phase is past what DisputeDesk handles. Talk to Razorpay support.",
               "missing_proof": "Proof the bank expects is missing. Close the failed check first.",
               "check_terms": "Not enough information to tell. Check Razorpay's reason code guide.",
               "may_qualify": "This case has what banks usually ask for. The bank still decides."}[verdict]
    return {"verdict": verdict, "summary": summary, "checks": checks}


# ---------------------------------------------------------------------------
# What each choice costs. Razorpay's dispute fees depend on the merchant's plan and aren't one published number,
# so only the disputed money is counted. No win rate is assumed: we show the win chance at which contesting beats conceding.
# ---------------------------------------------------------------------------

def money_outcomes(dispute: dict, checklist_verdict: str, offer_cents: int | None) -> dict:
    cents, currency = dispute_amount(dispute)
    lose, win = cents, 0
    out = {"win": from_cents(win), "lose": from_cents(lose), "accept": from_cents(cents), "currency": currency,
           "basis": "Disputed amount only. Any dispute fee on your Razorpay plan is extra.", "offer": None, "breakeven_pct": None}
    if offer_cents is not None and lose > win:
        out["offer"] = from_cents(offer_cents)
        # Contesting everything costs p*win + (1-p)*lose. Below this win chance, conceding part is cheaper.
        out["breakeven_pct"] = max(0, min(100, round((lose - offer_cents) * 100 / (lose - win))))
    return out


# ---------------------------------------------------------------------------
# Closing the gap: what's missing, how to get it, and which proof the seller can add. File types are checked
# from the file's own first bytes, because a file name can say anything.
# ---------------------------------------------------------------------------

PROOF_HELP = {
    "Tracking shows the item was delivered": ("carrier_record", "Download the proof of delivery (POD) from the courier or your shipping app. It should show delivered, the date and the address."),
    "Delivered to the address on the order": ("carrier_record", "The address on the POD must match the shipping address on the order, including the PIN code."),
    "Photo of the item before shipping": ("photo", "Add a photo of the item, taken before you shipped it."),
    "Original listing description": ("other", "Save a copy of your listing as a PDF or screenshot, and add it here."),
}
FILE_SIGNATURES = ((b"%PDF-", "application/pdf"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF8", "image/gif"))


def proof_gaps(missing: list[str]) -> list[dict]:
    return [{"label": m, "kind": PROOF_HELP[m][0], "how": PROOF_HELP[m][1]} for m in missing if m in PROOF_HELP]


def sniff_file_type(content: bytes) -> str | None:
    return next((mime for magic, mime in FILE_SIGNATURES if content.startswith(magic)), None)


# ---------------------------------------------------------------------------
# Evidence pack integrity: a fingerprint per item, and dates that cannot be true
# ---------------------------------------------------------------------------

def evidence_manifest(evidence: list[dict]) -> dict:
    rows = []
    for e in evidence:
        body = json.dumps({"id": e["id"], "source": e["source"], "summary": e["summary"], "content": e["content"]},
                          sort_keys=True, default=str)
        rows.append({"id": e["id"], "source": e["source"], "sha256": hashlib.sha256(body.encode()).hexdigest()})
    bundle = hashlib.sha256("".join(r["sha256"] for r in rows).encode()).hexdigest()
    return {"items": rows, "bundle": bundle}


def impossible_dates(evidence: list[dict], now: datetime | None = None) -> list[str]:
    """Problems a reviewer would spot at once, so the seller sees them first."""
    today, problems = (now or datetime.now(timezone.utc)).date().isoformat(), []
    for e in evidence:
        c = e["content"]
        if e["kind"] != "tracking":
            continue
        shipped, delivered = c.get("shipped_on"), c.get("delivered_on")
        if shipped and delivered and delivered < shipped:
            problems.append(f"{e['id']}: delivered on {delivered}, before it shipped on {shipped}.")
        for label, value in (("shipped", shipped), ("delivered", delivered)):
            if value and value > today:
                problems.append(f"{e['id']}: {label} date {value} is in the future.")
    return problems


# ---------------------------------------------------------------------------
# Outcome: the bank's final decision, for the won/lost record
# ---------------------------------------------------------------------------

def dispute_outcome(dispute: dict) -> str | None:
    status = dispute.get("status")
    return status if status in CLOSED_STATUSES else None


def next_step(dispute: dict, analysis: dict | None) -> dict:
    """One plain instruction per case, so a seller always knows what to do next."""
    outcome = dispute_outcome(dispute)
    if outcome:
        label = {"won": "Closed: you won", "lost": "Closed: buyer won"}.get(outcome, "Closed")
        return {"state": "done", "label": label}
    if seller_submissions(dispute):
        return {"state": "waiting", "label": "Filed. Waiting for the bank"}
    if not analysis:
        return {"state": "needs", "label": "Run the AI team"}
    if allowed_actions(dispute):
        return {"state": "needs", "label": "Review and approve"}
    if dispute.get("status") == "under_review":
        return {"state": "waiting", "label": "Under review at the bank"}
    return {"state": "waiting", "label": "Case ready. Past the respond-by date"}


def build_supporting_notes(reply_text: str, evidence: list[dict], limit: int = 6000) -> str:
    """Reply + a legend, so the bank's reviewer can tell what [E3] means. Customer-written items are left
    out unless the reply cites them, and anything flagged as AI manipulation is never included."""
    lines = [reply_text.strip(), "", "Evidence on file:"]
    for e in evidence:
        cited = f"{e['id']}]" in reply_text or f"{e['id']}," in reply_text
        if (e["trusted"] or cited) and not e["content"].get("injection_suspected"):
            by_buyer = "" if e["trusted"] or e.get("kind") == CALL_KIND else " (written by the customer)"
            lines.append(f"[{e['id']}] {e['source']}{by_buyer}: {e['summary']}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit - 1] + "…"


# Which Razorpay contest field each kind of evidence belongs in. Kinds not listed only appear in the explanation letter.
CONTEST_FIELD_FOR = {"tracking": "shipping_proof", "address_check": "shipping_proof", "chat": "customer_communication",
                     CALL_KIND: "customer_communication", "listing": "term_and_conditions", "buyer_history": "access_activity_log"}
DOC_FIELD_FOR = {"carrier_record": "shipping_proof"}   # seller-added photos and other files go under "others"


def contest_plan(dispute: dict, evidence: list[dict], reply_text: str) -> dict:
    """What to file, decided by code: one PDF per Razorpay field, built only from items the reply may show.
    The explanation letter (the verified reply + legend) is always there, because Razorpay needs at least one document."""
    groups: dict[str, list[str]] = {}
    for e in evidence:
        field = CONTEST_FIELD_FOR.get(e["kind"])
        if not field or e["content"].get("injection_suspected"):
            continue
        if not e["trusted"] and f"{e['id']}]" not in reply_text and f"{e['id']}," not in reply_text:
            continue
        groups.setdefault(field, []).append(f"[{e['id']}] {e['source']}: {e['summary']}")
    files = [{"field": "explanation_letter", "filename": f"{dispute['dispute_id']}-explanation.pdf",
              "title": f"Response to dispute {dispute['dispute_id']}", "body": build_supporting_notes(reply_text, evidence)}]
    for field, lines in groups.items():
        files.append({"field": field, "filename": f"{dispute['dispute_id']}-{field}.pdf",
                      "title": f"{field.replace('_', ' ').capitalize()} for dispute {dispute['dispute_id']}", "body": "\n\n".join(lines)})
    summary = re.sub(r"\s+", " ", reply_text).strip()
    return {"summary": summary if len(summary) <= 1000 else summary[:999] + "…", "files": files}
