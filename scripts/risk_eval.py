"""
Track 02 evaluation: precision and recall of DisputeDesk's two detectors, on a tuning split and a held-out split.

    python3 scripts/risk_eval.py            # uses the AI model in .env (the AI judge and AI manipulation flag run)
    python3 scripts/risk_eval.py --fake     # offline: measures the code layer only (the fake judge approves everything)

Detector 1, the unproven-claim verifier: planted claims (evals/data/*.jsonl), labelled by construction, go through the
same verify_claims() the app uses. Positive class = unsupported claim. A true positive is a false claim struck before
it reaches the bank. A false positive is a true claim struck, which weakens the seller's case; its cost is measured
in ₹ by re-running the code's decision with and without the wrongly struck claims.

Detector 2, the manipulation detector: customer messages that try to steer the AI, and innocent controls. Code word
patterns, the AI triage flag, and either.

Writes docs/risk_eval.md (and docs/risk_eval.json with a real model).
"""

import copy
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]
os.environ.setdefault("DISPUTEDESK_NO_AUTOAPP", "1")

from app import core, evidence  # noqa: E402
from app.agents import triage_case, verify_claims  # noqa: E402
from app.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.llm import FakeLLM, OpenAICompatibleLLM  # noqa: E402
from app.razorpay import normalize  # noqa: E402
from evals.dataset import HINGLISH_ATTACKS, HINGLISH_CONTROLS  # noqa: E402
from injection_fixtures import ATTACKS, CONTROLS, HELD_OUT_ATTACKS, HELD_OUT_CONTROLS  # noqa: E402

DATA = os.path.join(ROOT, "evals", "data")
SETTLE_MAX, MAX_OFFER_PCT = 2000, 50   # the app's default seller rules


class MemoryStore(evidence.Store):
    def __init__(self, data):  # noqa: D107 - no file
        self.data = data


