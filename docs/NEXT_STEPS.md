# Next steps (prioritised)

1. ~~Held-out precision/recall eval~~ Done: `scripts/risk_eval.py`, results in `docs/risk_eval.md`. Next: re-run
   `scripts/attack_eval.py` and `scripts/eval.py` on the real model (their old numbers were from the PayPal version and were removed).
2. **Razorpay test keys.** Add `rzp_test_` keys, run `python3 scripts/setup.py auth`, make a test payment with
   `setup.py order DD-1001`, and try `setup.py dispute` on any dispute Razorpay support can create in test mode.
3. **Gnani live checks.** `python3 scripts/setup.py voice "..."` for TTS; upload a real Hinglish call to check
   `transliterate` output and the quote checks.
4. **Real-model run** of `scripts/eval.py` and `scripts/attack_eval.py` with the new Razorpay/Hinglish prompts.
5. **Architecture doc** with a diagram (the form asks for the architecture).
6. **Public GitHub repo** (new repo, MIT licence) and hosting (Render blueprint is in `render.yaml`; keep the Gnani key off a public demo).
7. **5-minute pitch video** on Google Drive (public link).
8. Fill the form once everything above is final. It can't be edited after submitting.
