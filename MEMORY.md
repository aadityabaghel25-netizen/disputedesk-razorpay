# DisputeDesk for Razorpay: project memory

> Context file for any AI assistant or human picking up this project. Read this first. Update the
> "Status" and "Decisions log" sections after every session.

## 1. Who and what

- **Builder:** Ankit Solanki. Student with a DevOps background (AWS, Terraform, Kubernetes, Python) and
  banking/fintech experience. **New to building AI apps**, so explain every new AI concept in plain language,
  with DevOps analogies, before using it.
- **Project:** DisputeDesk for Razorpay, "an AI risk desk that defends small Indian sellers against chargebacks".
- **Origin:** a port of DisputeDesk, Ankit's PayPal AI Hackathon entry (`~/Downloads/disputedesk`, submitted to Devpost
  on Nov 1, 2026). This is a separate repo. Don't change the PayPal one.

## 2. The program (hard facts)

| Item | Value |
|---|---|
| Link | https://razorpay.com/buildathon/ · apply: https://forms.gle/d9r2gvxp8cmoZhon9 |
| What | Student-only hiring program for AI Builder Interns: ₹75,000/month, 6 or 12 months, in person in Bangalore from September |
| Track | **02, AI Risk Manager**: a working detector, verifier or auto-responder for one class of loss, with measured precision and recall on a held-out test set and honest false-positive cost. Defence only. "Chargeback evidence responder" is one of its examples |
| Form fields | email, name, college, graduation year, in-person availability, track, project name, objectives, GitHub URL, **5-min pitch video (public Google Drive link)**, build challenges & how solved, final confirmation |
| Submit | One shot, no edits afterwards. Sep 5 cohort closed; new entries roll into the next review cycle |

## 3. The product

Defend (agents build a cited evidence case, verifier strikes unproven claims, code builds contest PDFs per Razorpay
field and uploads them) · Concede part (code-computed partial contest within seller limits) · Gnani: Hindi/English/
Hinglish call recordings → exact quotes; Hinglish voice brief per case · deadline board, webhooks, audit log.

## 4. Golden rules

See `CLAUDE.md`. AI writes, code decides money (paise). No Razorpay write without a click. Every claim cites evidence.
Customer text is untrusted. Offline demo always works. Defence only.

## 5. Status (update every session)

- **2026-11-04 (first session):** project created by copying the PayPal codebase (no git history, no video, no PayPal
  research). Ported to Razorpay: `app/razorpay.py` (client + adapter + reason codes + HMAC), Razorpay-shaped demo data and
  an Indian shop (INR, Delhivery/Blue Dart/Ekart, PIN codes, Hinglish chats), core rules (status/`respond_by` permissions,
  contest checklist, money in paise with Indian grouping, contest plan), `app/pdf.py`, approve = upload PDFs → contest /
  partial contest / accept, Hinglish sample calls, Gnani TTS voice brief (`/disputes/{id}/voice`), eval cases in INR.
  101 offline tests pass; offline eval 20/20. PayPal client, Prevent (tracking) and sandbox scripts removed.
- **Not yet verified against real services:** Razorpay test keys (none in `.env` yet), Gnani TTS (key is in `.env`,
  not yet called), real AI model on the new prompts.
- **2026-11-05:** Track 02 eval built: `evals/dataset.py` (seeded; tune 30 cases/273 claims, held-out 40/370, frozen JSONL),
  `scripts/risk_eval.py` (verifier + manipulation detector, ₹ false-positive cost). Real model, held-out: verifier 88.2% precision /
  97.6% recall (100% precision excluding the mislabelled photo template), FP cost ₹0; AI manipulation flag 88.9% (run 1: 94.4%), 0 false alarms.
  Fixes from the tuning split: Hindi word-order injection pattern; judge verdicts aligned by claim_start. Held-out has been run twice (run 1 kept).
  106 tests pass. attack_eval ported; old PayPal-era eval/attack JSON removed.
- **Not done:** real-model `eval.py` and `attack_eval.py`, architecture doc/diagram, pitch video, GitHub repo, hosting, Razorpay test keys.

## 6. Decisions log

| Date | Decision |
|---|---|
| 2026-11-04 | Separate project folder `~/Downloads/disputedesk-razorpay` with its own git repo (Ankit's choice) |
| 2026-11-04 | Track 02 (AI Risk Manager), positioned as a chargeback evidence responder |
| 2026-11-04 | Adapter pattern: `app/razorpay.py` normalises Razorpay JSON into the existing case model, so core rules and agents stay gateway-neutral |
| 2026-11-04 | Settle = contest a smaller `amount` (concede the rest). Razorpay blocks refunds during a dispute and has no buyer-offer channel |
| 2026-11-04 | Evidence goes to Razorpay as code-built PDFs per contest field; Razorpay needs ≥1 document to submit |
| 2026-11-04 | Gnani STT defaults: `hi-IN,en-IN` + `transliterate`, so Hinglish quotes are readable, checkable and fit a Latin PDF |
| 2026-11-04 | Gnani TTS voice brief: words written by code from checked facts, never by AI |
| 2026-11-04 | Dispute fees aren't modelled (vary by Razorpay plan); only the disputed money is counted |
| 2026-11-04 | Prevent (auto-add tracking) dropped: Razorpay has no shipment-tracking API |
| 2026-11-05 | Held-out eval data is frozen; labels are never edited after seeing results (a known label error is reported, not fixed) |
| 2026-11-04 | Test-mode disputes aren't documented by Razorpay → demo data in Razorpay's exact shapes + `setup.py simulate` signed webhook |
