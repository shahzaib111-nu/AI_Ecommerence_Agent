"""Natural-language understanding: pure stdlib.

Normalizes Roman Urdu / English code-mixed text, then extracts entities:
product, color, size, quantity, city, phone, name, address, order id, budget,
plus "what kind of thing" filters (kurta, shoes, winter, women...) used for
browsing and recommendations.

Synonym tables come from the Phase 2 dataset work (kala/blk -> Black,
safaid -> White, mdm -> M, ...). Product matching is token overlap plus
difflib fuzzy matching, so 'kurtta' still finds 'Kurta' — but a generic word
like 'kurta' on its own never picks one specific product (it becomes a
browse filter instead).
"""
import difflib
import re
from collections import Counter

COLOR_SYNONYMS = {
    "black": ["black", "blk", "blak", "kala", "kaala", "kali"],
    "white": ["white", "wht", "safaid", "safed", "sufaid", "safaid"],
    "pink": ["pink", "gulabi"],
    "blue": ["blue", "neela", "neela", "nila"],
    "navy": ["navy", "navi", "navy blue"],
    "dark blue": ["dark blue"],
    "sky blue": ["sky blue", "aasmaani", "asmani"],
    "light blue": ["light blue"],
    "green": ["green", "sabz", "hara"],
    "emerald": ["emerald"],
    "red": ["red", "surkh", "laal", "lal"],
    "maroon": ["maroon", "marun", "mahroon"],
    "yellow": ["yellow", "peela", "pila"],
    "brown": ["brown", "bhura", "bhoora"],
    "grey": ["grey", "gray", "surmai"],
    "beige": ["beige"],
    "cream": ["cream"],
    "gold": ["gold", "golden", "sunahra"],
    "mustard": ["mustard"],
    "olive": ["olive"],
    "peach": ["peach"],
    "lilac": ["lilac"],
    "mint": ["mint"],
    "teal": ["teal"],
    "burgundy": ["burgundy"],
    "wine": ["wine"],
    "camel": ["camel"],
    "charcoal": ["charcoal"],
    "ivory": ["ivory"],
    "khaki": ["khaki"],
    "tan": ["tan"],
}
COLOR_CANON = {}
for _name in ["Black", "White", "Pink", "Blue", "Navy", "Dark Blue", "Sky Blue",
              "Light Blue", "Green", "Emerald", "Red", "Maroon", "Yellow",
              "Brown", "Grey", "Beige", "Cream", "Gold", "Mustard", "Olive",
              "Peach", "Lilac", "Mint", "Teal", "Burgundy", "Wine", "Camel",
              "Charcoal", "Ivory", "Khaki", "Tan"]:
    COLOR_CANON[_name.lower()] = _name
COLOR_WORDS = {w for syns in COLOR_SYNONYMS.values() for s in syns for w in s.split()}

SIZE_SYNONYMS = {
    "s": ["small", "s", "chota", "chhota"],
    "m": ["medium", "m", "mdm", "med", "midium"],
    "l": ["large", "l", "bara", "barra"],
    "xl": ["xl", "extra large", "extralarge"],
    "free": ["free size", "one size"],
}
SIZE_CANON = {"s": "S", "m": "M", "l": "L", "xl": "XL", "free": "Free"}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "ek": 1, "aik": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "panch": 5,
    "paanch": 5, "chay": 6, "chhe": 6, "saat": 7, "aath": 8, "nau": 9,
    "das": 10, "gyarah": 11, "barah": 12,
}

CITIES = ["Lahore", "Gujranwala", "Sialkot", "Sheikhupura", "Faisalabad",
          "Islamabad", "Rawalpindi", "Karachi", "Peshawar", "Multan",
          "Quetta", "Bahawalpur", "Hyderabad", "Sargodha", "Gujrat",
          "Sukkur", "Larkana", "Abbottabad", "Mardan", "Kasur", "Okara",
          "Sahiwal", "Jhang", "Rahim Yar Khan", "Dera Ghazi Khan", "Mirpur",
          "Muzaffarabad", "Jhelum", "Chiniot", "Wazirabad", "Daska",
          "Narowal", "Hafizabad", "Mandi Bahauddin", "Swat", "Gilgit",
          "Chakwal", "Attock", "Vehari", "Khanewal", "Nawabshah", "Mansehra"]

