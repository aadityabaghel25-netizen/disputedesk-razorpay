"""
Razorpay REST client used by the app (standard library only), plus the adapter that turns a
Razorpay dispute into the case the rest of the app understands.

DevOps analogy: this file is a Terraform provider. The core logic never sees Razorpay's JSON;
it sees one normalised "case", so the rules, agents and UI don't care which gateway it came from.

Read-only calls are safe for AI agents to use as tools.
Calls that change something (marked WRITE) are only ever triggered by a human
clicking Approve in the dashboard - never by the AI.
"""

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

BASE_URL = "https://api.razorpay.com"


class RazorpayError(Exception):
    def __init__(self, status: int, body):
        self.status, self.body = status, body
        super().__init__(f"Razorpay HTTP {status}: {body}")

    def summary(self) -> str:
        """Plain text for the seller. Razorpay puts the reason in error.description."""
        err = self.body.get("error") if isinstance(self.body, dict) else None
        if not isinstance(err, dict):
            return f"HTTP {self.status}: {str(self.body)[:300]}"
        bits = [err.get("code"), err.get("description"), err.get("field") and f"field {err['field']}"]
        return f"HTTP {self.status} " + ": ".join(b for b in bits if b)


def build_multipart(fields: dict, files: list | None = None) -> tuple[bytes, str]:
    """multipart/form-data body (Razorpay's Documents API requires this format)."""
    boundary = "----DisputeDesk" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts += [f"--{boundary}".encode(), f'Content-Disposition: form-data; name="{name}"'.encode(), b"", str(value).encode()]
    for field_name, filename, content, mime in files or []:
        parts += [f"--{boundary}".encode(),
                  f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode(),
                  f"Content-Type: {mime}".encode(), b"", content]
    parts += [f"--{boundary}--".encode(), b""]
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"


def verify_webhook_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """HMAC-SHA256 of the raw body with the webhook secret. Razorpay signs the exact bytes it sent,
    so this must run before the body is parsed. compare_digest stops timing attacks."""
    if not (signature and secret):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


# ---------------------------------------------------------------------------
# Reason codes -> one of our categories. Razorpay passes the card network's or NPCI's code,
# so the same complaint has many codes. Unknown codes fall back to OTHER and the seller sees the raw code.
# ---------------------------------------------------------------------------

NOT_RECEIVED, NOT_AS_DESCRIBED, UNAUTHORISED = "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", "UNAUTHORISED"
CREDIT_NOT_PROCESSED, DUPLICATE, OTHER = "CREDIT_NOT_PROCESSED", "DUPLICATE_TRANSACTION", "OTHER"

REASON_CODES = {
    # Razorpay's own text codes, seen in its API samples
    "goods_or_services_not_received_or_partially_received": NOT_RECEIVED,
    "goods_not_received": NOT_RECEIVED, "not_as_described": NOT_AS_DESCRIBED,
    "fraud": UNAUTHORISED, "fraudulent_transaction": UNAUTHORISED, "unauthorised": UNAUTHORISED,
    "credit_not_processed": CREDIT_NOT_PROCESSED, "duplicate_processing": DUPLICATE,
    # UPI (NPCI)
    "1064": NOT_RECEIVED, "1062": NOT_AS_DESCRIBED, "128": UNAUTHORISED, "1061": CREDIT_NOT_PROCESSED,
    # Mastercard
    "4855": NOT_RECEIVED, "4853": NOT_AS_DESCRIBED, "4837": UNAUTHORISED, "4840": UNAUTHORISED,
    "4849": UNAUTHORISED, "4870": UNAUTHORISED, "4871": UNAUTHORISED, "4860": CREDIT_NOT_PROCESSED, "4834": DUPLICATE,
    # Visa (category.code)
    "13.1": NOT_RECEIVED, "13.3": NOT_AS_DESCRIBED, "13.6": CREDIT_NOT_PROCESSED, "10.4": UNAUTHORISED, "12.6": DUPLICATE,
    # Amex
    "C08": NOT_RECEIVED, "C31": NOT_AS_DESCRIBED, "C32": NOT_AS_DESCRIBED, "C02": CREDIT_NOT_PROCESSED,
    "F24": UNAUTHORISED, "F29": UNAUTHORISED, "P08": DUPLICATE,
}


def reason_category(reason_code: str | None) -> str:
    code = (reason_code or "").strip()
    return REASON_CODES.get(code) or REASON_CODES.get(code.lower()) or OTHER


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def rupees(paise: int) -> str:
    return f"{int(paise) // 100}.{int(paise) % 100:02d}"


