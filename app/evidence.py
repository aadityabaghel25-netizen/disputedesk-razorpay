"""
Evidence gathering: plain code that collects every fact we can prove.

Each fact becomes an "evidence item" with a short id (E1, E2, ...). The AI may
only make claims that point to these ids. That's how we stop it inventing things.

trusted=False marks text written by the buyer: we show it to the AI as data,
never as instructions.
"""

import copy
import json
import re
from datetime import date, datetime, timedelta, timezone

from . import core


class Store:
    """The seller's own shop data (orders, shipping, chats). Mocked from data/store.json."""

    def __init__(self, path: str):
        with open(path, encoding="utf-8") as f:
            self.data = json.load(f)

    def order(self, invoice_id: str | None) -> dict | None:
        return self.data["orders"].get(invoice_id or "")

    def buyer_history(self, email: str) -> dict:
        return self.data.get("buyer_history", {}).get(email, {"previous_orders": 0, "previous_disputes": 0})


DATE_TOKEN = re.compile(r"\{paid\+(-?\d+)\}")


def resolve_dates(obj, paid: date, today: date | None = None):
    """Shop data stores dates as {paid+N}. Turn them into real dates relative to the
    payment date, never later than today (a parcel can't be delivered tomorrow)."""
    today = today or datetime.now(timezone.utc).date()

    def fix(text: str) -> str:
        return DATE_TOKEN.sub(lambda m: min(paid + timedelta(days=int(m.group(1))), today).isoformat(), text)

    if isinstance(obj, dict):
        return {k: resolve_dates(v, paid, today) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_dates(v, paid, today) for v in obj]
    return fix(obj) if isinstance(obj, str) else obj


