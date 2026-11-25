"""Per-message intent classifier — pure Python, no scikit-learn needed.

Features: TF-IDF over word unigrams, word bigrams and character 3/4-grams
(char n-grams make it robust to Roman Urdu spelling variation: 'chahiye',
'chahye', 'chaiye' share most of their n-grams). Entities are masked first
(phone -> ZPHONE, 5-digit id -> ZOID, other numbers -> ZNUM, colors ->
ZCOLOR) so the model learns the *shape* of a request, not specific values.

Model: multinomial logistic regression trained with mini-batch-free SGD and
L2 regularisation, stored as plain JSON (models/intent_model.json). JSON
instead of pickle means the model loads on any Python version, with no
library version mismatch — the reason the old sklearn pickle never loaded.
"""
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import nlu

_COLOR_RE = re.compile(r"\b(" + "|".join(sorted(
    {re.escape(s) for syns in nlu.COLOR_SYNONYMS.values() for s in syns},
    key=len, reverse=True)) + r")\b")


def mask(text):
    t = nlu.normalize(text)
    t = re.sub(r"03\d{9}", " zphone ", t)
    t = re.sub(r"\b[12]\d{4}\b", " zoid ", t)
    t = re.sub(r"\b\d+\b", " znum ", t)
    t = _COLOR_RE.sub(" zcolor ", t)
    return re.sub(r"\s+", " ", t).strip()


def features(text):
    t = mask(text)
    words = t.split()
    feats = Counter()
    for w in words:
        feats["w:" + w] += 1
    for a, b in zip(words, words[1:]):
        feats["b:" + a + "_" + b] += 1
    for w in words:
        if w.startswith("z") and w in ("zphone", "zoid", "znum", "zcolor"):
            continue
        p = f"<{w}>"
        for n in (3, 4):
            for i in range(len(p) - n + 1):
                feats["c:" + p[i:i + n]] += 1
    if not words:
        feats["w:<empty>"] = 1
    return feats


class IntentModel:
    def __init__(self, classes, idf, weights, bias, meta=None):
        self.classes = classes
        self.idf = idf
        self.weights = weights      # feature -> list[float] (one per class)
        self.bias = bias
        self.meta = meta or {}

    # ---------------------------------------------------------- vectorise
    def _vec(self, text):
        f = features(text)
        vec = {}
        for k, c in f.items():
            if k in self.idf:
                vec[k] = (1 + math.log(c)) * self.idf[k]
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {k: v / norm for k, v in vec.items()}

    def _scores(self, vec):
        s = list(self.bias)
        for k, v in vec.items():
            w = self.weights.get(k)
            if w:
                for j in range(len(s)):
                    s[j] += w[j] * v
        return s

    @staticmethod
    def _softmax(s):
        m = max(s)
        e = [math.exp(x - m) for x in s]
        z = sum(e)
        return [x / z for x in e]

    def predict_proba(self, text):
        p = self._softmax(self._scores(self._vec(text)))
        return sorted(zip(self.classes, p), key=lambda x: -x[1])

    def predict(self, text):
        intent, p = self.predict_proba(text)[0]
        return intent, p

    # ------------------------------------------------------------- train
    @classmethod
    def train(cls, texts, labels, epochs=18, lr=0.6, l2=1e-5, min_df=1, seed=7):
        classes = sorted(set(labels))
        cidx = {c: i for i, c in enumerate(classes)}
        feats = [features(t) for t in texts]
        df = Counter()
        for f in feats:
            df.update(f.keys())
        n = len(texts)
        idf = {k: math.log((1 + n) / (1 + c)) + 1 for k, c in df.items() if c >= min_df}
        model = cls(classes, idf, {}, [0.0] * len(classes))
        vecs = [model._vec(t) for t in texts]
        weights = defaultdict(lambda: [0.0] * len(classes))
        bias = [0.0] * len(classes)
        order = list(range(n))
        rng = random.Random(seed)
        K = len(classes)
        step = 0
        for ep in range(epochs):
            rng.shuffle(order)
            for i in order:
                step += 1
                eta = lr / (1 + 0.02 * ep)
                vec, y = vecs[i], cidx[labels[i]]
                s = list(bias)
                for k, v in vec.items():
                    w = weights[k]
                    for j in range(K):
                        s[j] += w[j] * v
                p = cls._softmax(s)
                p[y] -= 1.0
                for j in range(K):
                    bias[j] -= eta * p[j]
                for k, v in vec.items():
                    w = weights[k]
                    for j in range(K):
                        w[j] -= eta * (p[j] * v + l2 * w[j])
        model.weights = {k: [round(x, 5) for x in w] for k, w in weights.items()
                         if max(abs(x) for x in w) > 1e-4}
        model.bias = [round(b, 5) for b in bias]
        model.idf = {k: round(v, 4) for k, v in idf.items() if k in model.weights}
        return model

    # ------------------------------------------------------------ persist
    def save(self, path):
        Path(path).write_text(json.dumps({
            "type": "tfidf_logreg_v1", "classes": self.classes, "idf": self.idf,
            "weights": self.weights, "bias": self.bias, "meta": self.meta},
            separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(d["classes"], d["idf"], d["weights"], d["bias"], d.get("meta"))
