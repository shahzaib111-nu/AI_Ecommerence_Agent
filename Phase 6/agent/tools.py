"""Structured backend tools. Deterministic lookups over the SQLite store —
the agent never invents a price, a stock count or an order status.
"""
from datetime import date

from . import nlu
from .data_store import size_key


def _parse(d):
    return date.fromisoformat(d) if d else None


class Tools:
    def __init__(self, store, rules, today="2026-10-17"):
        self.store = store
        self.rules = rules
        self.today = _parse(today)

    # ---------------------------------------------------------------- stock
    def check_stock(self, product, color=None, size=None):
        variants = self.store.variants_of(product)
        if not variants:
            return {"found": False, "product": product}
        colors = sorted({v["color"] for v in variants})
        sizes = sorted({v["size"] for v in variants}, key=size_key)
        bad = {}
        if color and color not in colors:
            bad["color"] = color
        if size and size not in sizes:
            bad["size"] = size
        if bad:
            return {"found": False, "product": product, "invalid": bad,
                    "colors": colors, "sizes": sizes,
                    "alternatives": self._alternatives(product, color, size)}
        v = self.store.find_variant(product, color, size)
        avail = self.store.available(v["sku"])
        result = {"found": True, "sku": v["sku"], "product": v["name"],
                  "product_id": v["product_id"], "category": v["category"],
                  "color": v["color"], "size": v["size"], "price": v["price"],
                  "original_price": v["original_price"],
                  "is_sale": bool(v["is_sale"]), "available": avail,
                  "in_stock": avail > 0, "exact": bool(color and size),
                  "colors": colors, "sizes": sizes}
        if avail <= 0:
            result["alternatives"] = self._alternatives(product, v["color"], v["size"])
        return result

    def product_overview(self, product):
        """Price, colors, sizes and per-color availability of one product."""
        variants = self.store.variants_of(product)
        if not variants:
            return {"found": False, "product": product}
        by_color = {}
        for v in variants:
            a = self.store.available(v["sku"])
            c = by_color.setdefault(v["color"], {"color": v["color"], "available": 0, "sizes_in_stock": []})
            c["available"] += a
            if a > 0:
                c["sizes_in_stock"].append(v["size"])
        for c in by_color.values():
            c["sizes_in_stock"].sort(key=size_key)
        v0 = variants[0]
        return {"found": True, "product": v0["name"], "product_id": v0["product_id"],
                "category": v0["category"], "price": min(v["price"] for v in variants),
                "original_price": max(v["original_price"] for v in variants),
                "is_sale": bool(v0["is_sale"]), "description": v0["description"],
                "colors": sorted(by_color), "sizes": sorted({v["size"] for v in variants}, key=size_key),
                "by_color": [by_color[c] for c in sorted(by_color)],
                "available": sum(c["available"] for c in by_color.values())}

    def _alternatives(self, product, color=None, size=None):
        alts = []
        for v in self.store.variants_of(product):
            if (v["color"], v["size"]) == (color, size):
                continue
            avail = self.store.available(v["sku"])
            if avail > 0:
                alts.append({"sku": v["sku"], "color": v["color"],
                             "size": v["size"], "price": v["price"],
                             "available": avail})
        # prefer same color (different size), then same size (different color)
        alts.sort(key=lambda a: (a["color"] != color, a["size"] != size, size_key(a["size"])))
        return alts[:4]

    # ------------------------------------------------------------ recommend
    def recommend_products(self, budget=None, filters=None, gender=None,
                           mode="max", limit=6, exclude=()):
        if budget:
            lo, hi = (int(budget * 0.75), int(budget * 1.25)) if mode == "around" else (0, budget)
        else:
            lo, hi = 0, 10 ** 9
        prods = [p for p in self.store.product_summaries()
                 if p["available"] > 0 and lo <= p["price"] <= hi
                 and p["name"] not in exclude
                 and nlu.product_matches_filters(p["name"], p["category"], filters or [], gender)]
        if budget:
            prods.sort(key=lambda p: (abs(p["price"] - budget * (1.0 if mode == "around" else 0.85)), p["name"]))
        else:
            prods.sort(key=lambda p: (-p["available"], p["name"]))
        return prods[:limit]

    def browse(self, filters=None, gender=None, sale_only=False, limit=8):
        prods = [p for p in self.store.product_summaries()
                 if nlu.product_matches_filters(p["name"], p["category"], filters or [], gender)
                 and (not sale_only or p["is_sale"])]
        prods.sort(key=lambda p: (p["available"] <= 0, p["category"], p["price"]))
        return prods, prods[:limit]

    # ------------------------------------------------------------- delivery
    def delivery_charge(self, city, product_total):
        d = self.rules["delivery"]
        if product_total >= d["free_delivery_min_product_total"]:
            return 0
        if city and city.lower() in [c.lower() for c in d["tier_a_cities"]]:
            return d["tier_a_charge"]
        return d["default_charge"]

    def delivery_eta(self, city):
        d = self.rules["delivery"]
        if city and city.lower() in [c.lower() for c in d["tier_a_cities"]]:
            return tuple(d["eta_days_tier_a"])
        return tuple(d["eta_days_other"])

    # ---------------------------------------------------------- order status
    def get_order_status(self, order_id, phone):
        order = self.store.get_order(order_id)
        if not order:
            return {"found": False}
        if not phone or order["phone"] != phone:
            # privacy rule: share nothing at all on a mismatch
            return {"found": True, "phone_match": False}
        item = self.store.variant(order["sku"]) or {}
        return {"found": True, "phone_match": True, "order_id": order["order_id"],
                "status": order["status"], "courier": order["courier"],
                "tracking_no": order["tracking_no"], "city": order["city"],
                "total": order["total"], "order_date": order["order_date"],
                "delivered_date": order["delivered_date"], "qty": order["qty"],
                "item": f"{item.get('name', order['sku'])} ({item.get('color', '')}, {item.get('size', '')})",
                "eta_days": list(self.delivery_eta(order["city"]))}

    # -------------------------------------------------------------- exchange
    def check_exchange(self, order_id, phone, want_size=None, want_color=None,
                       want_product=None):
        order = self.store.get_order(order_id)
        if not order:
            return {"eligible": False, "reason": "order_not_found"}
        if not phone or order["phone"] != phone:
            return {"eligible": False, "reason": "phone_mismatch"}
        if order["status"] != "Delivered" or not order["delivered_date"]:
            return {"eligible": False, "reason": "order_not_delivered"}
        item = self.store.variant(order["sku"])
        if not item:
            return {"eligible": False, "reason": "item_not_found"}
        ex = self.rules["exchange"]
        days = (self.today - _parse(order["delivered_date"])).days
        if want_product and want_product.lower() != item["name"].lower():
            return {"eligible": False, "reason": "different_product_requested",
                    "days_since_delivery": days, "product": item["name"]}
        if days > ex["window_days"]:
            return {"eligible": False, "reason": "outside_7_day_window",
                    "days_since_delivery": days}
        if item["is_sale"] and not ex["sale_items_allowed"]:
            return {"eligible": False, "reason": "sale_item_not_exchangeable",
                    "days_since_delivery": days}
        if item["category"] in ex["non_exchangeable_categories"]:
            return {"eligible": False, "reason": "category_not_exchangeable",
                    "days_since_delivery": days}
        size = want_size or item["size"]
        color = want_color or item["color"]
        repl = self.store.find_variant(item["name"], color, size)
        if not repl or repl["sku"] == item["sku"]:
            return {"eligible": False, "reason": "replacement_not_specified",
                    "days_since_delivery": days}
        if self.store.available(repl["sku"]) <= 0:
            return {"eligible": False, "reason": "replacement_out_of_stock",
                    "days_since_delivery": days}
        return {"eligible": True, "days_since_delivery": days,
                "replacement_sku": repl["sku"], "product": item["name"],
                "old_sku": item["sku"], "old": f"{item['color']} {item['size']}",
                "new": f"{repl['color']} {repl['size']}",
                "exchange_courier_charge": ex["exchange_courier_charge"]}
