"""Zuordnung per Mehrfachabstimmung eines LLM, unabhängig vom Bildungsbereich.

Ablauf für eine Menge von Objekten (z. B. Themen) und ein Zielvokabular (z. B. Studienfächer):
1. Je Objekt eine Kandidatenliste (z. B. aus Embeddings, siehe embeddings.py).
2. n Stimmen: Das LLM wählt n-mal aus den Kandidaten, jedes Mal mit einer anderen
   Prompt-Perspektive, anderer Kandidaten-Reihenfolge und anderen Nachbarn im Batch.
   Zu jeder Wahl gibt es eine Begründung und Belegbegriffe aus dem Text des Objekts.
3. Auswertung: Übernommen wird, was mindestens min_stimmen-mal gewählt wurde. Die
   Konfidenz ist der Anteil der Stimmen; Belegbegriffe werden gegen den Text geprüft.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..llm import LLMClient, LLMConfig, run_tasks

FORMAT = """Antworte ausschließlich mit JSON:
{"ergebnisse": [{"id": "...", "auswahl": [{"code": "...", "begruendung": "...", "belegbegriffe": ["..."]}]}]}"""


@dataclass
class Ziel:
    code: str      # kurzer Schlüssel im Prompt, z. B. Notation
    uri: str
    label: str
    kontext: str = ""  # z. B. übergeordneter Begriff oder Kriterium

    def zeile(self) -> str:
        return f"{self.code} | {self.label}" + (f" | {self.kontext}" if self.kontext else "")


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def abstimmen(objekte: list[dict], kandidaten: dict[str, list[str]], ziele: dict[str, Ziel], systeme: list[str],
              out_path: Path, cfg: LLMConfig, max_auswahl: int = 3, batch: int = 10, on_progress=None,
              stop_event=None):
    """objekte: [{"id", "prompt": {...}}]; kandidaten: id -> Codes; systeme: ein Systemprompt je Stimme.

    Schreibt je Batch und Stimme eine Zeile nach out_path (fortsetzbar über run_tasks).
    """
    tasks = []
    for v, system in enumerate(systeme):
        order = sorted(objekte, key=lambda o: o["id"])
        random.Random(v).shuffle(order)
        for i in range(0, len(order), batch):
            chunk = order[i:i + batch]
            tid = f"v{v}:" + hashlib.sha256("|".join(o["id"] for o in chunk).encode()).hexdigest()[:16]
            tasks.append({"id": tid, "stimme": v, "objekte": chunk})

    def worker(client: LLMClient, task: dict) -> dict:
        v = task["stimme"]
        items = []
        for o in task["objekte"]:
            codes = list(kandidaten[o["id"]])
            random.Random(_seed(o["id"], v)).shuffle(codes)
            items.append({"id": o["id"], **o["prompt"], "kandidaten": [ziele[c].zeile() for c in codes]})
        res = client.chat_json(systeme[v] + "\n\n" + FORMAT, json.dumps(items, ensure_ascii=False))
        by_id = {str(r.get("id")): r for r in res.get("ergebnisse", []) if isinstance(r, dict)}
        antworten = {}
        for o in task["objekte"]:
            r = by_id.get(o["id"])
            if r is None:
                raise ValueError(f"keine Antwort für {o['id']}")
            erlaubt = set(kandidaten[o["id"]])
            wahl = []
            for a in r.get("auswahl") or []:
                code = str(a.get("code", "")).split("|")[0].strip() if isinstance(a, dict) else ""
                if code in erlaubt and code not in [w["code"] for w in wahl]:
                    wahl.append({"code": code, "begruendung": str(a.get("begruendung", "")).strip(),
                                 "belegbegriffe": [str(b).strip() for b in a.get("belegbegriffe", []) if str(b).strip()][:3]})
            antworten[o["id"]] = wahl[:max_auswahl]
        return {"id": task["id"], "stimme": v, "antworten": antworten}

    return run_tasks(tasks, worker, out_path, cfg, on_progress=on_progress, stop_event=stop_event)


def lade_stimmen(path: Path) -> dict[str, dict[int, list[dict]]]:
    """objekt-id -> {stimme: auswahl}"""
    out: dict[str, dict[int, list[dict]]] = defaultdict(dict)
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            for oid, wahl in row["antworten"].items():
                out[oid][row["stimme"]] = wahl
    return out


def auswerten(stimmen: dict[int, list[dict]], n_stimmen: int, text: str = "", min_stimmen: int = 2,
              max_auswahl: int = 3, aehnlichkeit: dict[str, float] | None = None) -> dict:
    """Wertet die Stimmen zu einem Objekt aus.

    status: "gesichert" (Hauptwahl in allen Stimmen und meist an erster Stelle),
            "wahrscheinlich" (Mehrheit), "unsicher" (keine Mehrheit oder Stimmen fehlen).
    """
    aehnlichkeit = aehnlichkeit or {}
    zahl, erst = Counter(), Counter()
    begr, belege = defaultdict(list), defaultdict(list)
    for wahl in stimmen.values():
        for pos, w in enumerate(wahl):
            zahl[w["code"]] += 1
            erst[w["code"]] += pos == 0
            if w["begruendung"] and w["begruendung"] not in begr[w["code"]]:
                begr[w["code"]].append(w["begruendung"])
            belege[w["code"]].extend(w["belegbegriffe"])
    rang = sorted(zahl, key=lambda c: (-zahl[c], -erst[c], -aehnlichkeit.get(c, 0.0), c))
    auswahl = [c for c in rang if zahl[c] >= min_stimmen][:max_auswahl] or rang[:1]
    low = text.casefold()
    details = []
    for c in auswahl:
        begriffe = list(dict.fromkeys(belege[c]))
        details.append({
            "code": c, "stimmen": zahl[c], "an_erster_stelle": erst[c], "konfidenz": round(zahl[c] / n_stimmen, 2),
            "aehnlichkeit": round(float(aehnlichkeit[c]), 3) if c in aehnlichkeit else None,
            "begruendungen": begr[c][:3],
            "belegbegriffe_im_text": [b for b in begriffe if b.casefold() in low][:5],
        })
    if not auswahl or len(stimmen) < n_stimmen or zahl[auswahl[0]] < min_stimmen:
        status = "unsicher"
    elif zahl[auswahl[0]] == n_stimmen and erst[auswahl[0]] >= max(1, n_stimmen - 1):
        status = "gesichert"
    else:
        status = "wahrscheinlich"
    verworfen = {c: zahl[c] for c in rang if c not in auswahl}
    return {"auswahl": auswahl, "status": status, "stimmen_abgegeben": len(stimmen), "details": details,
            "verworfen": verworfen}