def normalize(dispute: dict, payment: dict | None = None, order: dict | None = None) -> dict:
    """One Razorpay dispute (+ its payment and order) -> the app's case.

    Kept names: dispute_id, reason, status, create_time, seller_response_due_date, dispute_amount,
    disputed_transactions. `raw` keeps Razorpay's own object for the audit trail."""
    payment, order = payment or {}, order or {}
    notes = {**(order.get("notes") or {}), **(payment.get("notes") or {})}
    receipt = order.get("receipt") or notes.get("receipt") or notes.get("shop_order")
    currency = dispute.get("currency") or "INR"
    evidence = dispute.get("evidence") or {}
    return {
        "dispute_id": dispute["id"],
        "gateway": "razorpay",
        "reason": reason_category(dispute.get("reason_code")),
        "reason_code": dispute.get("reason_code"),
        "reason_description": dispute.get("reason_description"),
        "phase": dispute.get("phase"),
        "status": dispute.get("status"),
        "create_time": _iso(dispute.get("created_at")),
        "seller_response_due_date": _iso(dispute.get("respond_by")),
        "dispute_amount": {"value": rupees(dispute.get("amount", 0)), "currency_code": currency},
        "amount_deducted": rupees(dispute.get("amount_deducted") or 0),
        "evidence": evidence,
        "disputed_transactions": [{
            "payment_id": dispute.get("payment_id"),
            "order_id": payment.get("order_id") or order.get("id"),
            "invoice_number": receipt,
            "method": payment.get("method"),
            "create_time": _iso(payment.get("created_at")),
            "gross_amount": {"value": rupees(payment.get("amount", dispute.get("amount", 0))), "currency_code": currency},
            "shipping_address": notes.get("shipping_address"),
            "customer_contact": bool(payment.get("contact")),   # never the number itself
        }],
        "messages": [],   # Razorpay disputes carry no customer messages. Customer words come from the shop's chats and calls.
        "raw": dispute,
    }


# The contest form's evidence lists. Each takes Razorpay document ids.
CONTEST_FIELDS = ("shipping_proof", "billing_proof", "cancellation_proof", "customer_communication", "proof_of_service",
                  "explanation_letter", "refund_confirmation", "access_activity_log", "refund_cancellation_policy", "term_and_conditions")


class Razorpay:
    def __init__(self, key_id: str, key_secret: str, base_url: str = BASE_URL):
        self.key_id, self.key_secret, self.base_url = key_id, key_secret, base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    @property
    def test_mode(self) -> bool:
        return self.key_id.startswith("rzp_test_")

    # -- plumbing -------------------------------------------------------------

    @staticmethod
    def _send(req: urllib.request.Request):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode()
                return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode()
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = text
            raise RazorpayError(e.code, body) from None

    def request(self, method: str, path: str, *, json_body=None, raw_body: bytes | None = None,
                content_type: str | None = None, query: dict | None = None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
        creds = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}
        data = None
        if json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif raw_body is not None:
            data, headers["Content-Type"] = raw_body, content_type or "application/octet-stream"
        return self._send(urllib.request.Request(url, data=data, headers=headers, method=method))

    # -- READ -----------------------------------------------------------------

    def get_payment(self, payment_id: str) -> dict:
        return self.request("GET", f"/v1/payments/{payment_id}")

    def get_order(self, order_id: str) -> dict:
        return self.request("GET", f"/v1/orders/{order_id}")

    def get_raw_dispute(self, dispute_id: str) -> dict:
        return self.request("GET", f"/v1/disputes/{dispute_id}")

    def list_raw_disputes(self, count: int = 100) -> list[dict]:
        return self.request("GET", "/v1/disputes", query={"count": count}).get("items", [])

    def get_dispute(self, dispute_id: str) -> dict:
        """The normalised case: dispute + its payment + its order. A missing payment or order
        still gives a case; the Collector then says which records it couldn't read."""
        raw = self.get_raw_dispute(dispute_id)
        payment = order = None
        try:
            payment = self.get_payment(raw["payment_id"])
            if payment.get("order_id"):
                order = self.get_order(payment["order_id"])
        except RazorpayError:
            pass
        return normalize(raw, payment, order)

    def list_disputes(self) -> list[dict]:
        return [{"dispute_id": d["id"]} for d in self.list_raw_disputes()]

    # -- WRITE: disputes (human-approved only) --------------------------------

    def upload_document(self, filename: str, content: bytes, mime: str) -> str:
        body, ctype = build_multipart({"purpose": "dispute_evidence"}, [("file", filename, content, mime)])
        return self.request("POST", "/v1/documents", raw_body=body, content_type=ctype)["id"]

    def contest(self, dispute_id: str, amount_paise: int, summary: str, documents: dict[str, list[str]],
                others: list[dict] | None = None, submit: bool = True) -> dict:
        """documents maps a contest field (shipping_proof, ...) to Razorpay document ids.
        amount_paise below the dispute amount contests only part of it: the rest is conceded."""
        body = {"amount": amount_paise, "summary": summary[:1000], "action": "submit" if submit else "draft"}
        body.update({k: v for k, v in documents.items() if k in CONTEST_FIELDS and v})
        if others:
            body["others"] = others
        return self.request("PATCH", f"/v1/disputes/{dispute_id}/contest", json_body=body)

    def accept(self, dispute_id: str) -> dict:
        """Irreversible: the dispute is lost and the amount is deducted."""
        return self.request("POST", f"/v1/disputes/{dispute_id}/accept")

    # -- WRITE: test-mode setup helpers (scripts only, never the web app) ----------------

    def create_order(self, amount_paise: int, receipt: str, notes: dict) -> dict:
        return self.request("POST", "/v1/orders", json_body={"amount": amount_paise, "currency": "INR", "receipt": receipt, "notes": notes})

    def create_payment_link(self, amount_paise: int, receipt: str, description: str, notes: dict) -> dict:
        return self.request("POST", "/v1/payment_links", json_body={
            "amount": amount_paise, "currency": "INR", "reference_id": receipt, "description": description, "notes": notes})
