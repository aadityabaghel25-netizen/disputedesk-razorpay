"""
Setup helpers for Razorpay test mode and Gnani.

  python3 scripts/setup.py auth
      Checks that Razorpay accepts the keys in .env (reads one payment, changes nothing).

  python3 scripts/setup.py order DD-1001
      Creates a test-mode payment link for shop order DD-1001 (data/store.json). The shop order id and the
      delivery address go in the notes, which is how a dispute finds its way back to the shop order.
      Pay it with a Razorpay test card or test UPI id.

  python3 scripts/setup.py dispute disp_XXXXXXXX
      Prints a dispute exactly as the app sees it (after the adapter), to check a real one.

  python3 scripts/setup.py simulate http://127.0.0.1:8000 [disp_DemoTote1001]
      Sends a payment.dispute.created webhook, signed with RAZORPAY_WEBHOOK_SECRET, to the app.
      Razorpay's docs don't say how to raise a dispute in test mode, so this is how the webhook path is
      tested end to end: real HMAC signature, real parsing, real re-read through the app's gateway.

  python3 scripts/setup.py gnani
      Checks that Gnani accepts the key in .env. Costs nothing: it only lists your jobs.

  python3 scripts/setup.py voice "Namaste, yeh ek test hai."
      Makes a Hinglish voice clip with Gnani and saves it as voice-test.mp3 (uses a little Gnani credit).
"""

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.demo import DemoRazorpay  # noqa: E402
from app.llm import GNANI_JOBS, LLMError, _gnani_json, gnani_speak  # noqa: E402
from app.razorpay import Razorpay, RazorpayError  # noqa: E402


def check_gnani(api_key: str) -> int:
    if not api_key:
        print("[FAILED] GNANI_API_KEY is empty in .env.")
        return 1
    try:
        _gnani_json(GNANI_JOBS, api_key)
    except (LLMError, OSError) as e:
        print(f"[FAILED] {e}")
        return 1
    print("[OK] Gnani accepts this key.")
    return 0


def simulate(base_url: str, dispute_id: str, secret: str) -> int:
    """A signed webhook built from a demo dispute, posted to the app like Razorpay would."""
    if not secret:
        print("[FAILED] Set RAZORPAY_WEBHOOK_SECRET in .env (any long random text when testing locally).")
        return 1
    demo = DemoRazorpay()
    raw = demo._raw(dispute_id)
    event = {"entity": "event", "account_id": "acc_test", "event": "payment.dispute.created", "contains": ["payment", "dispute"],
             "payload": {"payment": {"entity": demo.get_payment(raw["payment_id"])}, "dispute": {"entity": raw}}, "created_at": int(time.time())}
    body = json.dumps(event).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/webhooks/razorpay", data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Razorpay-Event-Id": f"evt_sim_{int(time.time())}",
        "X-Razorpay-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()})
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"[OK] HTTP {resp.status}: {resp.read().decode()}")
    return 0


def main() -> int:
    s = get_settings()
    args = sys.argv[1:]
    if args[:1] == ["gnani"]:
        return check_gnani(s.gnani_api_key)
    if args[:1] == ["voice"] and len(args) > 1:
        out = os.path.join(PROJECT_ROOT, "voice-test.mp3")
        with open(out, "wb") as f:
            f.write(gnani_speak(s.gnani_api_key, args[1], s.gnani_tts_model, s.gnani_tts_voice, s.gnani_tts_language))
        print(f"[OK] Saved {out}")
        return 0
    if args[:1] == ["simulate"] and len(args) > 1:
        return simulate(args[1], args[2] if len(args) > 2 else "disp_DemoTote1001", s.razorpay_webhook_secret)
    rp = Razorpay(s.razorpay_key_id, s.razorpay_key_secret)
    if not rp.configured:
        print("Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env first (Dashboard -> Test Mode -> Account & Settings -> API Keys).")
        return 1
    if not rp.test_mode:
        print("[STOP] These are LIVE keys. DisputeDesk's setup helpers only run with test keys (rzp_test_...).")
        return 1
    with open(s.store_path, encoding="utf-8") as f:
        store = json.load(f)["orders"]
    try:
        if args[:1] == ["auth"]:
            rp.request("GET", "/v1/payments", query={"count": 1})
            print("[OK] Razorpay accepts these test keys.")
        elif args[:1] == ["order"]:
            shop = store[args[1]]
            paise = int(round(float(shop["price"]) * 100))
            address = (shop["shipment"].get("delivered_to") or "")
            link = rp.create_payment_link(paise, args[1], shop["item"], {"shop_order": args[1], "shipping_address": address})
            print(f"[OK] Test payment link for {shop['item']} (₹{shop['price']}):\n  {link['short_url']}\n"
                  "Pay it with a Razorpay test card or test UPI id. The payment's notes link it to the shop order.")
        elif args[:1] == ["dispute"]:
            print(json.dumps({k: v for k, v in rp.get_dispute(args[1]).items() if k != "raw"}, indent=1, ensure_ascii=False))
        else:
            print(__doc__)
    except RazorpayError as e:
        print(f"[FAILED] {e.summary()}")
        return 1
    except (IndexError, KeyError) as e:
        print(f"[FAILED] Missing or unknown argument ({e}).\n{__doc__}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
