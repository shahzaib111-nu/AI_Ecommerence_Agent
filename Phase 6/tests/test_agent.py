"""Plain-python tests for the F26-07 agent (no pytest needed).

Run:  python tests/test_agent.py     or     python -m tests.test_agent
Each test gets a fresh temporary database, like the evaluation harness.
"""
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.data_store import Store          # noqa: E402
from agent.engine import Engine             # noqa: E402

PASSED = []


def fresh():
    td = tempfile.mkdtemp()
    store = Store(str(Path(td) / "test.db"), seed_admin=False)
    return store, Engine(store)


def check(name, cond, extra=""):
    if not cond:
        raise AssertionError(f"FAIL: {name} {extra}")
    PASSED.append(name)
    print(f"  ok  {name}")


def say(eng, sid, msgs):
    out = None
    for m in msgs:
        out = eng.handle(sid, m)
    return out


def actions(eng, sid, msgs):
    return [eng.handle(sid, m)["trace"]["action"] for m in msgs]


def conversation_regressions():
    """The 'stuck / same reply' bugs from the old engine, one check each."""
    # greetings and small talk are understood, not escalated
    store, eng = fresh()
    acts = actions(eng, "g", ["hi", "hello", "how are you", "what can you do", "thanks"])
    check("greetings/small talk never escalate", "escalate" not in acts, str(acts))
    r = eng.handle("g2", "hi")
    check("plain 'hi' gets an English reply", r["lang"] == "en" and "Welcome" in r["reply"], r["reply"])

    # a hand-off is NOT sticky: the customer can keep shopping afterwards
    store, eng = fresh()
    r1 = eng.handle("h", "I want a discount")
    r2 = eng.handle("h", "ok forget it, black linen kurta M available?")
    check("hand-off gets a ticket number", r1["trace"]["action"] == "escalate" and r1.get("ticket"), str(r1))
    check("agent keeps helping after a hand-off",
          r2["trace"]["action"] == "check_stock" and "12" in r2["reply"], r2["reply"])
    r3 = eng.handle("h", "I want a discount please")
    check("same reason does not open a duplicate ticket",
          r3.get("ticket") == r1.get("ticket") and len(store.list_escalations()) == 1)

    # replies vary instead of repeating the same sentence
    store, eng = fresh()
    replies = {eng.handle("v", m)["reply"] for m in ["thanks", "thank you", "shukriya"]}
    check("thanks replies vary", len(replies) >= 2, str(replies))

    # follow-up questions use the product in context
    store, eng = fresh()
    eng.handle("f", "Is the black linen kurta available in medium?")
    r = eng.handle("f", "What about large?")
    check("follow-up 'what about large' checks Linen Kurta Black L",
          r["trace"]["action"] == "check_stock" and r["trace"]["tools"][0]["result"].get("sku") == "SKU0003",
          str(r["trace"]))

    # details may arrive one piece at a time
    store, eng = fresh()
    seq = ["order black linen kurta M", "my name is Sara", "03211234567",
           "house 3 block B DHA Lahore", "confirm"]
    acts = actions(eng, "d", seq)
    check("piecemeal details -> summary -> order",
          acts[-2] == "request_confirmation" and acts[-1] == "create_order", str(acts))

    # the summary can be edited before confirming
    store, eng = fresh()
    for m in ["black linen kurta L order kar do", "Ali, 03001112233, house 5 street 9 Gulberg Lahore"]:
        eng.handle("e", m)
    r = eng.handle("e", "size M kar do")
    check("editing size at the summary re-prices the order",
          r["trace"]["action"] == "request_confirmation" and "Black, M" in r["reply"], r["reply"])
    r = eng.handle("e", "what is the exchange policy?")
    check("policy question mid-confirmation is answered, summary kept",
          r["trace"]["action"] == "answer_policy" and eng.sessions["e"]["stage"] == "confirm", r["reply"])
    check("then 'yes' places the order", eng.handle("e", "yes")["trace"]["action"] == "create_order")

    # browsing by type returns product cards, not one random product
    store, eng = fresh()
    r = eng.handle("b", "show me kurtas")
    check("browse kurtas lists several kurtas",
          r["trace"]["action"] == "browse" and len(r["products"]) >= 4, str(r["trace"]))
    r = eng.handle("b2", "women kurti under 2500")
    check("recommendation respects budget and category",
          r["trace"]["action"] == "recommend" and r["products"]
          and all(p["price"] <= 2500 and "Kurti" in p["name"] for p in r["products"]), str(r["products"]))

    # policy answers are city-aware and grounded in policy_rules.json
    store, eng = fresh()
    r = eng.handle("p", "Bahawalpur ka delivery charge kitna hai?")
    check("non-major city delivery charge is Rs 350",
          r["trace"]["action"] == "answer_policy" and "350" in r["reply"], r["reply"])
    r = eng.handle("p2", "do you deliver to karachi?")
    check("English question gets an English answer", r["lang"] == "en" and "Karachi" in r["reply"], r["reply"])

    # privacy: wrong phone leaks nothing even with both in one message
    store, eng = fresh()
    r = eng.handle("x", "order 10231 status, phone 03459998888")
    check("one-message status with wrong phone is blocked",
          r["trace"]["action"] == "deny_status_mismatch" and "TCS" not in r["reply"], r["reply"])


