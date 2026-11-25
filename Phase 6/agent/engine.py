"""Dialogue manager: hybrid rules + trained intent model.

Per message:
  normalize / extract entities  (nlu.py)
  -> pending-confirmation handling (confirm / cancel / edit the cart)
  -> guardrails                  (guardrails.py, policy_rules.json)
  -> intent detection            high-precision rules first, then the
                                 trained per-message classifier
                                 (intent_model.py, models/intent_model.json)
  -> active flow or new intent   order / status / exchange / recommend flows
                                 keep their slots across turns; a different
                                 question mid-flow is answered and the flow
                                 resumes afterwards
  -> tools / policy answers      (tools.py, policy_rag.py)
  -> reply in the customer's language (English or Roman Urdu)

Every reply carries a trace {intent, action, entities, confidence, decision,
reason, tools[], ml, route} so the UI, the evaluation and the teacher can see
*why* the agent did what it did.

Hand-offs are not a dead end: the chat gets a ticket number and staff can
reply into the same chat, but the customer can keep shopping, tracking or
asking questions — the agent keeps helping with everything it is allowed to.
"""
import re
import threading
import time
from pathlib import Path

from . import guardrails, nlu
from .intent_model import IntentModel
from .policy_rag import PolicyRAG, detect_topic, topic_answer
from .tools import Tools

BASE = Path(__file__).resolve().parent.parent
MODEL_PATH = BASE / "models" / "intent_model.json"

CONFIRM_WORDS = {"yes", "haan", "han", "haa", "confirm", "confirmed", "theek", "thik", "sahi",
                 "ji", "jee", "ok", "okay", "bilkul", "done", "yep", "yup", "yeah", "sure",
                 "perfect", "correct", "proceed", "place"}
DENY_WORDS = {"no", "nahi", "nahin", "nai", "cancel", "mat", "nope", "rehne", "chhoro", "chhodo"}
ORDER_WORDS = (r"\border\b|\bbhej|chahiye|chahiyen|chahye|chaiye|\blena\b|\blene\b|lunga|lungi|"
               r"\bbuy\b|purchase|kar do|kardo|mangwa|book kar|\btake it\b|\bill take\b|"
               r"\bi will take\b|add to cart|checkout|\bsend me\b|\bdeliver kar")
STATUS_CUES = (r"order kahan|order status|status|\btrack|tracking|(mera|my|meri) parcel|parcel kahan|"
               r"parcel (kab|abhi)|where is my order|"
               r"mera order|my order|dispatch|shipped|kab (aye|aayega|aega|milega|pohanch)|"
               r"abhi tak nahi aaya|not arrived|hasnt arrived|courier kaun")
EXCHANGE_CUES = r"exchange|tabdeel|badalna|badal do|badalwa|size change|color change|ki jagah|swap|replace"
EXCHANGE_POLICY_FORM = (r"policy|kitne din|kab tak|hoga\b|hogi\b|ho sakta|ho sakti|hota hai|hoti hai|"
                        r"can i\b|can we|is it possible|allowed|rules|conditions|sale (ka|ke|ki|wal|item)")
EXCHANGE_ACTION_FORM = r"karna hai|karni hai|kar do|kardo|karwa|chahta|chahti|i want|mujhe|order \d|\d{5}"
RECOMMEND_CUES = (r"suggest|recommend|recommendation|options?\b|best seller|bestseller|popular|"
                  r"trending|kya best|best rahega|kya lun|what should i (buy|get)|gift|kuch acha|"
                  r"kuch achha|something nice|kuch naya|affordable|budget friendly|sasta|cheap|"
                  r"jaldi dikha|aur dikhao|more options|show more|aur options")
EXPLICIT_BROWSE = (r"catalog|catalogue|collection|categor|products|kya kya|what do you (sell|have)|"
                   r"kya bechte|everything|sab dikhao|new arrivals|aap ke paas kya|what is available|"
                   r"whats available|kya available|list|browse|sale items|on sale|\bsale\b")
# a follow-up about the product in context must actually ask about it —
# a bare 'hai' or '?' is not enough ('bhuk lagi hai' is not about a handbag)
FOLLOWUP_CUES = (r"what about|how about|and in\b|\bavailable\b|\bstock\b|\bprice\b|\brate\b|kitne ka|"
                 r"kitni ka|kitne ki|milega|mil jayega|\bcolou?rs?\b|\bsizes?\b|\bfabric\b|"
                 r"\bdetails?\b|\bdescribe\b|\bthis one\b|\bye wala\b|\bis it\b")
MANY_RE = r"\b(each|every|all|sab|saare|sare|sabhi|both|dono|everything)\b"
GREETING_RE = (r"(hi+|hey+|hello+|helo|hy|salam|salaam|assalam[a-z ]*|asalam[a-z ]*|aoa|"
               r"good (morning|evening|afternoon)|adaab|yo)\b")
SALAM_RE = r"^(salam|salaam|assalam|asalam|aoa|as salam)"


_MODEL_CACHE = {}


def _load_model(path):
    """Load the intent model once per file version (a broken or missing
    model must never break the chat — the rules still work without it)."""
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime)
    if key not in _MODEL_CACHE:
        try:
            _MODEL_CACHE.clear()
            _MODEL_CACHE[key] = IntentModel.load(path)
        except Exception:
            return None
    return _MODEL_CACHE[key]


def _money(n):
    return f"Rs {int(n):,}"