def _norm_address(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def order_address_text(order: dict | None) -> str | None:
    """The shipping address the customer gave at checkout. Razorpay has no address field, so shops put it in
    the order's notes; the adapter copies it onto the case."""
    return (order or {}).get("shipping_address") or None


def addresses_match(order_addr: str | None, delivered_to: str | None) -> bool:
    """Same first address line and same PIN code -> match. Simple and explainable."""
    if not order_addr or not delivered_to:
        return False
    street = _norm_address(order_addr.split(",")[0])
    pins = re.findall(r"\b\d{6}\b", order_addr) or re.findall(r"\b\d{5}\b", order_addr)
    d = _norm_address(delivered_to)
    return bool(street) and street in d and all(p in delivered_to for p in pins)


def link_to_order(gateway, dispute: dict) -> tuple[str | None, dict | None, list[str], date | None]:
    """Find the shop order id, the payment record and the payment date behind a dispute. The adapter already read
    the payment and order from Razorpay, so this only says what's missing."""
    notes = []
    tx = (dispute.get("disputed_transactions") or [{}])[0]
    invoice = tx.get("invoice_number")
    paid = core.parse_time(tx.get("create_time"))
    if not tx.get("order_id"):
        notes.append("Could not read the Razorpay payment or order behind this dispute.")
    return invoice, (tx if tx.get("payment_id") else None), notes, paid.date() if paid else None


INJECTION_WARNING = ("A customer message contains text that looks like instructions to an AI. "
                     "It was treated as plain data and ignored as instructions.")


def gather(gateway, store: Store, dispute: dict) -> tuple[list[dict], dict, list[str]]:
    """Returns (evidence_items, context, warnings)."""
    items: list[dict] = []
    warnings: list[str] = []

    def add(kind, source, trusted, summary, content):
        items.append({"id": f"E{len(items) + 1}", "kind": kind, "source": source,
                      "trusted": trusted, "summary": summary, "content": content})

    amount_cents, currency = core.dispute_amount(dispute)
    add("dispute", "Razorpay dispute", True,
        f"Dispute {dispute['dispute_id']}: reason {dispute.get('reason')} (code {dispute.get('reason_code')}), phase "
        f"{dispute.get('phase')}, amount {core.from_cents(amount_cents)} {currency}, status {dispute.get('status')}.",
        {"dispute_id": dispute["dispute_id"], "reason": dispute.get("reason"), "reason_code": dispute.get("reason_code"),
         "amount": core.from_cents(amount_cents), "currency": currency,
         "created": dispute.get("create_time"), "due": dispute.get("seller_response_due_date")})

    def flag(text: str) -> bool:
        flagged = bool(core.injection_flags(text))
        if flagged and INJECTION_WARNING not in warnings:
            warnings.append(INJECTION_WARNING)
        return flagged

    for m in core.dispute_statements(dispute):
        text = m.get("content", "")
        by = m.get("posted_by", "UNKNOWN")
        flags = flag(text) if by == "BUYER" else False
        add("dispute_message", f"Dispute message ({by.lower()})", by != "BUYER",
            f"{by.title()} wrote in the dispute on {(m.get('time_posted') or '')[:10]}.",
            {"posted_by": by, "date": (m.get("time_posted") or "")[:10], "text": text,
             "injection_suspected": flags})

    invoice, order, notes, paid = link_to_order(gateway, dispute)
    warnings += notes
    shop = store.order(invoice)
    if shop:
        if paid is None:
            paid = (core.parse_time(dispute.get("create_time")) or datetime.now(timezone.utc)).date()
            warnings.append("Payment date not found; shop dates are estimated from the dispute date.")
        shop = resolve_dates(copy.deepcopy(shop), paid)

    order_addr = order_address_text(order)
    if order:
        add("payment", "Razorpay payment", True,
            f"Razorpay payment {order.get('payment_id')} by {order.get('method') or 'unknown method'} for "
            f"{order['gross_amount']['value']} {order['gross_amount']['currency_code']} on {(order.get('create_time') or '')[:10]}, "
            f"order {order.get('order_id')} with receipt {invoice}, shipping address on the order: {order_addr or 'not recorded'}.",
            {"payment_id": order.get("payment_id"), "order_id": order.get("order_id"), "receipt": invoice, "method": order.get("method"),
             "amount": order["gross_amount"]["value"], "paid_on": (order.get("create_time") or "")[:10], "shipping_address": order_addr})
    else:
        warnings.append("No Razorpay payment found for this dispute.")

    if not shop:
        warnings.append(f"No shop order found for invoice '{invoice}'. Evidence will be limited.")
        return items, {"invoice": invoice, "order": order, "shop": None}, warnings

    s = shop["shipment"]
    if s.get("tracking_number"):
        add("tracking", f"Courier record ({s['carrier']}, simulated)", True,
            f"{s['carrier']} AWB {s['tracking_number']}: status {s['carrier_status']}, shipped "
            f"{s.get('shipped_on')}, delivered {s.get('delivered_on') or 'n/a'} to {s.get('delivered_to') or 'n/a'}"
            f"{', signed by ' + s['signature'] if s.get('signature') else ''}.",
            {k: s.get(k) for k in ("carrier", "tracking_number", "carrier_status", "shipped_on",
                                   "delivered_on", "delivered_to", "signature")})
        if s.get("carrier_status") == "DELIVERED":
            match = addresses_match(order_addr, s.get("delivered_to"))
            add("address_check", "DisputeDesk check (code)", True,
                ("Delivery address matches" if match else "Delivery address does NOT match")
                + " the shipping address on the order (street and PIN code).",
                {"order_address": order_addr, "delivered_to": s.get("delivered_to"), "match": match})
    else:
        add("shipping", "Shop records", True,
            f"Shipped on {s.get('shipped_on')} via {s.get('carrier')} without an AWB tracking number.",
            {"shipped_on": s.get("shipped_on"), "carrier": s.get("carrier"), "tracking_number": None})

    lst = shop.get("listing") or {}
    add("listing", "Shop listing", True, f"Listing '{lst.get('title')}': {lst.get('description')}",
        {"title": lst.get("title"), "description": lst.get("description")})

    for p in shop.get("packing_photos") or []:
        add("photo", "Shop packing photo", True, f"Packing photo: {p}", {"description": p})

    for c in shop.get("chat") or []:
        add("chat", f"Shop chat ({c['from']})", c["from"] != "buyer",
            f"{c['from'].title()} message on {c['at']}.",
            {"from": c["from"], "date": c["at"], "text": c["text"],
             "injection_suspected": c["from"] == "buyer" and flag(c["text"])})

    ctx = {"invoice": invoice, "order": order, "shop": shop, "buyer_email": shop["buyer"]["email"]}
    return items, ctx, warnings


def add_documents(items: list[dict], documents: list[dict], order: dict | None) -> None:
    """Proof the seller added. The dates and address they typed go through the same checks as shop records. The AI
    never reads the file itself, and every summary says who supplied it, so nobody mistakes it for a live carrier check."""

    def add(kind, source, summary, content):
        items.append({"id": f"E{len(items) + 1}", "kind": kind, "source": source, "trusted": True, "summary": summary,
                      "content": {**content, "seller_added": True}})

    order_addr = order_address_text(order)
    for doc in documents:
        f, name, sha = doc["fields"], doc["filename"], doc["sha256"]
        if doc["doc_type"] == "carrier_record":
            add("tracking", f"Courier record added by the seller ({name})",
                f"Seller added a {f['carrier']} record for AWB {f['tracking_number']}: shipped {f['shipped_on']}, delivered {f['delivered_on']} "
                f"to {f['delivered_to']}. File fingerprint {sha[:12]}. DisputeDesk didn't check this with the courier.",
                {"carrier": f["carrier"], "tracking_number": f["tracking_number"], "carrier_status": "DELIVERED", "shipped_on": f["shipped_on"],
                 "delivered_on": f["delivered_on"], "delivered_to": f["delivered_to"], "signature": None, "sha256": sha})
            if order_addr:
                match = addresses_match(order_addr, f["delivered_to"])
                add("address_check", "DisputeDesk check (code)",
                    ("Delivery address on the seller's record matches" if match else "Delivery address on the seller's record does NOT match")
                    + " the shipping address on the order (street and PIN code).",
                    {"order_address": order_addr, "delivered_to": f["delivered_to"], "match": match})
        elif doc["doc_type"] == "photo":
            add("photo", f"Photo added by the seller ({name})", f"Seller added a photo of the item. File fingerprint {sha[:12]}.", {"description": name, "sha256": sha})
        else:
            add("seller_document", f"Document added by the seller ({name})", f"Seller added this document. File fingerprint {sha[:12]}.", {"filename": name, "sha256": sha})


CALL_TOPICS = {"receipt": "whether the order arrived", "delivery": "how the order was delivered",
               "item_condition": "the condition of the item", "purchase": "whether they made the purchase", "other": "the order"}


def add_call_finding(items: list[dict], call: dict, turn: dict, quote: str, topic: str, stance: str) -> str:
    """One quote from one recording becomes one evidence item. The summary is written by code, and no requirement
    below ever accepts this kind, so a call can never stand in for a carrier record."""
    evidence_id = f"E{len(items) + 1}"
    items.append({"id": evidence_id, "kind": core.CALL_KIND, "trusted": False,
                  "source": f"Call with the {call['party']} ({call['origin']})",
                  "summary": f"On a call with the {call['party']} on {call['call_date']}, the {turn['speaker']} spoke about "
                             f"{CALL_TOPICS[topic]}. Transcript reviewed by the seller, who confirmed everyone agreed to recording. "
                             "Spoken words, not courier or payment records.",
                  "content": {"call_date": call["call_date"], "party": call["party"], "turns": [turn], "text": quote,
                              "topic": topic, "stance": stance, "injection_suspected": False,
                              "audio_sha256": call.get("audio_sha256")}})
    return evidence_id


# ---------------------------------------------------------------------------
# What a bank expects for each reason, checked by code against our evidence
# ---------------------------------------------------------------------------

def _has(items, kind, pred=lambda c: True):
    return [i["id"] for i in items if i["kind"] == kind and pred(i["content"])]


REQUIREMENTS = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": [
        ("Tracking shows the item was delivered",
         lambda it: _has(it, "tracking", lambda c: c.get("carrier_status") == "DELIVERED")),
        ("Delivered to the address on the order",
         lambda it: _has(it, "address_check", lambda c: c.get("match"))),
    ],
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": [
        ("Original listing description", lambda it: _has(it, "listing")),
        ("Photo of the item before shipping", lambda it: _has(it, "photo")),
    ],
    "CREDIT_NOT_PROCESSED": [
        ("Refund or cancellation record", lambda it: _has(it, "refund")),
    ],
    "UNAUTHORISED": [
        ("Tracking shows the item was delivered",
         lambda it: _has(it, "tracking", lambda c: c.get("carrier_status") == "DELIVERED")),
        ("Delivered to the address on the order",
         lambda it: _has(it, "address_check", lambda c: c.get("match"))),
    ],
}


def check_requirements(reason: str, items: list[dict]) -> tuple[list[str], list[str]]:
    met, missing = [], []
    for label, fn in REQUIREMENTS.get(reason, []):
        ids = fn(items)
        (met if ids else missing).append(f"{label} ({', '.join(ids)})" if ids else label)
    return met, missing
