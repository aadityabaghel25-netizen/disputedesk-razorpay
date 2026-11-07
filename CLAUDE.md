# CLAUDE.md: instructions for any AI coding assistant working on DisputeDesk for Razorpay

Read this file first, then `MEMORY.md` (project facts, decisions log, status) and
`docs/NEXT_STEPS.md` (prioritised task list). Update `MEMORY.md` → "Status" and
"Decisions log" at the end of every session. Log real obstacles in `docs/CHALLENGES.md`:
the application form asks for them.

## Who you are working with
- **Ankit Solanki**: student with a DevOps background (AWS, Terraform, K8s, Python), banking/fintech experience.
  **New to building AI apps.** He asked to be taught from the basics while building.
- Explain each new AI concept in plain words with a DevOps analogy before using it.
- He is on **macOS**, Python from **Anaconda** (`python3`), browser **Chrome**, shell **zsh**.
- Give terminal commands **one per code block, one line each**, no trailing `\`.
  He copy-pastes; multi-line pastes have caused stuck `>` prompts before.
- When something fails, ask for a screenshot of the Terminal or page.
- Never ask him to paste secrets into chat. Secrets live only in `.env` (gitignored).

## Goal
Get shortlisted in the **Razorpay AI Buildathon** (https://razorpay.com/buildathon/), **Track 02: AI Risk Manager**.
It is a student hiring program (AI Builder Intern, in person in Bangalore), not a prize hackathon.
Submission (Google Form, one shot, no edits after): public GitHub repo, **5-minute pitch video as a public Google
Drive link**, project objectives, build challenges and how they were solved, architecture.
Track 02 bar: a working detector/verifier/auto-responder for one class of loss, with **measured precision and
recall on a held-out test set**, honest false-positive cost, and **defence only** (anything offense-capable is disqualified).
No fixed deadline: entries roll into the next review cycle.

This is a separate project from the PayPal hackathon entry (`~/Downloads/disputedesk`, already submitted). Don't change that one.

## What DisputeDesk for Razorpay is
An AI risk desk for small Indian sellers on Razorpay: Defend (agents build a cited evidence case; verifier strikes
unproven claims; code builds the contest PDFs), Concede part (code-computed partial contest), Gnani speech AI for
Hindi/English/Hinglish call recordings and Hinglish voice briefs. Seller approves every Razorpay write.

## Golden rules (never break)
1. AI reads/writes; **code decides money** (integer paise, caps, deadlines).
2. **No Razorpay write without a human click.** Never auto-accept a dispute (accept is irreversible).
3. Every claim cites evidence ids; the reply and the contest PDFs are assembled by code from verified claims only.
4. Customer text (chats, calls) is untrusted data (prompt-injection flagged and ignored). AI gets read-only tools only.
5. Keep the offline demo mode (no keys) working: it is the never-fail demo fallback.
6. Keep all tests passing: `python3 -m unittest discover -s tests -v`.
7. Defence only. Nothing that could help someone commit fraud or attack a merchant.

## Commands
```bash
pip install -r requirements.txt                 # once
uvicorn app.main:app --reload                   # run app -> http://127.0.0.1:8000
python3 -m unittest discover -s tests -v        # offline tests
python3 scripts/eval.py                         # 20-case evaluation (uses .env model; --fake for offline)
python3 scripts/setup.py auth                   # check Razorpay test keys
python3 scripts/setup.py order DD-1001          # test-mode payment link for a shop order
python3 scripts/setup.py simulate http://127.0.0.1:8000   # signed payment.dispute.created webhook to the app
python3 scripts/setup.py gnani                  # check the Gnani key (free)
python3 scripts/setup.py voice "Namaste"        # Gnani TTS test clip -> voice-test.mp3
python3 scripts/risk_eval.py                    # Track 02 precision/recall, tuning + held-out (--fake = code layer only)
python3 scripts/gnani_check.py                  # live Gnani TTS -> STT Hinglish round trip (small Gnani cost)
```
If port 8000 is busy: `lsof -ti:8000 | xargs kill`.

## Architecture (files)
- `app/config.py` settings from `.env` (no Razorpay keys → demo mode; no AI key → FakeLLM)
- `app/razorpay.py` REST client (stdlib urllib), `normalize()` adapter (Razorpay dispute+payment+order → case), reason-code table, webhook HMAC
- `app/demo.py` offline Razorpay stand-in (`data/demo_disputes.json`, Razorpay's own JSON shapes); `Service.reset_demo` restores it
- `app/evidence.py` Collector, `{paid+N}` date resolver, per-reason requirement checks, mock Store, seller-added proof
- `app/core.py` deterministic rules: paise, deadlines, allowed actions (status `open` + before `respond_by`),
  policy lookup (`policy/dispute_rules.md`), injection flags, claim fact-check, contest checklist, contest plan (which PDF goes in which field)
- `app/pdf.py` tiny text-only PDF writer for contest documents
- `app/llm.py` OpenAI-compatible client, `ask_json`, tool loop, FakeLLM, Gnani STT (batch) and TTS
- `app/agents.py` the agent team
- `app/service.py` glue: sync, webhooks, analyze, approve (upload docs → contest / partial contest / accept), calls, voice brief
- `app/main.py` Starlette routes + Basic auth; `app/templates/*`; `app/static/agents.js` live agent layer
- `app/translit.py` Devanagari → Latin Hinglish for Gnani transcripts
- `evals/` labelled dataset (held-out split frozen: never edit labels or code because of a held-out result)
- `scripts/` setup + evals; `tests/test_app.py`; `docs/ARCHITECTURE.md`; `docs/SUBMISSION.md` (form answers + pitch script)

## Known Razorpay facts (from the docs, 2026-11-04)
- Base `https://api.razorpay.com/v1`, Basic auth `key_id:key_secret`. Test keys start `rzp_test_`.
- Dispute: `id` (`disp_`), `payment_id`, `amount` (paise), `reason_code`, `respond_by` (unix), `status`
  (`open`, `under_review`, `won`, `lost`, `closed`), `phase` (`fraud`, `retrieval`, `chargeback`, `pre_arbitration`, `arbitration`), `evidence`.
- Contest: `PATCH /v1/disputes/:id/contest` with `amount`, `summary` (≤1000 chars), evidence fields as lists of
  document ids, `others` [{type, document_ids}], `action` `draft`|`submit`. Submit needs ≥1 document; moves to `under_review`.
- Documents: `POST /v1/documents` multipart `file` + `purpose=dispute_evidence`; PDF/JPG/PNG.
- Accept: `POST /v1/disputes/:id/accept`, irreversible, only while `open` and before `respond_by`.
- Webhooks: `payment.dispute.created|won|lost|closed|under_review|action_required`; header `X-Razorpay-Signature`
  = HMAC-SHA256(raw body, webhook secret); `x-razorpay-event-id` for dedupe; order not guaranteed.
- **Not documented: how to create a dispute in test mode.** Hence demo data + `setup.py simulate`.
- Razorpay has no shipping-address field: the shop puts it in order/payment `notes` (`shipping_address`, `shop_order`).

## Known Gnani facts
- Header `X-API-Key-ID`; firewall needs a `User-Agent` header. One request at a time per account (429 without Retry-After).
- STT batch: `prisma-v3.0`, `language_code` like `hi-IN,en-IN`, diarization in batch only. Batch `format` accepts only
  `verbatim`, `transcribe`, `codemix` (**not** `transliterate`, which is instant-API only; verified live 2026-11-07).
  We use `codemix` and convert Devanagari to Latin in code (`app/translit.py`). Live check: `python3 scripts/gnani_check.py`.
- TTS: `POST https://api.vachana.ai/api/v1/tts/inference`, model `timbre-v2.5`, voice `Poorvi`, language `hi-en`, MP3 via `audio_config.container`.