def load(split: str) -> list[dict]:
    with open(os.path.join(DATA, f"{split}.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_case(case: dict) -> tuple[dict, list[dict], dict]:
    """A Razorpay dispute through the real adapter, the shop record through the real Collector.
    Returns (dispute, evidence items, ref -> evidence id)."""
    paid = int(datetime.fromisoformat(case["paid"] + "T10:00:00+00:00").timestamp())
    raw = {"id": f"disp_{case['id'].replace('-', '')}", "entity": "dispute", "payment_id": f"pay_{case['id']}", "amount": case["amount_paise"],
           "currency": "INR", "reason_code": case["reason_code"], "status": "open", "phase": "chargeback",
           "created_at": paid + 20 * 86400, "respond_by": paid + 40 * 86400, "evidence": {"submitted_at": None}}
    payment = {"id": f"pay_{case['id']}", "order_id": f"order_{case['id']}", "method": case["method"], "amount": case["amount_paise"],
               "created_at": paid, "notes": {"shop_order": case["id"]}}
    order = {"id": f"order_{case['id']}", "receipt": case["id"], "notes": {"shipping_address": case["order_address"]}}
    dispute = normalize(raw, payment, order)
    shop = {"orders": {case["id"]: {"item": case["item"], "price": f"{case['amount_paise'] // 100}.00",
                                    "buyer": {"name": "Test Customer", "email": f"{case['id']}@example.com"},
                                    "listing": case["listing"], "shipment": case["shipment"],
                                    "packing_photos": ["Item photographed in its box on {paid+1}"] if case["photo"] else [],
                                    "chat": case["chats"]}},
            "buyer_history": {}}
    items, _, _ = evidence.gather(None, MemoryStore(shop), dispute)
    refs = {}
    for kind in ("payment", "listing", "photo", "tracking", "address_check"):
        hit = next((i["id"] for i in items if i["kind"] == kind), None)
        if hit:
            refs[kind] = hit
    for n, item in enumerate(i for i in items if i["kind"] == "chat"):
        refs[f"chat:{n}"] = item["id"]
    if case["call"]:
        call = {"party": case["call"]["party"], "call_date": case["call"]["date"], "origin": "Gnani speech-to-text, checked by the seller",
                "audio_sha256": None}
        turns = core.parse_call_turns(case["call"]["transcript"])
        positive = next(t for t in turns if case["call"]["quote"] in t["text"])
        negated = next(t for t in turns if case["call"]["negated_quote"] in t["text"])
        refs["call"] = evidence.add_call_finding(items, call, positive, case["call"]["quote"], "receipt", "helps")
        refs["call_neg"] = evidence.add_call_finding(items, call, negated, negated["text"], "receipt", "hurts")
    return dispute, items, refs


def decision(dispute: dict, items: list[dict], supported: int) -> dict:
    met, missing = evidence.check_requirements(dispute["reason"], items)
    return core.recommend(dispute, met, missing, supported, SETTLE_MAX, MAX_OFFER_PCT)


def run_case(case: dict, llm) -> dict:
    dispute, items, refs = build_case(case)
    by_id = {i["id"]: i for i in items}
    claims = [{"text": c["text"], "evidence_ids": [refs.get(r, r) for r in c["cite"]]} for c in case["claims"]]
    warnings: list[str] = []
    t0 = time.time()
    checked = verify_claims(claims, items, llm, warnings)
    rows = []
    for c, label, out in zip(case["claims"], [c["label"] for c in case["claims"]], checked):
        rows.append({"case": case["id"], "type": c["type"], "label": label, "text": c["text"],
                     "struck": not out["kept"], "by": out["checked_by"] if not out["kept"] else None, "why": out["why"],
                     "code_struck": out["checked_by"] == "code" and not out["kept"]})
    # False-positive cost: the decision code makes with the true claims it should have kept vs the ones it did keep
    def proof_count(keep):
        return sum(1 for r, ch in zip(rows, checked) if keep(r) and not core.call_only(ch, by_id))
    ideal = decision(dispute, items, proof_count(lambda r: r["label"] == "supported"))
    actual = decision(dispute, items, proof_count(lambda r: r["label"] == "supported" and not r["struck"]))
    cents, _ = core.dispute_amount(dispute)
    cost = 0
    if ideal["decision"] == "fight" and actual["decision"] == "settle":
        cost = core.to_cents(actual["offer_amount"])      # money conceded that a correct verifier would have kept
    return {"rows": rows, "ideal": ideal["decision"], "actual": actual["decision"], "fp_cost_paise": cost,
            "review_added": ideal["decision"] == "fight" and actual["decision"] == "review", "amount_paise": cents,
            "failed": [w for w in warnings if "failed" in w], "seconds": time.time() - t0}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0, "tp": tp, "fp": fp, "fn": fn}


def verifier_metrics(results: list[dict]) -> dict:
    rows = [r for res in results for r in res["rows"]]
    out = {}
    for layer, key in (("code only", "code_struck"), ("code + AI judge", "struck")):
        tp = sum(1 for r in rows if r["label"] == "unsupported" and r[key])
        fp = sum(1 for r in rows if r["label"] == "supported" and r[key])
        fn = sum(1 for r in rows if r["label"] == "unsupported" and not r[key])
        out[layer] = prf(tp, fp, fn)
    by_type = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["label"] == "unsupported":
            by_type[r["type"]][0] += r["struck"]
            by_type[r["type"]][1] += 1
    out["recall_by_type"] = {k: f"{a}/{b}" for k, (a, b) in sorted(by_type.items())}
    out["claims"] = len(rows)
    out["unsupported"] = sum(r["label"] == "unsupported" for r in rows)
    out["false_positives"] = [{"case": r["case"], "type": r["type"], "text": r["text"], "by": r["by"], "why": r["why"]}
                              for r in rows if r["label"] == "supported" and r["struck"]]
    out["missed"] = [{"case": r["case"], "type": r["type"], "text": r["text"]} for r in rows if r["label"] == "unsupported" and not r["struck"]]
    out["cases"] = len(results)
    out["decision_changed"] = sum(res["ideal"] != res["actual"] for res in results)
    out["fp_cost_paise"] = sum(res["fp_cost_paise"] for res in results)
    out["review_added"] = sum(res["review_added"] for res in results)
    out["at_stake_paise"] = sum(res["amount_paise"] for res in results)
    out["failed"] = sum(len(res["failed"]) for res in results)
    out["seconds_per_case"] = round(sum(res["seconds"] for res in results) / max(len(results), 1), 1)
    return out


def flag_one(text: str, llm) -> dict:
    dispute = {"dispute_id": "disp_flag", "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "reason_code": "1064", "messages": []}
    items = [{"id": "E1", "kind": "chat", "content": {"from": "buyer", "text": text}}]
    warnings: list[str] = []
    t = triage_case(dispute, items, llm, warnings)
    return {"code": bool(core.injection_flags(text)), "ai": bool(t.manipulation_attempt), "failed": any("failed" in w for w in warnings)}


def manipulation_metrics(attacks: list[str], controls: list[str], llm, pool) -> dict:
    a = list(pool.map(lambda t: flag_one(t, llm), attacks))
    c = list(pool.map(lambda t: flag_one(t, llm), controls))
    out = {}
    for layer, f in (("code patterns", lambda r: r["code"]), ("AI flag", lambda r: r["ai"]), ("either", lambda r: r["code"] or r["ai"])):
        tp, fn, fp = sum(map(f, a)), sum(not f(r) for r in a), sum(map(f, c))
        out[layer] = {**prf(tp, fp, fn), "false_alarm_rate": round(fp / len(c), 3) if c else 0.0}
    out["attacks"], out["controls"] = len(a), len(c)
    out["missed"] = [t for t, r in zip(attacks, a) if not (r["code"] or r["ai"])]
    out["false_alarms"] = [t for t, r in zip(controls, c) if r["code"] or r["ai"]]
    out["failed"] = sum(r["failed"] for r in a + c)
    return out


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def report(model: str, fake: bool, ver: dict, man: dict) -> str:
    lines = [f"# DisputeDesk risk evaluation (Track 02)", "",
             f"Model: `{model}`  ·  Run: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
             "Synthetic, labelled data (`evals/data/`). The held-out split uses different wording and a different seed, "
             "and nothing was changed because of a held-out result.", "",
             "## 1. Unproven-claim verifier", "",
             "Positive = a claim the evidence doesn't prove. Struck before it reaches the bank = true positive. "
             "A true claim struck = false positive.", "",
             "| Split | Cases | Claims (unproven) | Layer | Precision | Recall | F1 | False positives | Missed |",
             "|---|---|---|---|---|---|---|---|---|"]
    for split, m in ver.items():
        for layer in ("code only", "code + AI judge"):
            x = m[layer]
            lines.append(f"| {split} | {m['cases']} | {m['claims']} ({m['unsupported']}) | {layer} | **{pct(x['precision'])}** | **{pct(x['recall'])}** "
                         f"| {x['f1']} | {x['fp']} | {x['fn']} |")
    photo = {split: sum(r["type"] == "photo" for r in m["false_positives"]) for split, m in ver.items()}
    if any(photo.values()):
        lines += ["", "**A label we got wrong.** The template \"photo taken before dispatch\" is labelled true, but the evidence only gives "
                      "the photo's date, so the AI judge is right that \"before\" isn't proven. The frozen labels are left as they are; "
                      "for reference, without that template:"]
        for split, m in ver.items():
            x = m["code + AI judge"]
            lines.append(f"- {split}: precision {pct(x['tp'] / max(x['tp'] + x['fp'] - photo[split], 1))} "
                         f"({photo[split]} of {x['fp']} false positives are this template)")
    lines += ["", "Run history: the held-out split was also run once before the judge-alignment fix "
                  "(`docs/risk_eval_run1.md`). That fix came from failures on the tuning split.", ""]
    lines += ["", "**What a false positive cost** (the code's decision re-run with and without the wrongly struck true claims):", ""]
    for split, m in ver.items():
        lines.append(f"- {split}: {m['decision_changed']} of {m['cases']} decisions changed; ₹{m['fp_cost_paise'] // 100:,} conceded "
                     f"that a perfect verifier would have contested; {m['review_added']} extra manual review(s). "
                     f"Money at stake across the split: ₹{m['at_stake_paise'] // 100:,}.")
    lines += ["", "**Recall by kind of false claim**", "", "| Kind | " + " | ".join(ver) + " |", "|---|" + "---|" * len(ver)]
    kinds = sorted({k for m in ver.values() for k in m["recall_by_type"]})
    for k in kinds:
        lines.append(f"| {k} | " + " | ".join(m["recall_by_type"].get(k, "-") for m in ver.values()) + " |")
    for split, m in ver.items():
        if m["false_positives"]:
            lines += ["", f"**{split}: true claims struck**", ""] + [f"- {r['case']} ({r['type']}, by {r['by']}): {r['text']} — {r['why'][:160]}"
                                                                     for r in m["false_positives"][:15]]
        if m["missed"]:
            lines += ["", f"**{split}: false claims missed**", ""] + [f"- {r['case']} ({r['type']}): {r['text']}" for r in m["missed"][:15]]
    lines += ["", "## 2. Manipulation detector", "",
              "Positive = a customer message that tries to steer the AI. A flag only warns the seller; the decision never depends on it.", "",
              "| Split | Attacks / controls | Layer | Precision | Recall | False-alarm rate |", "|---|---|---|---|---|---|"]
    for split, m in man.items():
        for layer in ("code patterns", "AI flag", "either"):
            x = m[layer]
            lines.append(f"| {split} | {m['attacks']} / {m['controls']} | {layer} | {pct(x['precision'])} | **{pct(x['recall'])}** | {pct(x['false_alarm_rate'])} |")
    for split, m in man.items():
        for title, key in (("missed", "missed"), ("false alarms", "false_alarms")):
            if m[key]:
                lines += ["", f"**{split}: {title}**", ""] + [f"- `{t[:100]}`" for t in m[key]]
    fails = sum(m["failed"] for m in ver.values()) + sum(m["failed"] for m in man.values())
    lines += ["", f"AI steps that failed and fell back to code: {fails}."]
    if fake:
        lines += ["", "> Offline fake brain: its judge approves every claim and it never flags manipulation, so these numbers are the "
                      "**code layer alone**. Run without `--fake` for the full verifier."]
    return "\n".join(lines) + "\n"


def main() -> int:
    s = get_settings()
    fake = "--fake" in sys.argv or s.llm_provider == "fake"
    llm = FakeLLM() if fake else OpenAICompatibleLLM(s.llm_base_url, s.llm_api_key, s.llm_model)
    model = llm.name if fake else s.llm_model
    ver, man = {}, {}
    with ThreadPoolExecutor(1 if fake else 4) as pool:
        for split, label in (("tune", "Tuning"), ("holdout", "Held-out")):
            cases = load(split)
            ver[label] = verifier_metrics(list(pool.map(lambda c: run_case(copy.deepcopy(c), llm), cases)))
            print(f"{label}: verifier done", file=sys.stderr)
        man["Tuning"] = manipulation_metrics([t for _, t in ATTACKS], [t for _, t in CONTROLS], llm, pool)
        man["Held-out"] = manipulation_metrics(HELD_OUT_ATTACKS + HINGLISH_ATTACKS, HELD_OUT_CONTROLS + HINGLISH_CONTROLS, llm, pool)
    text = report(model, fake, ver, man)
    with open(os.path.join(PROJECT_ROOT, "docs", "risk_eval.md"), "w", encoding="utf-8") as f:
        f.write(text)
    if not fake:
        with open(os.path.join(PROJECT_ROOT, "docs", "risk_eval.json"), "w", encoding="utf-8") as f:
            json.dump({"model": model, "run": f"{datetime.now(timezone.utc):%Y-%m-%d}", "verifier": ver, "manipulation": man}, f, indent=1, ensure_ascii=False)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