def model_and_checkout_checks():
    store, eng = fresh()
    check("intent model loads from JSON", eng.ml_model is not None)
    if eng.ml_model:
        check("model: greeting", eng.ml_model.predict("assalam o alaikum")[0] == "greeting")
        check("model: order status", eng.ml_model.predict("mera parcel kab aayega")[0] == "order_status")
    q = eng.quote([{"sku": "SKU0002", "qty": 2}], "Lahore")
    check("web quote: 2 x 2499 + 250", q["grand_total"] == 5248 and not q["problems"], str(q))
    r = eng.checkout("w", [{"sku": "SKU0002", "qty": 2}],
                     {"name": "Web User", "phone": "0300-1234567", "address": "house 1 street 2", "city": "Lahore"})
    check("web checkout creates an order and takes stock",
          r["ok"] and store.available("SKU0002") == 10, str(r))
    r = eng.checkout("w", [{"sku": "SKU0002", "qty": 1}], {"name": "", "phone": "123", "address": "", "city": ""})
    check("web checkout validates fields", not r["ok"] and {"name", "phone"} <= set(r["errors"]), str(r))
    r = eng.checkout("w", [{"sku": "SKU0003", "qty": 5}],
                     {"name": "A B", "phone": "03001234567", "address": "house 1 street 2", "city": "Lahore"})
    check("web checkout refuses more than in stock", not r["ok"], str(r))


