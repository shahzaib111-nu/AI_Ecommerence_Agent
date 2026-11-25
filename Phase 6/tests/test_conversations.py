"""Conversation test suite: ~100 realistic chats + a robustness fuzz test.

Run:  python tests/test_conversations.py

Each case is a list of customer messages and a check on the LAST reply:
  act      expected trace action (or a set of acceptable actions)
  has      substrings the reply must contain (commas in numbers are ignored)
  not_has  substrings the reply must NOT contain
  lang     'en' / 'ur' reply language
  prod     product the agent should be talking about (via trace/products)
  no_esc   no hand-off anywhere in the conversation
Each case gets a fresh in-memory database.
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.data_store import Store   # noqa: E402
from agent.engine import Engine      # noqa: E402

ACTIONS = {"check_stock", "request_missing_info", "request_confirmation", "create_order", "get_order_status",
           "deny_status_mismatch", "create_exchange", "escalate", "answer_policy", "recommend", "browse",
           "greet", "smalltalk", "help"}

CASES = [
    # ------------------------------------------------------------ social
    ("greet en", ["hi"], dict(act="greet", lang="en")),
    ("greet ur", ["assalam o alaikum"], dict(act="greet", has=["Wa alaikum assalam"])),
    ("greet twice", ["hello", "hello?"], dict(act="greet", no_esc=True)),
    ("how are you", ["how are you"], dict(act="smalltalk", has=["great"])),
    ("kaise ho", ["kaise ho bhai"], dict(act={"smalltalk", "greet"})),
    ("who are you", ["who are you?"], dict(act="smalltalk", has=["assistant"])),
    ("are you a bot", ["are you a bot"], dict(act="smalltalk")),
    ("help", ["what can you do"], dict(act="help")),
    ("thanks", ["thanks"], dict(act="smalltalk")),
    ("shukriya", ["shukriya"], dict(act="smalltalk")),
    ("bye", ["allah hafiz"], dict(act="smalltalk")),
    ("social never escalates", ["hi", "hello", "how are you", "who are you", "thanks", "ok", "bye"], dict(no_esc=True)),
    # ------------------------------------------------------ out of scope
    ("cake", ["i want chocolate cake"], dict(act="smalltalk", has=["clothing"], no_esc=True)),
    ("bhuk", ["bhuk lagi hai"], dict(act="smalltalk", not_has=["Handbag"], no_esc=True)),
    ("bhuk after product", ["handbag tote price", "bhuk lagi hai"], dict(act="smalltalk", not_has=["Handbag"])),
    ("cricket", ["cricket score kya hai"], dict(act="smalltalk", no_esc=True)),
    ("weather", ["how is the weather in lahore"], dict(act="smalltalk")),
    ("laptop", ["do you sell laptops"], dict(act="smalltalk")),
    ("repeated off-topic never escalates", ["pizza", "biryani chahiye", "movie dikhao"], dict(no_esc=True)),
    # ------------------------------------------------------------ browse
    ("show kurtas", ["show me kurtas"], dict(act="browse", has=["Linen Kurta", "Cotton Kurta"])),
    ("kurtay dikhao", ["kurtay dikhao"], dict(act="browse", lang="ur")),
    ("men kurtas", ["can you show me men kurtas"], dict(act="browse", not_has=["Kids Kurta"])),
    ("shoes", ["what shoes do you have"], dict(act="browse", has=["Khussa"])),
    ("abayas", ["show me abayas"], dict(act="browse", has=["Abaya Classic", "Embroidered Abaya"])),
    ("winter women", ["winter collection for women"], dict(act={"browse", "recommend"})),
    ("kids", ["bachon ke kapre dikhao"], dict(act="browse", has=["Kids"])),
    ("sale", ["sale items dikhao"], dict(act="browse")),
    ("categories", ["what do you sell?"], dict(act="browse", has=["categories"])),
    ("jeans", ["jeans hain?"], dict(act={"browse", "check_stock"}, has=["Jeans"])),
    # --------------------------------------------------------- recommend
    ("under 3000", ["something under 3000"], dict(act="recommend")),
    ("kurti 2500", ["women kurti under 2500"], dict(act="recommend", has=["Kurti"])),
    ("around 2000 kurta then men", ["Mujhe 2000 ke around koi acha kurta chahiye", "Men ke liye hai"], dict(act="recommend")),
    ("budget too low", ["shawl under 500"], dict(act="recommend", has=["closest"])),
    ("vague wants something", ["I need something nice"], dict(act="request_missing_info")),
    ("show more", ["3000 tak ka shawl ya cardigan dikhao", "aur dikhao"], dict(act="recommend")),
    ("wedding", ["shaadi ke liye kuch dikhao"], dict(act={"browse", "recommend"})),
    # ------------------------------------------------------------- stock
    ("exact stock", ["Is the black linen kurta available in medium?"], dict(act="check_stock", has=["12", "2499"], lang="en")),
    ("roman urdu stock", ["bhai blk linen kurta mdm mein hai kya? price kya hai?"], dict(act="check_stock", has=["12"], lang="ur")),
    ("typo kurtta", ["black linen kurtta M available?"], dict(act="check_stock", prod="Linen Kurta")),
    ("price only", ["linen kurta price?"], dict(act="check_stock", has=["2499", "Black"])),
    ("khussa price", ["price of khussa"], dict(act="check_stock", prod="Khussa Embroidered")),
    ("color only", ["navy linen kurta"], dict(act="check_stock", has=["Navy"])),
    ("out of stock alts", ["White cotton kurta M available hai?"], dict(act="check_stock", has=["out of stock", "alternatives"])),
    ("invalid size", ["linen kurta size 44"], dict(act="check_stock", has=["doesn't come"])),
    ("invalid color", ["red linen kurta M?"], dict(act="check_stock", has=["Red"])),
    ("followup large", ["Is the black linen kurta available in medium?", "What about large?"], dict(act="check_stock", has=["size L"])),
    ("followup color ur", ["linen kurta M hai?", "white mein?"], dict(act="check_stock", has=["White"])),
    ("was != wash", ["show me kurtas", "i was talking about kurta"], dict(act="browse", not_has=["Which size of Wash"])),
    ("abaya classic exact", ["abaya classic black 54 price?"], dict(act="check_stock", prod="Abaya Classic")),
    ("lawn 3 piece", ["sky blue lawn 3-piece stitched L ka kya rate hai?"], dict(act="check_stock", prod="Lawn 3-Piece Stitched")),
    # ------------------------------------------------------------- orders
    ("one-shot order", ["black linen kurta M order kar do", "Ali Raza, 03001234567, house 12 street 4 Model Town Lahore", "haan"],
     dict(act="create_order", has=["order number"])),
    ("piecemeal details", ["order black linen kurta M", "my name is Sara", "03211234567", "house 3 block B DHA Lahore", "confirm"],
     dict(act="create_order")),
    ("labelled block", ["2 navy blue khussa embroidered size 39 chahiye", "naam: Kinza Javed\nphone: 03207207547\naddress: house 36 street 17, I-8, Islamabad", "ji sahi hai"],
     dict(act="create_order")),
    ("address-name-phone order", ["teen navy khussa embroidered size 37 chahiye, order karna hai", "house 90 street 9, Latifabad, Hyderabad - Rabia Anwar - 03436550620", "yes confirm"],
     dict(act="create_order")),
    ("same + extra color", ["sky blue lawn 3-piece stitched L ka kya rate hai?", "2 same bhej do aur ek mint", "naam: Kinza\nphone: 03207207547\naddress: house 36 street 17, I-8, Islamabad"],
     dict(act="request_confirmation", has=["Sky Blue", "Mint"])),
    ("buy after stock", ["Is the black linen kurta available in medium?", "ok I want to buy it"], dict(act="request_missing_info", has=["name"])),
    ("ask size", ["I want to order the polo t-shirt"], dict(act="request_missing_info", has=["size"])),
    ("ask color after size", ["I want to order the polo t-shirt", "L"], dict(act="request_missing_info", has=["color"])),
    ("pick from list", ["show me kurtas", "i wanna buy each of every one"], dict(act="request_missing_info", has=["one at a time"], not_has=["Handbag"])),
    ("pick then product", ["show me kurtas", "i want to buy one", "Linen Kurta"], dict(act="request_missing_info", has=["size"])),
    ("edit size at summary", ["black linen kurta L order kar do", "Ali, 03001112233, house 5 street 9 Gulberg Lahore", "size M kar do"],
     dict(act="request_confirmation", has=["Black, M"])),
    ("policy mid-confirm", ["black linen kurta M order kar do", "Ali, 03001112233, house 5 street 9 Gulberg Lahore", "what is the exchange policy?"],
     dict(act="answer_policy", has=["still waiting"])),
    ("cancel cart", ["black linen kurta M order kar do", "Ali, 03001112233, house 5 street 9 Gulberg Lahore", "cancel"],
     dict(act="request_missing_info", has=["cancelled"], no_esc=True)),
    ("qty typo above limit at summary", ["black linen kurta M order", "Ali, 03001112233, house 5 street 9 Lahore", "mujhe 50 cahiye"],
     dict(act="escalate", has=["10"])),
    ("qty above limit", ["Polo tshirt black M 25 pieces chahiye shop ke liye"], dict(act="escalate")),
    ("cod above limit", ["Black chiffon party wear M 4 chahiye", "Nida Rauf 03041112222 DHA phase 5 Lahore"], dict(act="escalate", has=["25,000"])),
    ("invalid phone", ["black linen kurta M order", "abdullah naam hai 03000000 phone and house 55 b Johar Town Lahore"],
     dict(act="request_missing_info", has=["03001234567"])),
    ("phone words not address", ["black linen kurta M order", "Lahore", "name abdullah", "phone number 20000000"],
     dict(act="request_missing_info", has=["03001234567"], not_has=["address: phone"])),
    ("name parsing", ["black linen kurta M order", "name abdullah"], dict(has=["Abdullah"], not_has=["Name Abdullah"])),
    ("oos order offers alts", ["white cotton kurta M order kar do"], dict(act="check_stock", has=["out of stock"])),
    ("free delivery over 10k", ["5 black linen kurta M order kar do", "Ali, 03001112233, house 5 street 9 Gulberg Lahore"],
     dict(act="request_confirmation", has=["FREE"])),
    ("karachi delivery 350", ["black linen kurta M order kar do", "Zain, 03331234567, flat 2 block 5 Clifton, Karachi"],
     dict(act="request_confirmation", has=["350"])),
    ("question mid-details", ["I want to order the polo t-shirt", "black", "L", "do you deliver to karachi?"],
     dict(act="answer_policy", has=["Karachi"])),
    ("switch product type", ["linen kurta M order", "actually show me shoes"], dict(act="browse", has=["Khussa"])),
    # ------------------------------------------------------------- status
    ("status flow", ["Mera order kahan hai?", "order number 10231", "03001234567"], dict(act="get_order_status", has=["Shipped", "TCS884201"])),
    ("status one msg", ["track order 10231 phone 03001234567"], dict(act="get_order_status", has=["Shipped"])),
    ("status wrong phone", ["order 10231 ka status batao", "mera number 03459998888 hai"], dict(act="deny_status_mismatch", not_has=["TCS884201", "Shipped"])),
    ("status not found", ["order 99902 kahan hai? number 0306-3540115"], dict(act="escalate")),
    ("parcel days != status", ["Rawalpindi parcel kitne din mein pohanchay ga?"], dict(act="answer_policy", has=["2-4"])),
    # ----------------------------------------------------------- exchange
    ("exchange ok", ["Mujhe exchange karna hai, size chota hai", "order 10198", "03214567890", "L size chahiye"], dict(act="create_exchange")),
    ("exchange sale", ["order 10120 ka exchange karna hai", "03335551234", "L size chahiye"], dict(act="escalate", has=["sale"])),
    ("exchange late", ["Maine 12 din pehle suit liya tha, size exchange karna hai", "order 10044, phone 03451239876", "L chahiye"], dict(act="escalate", has=["7"])),
    ("exchange policy q", ["Exchange policy kya hai?"], dict(act="answer_policy", has=["7"])),
    ("sale exchange q", ["sale ka suit exchange hoga?"], dict(act="answer_policy", has=["final sale"])),
    # -------------------------------------------------------------- policy
    ("lahore cod", ["Lahore mein COD hai? delivery charges kitne honge?"], dict(act="answer_policy", has=["250"])),
    ("bahawalpur", ["Bahawalpur ka delivery charge kitna hai?"], dict(act="answer_policy", has=["350"])),
    ("abroad", ["do you ship abroad"], dict(act="answer_policy", has=["only deliver within Pakistan"])),
    ("showroom", ["Aapka showroom kahan hai? main aa kar dekhna chahta hun"], dict(act="answer_policy", has=["online"])),
    ("hours", ["staff kab tak reply karta hai?"], dict(act="answer_policy")),
    ("refund policy question", ["what is your refund policy?"], dict(act="answer_policy", no_esc=True)),
    ("free delivery", ["free delivery kab hoti hai"], dict(act="answer_policy", has=["10000"])),
    ("how to order", ["how do I order?"], dict(act="answer_policy")),
    ("delivery time en", ["how long does delivery take to karachi?"], dict(act="answer_policy", has=["4-6"], lang="en")),
    # ---------------------------------------------------------- guardrails
    ("refund", ["Mujhe refund chahiye, paisay wapas karo"], dict(act="escalate")),
    ("damaged", ["my kurta arrived torn"], dict(act="escalate", has=["photos"])),
    ("angry", ["bakwas service hai, fraud ho tum"], dict(act="escalate")),
    ("legal", ["main consumer court jaunga"], dict(act="escalate")),
    ("human", ["mujhe kisi insan se baat karni hai"], dict(act="escalate")),
    ("discount", ["discount do na"], dict(act="escalate", has=["fixed"])),
    ("last price", ["navy khussa embroidered size 38 last price kya hoga?"], dict(act="escalate")),
    ("jazzcash", ["JazzCash par advance payment kar sakti hun?"], dict(act="escalate", has=["COD"])),
    ("outside policy", ["Kya ye kapra dry clean ho sakta hai?"], dict(act="escalate")),
    ("gift wrap", ["gift wrapping aur message card ho jayega?"], dict(act="escalate")),
    ("ticket followup", ["Mera shawl phat gaya hai aur mujhe refund chahiye", "order 10150"], dict(act="escalate", has=["10150"])),
    ("keep shopping after handoff", ["I want a discount", "black linen kurta M available?"], dict(act="check_stock", has=["12"])),
    ("no duplicate tickets", ["I want a discount", "discount please"], dict(act="escalate", has=["already"])),
    # ------------------------------------------------------------- unclear
    ("unclear twice", ["hello", "kuch chahiye tha", "pata nahi yaar"], dict(act="escalate")),
    ("gibberish once", ["asdfgh"], dict(act="request_missing_info", no_esc=True)),
    ("unclear then clear", ["asdf", "black linen kurta M"], dict(act="check_stock")),
    ("empty-ish", ["?"], dict(act={"help", "request_missing_info", "answer_policy", "escalate"})),
    # ------------------------------------------- round 2: unseen phrasings
    ("size chart", ["size chart kahan hai"], dict(act="answer_policy", has=["S, M, L, XL"], no_esc=True)),
    ("which size", ["konsa size lun"], dict(act="answer_policy", no_esc=True)),
    ("xxl", ["XXL available hai?"], dict(act="answer_policy", has=["XL"], no_esc=True)),
    ("plus size", ["plus size kapre"], dict(act="answer_policy", has=["XL"])),
    ("fabric question", ["is the linen kurta cotton?"], dict(has=["linen"], prod="Linen Kurta")),
    ("material polo", ["what material is the polo shirt"], dict(has=["Cotton pique"])),
    ("quality no product", ["quality kaisi hai"], dict(act="request_missing_info", no_esc=True)),
    ("cheapest kurta", ["sabse sasta kurta konsa hai"], dict(act="recommend", has=["Wash & Wear Kurta"])),
    ("most expensive suit", ["most expensive suit"], dict(act="recommend", has=["Organza Formal Suit"])),
    ("best seller", ["best selling product"], dict(act="recommend")),
    ("eid kurta", ["eid ke liye kurta"], dict(act="browse", not_has=["Khussa", "Sandals"])),
    ("husband no handbag", ["husband ke liye kuch"], dict(not_has=["Handbag"])),
    ("formal shirt", ["formal shirt for office"], dict(not_has=["Organza"])),
    ("age to size", ["kids ke liye 4 saal ki beti ke liye frock"], dict(act="check_stock", has=["4-5Y"])),
    ("shoe 41 invalid", ["khussa 41 mein hai?"], dict(act="check_stock", has=["41"])),
    ("shoe 42 none", ["jootay size 42"], dict(act="browse", has=["42"])),
    ("belt 36", ["belt 36"], dict(act="check_stock", has=["36"])),
    ("abaya 54 navy", ["abaya 54 navy"], dict(act="check_stock", has=["Navy", "54"])),
    ("blue kurta close shades", ["blue kurta"], dict(act="browse", has=["Navy"])),
    ("kurta size M", ["kurta M chahiye"], dict(act="browse", has=["M"])),
    ("discount code info", ["discount code hai?"], dict(act="answer_policy", no_esc=True)),
    ("can i return info", ["can i return"], dict(act="answer_policy", no_esc=True)),
    ("contact", ["whatsapp number"], dict(act="answer_policy", no_esc=True)),
    ("kal tak", ["kal tak mil jayega?"], dict(act="answer_policy", has=["Exact date"])),
    ("compliment", ["bohat acha service hai"], dict(act="smalltalk", no_esc=True)),
    ("love store", ["i love this store"], dict(act="smalltalk", no_esc=True)),
    ("price alone no esc", ["price"], dict(act="request_missing_info", no_esc=True)),
    ("wrong size received", ["wrong size aa gaya"], dict(act="escalate")),
    ("address wrong", ["mera address galat hai"], dict(act="escalate")),
    ("manager", ["manager ko bulao"], dict(act="escalate")),
    ("dont want order", ["mujhe order nahi chahiye ab"], dict(act="escalate")),
    ("forgot order no", ["I forgot my order number"], dict(act="request_missing_info", has=["My orders"])),
    ("forgot in status", ["track my order", "i forgot"], dict(has=["My orders"])),
    ("owner", ["owner kaun hai"], dict(act="smalltalk", has=["Eman Fatima"])),
    ("fillers", ["hmm", "acha", "ek minute", "lol", "wait"], dict(act="smalltalk", no_esc=True)),
    ("emoji", ["👍"], dict(act="smalltalk")),
    ("restart", ["black linen kurta M order", "start over"], dict(act="smalltalk", has=["Fresh start"])),
    ("single color", ["black"], dict(act="browse", has=["Black"])),
    ("name token", ["linen"], dict(act="browse", has=["Linen Kurta", "Linen Kurti"])),
    ("next no list", ["next"], dict(act="recommend")),
    ("yes order it", ["yes order it"], dict(act="request_missing_info", has=["Which product"])),
    ("multi item", ["2 black linen kurta M aur 1 grey L chahiye", "Ali, 03001112233, house 5 street 9 Lahore"],
     dict(act="request_confirmation", has=["Black, M × 2", "Grey, L × 1"])),
    ("3 kurte pending qty", ["mujhe 3 kurte chahiye", "Linen Kurta", "black M"], dict(act="request_missing_info", has=["naam"])),
    ("goodbye loose", ["chalo phir baat hoti hai"], dict(act="smalltalk", has=["Allah Hafiz"])),
    ("kya scene", ["kya scene hai"], dict(act="smalltalk", not_has=["AI shopping assistant"])),
    ("confused", ["mujhe samajh nahi aa raha"], dict(act="help")),
    ("peshawar charge", ["peshawar ka charge"], dict(act="answer_policy", has=["350"])),
    ("cod other option", ["COD ke ilawa koi option"], dict(act="answer_policy")),
    ("order+phone one msg", ["order 10044 03451239876"], dict(act="get_order_status", has=["Delivered"])),
    ("late order", ["bohat late ho gaya order"], dict(act="request_missing_info", has=["order number"])),
    ("qty after stock accepts offer", ["printed kurti", "pink M", "2", "Hina Shah 03215557788 house 9 street 3 Satellite Town Sargodha", "theek hai confirm"],
     dict(act="create_order")),
    ("size answer not qty", ["I want to buy jeans", "32", "black", "Usman, 03001231231, flat 3 block A F-10 Islamabad", "actually make it 2", "yes"],
     dict(act="create_order", has=["6648"])),
    ("chappal number", ["peshawari chappal 10 number"], dict(act="check_stock", has=["10"])),
    ("kids hoodie age", ["grey hoodie for my son 8 years"], dict(act="check_stock", has=["8-9Y"])),
    ("salam once", ["assalam o alaikum kya haal hai"], dict(not_has=["Wa alaikum assalam! Wa alaikum"])),
    # ---------------------------------------------------------- language
    ("english stays english", ["do you deliver to karachi?"], dict(lang="en")),
    ("urdu stays urdu", ["mera order kahan hai"], dict(lang="ur")),
]


def run_case(name, msgs, exp):
    st = Store(":memory:", seed_admin=False)
    eng = Engine(st)
    sid = "t-" + name
    outs = [eng.handle(sid, m) for m in msgs]
    r = outs[-1]
    tr = r["trace"]
    rep = r["reply"]
    flat = rep.replace(",", "")
    problems = []
    act = exp.get("act")
    if act:
        ok = tr["action"] in act if isinstance(act, set) else tr["action"] == act
        if not ok:
            problems.append(f"action {tr['action']!r} != {act!r}")
    for h in exp.get("has", []):
        if h not in rep and h.replace(",", "") not in flat:
            problems.append(f"missing {h!r}")
    for h in exp.get("not_has", []):
        if h in rep:
            problems.append(f"should not contain {h!r}")
    if exp.get("lang") and r["lang"] != exp["lang"]:
        problems.append(f"lang {r['lang']} != {exp['lang']}")
    if exp.get("prod"):
        names = [p.get("name") for p in r["products"]] + \
            [str(t.get("result", {}).get("product")) for t in tr["tools"]]
        if exp["prod"] not in names:
            problems.append(f"product {exp['prod']!r} not in {names}")
    if exp.get("no_esc") and any(o["trace"]["action"] == "escalate" for o in outs):
        problems.append("escalated but should not")
    st.close()
    return problems, rep, tr


VOCAB = ("hi hello salam kurta kurti linen black white navy M L XL size price order bhej do chahiye "
         "kya hai available? delivery lahore karachi 10231 03001234567 refund exchange status track "
         "show me dikhao under 3000 budget yes no haan nahi cancel thanks bye ??? !!! cake pizza 😀 "
         "house 12 street 4 khussa abaya shawl winter women men kids sale 2 3 50 0 -1 asdf qwerty "
         "मुझे کرتا <script>alert(1)</script> ' \" ; DROP TABLE orders; --").split()


def fuzz(n_sessions=150, turns=12, seed=7):
    rng = random.Random(seed)
    st = Store(":memory:", seed_admin=False)
    eng = Engine(st)
    count, bad = 0, []
    for i in range(n_sessions):
        for _ in range(turns):
            k = rng.randint(0, 8)
            msg = " ".join(rng.choice(VOCAB) for _ in range(k))
            if rng.random() < 0.05:
                msg = "x" * rng.randint(1500, 2500)
            try:
                r = eng.handle(f"fz{i}", msg)
                count += 1
                assert isinstance(r["reply"], str) and r["reply"].strip(), "empty reply"
                assert r["trace"]["action"] in ACTIONS, f"unknown action {r['trace']['action']}"
                assert r["trace"]["decision"] in ("ACT", "ASK", "BLOCK", "HANDOFF")
            except Exception as ex:          # noqa: BLE001
                bad.append(f"{msg[:60]!r}: {type(ex).__name__}: {ex}")
    # the catalog must never go negative, whatever was said
    neg = st.conn.execute("SELECT COUNT(*) c FROM catalog WHERE stock<0").fetchone()["c"]
    st.close()
    return count, bad, neg


def store_feature_checks():
    st = Store(":memory:", seed_admin=False)
    eng = Engine(st)
    probs = []
    v = st.update_variant("SKU0003", add=10)
    if v["stock"] != 11:
        probs.append(f"add stock: {v['stock']}")
    v = st.update_variant("SKU0003", stock=0)
    if v["stock"] != 0:
        probs.append("set stock 0")
    v = st.update_variant("SKU0002", price=1999)
    if not (v["price"] == 1999 and v["is_sale"] == 1 and v["original_price"] == 2499):
        probs.append(f"price -> sale: {v}")
    res = st.add_product("Silk Kurta", "Men Kurta", "Pure silk.", 4999, ["Black", "Ivory"], ["M", "L"], 5)
    if len(res["skus"]) != 4:
        probs.append("add_product sku count")
    eng._names = None
    r = eng.handle("np", "silk kurta black M available?")
    if r["trace"]["action"] != "check_stock" or "5" not in r["reply"]:
        probs.append(f"new product not found by chat: {r['reply']}")
    try:
        st.add_product("Silk Kurta", "Men Kurta", "", 1, ["Black"], ["M"], 1)
        probs.append("duplicate product allowed")
    except ValueError:
        pass
    eng.handle("oos", "black linen kurta L hai?")   # SKU0003 now 0 -> demand logged
    eng.handle("oos2", "i want chocolate cake")
    ins = st.insights()
    if not ins["stockout_demand"] or not ins["unanswered"]:
        probs.append(f"insights missing demand/unanswered: {ins['stockout_demand']} {ins['unanswered']}")
    if not ins["tips"]:
        probs.append("no insight tips")
    st.close()
    return probs


def main():
    t0 = time.time()
    failed = 0
    for name, msgs, exp in CASES:
        problems, rep, tr = run_case(name, msgs, exp)
        if problems:
            failed += 1
            print(f"  FAIL {name}: {'; '.join(problems)}\n       reply: {rep[:200]!r}\n       trace: {tr['action']} {tr['reason']} {tr['route']}")
        else:
            print(f"  ok   {name}")
    probs = store_feature_checks()
    for p in probs:
        print(f"  FAIL store/inventory/insights: {p}")
    count, bad, neg = fuzz()
    for b in bad[:10]:
        print(f"  FAIL fuzz: {b}")
    if neg:
        print(f"  FAIL fuzz: {neg} variants went negative")
    print(f"\nConversations: {len(CASES) - failed}/{len(CASES)} passed · inventory/insights: "
          f"{'ok' if not probs else 'FAIL'} · fuzz: {count} random messages, {len(bad)} crashes "
          f"({time.time() - t0:.1f}s)")
    return 1 if failed or probs or bad or neg else 0


if __name__ == "__main__":
    sys.exit(main())
