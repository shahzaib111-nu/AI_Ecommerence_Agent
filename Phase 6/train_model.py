"""Train the per-message intent classifier (pure Python, no extra installs).

    python train_model.py          # -> models/intent_model.json + models/metrics.json

Training data
  1. data/intent_seeds.json      hand-written EN + Roman Urdu utterances
  2. catalog templates           generated from data/catalog.csv, orders.csv
                                 and policy cities ("{color} {product} {size}
                                 mein hai?", "order {id} ka status", ...)
  3. DEV conversations           every customer turn of the dev split of
                                 conversations.json, labelled per turn

Evaluation
  * held-out 20% of (1)+(2), stratified by intent
  * every customer turn of the TEST split of conversations.json — these are
    never used for training

The old Phase 3 model predicted one label per *whole conversation* (from
120 examples) and was pickled with scikit-learn, so it could not load on a
machine with a different sklearn version — and it could not tell a greeting
from an order. This model classifies each message and is stored as JSON.
"""
import csv
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from agent import nlu                      # noqa: E402
from agent.intent_model import IntentModel  # noqa: E402

DATA = BASE / "data"
MODELS = BASE / "models"
DATASET_INTENTS = {"place_order", "check_stock", "exchange", "policy_question",
                   "order_status", "recommend", "refund", "complaint",
                   "human_request", "payment_request", "change_order",
                   "discount_request", "cancel_order"}
NAMES = ["Ali Raza", "Sana Malik", "Hamza Tariq", "Ayesha Khan", "Bilal Ahmed",
         "Fatima Noor", "Usman Ghani", "Hina Shah", "Zain Abbas", "Maryam Iqbal",
         "Saad Butt", "Nida Rauf", "Kinza Javed", "Omer Farooq", "Rabia Anwar"]
STREETS = ["house 12 street 4 Model Town", "flat 3 block C Gulberg", "house 45 street 9 Cantt",
           "plot 7 sector G-10", "house 90 street 9 Latifabad", "house 2 DHA phase 5",
           "street 11 Satellite Town", "house 18 Johar Town", "house 5 Peoples Colony"]


def turn_label(conv_intent, text, idx, n_turns):
    """Per-turn label for a conversation turn (dataset labels whole convs)."""
    t = nlu.normalize(text)
    words = set(t.split())
    if idx > 0 and (words & {"haan", "han", "yes", "confirm", "sahi", "bilkul", "ji"}) \
            and len(words) <= 6 and not re.search(r"\d{3}", t):
        return "affirm"
    if nlu.extract_phone(text) and (nlu.looks_like_address(text) or idx > 0):
        if not re.search(r"order|status|exchange|kahan", t):
            return "provide_details"
    if idx > 0 and re.fullmatch(r"(mera number |phone )?\s*03\d{9}( hai)?", t):
        return "provide_details"
    if conv_intent in DATASET_INTENTS:
        if conv_intent == "place_order" and idx == 0 and \
                not re.search(r"order|bhej|chahiye|lena|mangwa|kar do", t):
            return "check_stock"
        if idx == 0 or conv_intent in ("recommend", "exchange", "order_status"):
            return conv_intent
    return None


def conv_examples(split):
    data = json.loads((DATA / "conversations.json").read_text(encoding="utf-8-sig"))
    out = []
    for c in data["conversations"]:
        if c.get("split") != split:
            continue
        turns = [t["text"] for t in c["turns"] if t["speaker"] == "customer"]
        for i, text in enumerate(turns):
            lab = turn_label(c["expected"]["intent"], text, i, len(turns))
            if lab:
                out.append((text, lab))
    return out


