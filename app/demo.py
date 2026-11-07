"""
Offline demo mode: a stand-in for Razorpay that reads data/demo_disputes.json.

Same method names as app.razorpay.Razorpay, and the same adapter turns its JSON into cases,
so the rest of the app can't tell the difference. This lets you (and the judges) run DisputeDesk
with zero keys, and gives us a demo that can never fail on stage.
Write calls are recorded, NOT sent anywhere.
"""

import copy
import json
import os
import time
from datetime import datetime, timezone

from .config import PROJECT_ROOT
from .razorpay import RazorpayError, normalize

DEMO_FILE = os.path.join(PROJECT_ROOT, "data", "demo_disputes.json")
DAY = 86400


class DemoRazorpay:
    configured = True
    is_demo = True
    test_mode = True

    def __init__(self, path: str = DEMO_FILE):
        self._path = path
        self.reset()

    def reset(self) -> None:
        """Back to the sample cases as they start, with deadlines counted from today."""
        with open(self._path, encoding="utf-8") as f:
            data = json.load(f)
        self._disputes = {d["id"]: d for d in data["disputes"]}
        self._payments, self._orders = data["payments"], data["orders"]
        self._keep_dates_current()
        self.documents: dict[str, dict] = {}
        self.sent: list[dict] = []   # what WOULD have been sent to Razorpay

    def _keep_dates_current(self) -> None:
        """Sample cases must not read as overdue when someone opens the demo weeks later."""
        newest = max(d["created_at"] for d in self._disputes.values())
        days = (int(datetime.now(timezone.utc).timestamp()) - newest) // DAY - 2
        if days <= 0:
            return
        for group, keys in ((self._disputes.values(), ("created_at", "respond_by")), (self._payments.values(), ("created_at",)),
                            (self._orders.values(), ("created_at",))):
            for obj in group:
                for k in keys:
                    obj[k] += days * DAY

    def _raw(self, dispute_id: str) -> dict:
        if dispute_id not in self._disputes:
            raise RazorpayError(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "The id provided does not exist"}})
        return self._disputes[dispute_id]

    def list_disputes(self) -> list[dict]:
        return [{"dispute_id": d} for d in self._disputes]

    def get_dispute(self, dispute_id: str) -> dict:
        raw = copy.deepcopy(self._raw(dispute_id))
        payment = copy.deepcopy(self._payments.get(raw["payment_id"]))
        order = copy.deepcopy(self._orders.get((payment or {}).get("order_id")))
        return normalize(raw, payment, order)

    def get_payment(self, payment_id: str) -> dict:
        if payment_id not in self._payments:
            raise RazorpayError(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "The id provided does not exist"}})
        return copy.deepcopy(self._payments[payment_id])

    def upload_document(self, filename: str, content: bytes, mime: str) -> str:
        doc_id = f"doc_Demo{len(self.documents) + 1:04d}"
        self.documents[doc_id] = {"name": filename, "size": len(content), "mime_type": mime}
        self.sent.append({"action": "upload_document", "payload": {"id": doc_id, "name": filename, "size": len(content)}})
        return doc_id

    def contest(self, dispute_id: str, amount_paise: int, summary: str, documents: dict, others=None, submit: bool = True) -> dict:
        d = self._raw(dispute_id)
        if d["status"] != "open":
            raise RazorpayError(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": f"Action not allowed when dispute is in {d['status']} status."}})
        if amount_paise > d["amount"]:
            raise RazorpayError(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "contest amount cannot be greater than dispute amount."}})
        d["evidence"].update({"amount": amount_paise, "summary": summary[:1000], **documents, "others": others or None})
        if submit:
            d["status"], d["evidence"]["submitted_at"] = "under_review", int(time.time())
        self.sent.append({"action": "contest", "dispute_id": dispute_id, "payload": {"amount": amount_paise, "fields": sorted(documents)}})
        return copy.deepcopy(d)

    def accept(self, dispute_id: str) -> dict:
        d = self._raw(dispute_id)
        if d["status"] != "open":
            raise RazorpayError(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": f"Action not allowed when dispute is in {d['status']} status."}})
        d["status"], d["amount_deducted"] = "lost", d["amount"]
        self.sent.append({"action": "accept", "dispute_id": dispute_id})
        return copy.deepcopy(d)
