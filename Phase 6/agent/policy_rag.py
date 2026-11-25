"""Policy answers: grounded topic answers + pure-python TF-IDF retrieval.

Two layers, both grounded in the store's own documents:

1. Topic answers. The most common policy questions (delivery charge / time,
   COD, free delivery, exchange rules, hours, showroom, international
   delivery, refunds, order limits) are answered from the values in
   data/policy_rules.json, in English or Roman Urdu, and city-aware.
2. Retrieval. Anything else is scored against every '## ' section of
   policy.md with TF-IDF cosine similarity. Below the gate the question is
   treated as outside the policy and the agent hands over instead of
   inventing an answer (Phase 4 hallucination-gate finding).
"""
import math
import re
from collections import Counter
from pathlib import Path

GATE = 0.12

STOP = set("the a an is are do does of to in for on and or hai hain kya ka ki "
           "ke mein me se par pe aur ye yeh wo woh i you we my your can what how "
           "please plz bhai ji".split())

# Roman Urdu -> English expansions so retrieval can match the English policy
EXPANSIONS = {
    "kitne": "how many", "din": "days", "dino": "days", "waqt": "time",
    "paisay": "payment", "paise": "payment", "raqam": "amount", "kharcha": "charges",
    "bahar": "outside", "mulk": "country", "wapas": "return", "wapis": "return",
    "tabdeel": "exchange", "badal": "exchange", "badalna": "exchange",
    "dukaan": "showroom", "dukan": "showroom", "staff": "staff human",
    "kapray": "clothing", "kapre": "clothing", "rate": "price",
}


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    out = []
    for w in words:
        if w in STOP:
            continue
        out.append(w)
        if w in EXPANSIONS:
            out.extend(EXPANSIONS[w].split())
    return out


class PolicyRAG:
    def __init__(self, policy_path):
        text = Path(policy_path).read_text(encoding="utf-8-sig")
        self.chunks = []
        for section in re.split(r"\n(?=## )", text):
            section = section.strip()
            if not section:
                continue
            lines = [l for l in section.split("\n") if l.strip()]
            title = lines[0].lstrip("# ").strip() if lines else "Policy"
            self.chunks.append({"title": title, "text": section})
        self.n = len(self.chunks)
        df = Counter()
        self.tfs = []
        for ch in self.chunks:
            tf = Counter(_tokens(ch["text"]))
            self.tfs.append(tf)
            for w in tf:
                df[w] += 1
        self.idf = {w: math.log((self.n + 1) / (c + 1)) + 1 for w, c in df.items()}
        self.norms = []
        for tf in self.tfs:
            self.norms.append(math.sqrt(sum((cnt * self.idf[w]) ** 2
                                            for w, cnt in tf.items())) or 1.0)

    def retrieve(self, query, top_k=3):
        # words the policy never uses carry no evidence either way — ignore
        # them rather than letting them dilute the score
        q = Counter(w for w in _tokens(query) if w in self.idf)
        if not q:
            return []
        qvec = {w: cnt * self.idf[w] for w, cnt in q.items()}
        qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
        scored = []
        for i, tf in enumerate(self.tfs):
            dot = sum(qvec[w] * tf.get(w, 0) * self.idf.get(w, 1)
                      for w in qvec if w in tf)
            scored.append((dot / (qnorm * self.norms[i]), i))
        scored.sort(reverse=True)
        return [{"title": self.chunks[i]["title"], "text": self.chunks[i]["text"],
                 "score": round(s, 4)} for s, i in scored[:top_k] if s > 0]

    def best(self, query):
        r = self.retrieve(query, top_k=1)
        return r[0] if r else {"title": None, "text": "", "score": 0.0}

    def grounded_answer(self, query):
        """Return (answer_text, chunk) or (None, chunk) if below the gate."""
        chunk = self.best(query)
        if chunk["score"] < GATE:
            return None, chunk
        qwords = set(_tokens(query))
        lines = [l.strip("-* ").strip() for l in chunk["text"].split("\n")
                 if l.strip().startswith(("-", "*", "1", "2", "3", "4", "5", "6"))]
        scored = sorted(((len(qwords & set(_tokens(l))), i, l) for i, l in enumerate(lines)),
                        key=lambda x: (-x[0], x[1]))
        picked = [l for c, i, l in scored if c > 0][:3]
        answer = " ".join(picked) if picked else " ".join(lines[:2])
        return (answer or chunk["text"][:400]), chunk


