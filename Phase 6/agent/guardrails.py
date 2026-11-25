"""Guardrail layer, driven by data/policy_rules.json.

Runs before any tool that changes state. Returns (reason, detail) for the
first rule that fires, else None. The AI never approves refunds, never
confirms COD orders above the limit, and never takes quantities above the
per-item limit — those always go to a human.
"""
import json
import re
from pathlib import Path

from .nlu import normalize

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "policy_rules.json"


def load_rules(path=None):
    return json.loads(Path(path or RULES_PATH).read_text(encoding="utf-8-sig"))


PATTERNS = [
    ("legal_threat", r"\b(court|wakeel|vakeel|lawyer|legal action|police|fia|consumer court|sue)\b"),
    ("angry_customer", r"\b(bakwas|fraud|bekar|ghatiya|bewakoof|cheat|cheater|dhoka|dhokay|loot|"
                       r"worst|thug|pagal|sharam|pathetic|scam|scammer|disgusting|useless|nonsense)\b"),
    ("damaged_or_wrong_item", r"\b(damaged|defective|torn|broken|ripped|stain|stained)\b|phat|kharab|"
                              r"wrong item|ghalat (item|cheez|size|color)|galat (item|cheez|size|color)|"
                              r"toot gaya|toota|rang utar|color fade|wrong size|wrong colou?r|"
                              r"(ghalat|galat) size aa|size (galat|ghalat) aa|colou?r different|different colou?r|"
                              r"picture se (alag|different)|kapra patla|quality (kharab|bekar|achi nahi|bad|poor)|"
                              r"bad quality|poor quality|nuqs|fake (hai|item|product)"),
    ("refund_request", r"\brefund\b|paisay wapas|paise wapas|paisay wapis|paise wapis|money back|"
                       r"\breturn\b.*(money|pais)|wapas kar.*pais|paise lauta"),
    ("return_request", r"\breturn karna\b|\bwant to return\b|\breturn this\b|\breturn kar\b|wapas lo\b"),
    ("human_request", r"\binsan\b|\binsaan\b|owner se|\bhuman\b|banda (bula|se|chahiye|do)|staff se baat|"
                      r"manager se|real banda|bot se nahi|real person|\bagent se\b|live agent|"
                      r"customer care se|call (me|karo|kar)|mujhe call|manager|(phone|call) karo|"
                      r"bulao|baat karao|talk to (someone|a person|staff|an agent)|speak to"),
    ("wrong_item_received", r"mangwaya tha|galat (bhej|aaya|aya)|ghalat (bhej|aaya|aya)|wrong (bhej|sent)"),
    ("discount_bargaining", r"\b(discount|bargain|bargaining)\b|kam karo|kam kar do|kam kardo|sasta karo|"
                            r"sasta kar do|rate kam|price kam|special price|deal karo|last price|"
                            r"final price|best price|price (bohat|bahut|bohot|bht) (zyada|ziada|zada)|"
                            r"mehenga hai|mehnga hai|thora kam|kuch kam"),
    ("online_payment_request", r"easypaisa|easy paisa|jazzcash|jazz cash|bank transfer|account number|"
                               r"online payment|card se pay|pay online|credit card|debit card|"
                               r"advance payment|bank details|iban"),
    ("order_cancel_request", r"\bcancel\b|order band|mansookh|order nahi chahiye|nahi chahiye ab|"
                             r"dont want (the|my|this) order|do not want (the|my) order"),
    ("order_change_request", r"address (galat|ghalat|wrong)|wrong address|(phone|mobile) number change|"
                             r"number change karna|address change|change (my )?address|\bchange\b[^.]*\border\b|"
                             r"\border\b[^.]*\bchange\b|galat address|ghalat address|order update"),
]

# topics policy.md simply does not cover: the agent must not invent an answer
OUTSIDE_POLICY = (r"dry clean|how to wash|wash (ho|kar|karna|karte|it|this)|washable|dhulai|dho (sakt|skt)|"
                  r"iron (kar|ho)|ironing|reseller|resell|shrink|warranty|guarantee|fade hota|rang (jata|chalta)|stitching|"
                  r"silai|alteration|tailor|rent par|iron hota|gift wrap|message card|"
                  r"custom (size|order|design)|wholesale|franchise|job|vacancy|careers|"
                  r"cash back|cashback|loyalty")

INFORMATIONAL = (r"\bpolicy\b|kitne din|how long|how many days|process|kab tak|kya hai\b|can i\b|"
                 r"kar sakt|ho sakt|possible|allowed")


def check_text(text, rules=None):
    t = normalize(text)
    for reason, pat in PATTERNS:
        if re.search(pat, t):
            # 'return' inside an exchange request is an exchange, not a refund
            if reason == "return_request" and "exchange" in t:
                continue
            # "what is your refund policy?" is a question, not a request
            if reason in ("refund_request", "return_request") and re.search(INFORMATIONAL, t) \
                    and not re.search(r"chahiye|karo|kar do|i want|mujhe|de do|dedo", t):
                continue
            if reason == "order_cancel_request" and re.search(r"cancel(lation)? policy", t):
                continue
            # "discount code hai?" / "koi offer?" asks about promotions — answered, not escalated
            if reason == "discount_bargaining" and re.search(r"\b(code|coupon|voucher|promo|offer|sale)\b", t) \
                    and not re.search(r"kam kar|kam karo|sasta kar|de do|dedo|give me", t):
                continue
            return reason, f"guardrail matched: {reason}"
    # escalation keywords straight from policy_rules.json as a backstop
    if rules:
        for kw in rules.get("escalation_keywords", []):
            if kw in ("return", "human", "refund", "cancel", "discount"):
                continue
            if re.search(rf"\b{re.escape(kw)}\b", t):
                return "policy_keyword_" + kw.replace(" ", "_"), f"keyword: {kw}"
    return None


def outside_policy(text):
    return bool(re.search(OUTSIDE_POLICY, normalize(text)))


def check_order_totals(product_total, delivery, qty_per_item, rules):
    """Return (reason, detail) if an order about to be confirmed breaks a rule."""
    if qty_per_item and qty_per_item > rules["order"]["max_qty_per_item"]:
        return ("quantity_above_limit",
                f"qty {qty_per_item} > max {rules['order']['max_qty_per_item']}")
    grand = product_total + delivery
    if grand > rules["payment"]["cod_max_total_without_human"]:
        return ("cod_above_limit",
                f"grand total {grand} > COD limit "
                f"{rules['payment']['cod_max_total_without_human']}")
    return None