URDU_WORDS = set("""hai hain hun hoon mein main me ka ki ke ko se par pe aur ya kya
kitna kitne kitni kab kahan kyun kaise kaisa chahiye chahta chahti karo kar karna
karni dena de bhej bhejo batao bata bataen dikhao dikha nahi nahin haan han ji acha
achha theek thik sahi bhai mujhe mera meri mere wala wali ye yeh wo woh bhi tum
aap ne to hi gaya gayi liya tha thi pehle abhi jaldi suno arre yaar pata kuch koi
hoga hogi sakta sakti sakte milega milegi kardo krdo krna kro shukriya salam
assalam walaikum alaikum khuda hafiz allah baat madad naam rehne lena lein dein
baji behen apni apna apne kab tak wapas""".split())

# common misspellings / Urdu words for product words in the catalog
PRODUCT_WORD_FIX = {"tshirt": "t-shirt", "tee": "t-shirt", "kurtta": "kurta",
                    "kurtay": "kurta", "kurtas": "kurta", "kurtis": "kurti",
                    "jootay": "shoes", "joota": "shoes", "juti": "khussa",
                    "khusa": "khussa", "chapal": "chappal", "chappals": "chappal",
                    "sweatshirt": "sweatshirt", "jean": "jeans", "trousers": "trouser",
                    "dupata": "dupatta", "dopatta": "dupatta", "abayas": "abaya",
                    "hijabs": "hijab", "shawls": "shawl", "hoodies": "hoodie",
                    "sweaters": "sweater", "frocks": "frock", "belts": "belt",
                    "bags": "bag", "suits": "suit", "waistcoats": "waistcoat",
                    "sandal": "sandals", "polos": "polo", "shirts": "shirt"}

# "what kind of thing" words -> predicate over (name, category).
# Used for browsing ("kurtas dikhao") and recommendations ("winter ke liye").
TYPE_KEYWORDS = [
    (r"\bkurtas?\b|\bkurtay\b", ("name", "kurta")),
    (r"\bkurtis?\b", ("name", "kurti")),
    (r"\bshalwar\s*kameez\b|\bkameez\b|\bshalwar\b", ("name", "shalwar")),
    (r"\bshoes?\b|\bjoot[ae]y?\b|\bfootwear\b|\bchappal|\bkhuss?a\b|\bsandals?\b", ("category", "footwear")),
    (r"\bkhuss?a\b", ("name", "khussa")),
    (r"\bchappal", ("name", "chappal")),
    (r"\bsandals?\b", ("name", "sandals")),
    (r"\bsuits?\b|\b[23]\s*-?\s*piece\b|\bco-?ord\b|\bparty wear\b|\bformal\b", ("category", "women stitched")),
    (r"\bunstitched\b|\bfabric\b|\bunstitch\b", ("category", "unstitched")),
    (r"\bdupatt?as?\b|\bdopatta\b", ("name", "dupatta")),
    (r"\bhijabs?\b|\bscarf\b", ("name", "hijab")),
    (r"\babayas?\b", ("name", "abaya")),
    (r"\bmodest\b", ("category", "modest")),
    (r"\bshawls?\b|\bchadar\b", ("name", "shawl")),
    (r"\bsweaters?\b|\bpullovers?\b", ("name", "sweater")),
    (r"\bcardigans?\b", ("name", "cardigan")),
    (r"\bhood(ie|y|ies)\b|\bsweatshirts?\b", ("name", "hood")),
    (r"\bjeans\b|\bdenim\b", ("name", "jeans")),
    (r"\btrousers?\b|\bpants\b|\bchinos?\b|\bpalazzo\b|\bbottoms?\b", ("category", "bottoms_or_trouser")),
    (r"\bt-?shirts?\b|\btees?\b|\bpolo\b", ("name", "t-shirt")),
    (r"\bshirts?\b", ("name", "shirt")),
    (r"\bwaistcoats?\b|\bwaist coat\b|\bvest\b", ("name", "waistcoat")),
    (r"\bfrocks?\b|\bdress(es)?\b", ("name", "frock_or_dress")),
    (r"\bbelts?\b", ("name", "belt")),
    (r"\bbags?\b|\bhandbags?\b|\btote\b|\bpurse\b", ("name", "bag")),
    (r"\baccessor(y|ies)\b", ("category", "accessories")),
    (r"\bwinter\b|\bsardi\b|\bsardiyon\b|\bthand\b|\bwarm\b|\bwool(en)?\b", ("category", "winter")),
    (r"\bwestern\b|\bcasual\b", ("category", "western")),
    (r"\bwedding\b|\bshaadi\b|\bshadi\b|\bmehndi\b|\bbaraat\b|\bevent\b|\bparty\b|\beid\b|\bfestive\b", ("occasion", "festive")),
    (r"\bsummer\b|\bgarmi\b|\blawn\b", ("occasion", "summer")),
]
GENDER_KEYWORDS = [
    (r"\bmen\b|\bmens\b|\bman\b|\bmale\b|\bgents?\b|\bmardana\b|\blarko\b|\blarkon\b|\bhusband\b|\bboys?\b|\babbu\b|\bbhai ke\b", "men"),
    (r"\bwomen\b|\bwomens\b|\bwoman\b|\bladies\b|\blady\b|\bfemale\b|\bgirls?\b|\blarki\b|\blarkiyon\b|\bzanana\b|\bwife\b|\bammi\b|\bbehen\b|\bsister\b|\bmother\b", "women"),
    (r"\bkids?\b|\bbach(a|ay|on|ey|chon|che)\b|\bchild(ren)?\b|\bbeta\b|\bbeti\b|\bson\b|\bdaughter\b|\bbaby\b", "kids"),
]
FESTIVE_PRODUCTS = {"Chiffon Party Wear", "Organza Formal Suit", "Velvet Winter Suit",
                    "Embroidered Kurta", "Jamawar Waistcoat", "Embroidered Abaya",
                    "Khussa Embroidered", "Embroidered Dupatta", "Embroidered Kurti",
                    "Kids Kurta Shalwar", "Kameez Shalwar Cotton", "Waistcoat Classic"}