# ---------------------------------------------------------------- topics
TOPIC_PATTERNS = [
    ("international", r"abroad|outside pakistan|international|bahar ke mulk|bahar mulk|"
                      r"\bmulk\b|overseas|\buk\b|\busa\b|dubai|saudi|canada|other country|foreign"),
    ("showroom", r"showroom|walk[- ]?in|\bshop kahan\b|store kahan|location|kahan (hai|ho|hain) aap|"
                 r"address kya|visit|aa kar|a kar dekh|physical store|outlet|\bdukaan\b|\bdukan\b"),
    ("hours", r"working hours|timing|timings|kab tak reply|staff kab|kab reply|office hours|"
              r"open hours|kab khulta|staff available|human kab|when.*(staff|reply|open)|band kab"),
    ("refund_policy", r"refund policy|refund kitne din|refund kab tak|refund process|"
                      r"how long.*refund|return policy"),
    ("exchange_policy", r"exchange"),
    ("cod", r"\bcod\b|cash on delivery|cash|payment method|pay kaise|payment kaise|"
            r"kaise pay|how (do|can) i pay|payment option"),
    ("free_delivery", r"free delivery|free shipping|delivery free|muft"),
    ("delivery_time", r"kitne din|how long|how many days|kab tak (aye|aaye|milega|pohanch|deliver)|"
                      r"delivery time|kab milega|kab aye|kab aayega|days|din mein|eta|"
                      r"jaldi deliver|kab pohanch"),
    ("delivery_charge", r"delivery charge|delivery charges|shipping charge|shipping cost|"
                        r"delivery fee|delivery kitn|courier charge|charges kitn|delivery ka kharcha|"
                        r"charge kitna|delivery cost|charges"),
    ("delivery_general", r"deliver|delivery|shipping|ship to|courier"),
    ("order_limits", r"max(imum)? (qty|quantity)|kitne pieces|bulk|limit"),
    ("how_to_order", r"how (do|can|to) i? ?(order|buy|place)|order kaise|kaise order|order karne ka tareeqa"),
    ("privacy", r"privacy|data safe|information safe|personal info"),
    ("sale", r"\bsale\b|offer|deals?\b"),
]


def detect_topic(t):
    for topic, pat in TOPIC_PATTERNS:
        if re.search(pat, t):
            return topic
    return None


def _money(n):
    return f"Rs {int(n):,}"


