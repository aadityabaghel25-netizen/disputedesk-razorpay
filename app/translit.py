"""
Devanagari -> Latin letters, the way Hinglish is typed on WhatsApp ("आ गया था" -> "aa gaya tha").

Gnani's batch speech-to-text (the one that separates two speakers on a call) writes Hindi in Devanagari when asked for
code-mixed output. The rest of DisputeDesk works in Latin letters: the quote checker, the Hinglish negations ("nahi"),
and the evidence PDFs, whose built-in font has no Devanagari. So code converts it, with plain rules and no AI.

It's an approximation (Hindi spelling in Latin letters isn't standardised), so the seller reads the transcript before
any quote from it can be used, as with every transcript.
"""

VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
          "ऑ": "o", "ॲ": "e"}
SIGNS = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e"}
CONSONANTS = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
              "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
              "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
              "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "य़": "y"}
NASAL = {"ं": "n", "ँ": "n"}
VIRAMA, NUKTA = "्", "़"
DIGITS = {chr(0x0966 + n): str(n) for n in range(10)}
PUNCT = {"।": ".", "॥": "."}
# English words Gnani writes in Devanagari on shopping calls. Spelled back in English, so the seller reads "parcel", not "parsal".
LOANWORDS = {"पार्सल": "parcel", "ऑर्डर": "order", "आर्डर": "order", "कॉल": "call", "डिलीवरी": "delivery", "डिलिवरी": "delivery",
             "रिफंड": "refund", "कूरियर": "courier", "कुरियर": "courier", "पेमेंट": "payment", "बॉक्स": "box", "ओटीपी": "OTP",
             "नंबर": "number", "पैकेट": "packet", "प्रोडक्ट": "product", "एड्रेस": "address", "ट्रैकिंग": "tracking",
             "रिकॉर्ड": "record", "कस्टमर": "customer", "प्रॉब्लम": "problem", "कलर": "colour", "फोटो": "photo",
             "कैंसिल": "cancel", "रिटर्न": "return", "बैंक": "bank", "यूपीआई": "UPI", "कार्ड": "card", "मैसेज": "message"}


def _word(word: str) -> str:
    if word in LOANWORDS:
        return LOANWORDS[word]
    out, i = [], 0
    chars = list(word)
    while i < len(chars):
        c = chars[i]
        if i + 1 < len(chars) and chars[i + 1] == NUKTA:   # क + ़ -> क़
            c, i = c + NUKTA, i + 1
        if c in CONSONANTS:
            out.append(CONSONANTS[c])
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt in SIGNS:
                out.append(SIGNS[nxt])
                i += 1
            elif nxt == VIRAMA:
                i += 1
            elif nxt and (nxt in CONSONANTS or nxt in NASAL):
                out.append("a")   # inherent vowel inside a word ("गया" -> "gaya"); dropped at the end below
            elif nxt and nxt not in CONSONANTS:
                out.append("a")
            else:
                out.append("a")   # word-final: removed after the loop (schwa deletion)
                out.append("\0")
        elif c in VOWELS:
            out.append(VOWELS[c])
        elif c in NASAL:
            out.append(NASAL[c])
        elif c in DIGITS:
            out.append(DIGITS[c])
        elif c in PUNCT:
            out.append(PUNCT[c])
        elif c in SIGNS:   # a sign with nothing before it
            out.append(SIGNS[c])
        else:
            out.append(c)
        i += 1
    text = "".join(out)
    # Hindi drops the inherent "a" at the end of a word: "मिल" is "mil", not "mila". One-letter words keep it ("न" -> "na").
    if text.endswith("a\0") and len(text) > 3:
        text = text[:-2]
    return text.replace("\0", "")


def to_latin(text: str) -> str:
    """Converts only Devanagari words; English already in Latin letters is left as it is."""
    out, word = [], []
    for ch in text:
        if "ऀ" <= ch <= "ॿ":
            word.append(ch)
        else:
            if word:
                out.append(_word("".join(word)))
                word = []
            out.append(ch)
    if word:
        out.append(_word("".join(word)))
    return "".join(out)