SUMMER_PRODUCTS = {"Linen Kurta", "Cotton Kurta", "Lawn 2-Piece Stitched",
                   "Lawn 3-Piece Stitched", "Unstitched Lawn 3-Piece", "Linen Kurti",
                   "Printed Kurti", "Plain Cotton Kurti", "Kids Frock Lawn",
                   "Casual Sandals", "Polo T-Shirt", "Graphic T-Shirt"}


def normalize(text):
    t = str(text or "").lower().strip()
    t = t.replace("'", "").replace("’", "")
    # 0300-1234567 / 0300 1234567 -> 03001234567
    t = re.sub(r"\b(03\d{2})[-\s](\d{7})\b", r"\1\2", t)
    t = re.sub(r"\b(03\d{2})\s(\d{3})\s(\d{4})\b", r"\1\2\3", t)
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)      # 2,500 -> 2500
    t = re.sub(r"[^\w\s\-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# words that are both English and Roman Urdu ('do' = give, 'to' = so, 'main' = I)
AMBIGUOUS = {"do", "to", "me", "main", "hi", "ya", "de", "pe", "ji", "bhi", "ke", "kar", "par", "se"}
EN_WORDS = set("""the you your is are do does did can could would will what which how why when where who
i im my me we our it its this that these those to for in of on and or with please want need have has
any some show tell give get buy order deliver delivery available price much many size color colour
there here about like from not dont yes no thanks thank hello hi hey sure ok okay looking find
something also more other than just only all one two its am be been was were if then so but""".split())


def urdu_share(text):
    words = [w for w in normalize(text).split() if w.isalpha()]
    if not words:
        return 0.0
    return sum(1 for w in words if w in URDU_WORDS) / len(words)


EN_GREETINGS = {"hi", "hii", "hiii", "hello", "hey", "helo", "hy", "thanks", "thank", "ok", "okay", "yes", "no"}


def language_of(text):
    """'ur' for Roman Urdu / code-mixed, 'en' for English, None if unsure."""
    words = [w for w in normalize(text).split() if w.isalpha()]
    if words and words[0] in EN_GREETINGS and len(words) <= 3 and \
            not any(w in URDU_WORDS - {"hi"} for w in words[1:]):
        return "en" if words[0] in ("hi", "hii", "hiii", "hello", "hey", "helo", "hy") else None
    if len(words) < 2 and not any(w in URDU_WORDS for w in words):
        return None
    ur = sum(1 for w in words if w in URDU_WORDS and w not in AMBIGUOUS)
    ur += 0.4 * sum(1 for w in words if w in URDU_WORDS and w in AMBIGUOUS and w not in EN_WORDS)
    en = sum(1 for w in words if w in EN_WORDS and w not in URDU_WORDS)
    if ur == 0 and en == 0:
        return None
    if len(words) <= 3 and en <= 1 and ur < 0.8:
        return None          # 'order kar do' — too short to tell, keep the chat's language
    return "ur" if ur >= max(0.8, en * 0.75) else "en"


def extract_phone(text):
    t = re.sub(r"[-\s]", "", str(text or ""))
    t = re.sub(r"^\+?92(3\d{9})", r"0\1", t)
    t = re.sub(r"\+92(3\d{9})", r"0\1", t)
    m = re.search(r"(?<!\d)(03\d{9})(?!\d)", t)
    return m.group(1) if m else None


def extract_order_id(text):
    t = normalize(text)
    t = re.sub(r"03\d{9}", " ", t)
    m = re.search(r"\border\D{0,14}?(\d{5})\b", t) or \
        re.search(r"(?:#|\bid\s*|\bnumber\s*|\bno\s*)(\d{5})\b", t)
    if m:
        return int(m.group(1))
    for m in re.finditer(r"\b(1\d{4}|2\d{4})\b", t):
        before = t[max(0, m.start() - 12):m.start()]
        after = t[m.end():m.end() + 10]
        if re.search(r"rs|rupe|budget|under|below|price|upto|max|around", before) or \
                re.search(r"^\s*(tak|rs|rupe|ke|ka budget|se kam)", after):
            continue
        return int(m.group(1))
    return None


def extract_budget(text):
    t = normalize(text)
    t = re.sub(r"03\d{9}", " ", t)
    pats = [r"(?:budget|under|below|max|maximum|upto|up to|within|less than|around|approx|about)\D{0,8}?(\d{3,6})",
            r"(?:rs|rupees|pkr)\s*(\d{3,6})\s*(?:tak|ke andar|se kam|ke around|budget)",
            r"(\d{3,6})\s*(?:rs|rupees|pkr)?\s*(?:tak|ke andar|se kam|ke around|ke qareeb|budget|max)"]
    for p in pats:
        m = re.search(p, t)
        if m:
            return int(m.group(1))
    return None


def budget_mode(text):
    t = normalize(text)
    if re.search(r"around|approx|about|ke around|qareeb|lagbhag", t):
        return "around"
    return "max"


def extract_city(text):
    t = " " + normalize(text) + " "
    for city in sorted(CITIES, key=len, reverse=True):
        if f" {city.lower()} " in t:
            return city
    return None


def _color_in(text):
    t = " " + normalize(text) + " "
    hits = []
    for canon_low, syns in COLOR_SYNONYMS.items():
        for s in sorted(syns, key=len, reverse=True):
            pos = t.find(f" {s} ")
            if pos >= 0:
                hits.append((pos, -len(s), COLOR_CANON.get(canon_low, canon_low.title())))
                break
    hits.sort()
    return hits[0][2] if hits else None


def all_colors_in(text):
    t = " " + normalize(text) + " "
    found = []
    for canon_low, syns in COLOR_SYNONYMS.items():
        for s in sorted(syns, key=len, reverse=True):
            pos = t.find(f" {s} ")
            if pos >= 0:
                found.append((pos, -len(s), COLOR_CANON.get(canon_low, canon_low.title())))
                break
    found.sort()
    out = []
    # 'navy blue' also contains 'blue' — drop a color nested inside another
    spans = []
    for pos, neg_len, name in found:
        if any(p <= pos < p + l for p, l in spans):
            continue
        spans.append((pos, -neg_len + 1))
        if name not in out:
            out.append(name)
    return out


def extract_size(text):
    t = normalize(text)
    m = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\s*(?:y|yr|yrs|years?|saal)\b", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}Y"
    m = re.search(r"\bsize\s*(?:no\s*|number\s*)?(\d{1,2}|xl|s|m|l|free)\b", t)
    if m:
        tok = m.group(1)
        return SIZE_CANON.get(tok, tok)
    m = re.search(r"\b(\d{1,2}|xl|s|m|l)\s+size\b", t)
    if m:
        tok = m.group(1)
        return SIZE_CANON.get(tok, tok)
    for syn, canon in (("extra large", "XL"), ("extralarge", "XL"), ("free size", "Free"),
                       ("one size", "Free"), ("medium", "M"), ("mdm", "M"),
                       ("midium", "M"), ("small", "S"), ("large", "L"), ("xl", "XL")):
        if re.search(rf"\b{syn}\b", t):
            return canon
    # single letter size as its own word ('s' 'm' 'l')
    m = re.search(r"(?:^|\s)(s|m|l)(?:\s|$)", t)
    if m:
        return SIZE_CANON[m.group(1)]
    return None


def extract_qty(text, product_names=()):
    t = normalize(text)
    t = re.sub(r"03\d{9}", " ", t)
    m = re.search(r"\b(?:qty|quantity|x)\s*(\d{1,3})\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,3})\s*(?:pieces?|pcs|items?|units?|qty|nos|piece|jor[ae]y?|pairs?)\b", t)
    if m and not re.search(rf"\b{m.group(1)}\s*-\s*piece", t):
        return int(m.group(1))
    product_words = {w for n in product_names for w in re.split(r"[\s\-&]+", n.lower()) if len(w) > 2}
    product_words |= {"kurta", "kurti", "shoes", "suit", "khussa", "chappal", "same"}
    toks = t.split()
    for i, w in enumerate(toks):
        n = None
        if w.isdigit() and len(w) <= 3:
            n = int(w)
        elif w in WORD_NUMBERS:
            n = WORD_NUMBERS[w]
        if n is None or n == 0:
            continue
        prev = toks[i - 1] if i else ""
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if prev in ("size", "number", "no", "order", "rs", "budget", "street", "house",
                    "block", "sector", "phase", "flat", "plot", "#") or nxt in ("y", "saal", "years"):
            continue
        if re.match(r"^-", nxt) or "-" in w:
            continue
        # 'do' alone is usually the verb 'give' ('bhej do') — only a number
        # when it opens a phrase like 'do black kurte'
        if w == "do" and not (i == 0 or prev in ("mujhe", "humein", "hamen", "aur", "and")):
            continue
        if nxt in COLOR_WORDS or nxt in product_words or \
                re.match(r"(order|bhej|chahiye|chahiyen|chahiyein|mangwa|de|dedo|dein|lena|lene)", nxt):
            return n
    m = re.search(r"^\s*(\d{1,3})\s*(?:order|bhej|chahiye)", t)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"\s*(\d{1,3})\s*", t)
    if m:
        return int(m.group(1))
    return None