def template_examples(rng):
    cat = list(csv.DictReader(open(DATA / "catalog.csv", encoding="utf-8-sig")))
    orders = list(csv.DictReader(open(DATA / "orders.csv", encoding="utf-8-sig")))
    rules = json.loads((DATA / "policy_rules.json").read_text(encoding="utf-8-sig"))
    cities = nlu.CITIES
    ex = []

    def pick():
        r = rng.choice(cat)
        return r["name"].lower(), r["color"].lower(), r["size"]

    stock_t = ["{c} {p} {s} mein hai?", "{p} {c} size {s} available hai kya", "is the {c} {p} available in {s}?",
               "do you have {p} in {c}", "{p} ki price kya hai", "{p} kitne ka hai", "how much is the {p}",
               "{c} {p} stock mein hai?", "{p} {s} size milega?", "{p} ke colors kya hain",
               "what sizes does the {p} come in", "price of {p} in {c}", "{c} {p} ka rate",
               "is {p} {c} {s} in stock?", "bhai {c} {p} {s} hai?", "{p} available?"]
    order_t = ["{q} {c} {p} size {s} chahiye, order karna hai", "{c} {p} {s} order kar do",
               "i want to order {q} {c} {p} in size {s}", "mujhe {q} {p} {c} chahiye",
               "{p} {c} {s} bhej do", "please order {p} {c} size {s}", "{q} pieces {c} {p} {s} chahiye",
               "book {q} {p} for me", "{p} lena hai {c} {s}", "{q} {p} mangwane hain"]
    for _ in range(140):
        p, c, s = pick()
        ex.append((rng.choice(stock_t).format(p=p, c=c, s=s), "check_stock"))
    for _ in range(120):
        p, c, s = pick()
        q = rng.choice(["1", "2", "3", "ek", "do", "teen", "4"])
        ex.append((rng.choice(order_t).format(p=p, c=c, s=s, q=q), "place_order"))
    for o in orders:
        oid, ph = o["order_id"], o["phone"]
        ex.append((rng.choice(["order {o} ka status batao", "where is order {o}", "order number {o} kahan hai",
                               "track {o}", "my order {o} phone {ph}", "order {o} {ph}", "{o} status",
                               "mera order {o} kab aayega"]).format(o=oid, ph=ph), "order_status"))
        ex.append((rng.choice(["order {o} exchange karna hai", "exchange order {o} size L",
                               "i want to exchange order {o}", "{o} ka size change karna hai exchange"]
                              ).format(o=oid), "exchange"))
    for _ in range(60):
        b = rng.choice([1500, 2000, 2500, 3000, 3500, 4000, 5000, 8000])
        cat_word = rng.choice(["kurta", "kurti", "shoes", "suit", "shawl", "hoodie", "sweater", "abaya", "kuch", "something", "dress"])
        ex.append((rng.choice(["{b} tak ka {k} dikhao", "budget {b} hai {k} suggest karo", "{k} under {b}",
                               "suggest a {k} under rs {b}", "{b} ke around {k} chahiye koi acha",
                               "recommend {k} within {b}", "best {k} for {b}"]).format(b=b, k=cat_word), "recommend"))
    for _ in range(60):
        cty = rng.choice(cities)
        ex.append((rng.choice(["{c} mein delivery kitne din mein", "delivery charges to {c}", "{c} ka delivery charge kitna hai",
                               "do you deliver to {c}", "{c} mein cod hai?", "how long to {c}", "{c} delivery time",
                               "shipping to {c} cost"]).format(c=cty), "policy_question"))
    for _ in range(70):
        nm, st, cty = rng.choice(NAMES), rng.choice(STREETS), rng.choice(rules["delivery"]["tier_a_cities"] + ["Karachi", "Multan"])
        ph = "03" + "".join(rng.choice("0123456789") for _ in range(9))
        ex.append((rng.choice(["{n}, {p}, {s}, {c}", "Mera naam {n} hai, {p}, {s} {c}", "naam: {n}\nphone: {p}\naddress: {s}, {c}",
                               "{s}, {c} - {n} - {p}", "my name is {n}, phone {p}, address {s} {c}", "{n} {p} {s} {c}",
                               "{p}", "{n}", "{s} {c}", "mera number {p} hai"]).format(n=nm, p=ph, s=st, c=cty),
                   "provide_details"))
    return ex