class Engine:
    def __init__(self, store, data_dir=None, today="2026-10-17", model_path=None):
        self.store = store
        self.data_dir = Path(data_dir or (BASE / "data"))
        self.rules = guardrails.load_rules(self.data_dir / "policy_rules.json")
        self.rag = PolicyRAG(self.data_dir / "policy.md")
        self.tools = Tools(store, self.rules, today=today)
        self.sessions = {}
        self.lock = threading.RLock()
        self.ml_model = None
        self.model_info = {}
        self.ml_model = _load_model(Path(model_path or MODEL_PATH))
        self.model_info = self.ml_model.meta if self.ml_model else {}
        self._names = None

    # ------------------------------------------------------------- sessions
    def _session(self, sid):
        s = self.sessions.get(sid)
        if s is None:
            s = self.sessions[sid] = {
                "stage": None, "items": [], "context": {}, "customer": {},
                "exchange": {}, "status": {}, "rec": {}, "unclear": 0,
                "last_action": None, "lang": None, "turn": 0, "tickets": {},
                "pending": {}, "awaiting": None, "updated": time.time()}
        s["updated"] = time.time()
        if len(self.sessions) > 2000:          # forget idle sessions
            cutoff = time.time() - 6 * 3600
            for k in [k for k, v in self.sessions.items() if v["updated"] < cutoff]:
                self.sessions.pop(k, None)
        return s

    def reset(self):
        with self.lock:
            self.sessions.clear()
            self.store.reset()
            self._names = None

    def names(self):
        if self._names is None:
            self._names = self.store.product_names()
        return self._names

    def session_state(self, sid):
        s = self.sessions.get(sid)
        if not s:
            return {"stage": None}
        return {"stage": s["stage"], "items": s["items"], "pending": s.get("pending"),
                "tickets": s["tickets"], "lang": s["lang"]}

    # -------------------------------------------------------------- helpers
    def _ml(self, text):
        if not self.ml_model:
            return None, 0.0, []
        try:
            probs = self.ml_model.predict_proba(text)
            return probs[0][0], probs[0][1], [(k, round(p, 3)) for k, p in probs[:3]]
        except Exception:
            return None, 0.0, []

    def _pick(self, s, opts):
        if isinstance(opts, str):
            return opts
        return opts[s["turn"] % len(opts)]

    def _say(self, s, en, ur):
        return self._pick(s, ur if s.get("lang") == "ur" else en)

    def _out(self, sid, s, reply, intent, action, conf, decision, reason="",
             tools=None, entities=None, products=None, suggestions=None, extra=None,
             reminder=True):
        if reminder and action not in ("create_order", "request_confirmation") and \
                s["stage"] == "confirm" and s.get("pending"):
            reply += self._say(s, " (Your order summary is still waiting — reply 'yes' to confirm or 'cancel'.)",
                               " (Aap ka order summary abhi confirm hona baqi hai — 'haan' likhein ya 'cancel'.)")
        if s.get("_prefix"):
            reply = s.pop("_prefix") + reply
        trace = {"intent": intent, "action": action,
                 "entities": entities or {}, "confidence": round(conf, 2),
                 "decision": decision, "reason": reason, "tools": tools or [],
                 "ml": s.get("_ml_trace"), "route": s.get("_route"), "stage": s["stage"]}
        s["last_action"] = action
        out = {"reply": reply, "trace": trace, "products": products or [],
               "suggestions": suggestions or [], "lang": s.get("lang") or "en"}
        if extra:
            out.update(extra)
        meta = {"action": action, "decision": decision, "products": out["products"],
                "suggestions": out["suggestions"], "trace": trace}
        for k in ("order", "ticket"):
            if k in out:
                meta[k] = out[k]
        msg_id = self.store.log_message(sid, "agent", reply, meta)
        out["message_id"] = msg_id
        self.store.audit(sid, "decision", {"intent": intent, "action": action})
        return out

    def _default_suggestions(self, s):
        if s.get("lang") == "ur":
            return ["Products dikhao", "Mera order kahan hai?", "Delivery charges?", "3000 tak kuch suggest karo"]
        return ["Show products", "Track my order", "Delivery charges?", "Suggest something under 3000"]

    LEADS = {
        "refund_request": ("I can't approve refunds myself — ", "Refund main khud approve nahi kar sakta — "),
        "return_request": ("Returns are handled by our staff — ", "Return hamari staff handle karti hai — "),
        "damaged_or_wrong_item": ("I'm really sorry about that! Please keep clear photos of the item ready "
                                  "(they're needed within 48 hours of delivery). ",
                                  "Bohat maazrat! Item ki saaf photos tayyar rakhein (delivery ke 48 ghante "
                                  "ke andar chahiye hoti hain). "),
        "wrong_item_received": ("Sorry you got the wrong item! Please keep photos ready. ",
                                "Maazrat ke ghalat item pohancha! Photos tayyar rakhein. "),
        "legal_threat": ("I'm sorry for the trouble you've had. ", "Aap ko jo pareshani hui us ke liye maazrat. "),
        "angry_customer": ("I'm really sorry for the trouble. ", "Pareshani ke liye dil se maazrat. "),
        "human_request": ("Sure — ", "Zaroor — "),
        "discount_bargaining": ("Our prices are fixed as shown in the catalog, so I can't offer a discount — ",
                                "Hamari prices catalog ke mutabiq fixed hain, main discount nahi de sakta — "),
        "online_payment_request": ("Cash on Delivery works everywhere. Online payments (JazzCash, Easypaisa, "
                                   "bank) are handled only by our staff — ",
                                   "COD har jagah available hai. Online payment (JazzCash, Easypaisa, bank) "
                                   "sirf staff handle karti hai — "),
        "order_cancel_request": ("Cancelling a confirmed order is done by our staff — ",
                                 "Confirmed order cancel karna staff ka kaam hai — "),
        "order_change_request": ("Changes to a confirmed order are done by our staff — ",
                                 "Confirmed order mein tabdeeli staff karti hai — "),
        "quantity_above_limit": ("Orders above 10 pieces of one item are handled by our staff — ",
                                 "Ek item ke 10 se zyada pieces ka order staff handle karti hai — "),
        "cod_above_limit": ("COD orders above Rs 25,000 need a staff member to confirm payment first — ",
                            "Rs 25,000 se zyada ke COD order ko staff pehle confirm karti hai — "),
        "outside_policy": ("I don't have verified information about that and I don't want to guess — ",
                           "Is baare mein mere paas verified maloomat nahi, main andaza nahi lagana chahta — "),
        "unclear_after_two_attempts": ("I'm having trouble understanding exactly what you need — ",
                                       "Mujhe theek se samajh nahi aa raha — "),
        "order_not_found": ("I couldn't find an order with that number — ",
                            "Is number ka koi order nahi mila — "),
        "sale_item_not_exchangeable": ("This item was bought on sale, and sale items are final sale — ",
                                       "Ye item sale mein liya gaya tha aur sale items final sale hote hain — "),
        "outside_7_day_window": ("This order was delivered more than 7 days ago, which is outside the exchange window — ",
                                 "Is order ki delivery ko 7 din se zyada ho gaye hain — "),
        "category_not_exchangeable": ("This category can't be exchanged under our policy — ",
                                      "Ye category policy ke mutabiq exchange nahi hoti — "),
        "replacement_out_of_stock": ("The size/color you want is out of stock right now — ",
                                     "Jo size/color aap ko chahiye wo abhi stock mein nahi — "),
        "different_product_requested": ("Exchanges are only for a different size or color of the same product — ",
                                        "Exchange sirf same product ke doosre size ya color mein hota hai — "),
        "order_not_delivered": ("This order hasn't been delivered yet, so it can't be exchanged yet — ",
                                "Ye order abhi deliver nahi hua, is liye exchange abhi nahi ho sakta — "),
        "insufficient_stock": ("That item just went out of stock — ", "Ye item abhi stock se khatam ho gaya — "),
        "reservation_expired_or_failed": ("Your 15-minute stock reservation expired — ",
                                          "Aap ki 15 minute ki reservation expire ho gayi — "),
    }

    def _escalate(self, sid, s, reason, intent, entities, message="", detail="", tools=None):
        existing = s["tickets"].get(reason)
        esc = self.store.get_escalation(existing) if existing else None
        if esc and esc["status"] == "open":
            ticket = existing
            tail = self._say(s, f"Your case is already with our staff (ticket #{ticket}) — they'll reply in this chat "
                                "during working hours (10 AM - 8 PM). Meanwhile I can still help with products, "
                                "orders or delivery questions.",
                             f"Aap ka case pehle se staff ke paas hai (ticket #{ticket}) — wo working hours "
                             "(10 AM - 8 PM) mein isi chat mein reply karenge. Tab tak main products, orders ya "
                             "delivery mein madad kar sakta hoon.")
        else:
            cust = s["customer"].get("name") or None
            oid = s["status"].get("order_id") or s["exchange"].get("order_id") or entities.get("order_id")
            ticket = self.store.add_escalation(sid, reason, detail or reason, message, oid, cust)
            s["tickets"][reason] = ticket
            self.store.audit(sid, "escalate", {"reason": reason, "ticket": ticket})
            tail = self._say(s, f"I've passed this to our staff team (ticket #{ticket}). They'll reply right here "
                                "during working hours (10 AM - 8 PM). Meanwhile, I'm still happy to help with "
                                "anything else.",
                             f"Main ne ye case staff team ko forward kar diya hai (ticket #{ticket}). Wo working "
                             "hours (10 AM - 8 PM) mein isi chat mein reply karenge. Tab tak kisi aur cheez mein "
                             "madad chahiye to batayein.")
        s["last_ticket"] = ticket
        lead = self.LEADS.get(reason)
        lead = (lead[1] if s.get("lang") == "ur" else lead[0]) if lead else ""
        if lead.endswith("— ") and s.get("lang") == "ur":
            tail = tail[0].lower() + tail[1:]
        reply = lead + tail
        s["stage"] = None if s["stage"] != "confirm" else s["stage"]
        s["awaiting"] = None
        return self._out(sid, s, reply, intent, "escalate", 0.9, "HANDOFF", reason=reason,
                         entities=entities, tools=tools, suggestions=self._default_suggestions(s),
                         extra={"ticket": ticket})

    # ----------------------------------------------------------------- main
    def handle(self, session_id, message, user=None):
        with self.lock:
            return self._handle(session_id, str(message or "")[:2000], user)

    def _handle(self, sid, message, user):
        s = self._session(sid)
        s["turn"] += 1
        s["_route"] = None
        if user and user.get("role") != "admin":
            if user.get("name") and not s["customer"].get("name"):
                s["customer"]["name"] = user["name"]
            if user.get("phone") and not s["customer"].get("phone"):
                s["customer"]["phone"] = user["phone"]
        self.store.log_message(sid, "customer", message)
        t = nlu.normalize(message)
        lang = nlu.language_of(message)
        if lang:
            s["lang"] = lang
        elif not s["lang"]:
            s["lang"] = "en"
        e = nlu.extract(message, self.names())
        entities = {k: v for k, v in e.items()
                    if v not in (None, [], {}) and k not in ("details", "budget_mode")}
        if any(e["details"].values()):
            entities["details"] = {k: v for k, v in e["details"].items() if v}
        ml_intent, ml_conf, ml_top = self._ml(message)
        s["_ml_trace"] = {"intent": ml_intent, "confidence": round(ml_conf, 3), "top": ml_top} \
            if ml_intent else None
        if re.match(SALAM_RE, t) and len(t.split()) > 3:
            s["_prefix"] = "Wa alaikum assalam! "

        if not t:
            return self._help(sid, s, entities)

        # ---- 1. a summary is waiting for confirmation
        if s["stage"] == "confirm":
            r = self._confirm_stage(sid, s, e, t, message, entities, ml_intent, ml_conf)
            if r:
                return r

        # ---- 2. topics the policy simply does not cover: never invent an answer
        if guardrails.outside_policy(message):
            return self._escalate(sid, s, "outside_policy", "policy_question", entities,
                                  message, "topic not in policy.md")

        # ---- 3. guardrails (before any state-changing flow)
        hit = guardrails.check_text(message, self.rules)
        if hit:
            reason = hit[0]
            if reason in ("order_cancel_request", "order_change_request") and \
                    s["stage"] == "order_details":
                if reason == "order_cancel_request":
                    return self._cancel_cart(sid, s, entities)
            elif reason in ("order_change_request", "return_request") and \
                    s["stage"] in ("status", "exchange"):
                pass
            else:
                return self._escalate(sid, s, reason, "escalation", entities, message, hit[1])

        # ---- 3b. clearly not about the store ('chocolate cake', 'bhuk lagi hai')
        oos = nlu.out_of_scope(message)
        if oos and not (e["product"] or e["types"] or e["order_id"] or e["details"]["phone"]):
            return self._out_of_scope(sid, s, oos, entities)

        # ---- 4. intent
        intent, conf, route = self._detect_intent(t, e, s, ml_intent, ml_conf, message)

        # ---- right after a hand-off, extra info (order number, phone, 'ok')
        # belongs to that ticket — it is not a new request
        if s["last_action"] == "escalate" and s["stage"] is None and s.get("last_ticket") and (
                intent in ("affirm", "deny", "provide_details", "unknown")
                or ((e.get("order_id") or e.get("phone")) and self._info_only(t))):
            return self._ticket_followup(sid, s, e, t, message, entities)
        s["_route"] = route
        if intent in ("refund", "complaint", "human_request", "discount_request",
                      "cancel_order", "change_order", "payment_request"):
            reason = {"refund": "refund_request", "complaint": "damaged_or_wrong_item",
                      "human_request": "human_request", "discount_request": "discount_bargaining",
                      "cancel_order": "order_cancel_request", "change_order": "order_change_request",
                      "payment_request": "online_payment_request"}[intent]
            return self._escalate(sid, s, reason, "escalation", entities, message,
                                  f"intent model: {intent} ({conf:.2f})")

        if intent not in ("unknown", "affirm", "deny", "thanks"):
            s["unclear"] = 0

        # ---- 5. route
        if intent == "greeting":
            return self._greet(sid, s, entities)
        if intent == "thanks":
            return self._thanks(sid, s, entities)
        if intent == "goodbye":
            return self._bye(sid, s, entities)
        if intent == "smalltalk":
            return self._smalltalk(sid, s, t, entities)
        if intent == "help":
            return self._help(sid, s, entities)
        if intent == "order_status":
            return self._status_flow(sid, s, e, t, message, entities)
        if intent == "exchange":
            return self._exchange_flow(sid, s, e, t, message, entities)
        if intent == "policy_question":
            return self._policy_answer(sid, s, e, t, message, entities)
        if intent == "recommend":
            return self._recommend(sid, s, e, t, entities)
        if intent == "browse":
            return self._browse(sid, s, e, t, entities)
        if intent == "check_stock":
            return self._stock(sid, s, e, t, entities)
        if intent == "pick_from_list":
            return self._pick_from_list(sid, s, e, t, entities)
        if intent in ("place_order", "provide_details") and \
                (s["stage"] == "order_details" or intent == "place_order"):
            return self._order_flow(sid, s, e, t, message, entities)
        if intent == "provide_details":
            return self._loose_details(sid, s, e, t, message, entities)
        if intent == "affirm":
            return self._affirm(sid, s, e, t, message, entities)
        if intent == "deny":
            return self._deny(sid, s, entities)
        return self._unclear(sid, s, t, message, entities)

    # ----------------------------------------------------- intent detection
    def _detect_intent(self, t, e, s, ml_intent, ml_conf, message):
        words = t.split()
        wset = set(words)
        order_words = bool(re.search(ORDER_WORDS, t))
        stage = s["stage"]
        awaiting = s.get("awaiting")
        ctx = s["context"]

        # -- slot answers for an active flow win over everything else
        if stage == "order_details":
            d = e["details"]
            if awaiting in ("size", "color", "qty", "product") and (
                    e["size"] or e["color"] or e["qty"] or e["product"]) and not e["order_id"]:
                return "place_order", 0.95, "slot:" + awaiting
            question = ("?" in message or re.search(r"\b(what|kya|how|kitne|kitna|do you|can i|kab)\b", t)) \
                and detect_topic(t) and not (d["phone"] or d["address"])
            if awaiting == "details" and not question and (d["phone"] or d["address"] or d["city"] or d["name"]
                                                           or self._bare_name(message) or nlu.bad_phone(message)):
                return "provide_details", 0.95, "slot:details"
            # 'I was talking about kurta' — a different kind of product than the cart
            cur = s["items"][0]["product"] if s["items"] else None
            if e["types"] and not e["product"] and cur and not nlu.product_matches_filters(
                    cur, (self.store.variants_of(cur) or [{}])[0].get("category", ""), e["types"]):
                return "browse", 0.85, "rule:switch_type"
            if e["product"] and order_words:
                return "place_order", 0.9, "rule:new_product_order"
            if ("same" in wset or e["qty"]) and s["items"] and not e["budget"]:
                return "place_order", 0.9, "rule:same_or_qty"
        if stage == "status" and (e["order_id"] or e["phone"]):
            return "order_status", 0.95, "slot:status"
        if stage == "exchange" and (e["order_id"] or e["phone"] or e["size"] or e["color"]):
            return "exchange", 0.95, "slot:exchange"

        # -- short social messages
        if re.fullmatch(GREETING_RE + r"( (there|ji|bhai|sir|madam|team|everyone|all|saira|assistant))*"
                        r"( (kaise ho|how are you))?", t):
            return "greeting", 0.95, "rule:greeting"
        if re.fullmatch(r"(ok |okay |acha |theek hai |great |nice |perfect )?(thanks?( you)?( so much| a lot)?|"
                        r"thank u|shukriya|bohat shukriya|jazak ?allah|thx|ty|meherbani)( (bhai|ji|sir))?", t):
            return "thanks", 0.95, "rule:thanks"
        if re.fullmatch(r"(ok |okay |chalo )?(bye( bye)?|good ?bye|allah hafiz|khuda hafiz|see you( later)?|"
                        r"take care|good night)( ji)?", t):
            return "goodbye", 0.95, "rule:goodbye"
        if re.fullmatch(r"(help|menu|options|start|commands|help me|madad|guide me|what can you do|"
                        r"kya kar sakte ho|aap kya kar sakte (ho|hain))", t):
            return "help", 0.95, "rule:help"

        # -- order status / exchange
        exch = bool(re.search(EXCHANGE_CUES, t))
        if exch:
            if re.search(EXCHANGE_POLICY_FORM, t) and not re.search(EXCHANGE_ACTION_FORM, t) \
                    and not e["order_id"]:
                return "policy_question", 0.9, "rule:exchange_policy"
            return "exchange", 0.9, "rule:exchange"
        if re.search(STATUS_CUES, t) and not (e["product"] and order_words):
            return "order_status", 0.9, "rule:status"
        if e["order_id"] and not order_words and not e["product"]:
            return "order_status", 0.85, "rule:order_id"

        # -- recommendations (budget / suggest / refine the last list)
        if e["budget"] or re.search(RECOMMEND_CUES, t):
            if not e["product"] or not order_words:
                return "recommend", 0.9, "rule:recommend"
        if s["last_action"] == "recommend" and not e["product"] and (
                e["gender"] or e["types"] or re.search(r"dikha|show|more|aur|options|bata", t)):
            return "recommend", 0.85, "rule:recommend_refine"

        # -- policy questions
        topic = detect_topic(t)
        if topic and not e["product"] and not (order_words and topic in ("delivery_general",)):
            if topic not in ("sale",) or not re.search(r"dikha|show", t):
                return "policy_question", 0.9, "rule:policy_topic:" + topic

        # -- choosing from the list just shown ('buy this one', 'each of them')
        if s.get("shown") and not e["product"] and s["last_action"] in ("browse", "recommend", "request_missing_info") \
                and (order_words or re.search(MANY_RE, t) or re.search(r"\b(buy|want|lena|le lo)\b", t)) \
                and not e["types"] and not e["budget"]:
            return "pick_from_list", 0.85, "rule:pick_from_list"
        if s.get("awaiting") == "pick" and e["product"]:
            return "place_order", 0.9, "rule:picked"

        # -- a specific product
        if e["product"]:
            if order_words and not re.search(r"\bhai\?|available|kya rate|price|kitne ka|\?$", t) or \
                    (order_words and (e["qty"] or re.search(r"order|bhej|kar do", t))):
                return "place_order", 0.9, "rule:product_order"
            return "check_stock", 0.9, "rule:product"

        # -- a kind of product ('kurtas', 'winter', 'shoes')
        if e["types"] or (e["gender"] and re.search(r"dikha|show|collection|kapre|clothes|kuch", t)):
            return "browse", 0.85, "rule:type_filter"
        if re.search(EXPLICIT_BROWSE, t):
            return "browse", 0.85, "rule:browse"

        # -- follow-ups about the product in context
        if ctx.get("product"):
            if order_words or re.search(r"\b(buy|want|le lo|lelo|book) (it|this|one)\b|lena hai|"
                                        r"order kar|ye wala|yehi|same", t):
                return "place_order", 0.85, "rule:context_order"
            if (e["color"] or e["size"]) or re.search(FOLLOWUP_CUES, t) and len(words) <= 8:
                return "check_stock", 0.8, "rule:context_followup"

        if s["stage"] == "order_details" and (e["color"] or e["size"]):
            return "place_order", 0.8, "rule:order_slot"
        if (e["color"] or e["size"]) and re.search(r"\?|hai|available|milega|what about", t):
            return "check_stock", 0.6, "rule:attribute_only"

        # -- confirmation words outside a summary
        if wset and wset <= (CONFIRM_WORDS | {"kar", "do", "hai", "please", "it", "go", "ahead", "ji", "g"}):
            return "affirm", 0.8, "rule:affirm"
        if wset and wset <= (DENY_WORDS | {"thanks", "shukriya", "abhi", "chahiye", "ji", "not", "now", "do"}):
            return "deny", 0.8, "rule:deny"
        if e["details"]["phone"] and (e["details"]["address"] or e["details"]["name"]):
            return "provide_details", 0.8, "rule:details"

        # -- the trained classifier handles everything the rules don't
        if ml_intent:
            strong = {"greeting", "thanks", "goodbye", "smalltalk", "help", "affirm", "deny"}
            if ml_conf >= 0.55 or (ml_intent in strong and ml_conf >= 0.35):
                if ml_intent in ("refund", "complaint", "human_request", "discount_request",
                                 "cancel_order", "change_order", "payment_request") and ml_conf < 0.75:
                    pass
                elif ml_intent == "check_stock" and not ctx.get("product"):
                    return "browse" if e["types"] else "unknown", ml_conf, "ml:check_stock_no_product"
                elif ml_intent == "place_order" and not (ctx.get("product") or s["items"]):
                    return ("browse" if e["types"] else "unknown"), ml_conf, "ml:order_no_product"
                else:
                    return ml_intent, ml_conf, "ml"
        if "?" in message or re.search(r"\bkya\b|sakta|sakti|kaise|how|what|why|when|where|do you|can i", t):
            return "policy_question", 0.5, "fallback:question"
        return "unknown", ml_conf, "fallback:unknown"

    @staticmethod
    def _info_only(t):
        """'order 10150', 'mera number 0300... hai' — nothing but identifiers."""
        rest = re.sub(r"03\d{9}|\b\d{5}\b", " ", t)
        rest = re.sub(r"\b(order|number|no|id|phone|mobile|mera|meri|my|is|hai|ye|yeh|raha|ka|ki|"
                      r"and|aur|ji|sir|plz|please|tracking|hain|its|here)\b", " ", rest)
        return not rest.strip()

    @staticmethod
    def _bare_name(message):
        raw = message.strip()
        if re.search(r"\d", raw) or len(raw) > 40:
            return None
        words = raw.split()
        if not 1 <= len(words) <= 4:
            return None
        if any(w.lower() in nlu.URDU_WORDS or w.lower() in nlu.COLOR_WORDS for w in words):
            return None
        if nlu.extract_city(raw) or nlu.looks_like_address(raw):
            return None
        if not all(re.fullmatch(r"[A-Za-z.]+", w) for w in words):
            return None
        if raw.lower() in CONFIRM_WORDS | DENY_WORDS:
            return None
        return raw.title()

    def _ticket_followup(self, sid, s, e, t, message, entities):
        tid = s["last_ticket"]
        added = []
        if e.get("order_id"):
            self.store.conn.execute("UPDATE escalations SET order_id=? WHERE id=?", (e["order_id"], tid))
            added.append(f"order #{e['order_id']}")
        if e.get("phone"):
            added.append(f"phone {e['phone']}")
        self.store.audit(sid, "ticket_followup", {"ticket": tid, "message": message[:200]})
        if added:
            reply = self._say(s, f"Thanks — I've added {' and '.join(added)} to ticket #{tid}. Our staff will "
                                 "reply here during working hours (10 AM - 8 PM).",
                              f"Shukriya — {' aur '.join(added)} ticket #{tid} mein add kar diya hai. Staff "
                              "working hours (10 AM - 8 PM) mein yahin reply karegi.")
        else:
            reply = self._say(s, f"Noted. Ticket #{tid} is with our staff and they'll reply here during working hours "
                                 "(10 AM - 8 PM). Anything else I can help with meanwhile?",
                              f"Note kar liya. Ticket #{tid} staff ke paas hai, wo working hours (10 AM - 8 PM) mein "
                              "yahin reply karenge. Tab tak kuch aur chahiye?")
        return self._out(sid, s, reply, "escalation", "escalate", 0.85, "HANDOFF",
                         reason="ticket_followup", entities=entities,
                         suggestions=self._default_suggestions(s), extra={"ticket": tid})

    # --------------------------------------------------------- social turns
    def _greet(self, sid, s, entities):
        t = nlu.normalize(self._last_text(sid))
        salam = bool(re.match(SALAM_RE, t))
        first = not s.get("greeted")
        s["greeted"] = True
        if first:
            en = ("Hello! Welcome to Saira Fashion House. I can show you products, check stock and prices, "
                  "place an order, track a parcel or help with an exchange. What are you looking for today?")
            ur = ("Saira Fashion House mein khush aamdeed! Main products dikha sakta hoon, stock aur price "
                  "check kar sakta hoon, order le sakta hoon, parcel track kar sakta hoon ya exchange mein "
                  "madad kar sakta hoon. Aaj kya dekhna hai?")
        else:
            en = ["Hi! I'm right here — what can I help you find?",
                  "Hello! Tell me what you're looking for and I'll check it for you."]
            ur = ["Ji, main yahin hoon! Batayein kis cheez mein madad karun?",
                  "Batayein kya chahiye — kurta, suit, shoes ya winter wear?"]
        reply = self._say(s, en, ur)
        if salam:
            reply = "Wa alaikum assalam! " + reply
        elif s.get("lang") == "ur" and first:
            reply = "Assalam o alaikum! " + reply
        reply += self._flow_hint(s)
        return self._out(sid, s, reply, "greeting", "greet", 0.95, "ACT",
                         entities=entities, suggestions=self._default_suggestions(s))

    def _last_text(self, sid):
        rows = self.store.conn.execute(
            "SELECT text FROM messages WHERE session_id=? AND role='customer' ORDER BY id DESC LIMIT 1",
            (sid,)).fetchone()
        return rows["text"] if rows else ""

    def _thanks(self, sid, s, entities):
        reply = self._say(s, ["You're welcome! Anything else I can help with?",
                              "Happy to help! Let me know if you need anything else.",
                              "My pleasure! Is there anything else you'd like to see?"],
                          ["Koi baat nahi! Aur kisi cheez mein madad chahiye?",
                           "Khushi hui madad kar ke! Aur kuch chahiye to batayein.",
                           "Shukriya aap ka! Aur kuch dekhna ho to bata dein."])
        return self._out(sid, s, reply + self._flow_hint(s), "thanks", "smalltalk", 0.95, "ACT",
                         entities=entities, suggestions=self._default_suggestions(s))

    def _bye(self, sid, s, entities):
        reply = self._say(s, "Thank you for visiting Saira Fashion House! Have a great day. 👋",
                          "Allah Hafiz! Saira Fashion House visit karne ka shukriya. 👋")
        return self._out(sid, s, reply + self._flow_hint(s), "goodbye", "smalltalk", 0.95, "ACT",
                         entities=entities)

    def _smalltalk(self, sid, s, t, entities):
        if re.search(r"who are you|kaun|your name|naam kya|bot|robot|human|insaan|real|ai\b|person|made you|banaya", t):
            reply = self._say(s, "I'm the AI shopping assistant of Saira Fashion House. I work from the live "
                                 "catalog and store policy, so prices and stock are always real. For anything I "
                                 "can't decide (refunds, discounts, complaints), I hand you to our human staff.",
                              "Main Saira Fashion House ka AI shopping assistant hoon. Main live catalog aur "
                              "store policy se kaam karta hoon, is liye prices aur stock hamesha asli hote hain. "
                              "Jo faisle main nahi kar sakta (refund, discount, complaint) wo staff ko de deta hoon.")
        elif re.search(r"how are you|kaise ho|kaisay ho|haal|how r u|whats up|kya chal", t):
            reply = self._say(s, "I'm doing great, thanks for asking! 😊 What can I help you shop for today?",
                              "Main bilkul theek hoon, poochne ka shukriya! 😊 Batayein aaj kya dekhna hai?")
        elif re.search(r"urdu|english|language|zuban", t):
            reply = self._say(s, "I understand both English and Roman Urdu — write however you like!",
                              "Main English aur Roman Urdu dono samajhta hoon — jaise marzi likhein!")
        else:
            reply = self._say(s, "Thank you! 😊 Shall I show you something from our collection?",
                              "Shukriya! 😊 Collection mein se kuch dikhaun?")
        return self._out(sid, s, reply, "smalltalk", "smalltalk", 0.9, "ACT",
                         entities=entities, suggestions=self._default_suggestions(s))

    def _help(self, sid, s, entities):
        reply = self._say(
            s, "Here's what I can do:\n• Show products by category, budget or occasion\n"
               "• Check stock, price, colors and sizes\n• Place a Cash-on-Delivery order\n"
               "• Track an order (order number + phone)\n• Start an exchange (within 7 days)\n"
               "• Answer delivery, payment and exchange questions\nJust type in English or Roman Urdu!",
            "Main ye sab kar sakta hoon:\n• Category, budget ya occasion ke hisaab se products dikhana\n"
            "• Stock, price, colors aur sizes check karna\n• COD order place karna\n"
            "• Order track karna (order number + phone)\n• Exchange start karna (7 din ke andar)\n"
            "• Delivery, payment aur exchange ke sawal\nEnglish ya Roman Urdu mein likhein!")
        return self._out(sid, s, reply, "help", "help", 0.95, "ACT", entities=entities,
                         suggestions=self._default_suggestions(s))

    def _flow_hint(self, s):
        if s["stage"] == "order_details" and s["items"]:
            it = s["items"][0]
            return self._say(s, f" (Your order for {it.get('product')} is still open — just continue whenever you like.)",
                             f" (Aap ka {it.get('product')} ka order abhi open hai — jab chahein continue karein.)")
        return ""

    def _affirm(self, sid, s, e, t, message, entities):
        ctx = s["context"]
        if s["last_action"] in ("check_stock",) and ctx.get("product") and ctx.get("in_stock"):
            return self._order_flow(sid, s, e, t, message, entities)
        reply = self._say(s, ["Great! What would you like to do next?", "Sure! What else can I show you?"],
                          ["Zabardast! Ab kya karna hai?", "Ji zaroor! Aur kya dikhaun?"])
        return self._out(sid, s, reply, "affirm", "smalltalk", 0.7, "ACT", entities=entities,
                         suggestions=self._default_suggestions(s))

    def _deny(self, sid, s, entities):
        if s["stage"] in ("order_details",):
            return self._cancel_cart(sid, s, entities)
        if s["stage"] in ("status", "exchange"):
            s["stage"] = None; s["status"] = {}; s["exchange"] = {}; s["awaiting"] = None
        if s["last_action"] in ("request_missing_info",) and s["unclear"]:
            return self._unclear(sid, s, "", "", entities)
        reply = self._say(s, "No problem! Let me know if there's anything else.",
                          "Koi masla nahi! Kuch aur chahiye to batayein.")
        return self._out(sid, s, reply, "deny", "smalltalk", 0.7, "ACT", entities=entities,
                         suggestions=self._default_suggestions(s))

    def _cancel_cart(self, sid, s, entities):
        self.store.release(sid)
        s["stage"] = None; s["items"] = []; s["pending"] = {}; s["awaiting"] = None
        reply = self._say(s, "Okay, I've cancelled that — nothing was ordered and the reserved stock is released.",
                          "Theek hai, order cancel kar diya — kuch order nahi hua aur reservation release ho gayi hai.")
        return self._out(sid, s, reply, "place_order", "request_missing_info", 0.9, "ASK",
                         reason="cart_cancelled", entities=entities,
                         suggestions=self._default_suggestions(s))

    def _unclear(self, sid, s, t, message, entities):
        s["unclear"] += 1
        if message:
            self.store.audit(sid, "unanswered", {"kind": "unclear", "message": message[:200]})
        if s["unclear"] >= 2:
            return self._escalate(sid, s, "unclear_after_two_attempts", "unknown", entities,
                                  message, "unclear after two clarification attempts")
        reply = self._say(s, ["Sorry, I didn't quite get that. Are you looking for a product, want to track an "
                              "order, or have a question about delivery or exchange? You can also tap an option below.",
                              "Could you tell me a bit more? For example: \"black linen kurta M\", "
                              "\"track order 10231\" or \"delivery charges to Karachi\"."],
                          ["Maazrat, theek se samajh nahi aaya. Aap ko koi product chahiye, order track karna hai, "
                           "ya delivery/exchange ka sawal hai? Neeche option bhi tap kar sakte hain.",
                           "Thora detail mein batayein — jaise \"black linen kurta M\", \"order 10231 track karo\" "
                           "ya \"Karachi delivery charges\"."])
        return self._out(sid, s, reply, "unknown", "request_missing_info", 0.4, "ASK",
                         reason="unclear_message", entities=entities,
                         suggestions=self._default_suggestions(s))

    # --------------------------------------------------------------- policy
    def _policy_answer(self, sid, s, e, t, message, entities):
        city = e.get("city") or s["customer"].get("city")
        topic = detect_topic(t)
        tools = []
        answer = topic_answer(topic, t, city, s.get("lang"), self.rules) if topic else None
        if answer:
            tools.append({"name": "policy_rules", "result": {"topic": topic, "city": city}})
        else:
            answer, chunk = self.rag.grounded_answer(t)
            tools.append({"name": "policy_rag", "result": {"section": chunk["title"], "score": chunk["score"]}})
            if answer is None:
                return self._escalate(sid, s, "outside_policy", "policy_question", entities, message,
                                      f"RAG score {chunk['score']} below gate", tools=tools)
        if city and topic in ("delivery_charge", "delivery_time", "delivery_general"):
            tools.append({"name": "delivery_charge",
                          "result": {"city": city, "charge": self.tools.delivery_charge(city, 0),
                                     "eta_days": list(self.tools.delivery_eta(city))}})
        sugg = (["Exchange policy?", "COD available hai?", "Products dikhao"] if s.get("lang") == "ur"
                else ["Exchange policy?", "Is COD available?", "Show products"])
        return self._out(sid, s, answer + self._flow_hint(s), "policy_question", "answer_policy", 0.85,
                         "ACT", tools=tools, entities=entities, suggestions=sugg)

    def _out_of_scope(self, sid, s, word, entities):
        reply = self._say(
            s, [f"I'm only the shopping assistant for Saira Fashion House, so I can't help with {word} 🙂 "
                "But I can show you clothes, shoes and accessories, check sizes, or track an order. What would you like?",
                f"Ha, {word} isn't something we sell — we're a clothing store! Want to see kurtas, suits, "
                "footwear or our winter collection?"],
            ["Main sirf Saira Fashion House ka shopping assistant hoon, is mein madad nahi kar sakta 🙂 "
             "Lekin kapre, shoes, accessories dikha sakta hoon, size check kar sakta hoon ya order track kar sakta hoon.",
             "Ye to hum nahi bechte — hum kapron ki dukaan hain! Kurte, suits, shoes ya winter "
             "collection dikhaun?"])
        self.store.audit(sid, "unanswered", {"kind": "out_of_scope", "word": word,
                                             "message": self._last_text(sid)[:200]})
        return self._out(sid, s, reply, "out_of_scope", "smalltalk", 0.9, "ASK",
                         reason="out_of_scope", entities=entities,
                         suggestions=self._default_suggestions(s), reminder=True)

    def _pick_from_list(self, sid, s, e, t, entities):
        shown = s.get("shown") or []
        s["awaiting"] = "pick"
        if re.search(MANY_RE, t):
            reply = self._say(s, "Happy to! Let's add them one at a time so each gets the right color and size. "
                                 "Which one first?",
                              "Zaroor! Ek ek kar ke add karte hain taake har ek ka sahi color aur size ho. "
                              "Pehle konsa?")
        else:
            reply = self._say(s, "Which one would you like? Tap one below or type its name.",
                              "Konsa wala chahiye? Neeche tap karein ya naam likh dein.")
        return self._out(sid, s, reply, "place_order", "request_missing_info", 0.8, "ASK",
                         reason="pick_from_list", entities=entities, suggestions=shown[:5])

    # ---------------------------------------------------------- browse / recs
    def _card(self, p):
        return {k: p.get(k) for k in ("product_id", "name", "category", "price", "original_price",
                                      "is_sale", "colors", "sizes", "available", "sku", "color", "size")
                if p.get(k) is not None}

    def _browse(self, sid, s, e, t, entities):
        filters, gender = e["types"], e["gender"]
        sale_only = bool(re.search(r"\bsale\b|discounted|on sale", t))
        if not filters and not gender and not sale_only:
            if not re.search(EXPLICIT_BROWSE, t):
                # 'wo wala dikhao' — vague, not a browse request
                return self._unclear(sid, s, t, "", entities)
            cats = self.store.categories()
            lines = ", ".join(c["category"] for c in cats)
            reply = self._say(s, f"We have {sum(c['products'] for c in cats)} products in {len(cats)} categories: "
                                 f"{lines}. Which one would you like to see?",
                              f"Hamare paas {len(cats)} categories mein {sum(c['products'] for c in cats)} products "
                              f"hain: {lines}. Konsi category dikhaun?")
            sugg = ["Men kurtas", "Women suits", "Footwear", "Winter collection", "Kids"]
            return self._out(sid, s, reply, "browse", "browse", 0.85, "ACT", entities=entities,
                             tools=[{"name": "list_categories", "result": {"count": len(cats)}}],
                             suggestions=sugg, extra={"categories": cats})
        allp, shown = self.tools.browse(filters, gender, sale_only)
        tools = [{"name": "browse_catalog", "result": {"filters": filters, "gender": gender,
                                                        "sale_only": sale_only, "matches": len(allp)}}]
        s["rec"] = {"filters": filters, "gender": gender, "shown": [p["name"] for p in shown]}
        # a new list replaces whatever single product was being discussed
        s["context"] = {}
        s["shown"] = [p["name"] for p in shown]
        if s["stage"] == "order_details" and s["_route"] == "rule:switch_type":
            self.store.release(sid)
            s["stage"] = None; s["items"] = []; s["awaiting"] = None
        if not shown:
            reply = self._say(s, "I couldn't find anything matching that in our catalog. Want to see all categories?",
                              "Is tarah ka koi product catalog mein nahi mila. Saari categories dikhaun?")
            return self._out(sid, s, reply, "browse", "browse", 0.6, "ASK", tools=tools,
                             entities=entities, suggestions=["Show categories"])
        names = "; ".join(f"{p['name']} ({_money(p['price'])})" for p in shown[:5])
        more = len(allp) - len(shown)
        reply = self._say(s, f"Here's what we have: {names}" + (f" — and {more} more." if more > 0 else ".") +
                          " Tap a product for colors and sizes, or tell me which one you like.",
                          f"Ye options hain: {names}" + (f" — aur {more} mazeed." if more > 0 else ".") +
                          " Kisi product par tap karein ya batayein konsa pasand hai.")
        return self._out(sid, s, reply, "browse", "browse", 0.85, "ACT", tools=tools,
                         entities=entities, products=[self._card(p) for p in shown],
                         suggestions=[f"{shown[0]['name']} price?", "Suggest under 3000"])

    def _recommend(self, sid, s, e, t, entities):
        rec = s["rec"] if s["last_action"] in ("recommend", "browse") else {}
        refine = s["last_action"] == "recommend"
        budget = e["budget"] or (rec.get("budget") if refine else None)
        mode = e["budget_mode"] if e["budget"] else rec.get("mode", "max")
        filters = e["types"] or rec.get("filters") or []
        gender = e["gender"] or rec.get("gender")
        more = bool(re.search(r"more|aur dikhao|aur options|next|mazeed", t))
        explicit = re.search(r"suggest|recommend|options|best|popular|trending|gift|cheap|sasta|affordable", t)
        if not (budget or filters or gender or explicit or refine):
            s["unclear"] += 1
            s["rec"] = {}
            reply = self._say(s, "Sure, I'd love to help! Who is it for — men, women or kids — and do you have a budget "
                                 "in mind? (e.g. \"women kurti under 2500\")",
                              "Zaroor! Kis ke liye chahiye — men, women ya kids — aur budget kitna hai? "
                              "(jaise \"women kurti 2500 tak\")")
            return self._out(sid, s, reply, "recommend", "request_missing_info", 0.6, "ASK",
                             reason="missing_preferences", entities=entities,
                             suggestions=["Men under 3000", "Women under 5000", "Kids"])
        s["unclear"] = 0
        exclude = rec.get("shown", []) if (refine and more) else []
        recs = self.tools.recommend_products(budget, filters, gender, mode, limit=5, exclude=exclude)
        relaxed = False
        if not recs and budget:
            recs = self.tools.recommend_products(None, filters, gender, limit=50)
            recs = sorted(recs, key=lambda p: p["price"])[:4]
            relaxed = True
        if not recs and refine and exclude:
            recs = self.tools.recommend_products(budget, filters, gender, mode, limit=5)
        s["rec"] = {"budget": budget, "mode": mode, "filters": filters, "gender": gender,
                    "shown": list(dict.fromkeys(exclude + [r["name"] for r in recs]))}
        if recs:
            s["context"] = {}
            s["shown"] = [r["name"] for r in recs]
        tools = [{"name": "recommend_products",
                  "result": {"count": len(recs), "budget": budget, "mode": mode,
                             "filters": filters, "gender": gender, "relaxed_budget": relaxed}}]
        if not recs:
            reply = self._say(s, "I couldn't find in-stock items for that. Try a different budget or category?",
                              "Is ke liye koi in-stock item nahi mila. Budget ya category badal kar try karein?")
            return self._out(sid, s, reply, "recommend", "recommend", 0.6, "ASK", tools=tools,
                             entities=entities, suggestions=["Show categories"])
        lines = "; ".join(f"{r['name']} {_money(r['price'])}" + (" (SALE)" if r["is_sale"] else "")
                          for r in recs)
        if relaxed:
            reply = self._say(s, f"Nothing in stock within {_money(budget)}, but the closest options are: {lines}.",
                              f"{_money(budget)} ke andar kuch in-stock nahi, lekin qareeb tareen options ye hain: {lines}.")
        else:
            bud = (f" around {_money(budget)}" if mode == "around" else f" under {_money(budget)}") if budget else ""
            bud_ur = (f" {_money(budget)} ke around" if mode == "around" else f" {_money(budget)} tak") if budget else ""
            reply = self._say(s, f"Here are some in-stock picks{bud}: {lines}. Tap one to see colors and sizes, "
                                 "or tell me which you like and I'll order it.",
                              f"Ye{bud_ur} in-stock options hain: {lines}. Kisi par tap karein ya batayein konsa "
                              "pasand aaya, main order kar dunga.")
        return self._out(sid, s, reply, "recommend", "recommend", 0.9, "ACT", tools=tools,
                         entities=entities, products=[self._card(r) for r in recs],
                         suggestions=(["Aur options dikhao"] if s.get("lang") == "ur" else ["Show more options"]))

    # ---------------------------------------------------------------- stock
    def _stock(self, sid, s, e, t, entities):
        ctx = s["context"]
        product = e["product"] or ctx.get("product")
        if not product:
            reply = self._say(s, "Which product should I check? Tell me the name, color and size "
                                 "(e.g. \"black linen kurta M\").",
                              "Konsa product check karun? Naam, color aur size bata dein (jaise \"black linen kurta M\").")
            s["awaiting"] = None
            return self._out(sid, s, reply, "check_stock", "request_missing_info", 0.5, "ASK",
                             entities=entities, suggestions=["Show products"])
        same = product == ctx.get("product")
        color = e["color"] or (ctx.get("color") if same else None)
        size = e["size"] or (ctx.get("size") if same else None)
        # a follow-up that names only one attribute keeps the other from context
        if not e["product"] and e["color"] and not e["size"]:
            size = ctx.get("size")
        if not e["product"] and e["size"] and not e["color"]:
            color = ctx.get("color")
        s["context"] = {"product": product, "color": color, "size": size}
        s["unclear"] = 0
        if not color and not size:
            ov = self.tools.product_overview(product)
            tools = [{"name": "product_overview", "result": {k: ov[k] for k in ("product", "price", "colors", "sizes", "available")}}]
            stock_cols = [c["color"] for c in ov["by_color"] if c["available"] > 0]
            sale = " (on SALE)" if ov["is_sale"] else ""
            sale_ur = " (SALE par)" if ov["is_sale"] else ""
            if ov["available"] <= 0:
                reply = self._say(s, f"{product} is {_money(ov['price'])}{sale}, but it's completely out of stock right now.",
                                  f"{product} {_money(ov['price'])}{sale_ur} ka hai, lekin abhi bilkul stock mein nahi.")
            else:
                reply = self._say(s, f"{product} is {_money(ov['price'])}{sale}. In stock in {', '.join(stock_cols)}; "
                                     f"sizes {', '.join(ov['sizes'])}. Which color and size would you like?",
                                  f"{product} ki price {_money(ov['price'])}{sale_ur} hai. Stock mein colors: "
                                  f"{', '.join(stock_cols)}; sizes: {', '.join(ov['sizes'])}. Konsa color aur size chahiye?")
            s["context"]["in_stock"] = ov["available"] > 0
            card = {"product_id": ov["product_id"], "name": product, "category": ov["category"],
                    "price": ov["price"], "original_price": ov["original_price"], "is_sale": ov["is_sale"],
                    "colors": ov["colors"], "sizes": ov["sizes"], "available": ov["available"]}
            return self._out(sid, s, reply, "check_stock", "check_stock", 0.9, "ACT", tools=tools,
                             entities=entities, products=[card],
                             suggestions=[f"{c} {ov['sizes'][len(ov['sizes']) // 2]}" for c in stock_cols[:3]])
        res = self.tools.check_stock(product, color, size)
        tools = [{"name": "check_stock", "result": res}]
        if not res["found"]:
            bad = res.get("invalid", {})
            what = " ".join(v for v in (bad.get("color"), bad.get("size")) if v)
            reply = self._say(s, f"{product} doesn't come in {what}. Available colors: {', '.join(res['colors'])}; "
                                 f"sizes: {', '.join(res['sizes'])}.",
                              f"{product} {what} mein nahi aata. Colors: {', '.join(res['colors'])}; "
                              f"sizes: {', '.join(res['sizes'])}.")
            s["context"] = {"product": product,
                            "color": None if bad.get("color") else color,
                            "size": None if bad.get("size") else size}
            return self._out(sid, s, reply, "check_stock", "check_stock", 0.7, "ASK", tools=tools,
                             entities=entities, suggestions=res["colors"][:3])
        s["context"].update({"color": res["color"] if color else None,
                             "size": res["size"] if size else None, "in_stock": res["in_stock"],
                             "sku": res["sku"]})
        card = self._card({"product_id": res["product_id"], "name": res["product"], "category": res["category"],
                           "price": res["price"], "original_price": res["original_price"],
                           "is_sale": res["is_sale"], "available": res["available"], "sku": res["sku"],
                           "color": res["color"], "size": res["size"]})
        if not res["exact"]:
            # only color or only size given: say which of the other attribute is in stock
            variants = self.store.variants_of(product)
            if color:
                opts = [v["size"] for v in variants if v["color"] == res["color"] and self.store.available(v["sku"]) > 0]
                what_en, what_ur = f"in {res['color']}", f"{res['color']} mein"
                ask_en, ask_ur = "sizes", "sizes"
            else:
                opts = [v["color"] for v in variants if v["size"] == res["size"] and self.store.available(v["sku"]) > 0]
                what_en, what_ur = f"in size {res['size']}", f"size {res['size']} mein"
                ask_en, ask_ur = "colors", "colors"
            if opts:
                reply = self._say(s, f"{product} {what_en} is {_money(res['price'])} — in stock {ask_en}: {', '.join(opts)}. "
                                     f"Which one would you like?",
                                  f"{product} {what_ur} {_money(res['price'])} ka hai — stock mein {ask_ur}: "
                                  f"{', '.join(opts)}. Konsa chahiye?")
                s["context"]["in_stock"] = True
            else:
                reply = self._say(s, f"Sorry, {product} {what_en} is out of stock right now.",
                                  f"Maazrat, {product} {what_ur} abhi stock mein nahi.")
                s["context"]["in_stock"] = False
            card.pop("sku", None); card.pop("color" if not color else "size", None)
            return self._out(sid, s, reply, "check_stock", "check_stock", 0.9, "ACT", tools=tools,
                             entities=entities, products=[card], suggestions=opts[:4])
        sale = " (sale price)" if res["is_sale"] else ""
        if res["in_stock"]:
            low = res["available"] <= 3
            reply = self._say(s, [f"Yes! {res['product']} in {res['color']}, size {res['size']} is in stock "
                                  f"({res['available']} available) at {_money(res['price'])}{sale}."
                                  + (" Only a few left!" if low else "") + " Would you like to order it?",
                                  f"Good news — {res['product']} {res['color']} {res['size']} is available: "
                                  f"{res['available']} pieces at {_money(res['price'])}{sale}. Shall I place an order?"],
                              [f"Ji, {res['product']} {res['color']} size {res['size']} available hai — "
                               f"{res['available']} pieces hain, price {_money(res['price'])} hai."
                               + (" Sirf thore bache hain!" if low else "") + " Order karna hai to qty bata dein.",
                               f"Ji haan, {res['product']} {res['color']} {res['size']} stock mein hai "
                               f"({res['available']} pieces), price {_money(res['price'])}. Order kar dun?"])
            sugg = (["Haan order kar do", "Doosre colors?"] if s.get("lang") == "ur"
                    else ["Yes, order 1", "Other colors?"])
        else:
            self.store.audit(sid, "stockout_demand", {"sku": res["sku"], "product": res["product"],
                                                      "color": res["color"], "size": res["size"]})
            alts = res.get("alternatives", [])
            alt_txt = "; ".join(f"{a['color']} {a['size']} ({a['available']} left)" for a in alts)
            reply = self._say(s, f"Sorry, {res['product']} {res['color']} size {res['size']} is out of stock (0 available)."
                                 + (f" Available instead: {alt_txt}." if alts else ""),
                              f"Maazrat, {res['product']} {res['color']} size {res['size']} out of stock hai (0 available)."
                              + (f" Ye alternatives available hain: {alt_txt}." if alts else ""))
            sugg = [f"{a['color']} {a['size']}" for a in alts[:3]]
        return self._out(sid, s, reply, "check_stock", "check_stock", 0.95, "ACT", tools=tools,
                         entities=entities, products=[card], suggestions=sugg)

    # ------------------------------------------------------------ status flow
    def _status_flow(self, sid, s, e, t, message, entities):
        st = s["status"]
        if s["stage"] != "status":
            st.clear()
        if e.get("order_id"):
            st["order_id"] = e["order_id"]
        if e.get("phone"):
            st["phone"] = e["phone"]
        s["stage"] = "status"
        if not st.get("order_id"):
            s["awaiting"] = "order_id"
            reply = self._say(s, "Sure! Please share your order number (e.g. 10231).",
                              "Zaroor! Apna order number bata dein (masalan 10231).")
            return self._out(sid, s, reply, "order_status", "request_missing_info", 0.8, "ASK",
                             reason="missing_order_id", entities=entities)
        if not st.get("phone"):
            s["awaiting"] = "phone"
            reply = self._say(s, "For privacy, please also share the phone number used on the order.",
                              "Privacy ke liye wo phone number bhi bata dein jo order par diya tha.")
            return self._out(sid, s, reply, "order_status", "request_missing_info", 0.8, "ASK",
                             reason="missing_phone", entities=entities)
        res = self.tools.get_order_status(st["order_id"], st["phone"])
        tools = [{"name": "get_order_status", "result": {
            "found": res["found"], "phone_match": res.get("phone_match"),
            "status": res.get("status")}}]
        oid = st["order_id"]
        s["stage"] = None; s["status"] = {}; s["awaiting"] = None
        if not res["found"]:
            s["status"]["order_id"] = oid
            r = self._escalate(sid, s, "order_not_found", "order_status", entities, message,
                               f"order {oid} not found", tools=tools)
            s["status"] = {}
            return r
        if not res["phone_match"]:
            reply = self._say(s, "Sorry, I couldn't verify the order with those details, so I can't share its status. "
                                 "Please double-check the order number and the phone number used on the order.",
                              "Maazrat, di gayi maloomat se order verify nahi ho saka, is liye status share nahi kar "
                              "sakta. Order number aur phone number dobara check kar ke bhejein.")
            return self._out(sid, s, reply, "order_status", "deny_status_mismatch", 0.95, "BLOCK",
                             reason="phone_mismatch", tools=tools, entities=entities)
        stt = res["status"]
        lo, hi = res["eta_days"]
        if s.get("lang") == "ur":
            reply = f"Order #{oid} ({res['item']}, qty {res['qty']}) ka status: {stt}."
            if res.get("tracking_no"):
                reply += f" Courier: {res['courier']}, tracking number: {res['tracking_no']}."
            if stt in ("Placed", "Packed", "Shipped"):
                reply += f" {res['city']} mein delivery aam taur par {lo}-{hi} working days mein hoti hai."
            if stt == "Delivered" and res.get("delivered_date"):
                reply += f" Delivery {res['delivered_date']} ko hui."
        else:
            reply = f"Order #{oid} ({res['item']}, qty {res['qty']}) is {stt}."
            if res.get("tracking_no"):
                reply += f" Courier: {res['courier']}, tracking number {res['tracking_no']}."
            if stt in ("Placed", "Packed", "Shipped"):
                reply += f" Delivery to {res['city']} usually takes {lo}-{hi} working days."
            if stt == "Delivered" and res.get("delivered_date"):
                reply += f" It was delivered on {res['delivered_date']}."
        tools[0]["result"].update({"courier": res.get("courier"), "tracking_no": res.get("tracking_no")})
        return self._out(sid, s, reply, "order_status", "get_order_status", 0.97, "ACT",
                         tools=tools, entities=entities,
                         suggestions=(["Exchange karna hai", "Shukriya"] if s.get("lang") == "ur"
                                      else ["Start an exchange", "Thanks!"]),
                         extra={"order_status": {"order_id": oid, "status": stt,
                                                 "tracking_no": res.get("tracking_no"),
                                                 "courier": res.get("courier")}})

    # ---------------------------------------------------------- exchange flow
    def _exchange_flow(self, sid, s, e, t, message, entities):
        ex = s["exchange"]
        if s["stage"] != "exchange":
            ex.clear()
        if e.get("order_id"):
            ex["order_id"] = e["order_id"]
        if e.get("phone"):
            ex["phone"] = e["phone"]
        if e.get("size"):
            ex["want_size"] = e["size"]
        if e.get("color"):
            ex["want_color"] = e["color"]
        m = re.search(r"(?:ki jagah|ke badle|ke bajaye|instead of)(.*)", t)
        if m:
            wanted = nlu.match_product(m.group(1), self.names())
            if wanted:
                ex["want_product"] = wanted
        elif re.search(r"\binstead\b|\bdoosra product\b|\bdifferent product\b", t) and e.get("product"):
            ex["want_product"] = e["product"]
        s["stage"] = "exchange"
        if not ex.get("order_id"):
            s["awaiting"] = "order_id"
            reply = self._say(s, "I can help with that! First, please share your order number.",
                              "Main madad karta hoon! Exchange ke liye pehle apna order number bata dein.")
            return self._out(sid, s, reply, "exchange", "request_missing_info", 0.8, "ASK",
                             reason="missing_order_id", entities=entities)
        if not ex.get("phone"):
            s["awaiting"] = "phone"
            reply = self._say(s, "Please share the phone number used on the order so I can verify it.",
                              "Order par diya gaya phone number bata dein taake order verify ho sake.")
            return self._out(sid, s, reply, "exchange", "request_missing_info", 0.8, "ASK",
                             reason="missing_phone", entities=entities)
        order = self.store.get_order(ex["order_id"])
        if not order:
            r = self._escalate(sid, s, "order_not_found", "exchange", entities, message,
                               f"order {ex['order_id']} not found")
            s["exchange"] = {}
            return r
        if order["phone"] != ex["phone"]:
            s["stage"] = None; s["exchange"] = {}; s["awaiting"] = None
            reply = self._say(s, "I couldn't verify the order with those details, so I can't start an exchange. "
                                 "Please check the order number and phone number.",
                              "Di gayi maloomat se order verify nahi ho saka, is liye exchange start nahi kar sakta.")
            return self._out(sid, s, reply, "exchange", "deny_status_mismatch", 0.95, "BLOCK",
                             reason="phone_mismatch", entities=entities)
        if not (ex.get("want_size") or ex.get("want_color") or ex.get("want_product")):
            item = self.store.variant(order["sku"]) or {}
            s["awaiting"] = "exchange_variant"
            reply = self._say(s, f"Found it — you ordered {item.get('name')} ({item.get('color')}, {item.get('size')}). "
                                 "Which size or color would you like instead?",
                              f"Order mil gaya — aap ne {item.get('name')} ({item.get('color')}, {item.get('size')}) "
                              "liya tha. Replacement mein konsa size ya color chahiye?")
            return self._out(sid, s, reply, "exchange", "request_missing_info", 0.8, "ASK",
                             reason="missing_replacement", entities=entities)
        res = self.tools.check_exchange(ex["order_id"], ex["phone"], ex.get("want_size"),
                                        ex.get("want_color"), ex.get("want_product"))
        tools = [{"name": "check_exchange", "result": res}]
        oid = ex["order_id"]
        s["stage"] = None; s["awaiting"] = None
        if res["eligible"]:
            s["exchange"] = {}
            xid = self.store.add_exchange(oid, res["old_sku"], res["replacement_sku"],
                                          res["exchange_courier_charge"])
            self.store.audit(sid, "exchange_created", {"order_id": oid, "exchange_id": xid,
                                                       "replacement": res["replacement_sku"]})
            reply = self._say(s, f"Done! Exchange #{xid} is created for order #{oid}: {res['product']} "
                                 f"{res['old']} → {res['new']}. Please send the original item back unused with tags; "
                                 f"the replacement ships once we receive it. Exchange courier charge: "
                                 f"{_money(res['exchange_courier_charge'])}.",
                              f"Ho gaya! Order #{oid} ke liye exchange #{xid} ban gaya: {res['product']} "
                              f"{res['old']} → {res['new']}. Original item unused aur tags ke saath wapas bhejein; "
                              f"replacement milte hi dispatch hogi. Exchange courier charge "
                              f"{_money(res['exchange_courier_charge'])} hai.")
            return self._out(sid, s, reply, "exchange", "create_exchange", 0.95, "ACT", tools=tools,
                             entities=entities, extra={"exchange": {"id": xid, "order_id": oid}})
        if res["reason"] == "replacement_not_specified":
            s["stage"] = "exchange"; s["awaiting"] = "exchange_variant"
            ex.pop("want_size", None); ex.pop("want_color", None)
            reply = self._say(s, "That's the same size and color you already have — which different size or color "
                                 "would you like?",
                              "Ye to wahi size aur color hai jo aap ke paas hai — konsa doosra size ya color chahiye?")
            return self._out(sid, s, reply, "exchange", "request_missing_info", 0.7, "ASK", tools=tools,
                             reason="same_variant", entities=entities)
        r = self._escalate(sid, s, res["reason"], "exchange", entities, message, str(res), tools=tools)
        s["exchange"] = {}
        return r

    # ----------------------------------------------------------- order flow
    def _loose_details(self, sid, s, e, t, message, entities):
        d = e["details"]
        for k in ("name", "phone", "address", "city"):
            if d.get(k):
                s["customer"][k] = d[k]
        reply = self._say(s, "Thanks, I've noted your details. What would you like to order?",
                          "Shukriya, details note kar li hain. Kya order karna hai?")
        return self._out(sid, s, reply, "provide_details", "request_missing_info", 0.6, "ASK",
                         entities=entities, suggestions=self._default_suggestions(s))

    def _order_flow(self, sid, s, e, t, message, entities):
        ctx = s["context"]
        rules = self.rules
        if s["stage"] != "order_details":
            s["items"] = []
        is_details_turn = s["stage"] == "order_details" and (
            s.get("awaiting") == "details" or (e["details"]["phone"] and nlu.looks_like_address(message)))
        product = None if is_details_turn else e["product"]

        # ---- build / update the cart items
        if "same" in t.split() and (s["items"] or ctx.get("product")):
            base = dict(s["items"][0]) if s["items"] else {
                "product": ctx.get("product"), "color": ctx.get("color"), "size": ctx.get("size")}
            base["qty"] = e["qty"] or 1
            items = [base]
            for c in e["colors"]:
                if c != base.get("color"):
                    extra = dict(base); extra["color"] = c
                    m = re.search(rf"\b(\w+)\s+{re.escape(c.lower())}\b", t)
                    n = m and (nlu.WORD_NUMBERS.get(m.group(1)) or (int(m.group(1)) if m.group(1).isdigit() else None))
                    extra["qty"] = n or 1
                    items.append(extra)
            s["items"] = items
        elif product:
            item = {"product": product, "color": e["color"], "size": e["size"], "qty": e["qty"]}
            if product == ctx.get("product"):
                item["color"] = item["color"] or ctx.get("color")
                item["size"] = item["size"] or ctx.get("size")
            if s["items"] and re.search(r"\b(aur|and|also|bhi|plus)\b", t) and \
                    s["items"][0]["product"] != product:
                s["items"].append(item)
            else:
                s["items"] = [item]
        elif s["items"]:
            cur = s["items"][-1]
            if not is_details_turn:
                if e["color"]:
                    cur["color"] = e["color"]
                if e["size"]:
                    cur["size"] = e["size"]
                if e["qty"]:
                    cur["qty"] = e["qty"]
        elif ctx.get("product"):
            s["items"] = [{"product": ctx.get("product"), "color": e["color"] or ctx.get("color"),
                           "size": e["size"] or ctx.get("size"), "qty": e["qty"]}]
        s["stage"] = "order_details"
        if not s["items"]:
            s["awaiting"] = "product"
            reply = self._say(s, "Sure! Which product would you like to order? (e.g. \"black linen kurta M\")",
                              "Zaroor! Konsa product order karna hai? (jaise \"black linen kurta M\")")
            return self._out(sid, s, reply, "place_order", "request_missing_info", 0.7, "ASK",
                             reason="missing_product", entities=entities,
                             suggestions=["Show products"])

        # ---- customer details from this turn
        d = e["details"]
        cust = s["customer"]
        for k in ("name", "phone", "address", "city"):
            if d.get(k):
                cust[k] = d[k]
        if e.get("city"):
            cust["city"] = e["city"]
        if s.get("awaiting") == "details" and not cust.get("name") and not d.get("name"):
            nm = self._bare_name(message)
            if nm:
                cust["name"] = nm
        bad = nlu.bad_phone(message) if not d.get("phone") else None
        # a free-text address: has a number AND real words, and isn't a phone number
        addr_words = [w for w in re.findall(r"[a-z]{3,}", t)
                      if w not in ("phone", "phome", "number", "mobile", "address", "mera", "meri",
                                   "hai", "naam", "name", "and", "aur", "city")]
        if s.get("awaiting") == "details" and not cust.get("address") and not d.get("address") \
                and cust.get("name") and len(t) >= 6 and not d.get("name") and not d.get("phone") \
                and not bad and not e["qty"] and not self._bare_name(message) \
                and re.search(r"\d", t) and len(addr_words) >= 1:
            cust["address"] = message.strip()
        if bad:
            s["awaiting"] = "details"
            got = ", ".join(f"{k}: {cust[k]}" for k in ("name", "address", "city") if cust.get(k))
            reply = self._say(s, f"Hmm, {bad} doesn't look like a valid mobile number — Pakistani numbers have 11 "
                                 "digits and start with 03, like 03001234567. Could you send it again?"
                                 + (f" (I've noted {got}.)" if got else ""),
                              f"{bad} sahi mobile number nahi lag raha — Pakistani number 11 digits ka hota hai aur "
                              "03 se shuru hota hai, jaise 03001234567. Dobara bhej dein?"
                              + (f" ({got} note kar liya hai.)" if got else ""))
            return self._out(sid, s, reply, "place_order", "request_missing_info", 0.85, "ASK",
                             reason="invalid_phone", entities=entities)

        # ---- validate every item
        for it in s["items"]:
            if not it.get("qty"):
                it["qty"] = 1
            if it["qty"] > rules["order"]["max_qty_per_item"]:
                return self._escalate(sid, s, "quantity_above_limit", "place_order", entities, message,
                                      f"qty {it['qty']} of {it['product']}")
            variants = self.store.variants_of(it["product"])
            sizes = sorted({v["size"] for v in variants}, key=lambda x: (len(x), x))
            colors = sorted({v["color"] for v in variants})
            if it.get("color") and it["color"] not in colors:
                bad = it["color"]; it["color"] = None
                s["awaiting"] = "color"
                reply = self._say(s, f"{it['product']} doesn't come in {bad}. Available colors: {', '.join(colors)}.",
                                  f"{it['product']} {bad} mein nahi aata. Colors: {', '.join(colors)}.")
                return self._out(sid, s, reply, "place_order", "request_missing_info", 0.85, "ASK",
                                 reason="invalid_color", entities=entities, suggestions=colors[:4])
            if it.get("size") and it["size"] not in sizes:
                bad = it["size"]; it["size"] = None
                s["awaiting"] = "size"
                reply = self._say(s, f"{it['product']} doesn't come in size {bad}. Available sizes: {', '.join(sizes)}.",
                                  f"{it['product']} ka size {bad} nahi hota. Sizes: {', '.join(sizes)}.")
                return self._out(sid, s, reply, "place_order", "request_missing_info", 0.85, "ASK",
                                 reason="invalid_size", entities=entities, suggestions=sizes[:5])
            if len(sizes) > 1 and not it.get("size"):
                s["awaiting"] = "size"
                in_stock = [v["size"] for v in variants if (not it.get("color") or v["color"] == it["color"])
                            and self.store.available(v["sku"]) > 0]
                in_stock = sorted(set(in_stock), key=lambda x: sizes.index(x)) or sizes
                reply = self._say(s, f"Which size of {it['product']}{' in ' + it['color'] if it.get('color') else ''}? "
                                     f"In stock: {', '.join(in_stock)}.",
                                  f"{it['product']}{' ' + it['color'] if it.get('color') else ''} ka size bata dein — "
                                  f"available sizes: {', '.join(in_stock)}.")
                return self._out(sid, s, reply, "place_order", "request_missing_info", 0.85, "ASK",
                                 reason="missing_size", entities=entities, suggestions=in_stock[:5])
            if len(colors) > 1 and not it.get("color"):
                s["awaiting"] = "color"
                in_stock = sorted({v["color"] for v in variants if v["size"] == it.get("size")
                                   and self.store.available(v["sku"]) > 0}) or colors
                reply = self._say(s, f"Which color of {it['product']}? In stock: {', '.join(in_stock)}.",
                                  f"{it['product']} ka color bata dein — available colors: {', '.join(in_stock)}.")
                return self._out(sid, s, reply, "place_order", "request_missing_info", 0.85, "ASK",
                                 reason="missing_color", entities=entities, suggestions=in_stock[:5])
            v = self.store.find_variant(it["product"], it.get("color"), it.get("size"))
            if not v:
                continue
            avail = self.store.available(v["sku"])
            if avail < it["qty"]:
                res = self.tools.check_stock(it["product"], it.get("color"), it.get("size"))
                alts = res.get("alternatives", [])
                alt_txt = "; ".join(f"{a['color']} {a['size']} ({a['available']})" for a in alts)
                if avail > 0:
                    reply = self._say(s, f"Only {avail} of {it['product']} {v['color']} {v['size']} left — want {avail} instead?",
                                      f"{it['product']} {v['color']} {v['size']} ke sirf {avail} pieces bache hain — {avail} kar dun?")
                    s["awaiting"] = "qty"
                else:
                    reply = self._say(s, f"Sorry, {it['product']} {v['color']} size {v['size']} is out of stock (0 available), "
                                         "so I can't order it." + (f" Available instead: {alt_txt}." if alts else ""),
                                      f"Maazrat, {it['product']} {v['color']} size {v['size']} out of stock hai (0 available), "
                                      "is liye order nahi ho sakta." + (f" Ye alternatives available hain: {alt_txt}." if alts else ""))
                    it["size"] = None if any(a["color"] == v["color"] for a in alts) else it["size"]
                    it["color"] = it["color"] if it["size"] is None else None
                    s["awaiting"] = "size" if it["size"] is None else "color"
                return self._out(sid, s, reply, "check_stock", "check_stock", 0.95, "ACT",
                                 tools=[{"name": "check_stock", "result": res}], entities=entities,
                                 suggestions=[f"{a['color']} {a['size']}" for a in alts[:3]])

        # ---- customer details
        missing = [k for k in ("name", "phone", "address", "city") if not cust.get(k)]
        if missing:
            s["awaiting"] = "details"
            labels_en = {"name": "name", "phone": "phone number", "address": "full address", "city": "city"}
            labels_ur = {"name": "naam", "phone": "phone number", "address": "poora address", "city": "city"}
            have = [k for k in ("name", "phone", "address", "city") if cust.get(k)]
            if len(missing) == 4:
                reply = self._say(s, "Great choice! To place the order, please send your name, phone number, full address "
                                     "and city (all in one message is fine).",
                                  "Order ke liye apna naam, phone number, poora address aur city bata dein "
                                  "(ek hi message mein likh dein).")
            else:
                got = ", ".join(f"{k}: {cust[k]}" for k in have)
                reply = self._say(s, f"Thanks{' ' + cust['name'].split()[0] if cust.get('name') else ''}! Got {got}. "
                                     f"Now please share your {', '.join(labels_en[k] for k in missing[:-1])}"
                                     f"{' and ' if len(missing) > 1 else ''}{labels_en[missing[-1]]}.",
                                  f"Shukriya! {got} note kar liya. Ab apna {' aur '.join(labels_ur[k] for k in missing)} "
                                  "bata dein.")
            return self._out(sid, s, reply, "place_order", "request_missing_info", 0.85, "ASK",
                             reason="missing_" + "_".join(missing), entities=entities)
        if not re.fullmatch(r"03\d{9}", cust.get("phone") or ""):
            s["awaiting"] = "details"
            cust.pop("phone", None)
            reply = self._say(s, "Please share a valid mobile number like 03001234567.",
                              "Sahi mobile number bata dein, jaise 03001234567.")
            return self._out(sid, s, reply, "place_order", "request_missing_info", 0.8, "ASK",
                             reason="invalid_phone", entities=entities)
        return self._summarize(sid, s, message, entities)

    def _summarize(self, sid, s, message, entities):
        items, product_total = [], 0
        for it in s["items"]:
            v = self.store.find_variant(it["product"], it.get("color"), it.get("size"))
            if not v or self.store.available(v["sku"]) < it["qty"]:
                return self._escalate(sid, s, "insufficient_stock", "place_order", entities, message)
            items.append({"sku": v["sku"], "qty": it["qty"], "price": v["price"],
                          "name": v["name"], "color": v["color"], "size": v["size"],
                          "product_id": v["product_id"]})
            product_total += v["price"] * it["qty"]
        city = s["customer"]["city"]
        delivery = self.tools.delivery_charge(city, product_total)
        grand = product_total + delivery
        hit = guardrails.check_order_totals(
            product_total, delivery, max(i["qty"] for i in items), self.rules)
        if hit:
            return self._escalate(sid, s, hit[0], "place_order", entities, message, hit[1])
        self.store.release(sid)
        if not self.store.reserve(sid, [(i["sku"], i["qty"]) for i in items],
                                  self.rules["order"]["reservation_minutes"]):
            return self._escalate(sid, s, "insufficient_stock", "place_order", entities, message)
        lo, hi = self.tools.delivery_eta(city)
        s["pending"] = {"items": items, "product_total": product_total,
                        "delivery": delivery, "grand": grand, "eta": [lo, hi],
                        "expires": time.time() + self.rules["order"]["reservation_minutes"] * 60}
        s["stage"] = "confirm"
        s["awaiting"] = "confirm"
        c = s["customer"]
        lines = "\n".join(f"• {i['name']} — {i['color']}, {i['size']} × {i['qty']} = {_money(i['price'] * i['qty'])}"
                          for i in items)
        dl = _money(delivery) if delivery else "FREE"
        if s.get("lang") == "ur":
            reply = (f"Order summary:\n{lines}\nProduct total: {_money(product_total)}\nDelivery ({city}): {dl}\n"
                     f"Grand total: {_money(grand)} (Cash on Delivery)\nDeliver to: {c['name']}, {c['phone']}, "
                     f"{c['address']}, {city}\nStock 15 minutes ke liye reserve kar diya hai — confirm karne ke liye "
                     "'haan' likhein, ya batayein kya badalna hai.")
            sugg = ["Haan confirm", "Size badlo", "Cancel"]
        else:
            reply = (f"Order summary:\n{lines}\nProduct total: {_money(product_total)}\nDelivery ({city}): {dl}\n"
                     f"Grand total: {_money(grand)} (Cash on Delivery)\nDeliver to: {c['name']}, {c['phone']}, "
                     f"{c['address']}, {city}\nI've reserved the stock for 15 minutes — reply 'yes' to confirm, "
                     "or tell me what to change.")
            sugg = ["Yes, confirm", "Change size", "Cancel"]
        tools = [{"name": "reserve_stock", "result": {"items": [i["sku"] for i in items], "minutes": 15}},
                 {"name": "delivery_charge", "result": {"city": city, "charge": delivery}}]
        return self._out(sid, s, reply, "place_order", "request_confirmation", 0.95, "ACT",
                         tools=tools, entities=entities, suggestions=sugg,
                         extra={"summary": s["pending"]})

    def _confirm_stage(self, sid, s, e, t, message, entities, ml_intent, ml_conf):
        words = set(t.split())
        deny = (words & DENY_WORDS) or re.search(r"rehne do|mat karo|dont|do not|not now|abhi nahi", t)
        if deny and not re.search(r"\bnahi\b.*\b(size|color|colour)\b", t):
            return self._cancel_cart(sid, s, entities)
        changes = e["size"] or e["color"] or e["qty"] or e["product"] or e["details"]["phone"] or \
            e["details"]["address"] or e["city"] or re.search(r"\bchange\b|\bbadal|\binstead\b", t)
        if detect_topic(t) and ("?" in message or re.search(r"\b(what|kya|how|kitne|kitna|do you|can i)\b", t)) \
                and not (e["size"] or e["qty"] or e["details"]["phone"] or e["details"]["address"]):
            return None      # a policy question: answer it, the summary keeps waiting
        affirm = (words & CONFIRM_WORDS) or re.search(r"bhej do|kar do|kardo|place (it|the order)|go ahead", t)
        if affirm and not (e["size"] or e["color"] or e["qty"] or e["product"] or e["city"]):
            return self._create_order(sid, s, entities)
        if changes:
            if re.search(r"\bchange\b|\bbadal|\bbadlo\b", t) and not (e["size"] or e["color"] or e["qty"] or e["product"]
                                                            or e["details"]["phone"] or e["details"]["address"] or e["city"]):
                reply = self._say(s, "Sure — what would you like to change? (size, color, quantity or address)",
                                  "Zaroor — kya badalna hai? (size, color, quantity ya address)")
                return self._out(sid, s, reply, "place_order", "request_confirmation", 0.8, "ASK",
                                 entities=entities, reminder=False,
                                 suggestions=["M", "L", "2 pieces"])
            self.store.release(sid)
            s["stage"] = "order_details"
            s["pending"] = {}
            if s["items"] and not e["product"]:
                it = s["items"][-1]
                for k in ("color", "size", "qty"):
                    if e[k]:
                        it[k] = e[k]
            d = e["details"]
            for k in ("phone", "address", "city"):
                if d.get(k):
                    s["customer"][k] = d[k]
            s["awaiting"] = None
            if e["product"]:
                return self._order_flow(sid, s, e, t, message, entities)
            e2 = dict(e); e2["product"] = None
            return self._order_flow(sid, s, e2, t, message, entities)
        if ml_intent == "affirm" and ml_conf >= 0.6:
            return self._create_order(sid, s, entities)
        if len(words) <= 3 and not re.search(r"\?", message):
            reply = self._say(s, "Please confirm the summary — reply 'yes' to place the order or 'cancel' to drop it.",
                              "Summary confirm kar dein — order place karne ke liye 'haan' likhein, ya 'cancel'.")
            return self._out(sid, s, reply, "place_order", "request_confirmation", 0.8, "ASK",
                             entities=entities, reminder=False,
                             suggestions=["Yes, confirm", "Cancel"])
        return None    # a different question: answer it, the summary keeps waiting

    def _create_order(self, sid, s, entities):
        p = s.get("pending", {})
        ids = self.store.create_orders(sid, s["customer"], p.get("items", []),
                                       s["customer"].get("city"), p.get("grand", 0),
                                       delivery=p.get("delivery", 0), channel="chat")
        if not ids:
            s["stage"] = "order_details"
            return self._escalate(sid, s, "reservation_expired_or_failed", "place_order", entities)
        grand = p.get("grand")
        lo, hi = p.get("eta", [2, 6])
        city = s["customer"].get("city")
        s["stage"] = None; s["items"] = []; s["pending"] = {}; s["awaiting"] = None
        self.store.audit(sid, "order_created", {"order_ids": ids, "grand_total": grand})
        idtxt = ", ".join(f"#{i}" for i in ids)
        reply = self._say(s, f"Thank you! 🎉 Your order is confirmed — order number {idtxt}. Grand total "
                             f"{_money(grand)}, payable in cash to the courier. Delivery to {city} takes "
                             f"{lo}-{hi} working days. You can track it here any time with your order number and phone.",
                          f"Shukriya! 🎉 Aap ka order confirm ho gaya hai — order number {idtxt}. Grand total "
                          f"{_money(grand)} COD par courier ko ada karna hai. {city} mein delivery {lo}-{hi} "
                          "working days mein hogi. Order number aur phone se kabhi bhi track kar sakte hain.")
        tools = [{"name": "create_order", "result": {"order_ids": ids, "grand_total": grand}}]
        return self._out(sid, s, reply, "place_order", "create_order", 0.98, "ACT", tools=tools,
                         entities=entities,
                         suggestions=(["Order track karo", "Aur shopping"] if s.get("lang") == "ur"
                                      else ["Track my order", "Continue shopping"]),
                         extra={"order": {"order_ids": ids, "grand_total": grand,
                                          "phone": s["customer"].get("phone")}})

    # ------------------------------------------------------- web checkout
    def quote(self, items, city=None):
        """Price a web cart: [{sku, qty}] -> totals, delivery, problems."""
        lines, product_total, problems = [], 0, []
        for it in items:
            v = self.store.variant(it.get("sku"))
            qty = int(it.get("qty") or 0)
            if not v or qty <= 0:
                problems.append({"sku": it.get("sku"), "problem": "invalid_item"})
                continue
            avail = self.store.available(v["sku"])
            if qty > self.rules["order"]["max_qty_per_item"]:
                problems.append({"sku": v["sku"], "problem": "quantity_above_limit",
                                 "max": self.rules["order"]["max_qty_per_item"]})
            if avail < qty:
                problems.append({"sku": v["sku"], "problem": "insufficient_stock", "available": avail})
            lines.append({"sku": v["sku"], "qty": qty, "price": v["price"], "name": v["name"],
                          "color": v["color"], "size": v["size"], "product_id": v["product_id"],
                          "available": avail, "line_total": v["price"] * qty})
            product_total += v["price"] * qty
        delivery = self.tools.delivery_charge(city, product_total) if lines else 0
        grand = product_total + delivery
        if grand > self.rules["payment"]["cod_max_total_without_human"]:
            problems.append({"problem": "cod_above_limit",
                             "limit": self.rules["payment"]["cod_max_total_without_human"]})
        lo, hi = self.tools.delivery_eta(city)
        return {"items": lines, "product_total": product_total, "delivery": delivery,
                "grand_total": grand, "eta_days": [lo, hi], "problems": problems,
                "free_delivery_min": self.rules["delivery"]["free_delivery_min_product_total"]}

    def checkout(self, session_id, items, customer):
        with self.lock:
            name = (customer.get("name") or "").strip()
            phone = nlu.extract_phone(customer.get("phone") or "")
            address = (customer.get("address") or "").strip()
            city = nlu.extract_city(customer.get("city") or "") or (customer.get("city") or "").strip().title()
            errors = {}
            if len(name) < 2:
                errors["name"] = "Please enter your name."
            if not phone:
                errors["phone"] = "Enter a valid mobile number like 03001234567."
            if len(address) < 6:
                errors["address"] = "Please enter your full address."
            if len(city) < 3:
                errors["city"] = "Please enter your city."
            if errors:
                return {"ok": False, "errors": errors}
            q = self.quote(items, city)
            if not q["items"]:
                return {"ok": False, "error": "Your cart is empty."}
            blocking = [p for p in q["problems"] if p["problem"] in ("invalid_item", "insufficient_stock")]
            if blocking:
                return {"ok": False, "error": "Some items are no longer available in that quantity.",
                        "problems": blocking, "quote": q}
            esc = [p for p in q["problems"] if p["problem"] in ("quantity_above_limit", "cod_above_limit")]
            if esc:
                reason = esc[0]["problem"]
                tid = self.store.add_escalation(session_id, reason, f"web checkout: {q['grand_total']}",
                                                f"Web cart: {', '.join(i['name'] + ' x' + str(i['qty']) for i in q['items'])}",
                                                None, name)
                return {"ok": False, "escalated": True, "ticket": tid, "reason": reason,
                        "error": ("Orders above Rs 25,000 COD need a staff member to confirm payment first."
                                  if reason == "cod_above_limit" else
                                  "Orders above 10 pieces of one item are handled by our staff.")
                        + f" We've opened ticket #{tid}; our team will contact you on {phone}."}
            sid = f"web-checkout-{session_id}-{int(time.time() * 1000)}"
            if not self.store.reserve(sid, [(i["sku"], i["qty"]) for i in q["items"]], 5):
                return {"ok": False, "error": "Some items just went out of stock. Please review your cart."}
            cust = {"name": name.title(), "phone": phone, "address": address}
            ids = self.store.create_orders(sid, cust, q["items"], city, q["grand_total"],
                                           delivery=q["delivery"], channel="web")
            if not ids:
                self.store.release(sid)
                return {"ok": False, "error": "Order could not be created, please try again."}
            self.store.audit(session_id, "order_created", {"order_ids": ids, "grand_total": q["grand_total"],
                                                           "channel": "web"})
            return {"ok": True, "order_ids": ids, "grand_total": q["grand_total"],
                    "delivery": q["delivery"], "product_total": q["product_total"],
                    "eta_days": q["eta_days"], "phone": phone, "city": city}