_TOKEN_FREQ_CACHE = {}


def _token_freq(product_names):
    key = tuple(product_names)
    if key not in _TOKEN_FREQ_CACHE:
        c = Counter()
        for n in product_names:
            for w in set(_name_tokens(n)):
                c[w] += 1
        _TOKEN_FREQ_CACHE.clear()
        _TOKEN_FREQ_CACHE[key] = c
    return _TOKEN_FREQ_CACHE[key]


def _name_tokens(name):
    # numeric / one-letter tokens ('2' in 'Lawn 2-Piece') never count
    return [w for w in re.split(r"[\s\-&]+", name.lower())
            if (w.isalpha() and len(w) > 1) or w == "t-shirt"]


def match_product(text, product_names):
    """Return best-matching catalog product name, or None.

    A match is accepted only when the message hits a token that singles out
    the product (a token few products share) or hits every token of the name.
    So 'linen kurta' -> Linen Kurta, 'khussa' -> Khussa Embroidered, but
    plain 'kurta' (5 products) -> None.
    """
    t = normalize(text)
    words = [PRODUCT_WORD_FIX.get(w, w) for w in t.split()]
    t2 = " ".join(words)
    words += [w for w in re.split(r"[\s\-]+", t2) if w]
    if "t-shirt" in t2 or "tshirt" in t:
        words.append("t-shirt")
    wordset = set(words)
    freq = _token_freq(product_names)
    pm = re.search(r"\b(\d)\s*-?\s*(?:piece|pc)\b", t)
    generic = {"women", "men", "kids", "wear", "stitched"}
    best, best_score = None, 0.0
    for name in product_names:
        toks = _name_tokens(name)
        if not toks:
            continue
        exact = [w for w in toks if w in wordset]
        fuzzy = [w for w in toks if w not in wordset and len(w) > 3 and
                 difflib.get_close_matches(w, words, n=1, cutoff=0.84)]
        hit = set(exact) | set(fuzzy)
        if not hit:
            continue
        distinct = [w for w in hit if w not in generic and freq[w] <= 1]
        all_hit = all(w in hit for w in toks if w not in generic)
        if not (distinct or all_hit):
            continue
        if not any(w in exact for w in toks if w not in generic):
            continue
        unmatched = len([w for w in toks if w not in hit])
        score = 2 * len(exact) + 1.2 * len(fuzzy) - 0.4 * unmatched + \
            (0.5 if all_hit else 0) + 0.3 * len(distinct)
        if pm and "piece" in name.lower():
            score += 3 if f"{pm.group(1)}-piece" in name.lower() else -3
        if score > best_score:
            best, best_score = name, score
    return best if best_score >= 1.5 else None


