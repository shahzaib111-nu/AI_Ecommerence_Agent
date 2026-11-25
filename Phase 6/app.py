"""F26-07 — Saira Fashion House AI Sales & Customer Support Agent.

Run:  pip install -r requirements.txt
      python app.py
Then open http://127.0.0.1:8000

The runtime agent is pure stdlib (see agent/); FastAPI only serves HTTP.
Staff pages (dashboard, hand-offs, orders, evaluation) need a staff login:
ADMIN_EMAIL / ADMIN_PASSWORD (defaults admin@saira.pk / Admin@1234).
"""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import nlu
from agent.data_store import ORDER_STATUSES, Store
from agent.engine import Engine

BASE = Path(__file__).resolve().parent
store = Store(os.environ.get("DB_PATH") or BASE / "shop.db")
engine = Engine(store, today=os.environ.get("STORE_TODAY", "2026-10-17"))

app = FastAPI(title="Saira Fashion House", version="2.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


# ------------------------------------------------------------------ models
class ChatIn(BaseModel):
    session_id: str = Field("web-1", max_length=80)
    message: str = Field(..., max_length=2000)


class AuthIn(BaseModel):
    name: str | None = None
    email: str
    phone: str | None = None
    password: str


class ReviewIn(BaseModel):
    stars: int
    user_name: str | None = None
    comment: str | None = None


class CartItem(BaseModel):
    sku: str
    qty: int = 1


class QuoteIn(BaseModel):
    items: list[CartItem]
    city: str | None = None


class CheckoutIn(BaseModel):
    session_id: str = "web-1"
    items: list[CartItem]
    name: str
    phone: str
    address: str
    city: str


class TrackIn(BaseModel):
    order_id: str
    phone: str


class StatusIn(BaseModel):
    status: str


class StaffReplyIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class ContentionIn(BaseModel):
    sku: str | None = None
    product_id: str | None = None
    size: str | None = None
    customers: int = Field(5, ge=2, le=64)
    qty: int = Field(1, ge=1, le=10)


# ------------------------------------------------------------------- auth
def current_user(authorization: str | None = Header(default=None)):
    return store.user_by_token((authorization or "").replace("Bearer ", "").strip())


def require_admin(user=Depends(current_user)):
    if not user:
        raise HTTPException(401, "staff login required")
    if user.get("role") != "admin":
        raise HTTPException(403, "staff only")
    return user


def err(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


# ------------------------------------------------------------------ pages
@app.get("/")
def home():
    idx = BASE / "static" / "index.html"
    return FileResponse(idx) if idx.exists() else JSONResponse(
        {"service": "F26-07 agent", "docs": "/docs"})


@app.get("/assets/{name}")
def asset(name: str):
    p = BASE / "static" / "assets" / Path(name).name
    return FileResponse(p) if p.exists() else err("asset not found", 404)


@app.get("/api/status")
def status():
    prods = store.product_summaries()
    return {"ok": True, "service": "F26-07 Saira Fashion House Agent",
            "products": len(prods), "variants": store.catalog_search(limit=1)["total"],
            "categories": len({p["category"] for p in prods}),
            "ml_model_loaded": engine.ml_model is not None,
            "model": {k: engine.model_info.get(k) for k in
                      ("trained_on", "classes", "heldout_accuracy", "test_turn_accuracy", "created")},
            # the login dialog shows the demo staff login only while the
            # default password is in use (set ADMIN_PASSWORD when deploying)
            "demo_staff_login": "ADMIN_PASSWORD" not in os.environ}


# ------------------------------------------------------------------ auth
@app.post("/api/auth/signup")
def signup(body: AuthIn):
    if not body.name or not body.name.strip():
        return err("Please enter your name.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email.strip()):
        return err("Please enter a valid email address.")
    phone = nlu.extract_phone(body.phone or "")
    if not phone:
        return err("Enter a valid mobile number like 03001234567.")
    pw = body.password
    if len(pw) < 8:
        return err("Password must be at least 8 characters long.")
    if not any(c.isdigit() for c in pw):
        return err("Password must contain a number.")
    if not any(not c.isalnum() for c in pw):
        return err("Password must contain a symbol such as @ # or !")
    uid = store.create_user(body.name.strip(), body.email.strip(), phone, pw)
    if uid is None:
        return err("An account with this email already exists.", 409)
    user = {"id": uid, "name": body.name.strip(), "email": body.email.strip().lower(),
            "phone": phone, "role": "customer"}
    return {"token": store.new_token(uid), "user": user}


@app.post("/api/auth/login")
def login(body: AuthIn):
    user = store.verify_user(body.email, body.password)
    if not user:
        return err("Email or password is incorrect.", 401)
    return {"token": store.new_token(user["id"]), "user": user}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    store.drop_token((authorization or "").replace("Bearer ", "").strip())
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return {"user": user}


@app.get("/api/my/orders")
def my_orders(user=Depends(current_user)):
    if not user:
        return err("login required", 401)
    return {"user": user, "orders": store.orders_for_phone(user["phone"])}


# --------------------------------------------------------------- catalog
@app.get("/api/categories")
def categories():
    return {"categories": store.categories()}


@app.get("/api/products")
def products(q: str | None = None, category: str | None = None,
             gender: str | None = None, in_stock: bool = False, sale: bool = False,
             min_price: int | None = None, max_price: int | None = None,
             sort: str = "featured", limit: int = Query(24, le=200), offset: int = 0):
    items = store.product_summaries()
    if q:
        ql = q.lower().strip()
        filters = nlu.extract_type_filters(ql)
        words = [w for w in re.split(r"\s+", ql) if w]

        def hit(p):
            hay = " ".join([p["name"], p["category"], " ".join(p["colors"]), p["description"] or ""]).lower()
            if all(w in hay for w in words):
                return True
            return bool(filters) and nlu.product_matches_filters(p["name"], p["category"], filters)
        items = [p for p in items if hit(p)]
    if category:
        items = [p for p in items if p["category"].lower() == category.lower()]
    if gender in ("men", "women", "kids"):
        items = [p for p in items if p["category"].lower().startswith(gender)]
    if in_stock:
        items = [p for p in items if p["available"] > 0]
    if sale:
        items = [p for p in items if p["is_sale"]]
    if min_price is not None:
        items = [p for p in items if p["price"] >= min_price]
    if max_price is not None:
        items = [p for p in items if p["price"] <= max_price]
    keyf = {"price_asc": lambda p: (p["price"], p["name"]),
            "price_desc": lambda p: (-p["price"], p["name"]),
            "name": lambda p: p["name"],
            "stock": lambda p: (-p["available"], p["name"])}.get(
        sort, lambda p: (p["available"] <= 0, not p["is_sale"], p["category"], p["name"]))
    items.sort(key=keyf)
    for p in items:
        p.pop("color_stock", None)
    return {"total": len(items), "items": items[offset:offset + limit]}


@app.get("/api/products/{product_id}")
def product(product_id: str):
    p = store.product_detail(product_id)
    return p if p else err("product not found", 404)


@app.get("/api/catalog")
def catalog(q: str | None = None, category: str | None = None,
            in_stock: bool = False, limit: int = Query(50, le=600), offset: int = 0):
    """Variant-level catalog (one row per SKU)."""
    return store.catalog_search(q=q, category=category, in_stock=in_stock,
                                limit=limit, offset=offset)


# ------------------------------------------------------------------ chat
@app.post("/api/chat")
def chat(body: ChatIn, user=Depends(current_user)):
    if not body.message.strip():
        return err("message is empty")
    return engine.handle(body.session_id, body.message, user=user)


@app.get("/api/chat/history")
def chat_history(session_id: str, after: int = 0):
    msgs = store.messages_for(session_id, after)
    tickets = store.list_escalations(session_id=session_id)
    return {"messages": [{"id": m["id"], "role": m["role"], "text": m["text"], "ts": m["ts"],
                          "meta": m["meta"]} for m in msgs],
            "open_tickets": [t["id"] for t in tickets if t["status"] == "open"],
            "state": engine.session_state(session_id)}


# ------------------------------------------------------------ cart / web
@app.post("/api/cart/quote")
def cart_quote(body: QuoteIn):
    return engine.quote([i.model_dump() for i in body.items], body.city)


@app.post("/api/checkout")
def checkout(body: CheckoutIn):
    res = engine.checkout(body.session_id, [i.model_dump() for i in body.items],
                          {"name": body.name, "phone": body.phone,
                           "address": body.address, "city": body.city})
    return res if res.get("ok") else JSONResponse(res, status_code=409 if res.get("escalated") else 400)


@app.post("/api/track")
def track(body: TrackIn):
    oid = re.sub(r"\D", "", body.order_id)
    phone = nlu.extract_phone(body.phone)
    if not oid or not phone:
        return err("Enter your order number and the phone number used on the order.")
    res = engine.tools.get_order_status(int(oid), phone)
    if not res["found"]:
        return err("No order found with that number.", 404)
    if not res["phone_match"]:
        return err("We couldn't verify this order with that phone number.", 403)
    return res


# ---------------------------------------------------------------- reviews
@app.post("/api/reviews")
def add_review(body: ReviewIn, user=Depends(current_user)):
    if not user:
        return err("Please sign in first.", 401)
    if body.stars < 1 or body.stars > 5:
        return err("stars must be 1 to 5")
    store.add_review(body.stars, user["name"], body.comment)
    return {"ok": True, "summary": store.reviews_summary()}


@app.get("/api/reviews")
def reviews():
    return store.reviews_summary()


# ------------------------------------------------------------------ staff
@app.get("/api/dashboard")
def dashboard(_=Depends(require_admin)):
    return store.dashboard()


@app.get("/api/orders")
def orders(limit: int = Query(100, le=500), status: str | None = None,
           q: str | None = None, _=Depends(require_admin)):
    return {"orders": store.list_orders(limit, status, q), "statuses": ORDER_STATUSES}


@app.patch("/api/orders/{order_id}")
def set_order_status(order_id: int, body: StatusIn, user=Depends(require_admin)):
    o = store.update_order_status(order_id, body.status)
    if not o:
        return err("order not found or invalid status", 404)
    store.audit(None, "order_status", {"order_id": order_id, "status": body.status, "by": user["email"]})
    return o


@app.get("/api/escalations")
def escalations(status: str | None = None, _=Depends(require_admin)):
    return {"escalations": store.list_escalations(status)}


@app.get("/api/escalations/{esc_id}")
def escalation(esc_id: int, _=Depends(require_admin)):
    esc = store.get_escalation(esc_id)
    if not esc:
        return err("not found", 404)
    msgs = store.messages_for(esc["session_id"])
    return {"escalation": esc, "transcript": [
        {"id": m["id"], "role": m["role"], "text": m["text"], "ts": m["ts"],
         "action": (m["meta"] or {}).get("action")} for m in msgs]}


@app.post("/api/escalations/{esc_id}/reply")
def staff_reply(esc_id: int, body: StaffReplyIn, user=Depends(require_admin)):
    esc = store.get_escalation(esc_id)
    if not esc:
        return err("not found", 404)
    mid = store.log_message(esc["session_id"], "staff", body.text.strip(),
                            {"by": user["name"], "ticket": esc_id})
    store.audit(esc["session_id"], "staff_reply", {"ticket": esc_id, "by": user["email"]})
    return {"ok": True, "message_id": mid}


@app.post("/api/escalations/{esc_id}/resolve")
def resolve(esc_id: int, _=Depends(require_admin)):
    ok = store.resolve_escalation(esc_id)
    return {"resolved": ok, "id": esc_id}


@app.get("/api/model")
def model_info(_=Depends(require_admin)):
    import json
    mp = BASE / "models" / "metrics.json"
    metrics = json.loads(mp.read_text()) if mp.exists() else None
    if metrics:
        for k in ("heldout", "test_conversation_turns"):
            metrics.get(k, {}).pop("errors", None)
    return {"loaded": engine.ml_model is not None, "info": engine.model_info, "metrics": metrics}


_eval_lock = threading.Lock()


@app.post("/api/evaluation/run")
def evaluation_run(_=Depends(require_admin)):
    from evaluation import run_evaluation
    if not _eval_lock.acquire(blocking=False):
        return err("an evaluation is already running", 409)
    try:
        t0 = time.time()
        result = run_evaluation(store_factory=lambda p: Store(p, seed_admin=False))
        result["seconds"] = round(time.time() - t0, 1)
        store.set_kv("last_evaluation", result)
        return result
    finally:
        _eval_lock.release()


@app.get("/api/evaluation")
def evaluation_last(_=Depends(require_admin)):
    return store.get_kv("last_evaluation") or {"status": "not run yet"}


@app.post("/api/test/contention")
def contention(body: ContentionIn, _=Depends(require_admin)):
    """N simulated customers race to reserve the same variant.

    The atomic reserve (BEGIN IMMEDIATE + stock check) must guarantee that
    total reserved never exceeds the stock on hand.
    """
    sku = body.sku
    if not sku and body.product_id:
        rows = [r for r in store.catalog_search(limit=1000)["items"]
                if r["product_id"] == body.product_id
                and (not body.size or r["size"] == body.size)]
        sku = rows[0]["sku"] if rows else None
    if not sku or not store.variant(sku):
        return err("sku or product_id not found", 404)
    before = store.available(sku)
    tag = f"contention-{int(time.time() * 1000)}"

    def try_buy(i):
        return store.reserve(f"{tag}-{i}", [(sku, body.qty)], minutes=15)

    with ThreadPoolExecutor(max_workers=body.customers) as ex:
        results = list(ex.map(try_buy, range(body.customers)))
    won = sum(1 for r in results if r)
    after = store.available(sku)
    for i in range(body.customers):          # a test must not keep stock locked
        store.release(f"{tag}-{i}")
    return {"sku": sku, "customers": body.customers, "qty_each": body.qty,
            "available_before": before, "reservations_won": won,
            "reserved_units": won * body.qty, "available_after": after,
            "oversold": won * body.qty > before}


@app.post("/api/reset")
def reset(_=Depends(require_admin)):
    engine.reset()
    return {"reset": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")))