def topic_answer(topic, t, city, lang, rules):
    """Grounded answer for a known topic, or None. Facts come only from
    policy_rules.json (kept in sync with policy.md)."""
    d, pay, ex, order = rules["delivery"], rules["payment"], rules["exchange"], rules["order"]
    tier_a = [c.lower() for c in d["tier_a_cities"]]
    major = ", ".join(d["tier_a_cities"])
    ur = lang == "ur"
    a_lo, a_hi = d["eta_days_tier_a"]
    o_lo, o_hi = d["eta_days_other"]

    def city_line():
        if not city:
            return None
        if city.lower() in tier_a:
            ch, lo, hi = d["tier_a_charge"], a_lo, a_hi
        else:
            ch, lo, hi = d["default_charge"], o_lo, o_hi
        return (f"{city} ke liye delivery charge {_money(ch)} hai aur delivery {lo}-{hi} "
                f"working days mein hoti hai." if ur else
                f"For {city}, delivery is {_money(ch)} and takes {lo}-{hi} working days.")

    if topic == "international":
        return ("Maazrat, hum sirf Pakistan ke andar deliver karte hain — bahar ke mulk "
                "delivery available nahi hai." if ur else
                "Sorry, we only deliver within Pakistan — international delivery is not available.")
    if topic == "showroom":
        return ("Hum Gujranwala based online store hain — koi walk-in showroom nahi hai. "
                "Aap yahin chat ya website se order kar sakte hain, poore Pakistan mein delivery hoti hai."
                if ur else
                "We are an online-only store based in Gujranwala — there is no walk-in showroom. "
                "You can order right here and we deliver all over Pakistan.")
    if topic == "hours":
        return (f"Main (AI assistant) 24/7 reply karta hoon. Human staff roz "
                f"{rules.get('human_hours', '10:00-20:00 PKT')} available hoti hai — is time ke "
                "baad forward hone wali chats ka jawab agli subah milta hai." if ur else
                f"I (the AI assistant) reply 24/7. Human staff are available daily "
                f"{rules.get('human_hours', '10:00-20:00 PKT')}; chats handed over after hours "
                "are answered the next morning.")
    if topic == "refund_policy":
        return ("Refund ka faisla sirf hamari staff karti hai — main refund approve nahi kar sakta. "
                f"Damaged/ghalat item ki photos delivery ke {rules['refund']['damage_report_hours']} "
                "ghante ke andar bhejein. Approved refund 7-10 working days mein process hota hai."
                if ur else
                "Refunds are decided by our staff only — I can't approve one myself. For a damaged "
                f"or wrong item, send photos within {rules['refund']['damage_report_hours']} hours of "
                "delivery. Approved refunds are processed in 7-10 working days.")
    if topic == "exchange_policy":
        cats = ", ".join(ex["non_exchangeable_categories"])
        if re.search(r"\bsale\b", t):
            return ("Sale wale items final sale hote hain — unka exchange nahi hota." if ur else
                    "Sale items are final sale and cannot be exchanged.")
        return (f"Exchange delivery ke {ex['window_days']} din ke andar ho sakta hai: item sale "
                "ka na ho, unused ho aur tags lage hon, aur aap ko same product ka doosra size ya "
                f"color chahiye jo stock mein ho. Exchange courier charge {_money(ex['exchange_courier_charge'])} "
                f"hai. Ye categories exchange nahi hotin: {cats}." if ur else
                f"Exchanges are possible within {ex['window_days']} days of delivery if the item is "
                "not from the sale, unused with tags, and you want a different size or color of the "
                f"same product that is in stock. The exchange courier charge is "
                f"{_money(ex['exchange_courier_charge'])}. Not exchangeable: {cats}.")
    if topic == "cod":
        line = (f"Ji, Cash on Delivery (COD) har jagah available hai — parcel milne par courier ko "
                f"cash ada karein. {_money(pay['cod_max_total_without_human'])} se zyada ke COD "
                "order staff confirm karti hai. Online payment (JazzCash, Easypaisa, bank) sirf "
                "staff handle karti hai." if ur else
                "Yes, Cash on Delivery (COD) is available everywhere — you pay the courier in cash "
                f"when the parcel arrives. COD orders above {_money(pay['cod_max_total_without_human'])} "
                "are confirmed by our staff first. Online payment (JazzCash, Easypaisa, bank "
                "transfer) is handled by staff only.")
        cl = city_line()
        return line + (" " + cl if cl and re.search(r"deliver|charge", t) else "")
    if topic == "free_delivery":
        return (f"{_money(d['free_delivery_min_product_total'])} ya us se zyada ke product total "
                "par delivery free hai." if ur else
                f"Delivery is free when the product total is {_money(d['free_delivery_min_product_total'])} or more.")
    if topic in ("delivery_time", "delivery_charge", "delivery_general"):
        cl = city_line()
        if cl:
            extra = (f" {_money(d['free_delivery_min_product_total'])}+ ke order par delivery free hai."
                     if ur else f" Orders of {_money(d['free_delivery_min_product_total'])}+ ship free.")
            return cl + extra
        if topic == "delivery_time":
            return (f"{major}: {a_lo}-{a_hi} working days. Baqi shehar: {o_lo}-{o_hi} working days."
                    if ur else
                    f"{major}: {a_lo}-{a_hi} working days. All other cities: {o_lo}-{o_hi} working days.")
        return (f"Delivery charges: {major} ke liye {_money(d['tier_a_charge'])}, baqi shehron ke "
                f"liye {_money(d['default_charge'])}. {_money(d['free_delivery_min_product_total'])}+ "
                f"ke order par delivery free. Time: {a_lo}-{a_hi} din (major cities), {o_lo}-{o_hi} din (baqi)."
                if ur else
                f"Delivery is {_money(d['tier_a_charge'])} to {major}, and {_money(d['default_charge'])} "
                f"to all other cities — free on orders of {_money(d['free_delivery_min_product_total'])}+. "
                f"It takes {a_lo}-{a_hi} working days to major cities and {o_lo}-{o_hi} elsewhere.")
    if topic == "order_limits":
        return (f"Ek item ke maximum {order['max_qty_per_item']} pieces per order. Is se zyada "
                "(bulk) order hamari staff handle karti hai." if ur else
                f"The limit is {order['max_qty_per_item']} pieces of one item per order. Bigger "
                "(bulk) orders are handled by our staff.")
    if topic == "how_to_order":
        return ("Bas product ka naam, color, size aur quantity bata dein — phir naam, phone aur "
                "address. Main summary dikhaunga, aap 'haan' likhein aur order confirm. Payment COD."
                if ur else
                "Just tell me the product, color, size and quantity, then your name, phone and "
                "address. I'll show a summary — reply 'yes' and the order is placed. Payment is COD.")
    if topic == "privacy":
        return ("Aap ki details sirf aap ke order ke liye use hoti hain. Order status bhi sirf "
                "order number aur wahi phone number dene par share hota hai." if ur else
                "Your details are used only for your order. Order status is shared only when both "
                "the order number and the phone number on the order are given.")
    if topic == "sale":
        return ("Sale items catalog mein 'SALE' tag ke saath dikhte hain aur unki price wahi hai "
                "jo catalog mein likhi hai. Yaad rahe, sale items exchange nahi hote." if ur else
                "Sale items are tagged SALE in the catalog at the price shown there. Note that "
                "sale items are final sale and cannot be exchanged.")
    return None