def extract_type_filters(text):
    """Return list of (field, value) filters such as ('name','kurta')."""
    t = normalize(text)
    t = " ".join(PRODUCT_WORD_FIX.get(w, w) for w in t.split())
    out = []
    for pat, flt in TYPE_KEYWORDS:
        if re.search(pat, t) and flt not in out:
            out.append(flt)
    # a specific footwear word wins over the generic footwear filter
    if any(f in out for f in (("name", "khussa"), ("name", "chappal"), ("name", "sandals"))):
        out = [f for f in out if f != ("category", "footwear")]
    if ("name", "t-shirt") in out:
        out = [f for f in out if f != ("name", "shirt")]
    return out


def extract_gender(text):
    t = normalize(text)
    for pat, g in GENDER_KEYWORDS:
        if re.search(pat, t):
            return g
    return None


def product_matches_filters(name, category, filters, gender=None):
    """True if a product (name, category) fits the type filters and gender."""
    n, c = name.lower(), category.lower()
    if gender == "men" and not (c.startswith("men") or c in ("footwear", "accessories")):
        return False
    if gender == "women" and not (c.startswith("women") or c in ("footwear", "accessories")):
        return False
    if gender == "kids" and c != "kids":
        return False
    if gender == "men" and c == "footwear" and "khussa" in n:
        pass
    if not filters:
        return True
    for field, val in filters:
        if field == "name":
            if val == "frock_or_dress":
                if "frock" in n or c == "women stitched":
                    return True
            elif val == "hood":
                if "hood" in n:
                    return True
            elif val in n:
                return True
        elif field == "category":
            if val == "bottoms_or_trouser":
                if "bottoms" in c or "trouser" in n or "pants" in n:
                    return True
            elif val in c:
                return True
        elif field == "occasion":
            if val == "festive" and name in FESTIVE_PRODUCTS:
                return True
            if val == "summer" and name in SUMMER_PRODUCTS:
                return True
    return False