def stratified_split(examples, frac, rng):
    by = defaultdict(list)
    for e in examples:
        by[e[1]].append(e)
    train, held = [], []
    for lab, items in by.items():
        rng.shuffle(items)
        k = max(1, int(len(items) * frac)) if len(items) >= 5 else 0
        held += items[:k]
        train += items[k:]
    return train, held


def report(model, examples):
    y, yhat = [e[1] for e in examples], [model.predict(e[0])[0] for e in examples]
    labels = sorted(set(y) | set(yhat))
    per = {}
    for lab in labels:
        tp = sum(1 for a, b in zip(y, yhat) if a == lab and b == lab)
        fp = sum(1 for a, b in zip(y, yhat) if a != lab and b == lab)
        fn = sum(1 for a, b in zip(y, yhat) if a == lab and b != lab)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        per[lab] = {"precision": round(p, 4), "recall": round(r, 4),
                    "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
                    "support": sum(1 for a in y if a == lab)}
    acc = sum(1 for a, b in zip(y, yhat) if a == b) / (len(y) or 1)
    macro = sum(v["f1"] for v in per.values() if v["support"]) / \
        (sum(1 for v in per.values() if v["support"]) or 1)
    errors = [{"text": e[0], "expected": a, "predicted": b}
              for e, a, b in zip(examples, y, yhat) if a != b]
    return {"n": len(y), "accuracy": round(acc, 4), "macro_f1": round(macro, 4),
            "per_intent": per, "errors": errors[:40]}


def main():
    rng = random.Random(42)
    seeds = json.loads((DATA / "intent_seeds.json").read_text(encoding="utf-8"))["intents"]
    seed_ex = [(t, lab) for lab, items in seeds.items() for t in items]
    gen = template_examples(rng)
    train, held = stratified_split(seed_ex + gen, 0.2, rng)
    dev = conv_examples("dev")
    test = conv_examples("test")
    t0 = time.time()
    model = IntentModel.train([t for t, _ in train + dev], [l for _, l in train + dev])
    secs = time.time() - t0
    held_r, test_r = report(model, held), report(model, test)
    # final model is refit on everything except the TEST conversations
    final = IntentModel.train([t for t, _ in train + held + dev],
                              [l for _, l in train + held + dev])
    counts = Counter(l for _, l in train + held + dev)
    final.meta = {"trained_on": sum(counts.values()), "classes": len(counts),
                  "per_intent_examples": dict(sorted(counts.items())),
                  "heldout_accuracy": held_r["accuracy"], "test_turn_accuracy": test_r["accuracy"],
                  "created": time.strftime("%Y-%m-%d %H:%M")}
    MODELS.mkdir(exist_ok=True)
    final.save(MODELS / "intent_model.json")
    metrics = {
        "model": "TF-IDF (word 1-2 grams + char 3-4 grams) + multinomial logistic regression, pure Python",
        "train_examples": len(train) + len(dev), "final_examples": sum(counts.values()),
        "classes": sorted(counts), "train_seconds": round(secs, 1),
        "heldout": held_r, "test_conversation_turns": test_r,
    }
    (MODELS / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    old = MODELS / "intent_model.pkl"
    if old.exists():
        old.unlink()
    print(f"Trained on {len(train) + len(dev)} examples, {len(counts)} intents ({secs:.1f}s)")
    print(f"Held-out accuracy:              {held_r['accuracy']:.4f}  (macro F1 {held_r['macro_f1']:.4f}, n={held_r['n']})")
    print(f"Test-conversation turn accuracy: {test_r['accuracy']:.4f}  (macro F1 {test_r['macro_f1']:.4f}, n={test_r['n']})")
    print(f"Saved models/intent_model.json ({(MODELS / 'intent_model.json').stat().st_size // 1024} KB) and models/metrics.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
