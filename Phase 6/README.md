# F26-07 — AI Sales & Customer Support Agent

**Saira Fashion House** (fictional store, Gujranwala) sells online. Customers
write in Roman Urdu / English mix — *"bhai blk linen kurta mdm mein hai kya?"* —
and the agent turns that into real actions: show products, check stock and
price, place a Cash-on-Delivery order, track an order, start an exchange,
answer policy questions, or hand the chat to a human when the rules say it must.

The web app is a full storefront around the agent: catalog with filters,
product pages, cart and checkout, order tracking, customer accounts, and a
staff console where hand-offs are answered right inside the customer's chat.

## Run it

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:8000
```

The first run creates `shop.db` (SQLite) and loads `data/catalog.csv`
(572 variants, 50 products) and `data/orders.csv` (51 orders). Older
`shop.db` files are migrated automatically.

**Staff console:** sign in with `admin@saira.pk` / `Admin@1234` (the login
dialog shows this hint only while the default password is in use). Set
`ADMIN_EMAIL` / `ADMIN_PASSWORD` before deploying.

### Deploy

- **Render / Railway / Heroku-style hosts:** root directory `Phase 6`, build
  command `pip install -r requirements.txt`; the `Procfile` starts uvicorn.
- **Docker:** `docker build -t f26-07-agent . && docker run -p 8000:8000 -e ADMIN_PASSWORD=change-me f26-07-agent`
- Env vars (all optional): `PORT` (8000), `HOST` (127.0.0.1, only for
  `python app.py`), `DB_PATH` (`shop.db`), `ADMIN_EMAIL`, `ADMIN_PASSWORD`,
  `STORE_TODAY` (the date exchange windows are measured from; default
  `2026-10-17`, the dataset's "today").

## What's in the website

| Page | What it does |
|---|---|
| Home | Hero, categories, sale items, assistant intro, reviews |
| Shop | Search, Men/Women/Kids, 16 categories, sale, in-stock, price cap, sort, load more |
| Product | Color swatches, sizes (out-of-stock ones disabled), live stock, quantity, add to cart, "ask Eman" |
| Cart | Quantities, live delivery quote by city, free-delivery hint, COD-limit warning, checkout with validation |
| Track order | Order number + phone → status timeline, courier, tracking number, ETA |
| My orders | Orders placed with the account's phone (web or chat) |
| Chat (every page) | Product cards, quick-reply chips, ticket banner, staff replies, chat history kept across reloads, optional "Reasoning" view of each decision |
| Staff console | Dashboard · **Insights** (sales trend, revenue by category, chat→order conversion, restock tips, out-of-stock items customers asked for, messages the bot couldn't answer) · **Inventory** (search/filter variants, set stock or +5/+10, change price, add new products) · Hand-offs (transcript, reply into the customer's chat, resolve) · Orders (search, filter, change status) · Evaluation & model |

Built with Tailwind CSS (CDN, no build step) and vanilla JavaScript
(`static/index.html`, `static/app.js`). Dark mode included.

## How the agent works

```
message → NLU (agent/nlu.py)            Roman Urdu synonyms, fuzzy product match,
                                        product/color/size/qty/city/phone/order id/
                                        budget, product types (kurta, shoes, winter…),
                                        gender, name/address blocks, language
        → confirmation stage            yes / cancel / edit the cart ("size M kar do")
        → guardrails (guardrails.py)    refund, damaged, legal, angry, human request,
                                        discount, online payment, cancel/change,
                                        topics outside the policy
        → intent (engine.py)            high-precision rules first, then the trained
                                        per-message classifier (intent_model.py)
        → flows                         order · status · exchange · recommend; slots
                                        persist, and a side question mid-flow is
                                        answered without losing the cart
        → tools / policy                check_stock, product overview, browse,
          (tools.py, policy_rag.py)     recommend, delivery charge/ETA, order status
                                        (order id + matching phone), exchange rules;
                                        policy answers from policy_rules.json, then
                                        TF-IDF retrieval over policy.md with a gate —
                                        below the gate the agent hands over instead
                                        of guessing
        → reply                         English or Roman Urdu, varied wording,
                                        product cards, suggestions, trace
```

Hand-offs are **not a dead end**: the customer gets a ticket number, staff
reply into the same chat, and the agent keeps helping with anything it is
allowed to (browsing, ordering, tracking) while the ticket is open.

Every reply includes a `trace` (intent, action, entities, confidence, decision
ACT/ASK/BLOCK/HANDOFF, reason, route, model prediction, tools used).

## The intent model

```bash
python train_model.py     # ~5 s, no extra installs
```

- **Model:** TF-IDF over word 1-2 grams and character 3-4 grams, multinomial
  logistic regression trained with SGD — pure Python, saved as JSON
  (`models/intent_model.json`), so it loads on any Python version.
- **Data:** `data/intent_seeds.json` (hand-written EN + Roman Urdu utterances,
  22 intents incl. greeting, small talk, browse, affirm/deny) + templates built
  from the catalog/orders/cities + every customer turn of the **dev**
  conversations. **Test** conversations are never trained on.
- **Scores** (`models/metrics.json`): held-out accuracy 0.863 (macro F1 0.748,
  n=219 short, hard utterances); per-turn accuracy on the unseen test
  conversations 0.979 (n=96).

Safety-critical decisions (refunds, limits, privacy, COD cap) stay in
deterministic rules; the model routes everything else (greetings, browsing,
vague requests, paraphrases).

## Evaluation and tests

```bash
python evaluation.py         # replays all 172 labelled conversations (~3 s)
python tests/test_agent.py           # 44 unit checks, no pytest needed
python tests/test_conversations.py   # 112 realistic chats + inventory/insights + 1,800-message fuzz test
python F26-07.py             # scripted demo + evaluation
```

Evaluation gives each conversation a fresh database and compares the final
action and escalation decision with the labels: action accuracy **1.000**,
escalation precision / recall / F1 **1.000** (172/172; was 0.884 before this
rebuild). These conversations — dev *and* test — were used while fixing the
dialogue manager, so treat this as a replay score, not a blind test.

## API (selected)

`POST /api/chat` · `GET /api/chat/history` · `GET /api/products` ·
`GET /api/products/{id}` · `GET /api/categories` · `POST /api/cart/quote` ·
`POST /api/checkout` · `POST /api/track` · `POST /api/auth/{signup,login,logout}` ·
`GET /api/my/orders` · `GET|POST /api/reviews` — and, for staff:
`GET /api/dashboard` · `GET /api/orders` · `PATCH /api/orders/{id}` ·
`GET /api/escalations[/{id}]` · `POST /api/escalations/{id}/reply|resolve` ·
`GET /api/admin/inventory` · `PATCH /api/admin/variants/{sku}` · `POST /api/admin/products` ·
`GET /api/admin/insights` · `POST /api/evaluation/run` · `POST /api/test/contention` · `POST /api/reset`.
Interactive docs at `/docs`.

## Layout

```
app.py              FastAPI routes
agent/              nlu.py · intent_model.py · guardrails.py · policy_rag.py ·
                    tools.py · engine.py · data_store.py (SQLite)
data/               catalog.csv · orders.csv · conversations.json · policy.md ·
                    policy_rules.json · intent_seeds.json
models/             intent_model.json · metrics.json
static/             index.html · app.js · assets/
train_model.py      intent model training
evaluation.py       conversation replay harness
tests/              test_agent.py
```