ADDRESS_WORDS = (r"house|h\s*no|flat|shop|plot|street|st\b|road|rd\b|mohalla|muhalla|block|"
                 r"sector|town|gulberg|cantt|model|dha|phase|colony|chowk|bazaar|bazar|"
                 r"market|village|gali|lane|avenue|scheme|housing|society|near|opposite|"
                 r"apartment|floor|i-\d|f-\d|g-\d|e-\d")


def looks_like_address(text):
    t = normalize(text)
    return bool(re.search(rf"\b({ADDRESS_WORDS})", t)) and len(t) >= 8


def _clean_name(cand):
    cand = re.sub(r"[^A-Za-z .]", " ", cand or "")
    cand = re.sub(r"\b(mera|meri|naam|name|is|hai|hun|hoon|i am|im|my|ji|ye|yeh|raha|"
                  r"customer|order|phone|number|address|city|mr|mrs|ms)\b", " ", cand, flags=re.I)
    cand = re.sub(r"\s+", " ", cand).strip(" .")
    words = cand.split()
    if not (1 <= len(words) <= 4) or not (2 <= len(cand) <= 40):
        return None
    if any(w.lower() in URDU_WORDS or w.lower() in COLOR_WORDS for w in words):
        return None
    if re.search(rf"\b({ADDRESS_WORDS})\b", cand, re.I):
        return None
    if extract_city(cand) and len(words) == 1:
        return None
    return cand.title()