def main():
    # C001 — stock check: Black Linen Kurta M = 12 available, Rs 2499
    store, eng = fresh()
    r = say(eng, "t1", ["Salam, black linen kurta M mein available hai?"])
    tr = r["trace"]
    check("C001 action check_stock", tr["action"] == "check_stock", str(tr))
    tool = tr["tools"][0]["result"]
    check("C001 available 12 / price 2499",
          tool["available"] == 12 and tool["price"] == 2499, str(tool))
    check("C001 reply mentions 12 and 2499",
          "12" in r["reply"] and "2499" in r["reply"].replace(",", ""), r["reply"])

    # C003 — out of stock White Cotton Kurta M, alternatives offered
    store, eng = fresh()
    r = say(eng, "t2", ["White cotton kurta medium chahiye, hai?"])
    # 'chahiye' routes to order path in some phrasings; stock answer must
    # still say out of stock with alternatives via check_stock tool
    replies = r["reply"]
    check("C003 out of stock mentioned", "out of stock" in replies.lower()
          or "0 available" in replies, replies)
    store2, eng2 = fresh()
    r2 = eng2.handle("t2b", "White cotton kurta M available hai?")
    tool = r2["trace"]["tools"][0]["result"]
    check("C003 tool: 0 available + alternatives",
          tool["available"] == 0 and len(tool.get("alternatives", [])) > 0,
          str(tool))

    # C007 — full order flow: 2x Black Linen Kurta M, Lahore, 4998+250=5248
    store, eng = fresh()
    say(eng, "t3", ["Black linen kurta M mein hai?"])
    say(eng, "t3", ["2 order kar do"])
    r = say(eng, "t3", ["Mera naam Ali Raza hai, 03001234567, house 12 street 4 Model Town Lahore"])
    check("C007 summary before confirm",
          r["trace"]["action"] == "request_confirmation"
          and "5248" in r["reply"].replace(",", ""), r["reply"])
    r = say(eng, "t3", ["Haan confirm"])
    check("C007 order created", r["trace"]["action"] == "create_order", str(r["trace"]))
    check("C007 stock dropped by 2",
          store.available("SKU0002") == 10, str(store.available("SKU0002")))

    # C012 — order status with correct phone: 10231 Shipped, TCS884201
    store, eng = fresh()
    say(eng, "t4", ["Mera order kahan hai?"])
    say(eng, "t4", ["order number 10231"])
    r = say(eng, "t4", ["03001234567"])
    check("C012 status Shipped + tracking",
          r["trace"]["action"] == "get_order_status"
          and "Shipped" in r["reply"] and "TCS884201" in r["reply"], r["reply"])

    # C013 — wrong phone: deny, leak nothing
    store, eng = fresh()
    say(eng, "t5", ["order 10231 ka status batao"])
    r = say(eng, "t5", ["mera number 03459998888 hai"])
    check("C013 denied on phone mismatch",
          r["trace"]["action"] == "deny_status_mismatch", str(r["trace"]))
    check("C013 no details leaked",
          "TCS884201" not in r["reply"] and "Shipped" not in r["reply"], r["reply"])

    # C014 — exchange eligible: order 10198 -> replacement SKU0303
    store, eng = fresh()
    say(eng, "t6", ["Mujhe exchange karna hai, size chota hai"])
    say(eng, "t6", ["order 10198"])
    say(eng, "t6", ["03214567890"])
    r = say(eng, "t6", ["L size chahiye"])
    tool = r["trace"]["tools"][0]["result"] if r["trace"]["tools"] else {}
    check("C014 exchange created with SKU0303",
          r["trace"]["action"] == "create_exchange"
          and tool.get("replacement_sku") == "SKU0303", str(r))

    # C015 — sale item exchange escalates
    store, eng = fresh()
    say(eng, "t7", ["order 10120 ka exchange karna hai"])
    say(eng, "t7", ["03335551234"])
    r = say(eng, "t7", ["L size chahiye"])
    check("C015 sale item escalates",
          r["trace"]["action"] == "escalate"
          and r["trace"]["reason"] == "sale_item_not_exchangeable", str(r["trace"]))

    # Refund always escalates
    store, eng = fresh()
    r = say(eng, "t8", ["Mujhe refund chahiye, paisay wapas karo"])
    check("refund escalates", r["trace"]["action"] == "escalate"
          and r["trace"]["decision"] == "HANDOFF", str(r["trace"]))
    check("refund queued", len(store.list_escalations()) == 1)

    # Guardrails: quantity > 10 and COD total > 25000
    store, eng = fresh()
    r = say(eng, "t9", ["Polo tshirt black M 25 pieces chahiye shop ke liye"])
    check("qty>10 escalates", r["trace"]["action"] == "escalate", str(r["trace"]))
    store, eng = fresh()
    say(eng, "t10", ["Black linen kurta M 10 order kar do"])
    say(eng, "t10", ["Mera naam Test User hai, 03009998877, house 1 street 2 Lahore"])
    r = eng.handle("t10", "haan confirm")  # 10x2499+0 delivery = 24990 -> ok
    check("qty=10 within COD limit creates order",
          r["trace"]["action"] == "create_order", str(r))

    # Policy via RAG: Lahore delivery = Rs 250
    store, eng = fresh()
    r = say(eng, "t11", ["Lahore mein COD hai? delivery charges kitne honge?"])
    check("policy Lahore charge 250 in reply",
          r["trace"]["action"] == "answer_policy" and "250" in r["reply"],
          r["reply"])

    # Out-of-policy question is gated, not answered
    store, eng = fresh()
    r = say(eng, "t12", ["Kya ye kapra dry clean ho sakta hai?"])
    check("out-of-policy gated/escalated",
          r["trace"]["action"] == "escalate"
          and r["trace"]["reason"] == "outside_policy", str(r["trace"]))

    # Contention: parallel reservations never oversell (SKU0003 has stock 1)
    store, eng = fresh()
    results = []

    def buy(i):
        results.append(store.reserve(f"race-{i}", [("SKU0003", 1)], minutes=15))
    threads = [threading.Thread(target=buy, args=(i,)) for i in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    check("contention: exactly 1 of 6 wins the last unit",
          sum(1 for x in results if x) == 1, str(results))
    check("contention: available never negative",
          store.available("SKU0003") == 0, str(store.available("SKU0003")))

    conversation_regressions()
    model_and_checkout_checks()

    print(f"\nAll {len(PASSED)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
