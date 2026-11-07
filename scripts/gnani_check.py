"""
Live Gnani round trip: does a Hinglish phone call survive speech-to-text well enough for DisputeDesk's quote checks?

    python3 scripts/gnani_check.py

1. Gnani text-to-speech voices a made-up two-person call (shop agent + customer, Hindi and English mixed).
2. The clips are joined into one WAV, like a recorded call.
3. Gnani batch speech-to-text transcribes it with the app's own settings (hi-IN,en-IN, transliterate, two speakers).
4. Code measures word error rate against the script, and checks that the order number and the quote the
   verifier would need come back word for word.

Uses a little Gnani credit (one TTS call per line, one STT job). Writes docs/gnani_check.md.
"""

import io
import os
import re
import sys
import wave
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import core, translit  # noqa: E402
from app.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.llm import gnani_speak, gnani_transcribe  # noqa: E402

RATE = 16000
CALL = [  # (speaker, voice, text). Hindi written in Latin letters, the way the app expects Gnani to write it back.
    ("Agent", "Kaveri", "Namaste, main Kala Leather se bol rahi hoon. Order DD 1003 ke baare mein call hai. Yeh call record ho rahi hai, theek hai?"),
    ("Customer", "Poorvi", "Haan ji, theek hai."),
    ("Agent", "Kaveri", "Kya aapko keychain wala parcel mil gaya?"),
    ("Customer", "Poorvi", "Parcel last week aa gaya tha, but maine abhi tak khola nahi."),
]
QUOTE = "Parcel last week aa gaya tha"


def words(text: str) -> list[str]:
    return re.sub(r"[^\w ]+", " ", text.lower()).split()


def wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate: (substitutions + deletions + insertions) / words in the script. Plain edit distance."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / max(len(ref), 1)


def loose(ws: list[str]) -> list[str]:
    """Spelling-insensitive form of a word: Hinglish has no standard spelling ("theek" / "thik", "hoon" / "hun"),
    so both sides are folded the same way before comparing. This measures hearing, not spelling."""
    out = []
    for w in ws:
        for a, b in (("aa", "a"), ("ee", "i"), ("oo", "u"), ("ei", "e"), ("w", "v"), ("ph", "f"), ("z", "j")):
            w = w.replace(a, b)
        if len(w) > 2 and w.endswith("n") and w[-2] in "aeiou":
            w = w[:-1]   # nasal endings: "nahin" / "nahi", "mein" / "men", "hun" / "hu"
        out.append(w)
    return out


def join_wavs(clips: list[bytes], gap_s: float = 0.6) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        for clip in clips:
            with wave.open(io.BytesIO(clip)) as r:
                if (r.getframerate(), r.getnchannels(), r.getsampwidth()) != (RATE, 1, 2):
                    raise SystemExit(f"Unexpected audio format from Gnani: {r.getparams()}")
                w.writeframes(r.readframes(r.getnframes()))
            w.writeframes(b"\x00\x00" * int(RATE * gap_s))
    return out.getvalue()


def main() -> int:
    s = get_settings()
    if not s.gnani_api_key:
        print("[FAILED] GNANI_API_KEY is empty in .env.")
        return 1
    clips = [gnani_speak(s.gnani_api_key, text, s.gnani_tts_model, voice, "hi-en", container="wav", sample_rate=RATE) for _, voice, text in CALL]
    audio = join_wavs(clips)
    raw = gnani_transcribe(s.gnani_api_key, s.gnani_model, s.gnani_language, audio, "DD-1003-roundtrip.wav",
                                  keyterms=["DD-1003", "keychain", "Kala Leather"], output_format=s.gnani_format)
    transcript = translit.to_latin(raw)
    script = " ".join(t for _, _, t in CALL)
    heard = re.sub(r"(?m)^Speaker \d+:\s*", "", transcript)
    error = wer(words(script), words(heard))
    loose_error = wer(loose(words(script)), loose(words(heard)))
    quote_ok = " ".join(loose(words(QUOTE))) in " ".join(loose(words(heard)))
    speakers = len(set(re.findall(r"(?m)^(Speaker \d+):", transcript)))
    order_ok = core.find_reference(heard, [("order DD-1003", "DD-1003")]) is not None
    lines = ["# Gnani live round trip", "",
             f"Run: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · TTS `{s.gnani_tts_model}` (voices Kaveri and Poorvi, `hi-en`) · "
             f"STT `{s.gnani_model}` (`{s.gnani_language}`, `{s.gnani_format}`, two speakers) · {len(audio) / (2 * RATE):.0f} s of audio", "",
             "| Check | Result |", "|---|---|",
             f"| Word error rate, spelling-insensitive (what was heard) | **{loose_error * 100:.1f}%** |",
             f"| Word error rate, strict spelling | {error * 100:.1f}% |",
             f"| Speakers found | {speakers} of 2 |",
             f"| Order DD-1003 found, so the call can be matched to its case | {'yes' if order_ok else 'no'} |",
             f"| The quote the verifier needs (\"{QUOTE}\") came back (spelling-insensitive) | {'yes' if quote_ok else 'no'} |", "",
             "**Script**", "", "```", *[f"{who}: {t}" for who, _, t in CALL], "```", "",
             "**What Gnani heard** (codemix output)", "", "```", raw, "```", "",
             "**After code converts Devanagari to Latin letters** (what the seller reviews)", "", "```", transcript, "```", "",
             "Every transcript is checked by the seller before any quote can reach a reply, so a mishearing is caught there; "
             "and a quote that isn't word for word in the transcript is struck by code."]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(PROJECT_ROOT, "docs", "gnani_check.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