def extract_name_address(text):
    """Heuristics for order-details turns. Handles 'naam: X / phone: Y /
    address: Z' blocks, 'Mera naam X hai, 0300..., house 12 ...', 'X, 0300...,
    house ...' and 'address - name - phone' orderings."""
    raw = str(text or "")
    phone = extract_phone(raw)
    city = extract_city(raw)
    name = address = None

    # labelled lines: "naam: X", "name - X", "address: ..."
    for line in re.split(r"[\n;]|(?<=\d),(?=\s*[A-Za-z])", raw):
        m = re.match(r"\s*(naam|name|customer name)\s*[:=\-]\s*(.+)", line, re.I)
        if m:
            name = _clean_name(m.group(2)) or name
        m = re.match(r"\s*(address|addr|pata|ghar ka pata)\s*[:=\-]\s*(.+)", line, re.I)
        if m:
            address = m.group(2).strip(" ,.")
    if not name:
        m = re.search(r"(?:mera naam|my name is|my name|name is|i am|i'm|naam)\s*[:\-]?\s*"
                      r"([A-Za-z][A-Za-z .]{1,40}?)(?=\s+(?:hai|hun|hoon|and|aur)\b|,|\.|\n|\d|$)",
                      raw, re.I)
        if m:
            name = _clean_name(m.group(1))
    parts = [p.strip() for p in re.split(r"\s+-\s+|[,\n|]", raw) if p.strip()]
    if not name and phone:
        # "Ali Raza, 0300..., house 12" or "house..., Lahore - Rabia Anwar - 0343..."
        # the part closest to the phone number wins
        pidx = next((i for i, p in enumerate(parts) if extract_phone(p)), 0)
        order = sorted(range(len(parts)), key=lambda i: (abs(i - pidx), i))
        for i in order:
            p = parts[i]
            if extract_phone(p):
                before = re.split(r"(?:\+92|0)3\d{2}[-\s]?\d{7}", p)[0]
                cand = _clean_name(before) if before.strip() else None
            elif looks_like_address(p) or extract_city(p) == p.strip().title():
                continue
            else:
                cand = _clean_name(p)
            if cand:
                name = cand
                break
    if not address:
        m = re.search(rf"(\b(?:{ADDRESS_WORDS})\b.*)", raw, re.I | re.S)
        if m:
            addr = m.group(1)
            addr = re.sub(r"(?:\+92|0)3\d{2}[-\s]?\d{7}", " ", addr)
            if name:
                addr = re.sub(r"[-,]?\s*" + re.escape(name) + r"\s*[-,]?", " ", addr, flags=re.I)
            addr = re.sub(r"\b(phone|number|mobile|contact)\b\s*[:\-]?", " ", addr, flags=re.I)
            addr = re.sub(r"\s+", " ", addr.replace("\n", ", ")).strip(" ,.-")
            if len(addr) >= 4:
                address = addr
    if address and city and address.lower().endswith(city.lower()) and \
            len(address) > len(city) + 3:
        address = address[: -len(city)].strip(" ,-")
    if not address and phone and name:
        # fallback: whatever is left after removing name / phone / city
        rest = re.sub(r"(?:\+92|0)3\d{2}[-\s]?\d{7}", " ", raw)
        rest = re.sub(re.escape(name), " ", rest, flags=re.I)
        if city:
            rest = re.sub(re.escape(city), " ", rest, flags=re.I)
        rest = re.sub(r"mera naam|hai|number|address|phone|mera|deta hun|ye raha|ye hai",
                      " ", rest, flags=re.I)
        rest = re.sub(r"[^A-Za-z0-9\- ]", " ", rest)
        rest = re.sub(r"\s+", " ", rest).strip(" ,-")
        if len(rest) >= 6:
            address = rest.title()
    return {"name": name, "phone": phone, "city": city, "address": address}


def extract(text, product_names):
    """Full entity extraction for one customer message."""
    return {
        "product": match_product(text, product_names),
        "color": _color_in(text),
        "colors": all_colors_in(text),
        "size": extract_size(text),
        "qty": extract_qty(text, product_names),
        "city": extract_city(text),
        "phone": extract_phone(text),
        "order_id": extract_order_id(text),
        "budget": extract_budget(text),
        "budget_mode": budget_mode(text),
        "types": extract_type_filters(text),
        "gender": extract_gender(text),
        "details": extract_name_address(text),
    }
