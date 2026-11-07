# Gnani live round trip

Run: 2026-11-07 17:56 UTC · TTS `timbre-v2.5` (voices Kaveri and Poorvi, `hi-en`) · STT `prisma-v3.0` (`hi-IN,en-IN`, `codemix`, two speakers) · 16 s of audio

| Check | Result |
|---|---|
| Word error rate, spelling-insensitive (what was heard) | **10.6%** |
| Word error rate, strict spelling | 27.7% |
| Speakers found | 2 of 2 |
| Order DD-1003 found, so the call can be matched to its case | yes |
| The quote the verifier needs ("Parcel last week aa gaya tha") came back (spelling-insensitive) | yes |

**Script**

```
Agent: Namaste, main Kala Leather se bol rahi hoon. Order DD 1003 ke baare mein call hai. Yeh call record ho rahi hai, theek hai?
Customer: Haan ji, theek hai.
Agent: Kya aapko keychain wala parcel mil gaya?
Customer: Parcel last week aa gaya tha, but maine abhi tak khola nahi.
```

**What Gnani heard** (codemix output)

```
Speaker 1: नमस्ते, मैं काला लेदर से बोल रही हूँ, ऑर्डर DD-1003 के बारे में कॉल है, यह कॉल रिकॉर्ड हो रही है, ठीक है।
Speaker 2: हाँ जी, ठीक है।
Speaker 1: क्या आपको कीचेन वाला पार्सल मिल गया?
Speaker 2: पार्सल last week आ गया था but मैंने अभी तक खोला नहीं।
```

**After code converts Devanagari to Latin letters** (what the seller reviews)

```
Speaker 1: namaste, main kala ledar se bol rahi hun, order DD-1003 ke bare men call hai, yah call record ho rahi hai, thik hai.
Speaker 2: han ji, thik hai.
Speaker 1: kya aapako kichen vala parcel mil gaya?
Speaker 2: parcel last week aa gaya tha but mainne abhi tak khola nahin.
```

Every transcript is checked by the seller before any quote can reach a reply, so a mishearing is caught there; and a quote that isn't word for word in the transcript is struck by code.
