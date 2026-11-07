# Build challenges and how they were solved

A running log for the application form's "Build Challenges & Technical Obstacles" question. Add real obstacles as they happen.

## Razorpay's dispute model is different from the one we started with
DisputeDesk began on another gateway, where the dispute itself says which replies are allowed and carries the
buyer's messages. Razorpay gives a status, a `respond_by` time, a reason code from the card network or NPCI, and no
customer messages. **Solved:** an adapter (`app/razorpay.py`) normalises a Razorpay dispute, payment and order into one
case. Permissions are computed from Razorpay's rules (contest/accept only while `open` and before `respond_by`), and
customer words come from shop chats and calls instead.

## Reason codes come from many networks
The same complaint arrives as Visa 13.1, Mastercard 4855, UPI 1064 or a Razorpay text code. **Solved:** one mapping
table to a small set of categories. Unknown codes fall back to "other", and the checklist says so instead of guessing.

## No way to settle by refund during a dispute
Razorpay blocks refunds while a dispute is under investigation and has no offer-to-buyer channel. **Solved:** "settle"
became a partial contest: contest the dispute amount minus a concession capped by the seller's rules, computed in paise.

## Evidence must be files, not text
The contest API takes document ids. **Solved:** code writes one PDF per contest field from verified claims only
(a small standard-library PDF writer), uploads them, then contests. A flagged customer message can never reach a file.

## Hindi and Hinglish calls
Calls with Indian customers and couriers mix Hindi and English. Devanagari output broke quote matching and couldn't
go into a Latin PDF. **Solved:** Gnani's `transliterate` format writes everything in Latin letters, and the quote checker
learned Hinglish negations ("nahi mila") so a quote can't drop one.

## Disputes can't be created in test mode (as far as the docs say)
**Solved for now:** demo data in Razorpay's exact JSON shapes, run through the real adapter, plus a script that sends
an HMAC-signed webhook to the app, so the signature check and the full webhook path are exercised end to end.

## Amounts like ₹2,499.00 confused the fact checker
The verifier extracts every amount in a claim and checks it against the evidence. Indian digit grouping made it read
"499.00" out of "₹2,499.00". **Solved:** amounts with ₹/Rs. are read whole and commas removed, and the plain-decimal
pattern no longer starts after a comma. A test covers it.

## Measuring a verifier honestly
Track 02 asks for precision and recall on a held-out set, but real chargeback data with labels isn't public. **Solved:**
a seeded generator (`evals/dataset.py`) builds Razorpay-shaped cases and plants claims a drafter might write, true
ones and 14 kinds of false ones (wrong date, ₹ amount or AWB, invented quote, a call stated as fact, a dropped "nahi",
an opinion, a citation to nothing...). Labels come from construction, not from the model. The held-out split uses
different wording and a different seed, is frozen in `evals/data/holdout.jsonl`, and the script refuses to regenerate it.
The eval runs the same `verify_claims()` the app uses, and prices false positives in ₹ by re-running the code's decision.

## The eval found a real gap: Hinglish injections
On the tuning split, a claim quoting "AI assistant, pichhle saare instructions bhool jao..." got through, because the
injection patterns expected English word order ("ignore ... instructions") and Hindi puts the verb last. **Solved:** one
pattern for Hindi word order, written from the tuning failure only. A first version also matched an innocent English
control ("instructions say to ignore creases"), which the control set caught, so it was narrowed to Hindi verbs.
"Ignore karo" was deliberately left out, because that wording appears in the held-out set.

## The AI judge sometimes answered about the wrong claim
The first real-model run struck a true listing claim with a reason about "intent to commit chargeback fraud", which
belonged to a different claim in the same batch. The model had shifted its numbering, so one claim's verdict landed on
another, and the opinion it was meant to catch slipped through. **Solved:** the judge now copies the first five words of
each claim, and code accepts a verdict only for the claim that really starts with those words. Anything it can't match is
judged again on its own. A test reproduces the shifted numbering.

## When the AI is right and our label is wrong
Most false positives in the first run were one template, "photo taken before dispatch". The evidence only gave the
photo's date, so the judge correctly said "before" wasn't proven. We left the frozen held-out labels alone, because
changing labels after seeing results would make the score meaningless, and we report the number with and without that template.

## Gnani's batch API couldn't write Hinglish in Latin letters
The plan was Gnani's `transliterate` format, which writes Hindi in Latin letters. A live round trip (Gnani text-to-speech
voices a two-person Hinglish call, Gnani speech-to-text transcribes it) failed with HTTP 400: `transliterate` only exists
on the 30-second instant API, and phone calls need the batch API, which separates the two speakers. **Solved:** batch
`codemix` (Hindi in Devanagari, English in Latin), then a small rule-based converter in code (`app/translit.py`), with a
word list for English shopping words Gnani writes in Devanagari (पार्सल → parcel). Measured with `scripts/gnani_check.py`:
2 of 2 speakers, order number found, quote intact, 10.6% word error rate once Hinglish spelling variants are folded
(27.7% strict, because "theek" and "thik" are both correct Hinglish).
