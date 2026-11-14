"""Themen aus der WLO-Themen-Abstraktion für die Seeds (Herkunft „wlo_abstraktion“).

`python -m sem wlo-themen --zusammenfuehren` liest die Abstraktion (sem/wlo_themen.py) und schreibt je
Bildungsbereich eine Themen-Datei nach data/lokal/wlo_abstraktion/ (themen_schule.jsonl, themen_hochschule.jsonl).
Die Bereiche Schule und Hochschule laden sie, wenn vorhanden, als weitere Themenquelle; über die Filter
herkunft/quellen ist sie an- und abschaltbar, über wlo_anteil der Anteil der Seeds steuerbar.

- Schule: Primarstufe, Sekundarstufe I und II aus der Bildungsstufe der WLO-Datensätze, Fach = OEH discipline.
- Hochschule: Fach = OEH-Hochschulfächersystematik (Destatis), Lehrebene offen.
- Berufs-, Erwachsenen- und Elementarbildung: Die Abstraktion läuft mit, gemappt wird standardmäßig nicht
  (--bereiche; die Bereiche haben noch keinen Seed-Bau für diese Themen, gezählt wird in der Statistik).

Dubletten werden verknüpft, nicht gelöscht: gleiches normalisiertes Thema im selben Bereich und Fach → ein Thema mit
allen Node-IDs und deren Zahl als Gewicht. Nahe Dubletten (Embedding-Ähnlichkeit) und Überschneidungen mit den
vorhandenen Themen des Bereichs (Themenbaum, Keywords, OpenAlex, GND, RVK, Fachprofile) stehen in "verknuepft_mit".
Fachbegriffe werden gegen die GND-Labels der vorhandenen Mappings abgeglichen, auf Wunsch der Rest gegen lobid
(--gnd-online, derselbe Weg wie gnd-ergaenzen). Quelltexte stehen in keiner Datei, nur Node-IDs.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from . import paths
from .bereiche.base import WLO_THEMA_PREFIX
from .wlo_themen import CTX, HFS, lade_jsonl, norm, ordner, vokabulare

GND_PREFIX = "https://d-nb.info/gnd/"
SCHULSTUFEN = {CTX + "grundschule": "primar", CTX + "sekundarstufe_1": "sek1", CTX + "sekundarstufe_2": "sek2"}
BEREICHE_STUFE = {CTX + "berufliche_bildung": "berufsbildung", CTX + "erwachsenenbildung": "erwachsenenbildung",
                  CTX + "elementarbereich": "elementar", CTX + "hochschule": "hochschule"}
STANDARD_BEREICHE = ("schule", "hochschule")
QUELLE = {"name": "WirLernenOnline (Themen-Abstraktion aus Titel und Beschreibung der Datensätze)",
          "uri": "https://wirlernenonline.de",
          "lizenz": "CC0-1.0 (vom Projekt abstrahiertes Thema; Titel und Beschreibungen nicht übernommen)"}
METHODE = ("LLM-Themen-Abstraktion aus Titel und Beschreibung von WLO-Datensätzen (Kopierprüfung bestanden); Fach "
           "und Bildungsstufe aus den Metadaten der Datensätze{vorschlag}; gleiche Themen je Fach zusammengefasst")
# Ähnlichkeit (multilingual-e5-small, beide Seiten mit „query:“) ab der verknüpft wird; gemessen im 2.000er-Testlauf
SCHWELLE_DUBLETTE = 0.93
SCHWELLE_VORHANDEN = 0.92
MAX_VERKNUEPFUNGEN = 3
DATEIEN = {"schule": "themen_schule.jsonl", "hochschule": "themen_hochschule.jsonl"}


def themen_pfad(bereich: str) -> Path:
    return ordner() / DATEIEN[bereich]


def themen(bereich: str) -> list[dict]:
    """Themen eines Bereichs aus der lokalen Datei (leer, wenn es sie nicht gibt)."""
    p = themen_pfad(bereich) if bereich in DATEIEN else None
    return lade_jsonl(p) if p and p.exists() else []


def belege_kompakt(topic: dict) -> dict:
    """Nachweise für den Seed: Zahl der WLO-Datensätze, bis zu 10 Node-Links, Verknüpfungen, GND der Fachbegriffe."""
    from .quellen.wlo import NODE_URI

    w = topic["wlo"]
    return {"wlo": {"datensaetze": w["gewicht"], "quelle": QUELLE["uri"], "vorschlag": w["vorschlag"],
                    "nodes": [NODE_URI.format(n) for n in w["node_ids"][:10]]},
            "verknuepft_mit": [{k: x[k] for k in ("id", "label", "art", "aehnlichkeit")}
                               for x in topic.get("verknuepft_mit") or []],
            "fachbegriffe_gnd": [f["gnd"]["uri"] for f in topic.get("fachbegriffe") or [] if f.get("gnd")]}


# --- GND ----------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def gnd_index() -> dict[str, dict]:
    """normalisiertes Label → {"uri", "label"} aus den GND-Treffern aller Mappings im Repo (Themen, RVK-Verweise,
    Ersatz für Motiv-Sätze)."""
    out: dict[str, dict] = {}

    def dazu(label, uri, gnd_label=None):
        k = norm(label)
        if k and uri and k not in out:
            out[k] = {"uri": uri, "label": gnd_label or label}

    for p in sorted(paths.MAPPING_DIR.glob("*/themen_*.jsonl")):
        for d in lade_jsonl(p):
            g = d.get("gnd")
            if str(d.get("id", "")).startswith(GND_PREFIX):
                lab = d.get("label_gnd") or d.get("label_de")
                for x in (d.get("label_gnd"), d.get("label_de")):
                    dazu(x, d["id"], lab)
            elif isinstance(g, dict) and str(g.get("uri", "")).startswith(GND_PREFIX):
                dazu(g.get("label"), g["uri"])
                dazu(d.get("label_de"), g["uri"], g.get("label"))
            elif isinstance(g, list):  # RVK: [[gnd_id, label], …]
                for e in g:
                    if isinstance(e, list) and len(e) == 2:
                        dazu(e[1], GND_PREFIX + e[0])
    erg = paths.MAPPING_DIR / "gnd_ergaenzungen.jsonl"
    if erg.exists():
        for d in lade_jsonl(erg):
            m = d.get("motiv_ersatz")
            if m:
                dazu(m.get("label"), m.get("uri"))
    return out


def gnd_abgleich(begriffe: list[str], index: dict[str, dict], online: dict[str, dict | None] | None = None) -> list:
    """Je Fachbegriff {"label", "gnd": {"uri", "label"} oder None}; online = Ergebnisse von lobid (norm → Treffer)."""
    out = []
    for b in begriffe:
        g = index.get(norm(b)) or (online or {}).get(norm(b))
        out.append({"label": b, "gnd": {"uri": g["uri"], "label": g["label"]} if g else None})
    return out


def gnd_online(begriffe: list[str], workers: int = 8, on_progress=None) -> dict[str, dict | None]:
    """Exakte Suche bei lobid (zwischengespeichert in data/cache), wie bei gnd-ergaenzen."""
    from concurrent.futures import ThreadPoolExecutor

    from .quellen import gnd

    labels = sorted({b for b in begriffe if len(norm(b)) >= 3})
    out: dict[str, dict | None] = {}

    def suche(b):
        try:
            r = gnd.suche_exakt(b)
        except Exception:  # noqa: BLE001 - ein fehlender Treffer ist kein Abbruchgrund
            r = None
        return b, ({"uri": r["uri"], "label": r["label"]} if r and gnd.ist_thema(r) else None)

    with ThreadPoolExecutor(workers) as ex:
        for i, (b, r) in enumerate(ex.map(suche, labels), 1):
            out[norm(b)] = r
            if on_progress and i % 200 == 0:
                on_progress(f"lobid: {i} von {len(labels)}")
    return out


# --- Themen bauen -------------------------------------------------------------------------------------

def _hash(*teile: str) -> str:
    return hashlib.sha1("\x1f".join(teile).encode("utf-8")).hexdigest()[:16]


def ziele(rec: dict) -> dict[str, dict]:
    """Bereiche, für die eine Abstraktion taugt: {bereich: {"faecher": [uri], "stufen": [...]}}."""
    out: dict[str, dict] = {}
    stufen = [s["uri"] for s in rec["stufe"]]
    disc = [f["uri"] for f in rec["fach"] if f["uri"] in vokabulare()["discipline"].by_uri]
    hfs = [f["uri"] for f in rec["fach"] if HFS in f["uri"]]
    schul = [SCHULSTUFEN[s] for s in stufen if s in SCHULSTUFEN]
    if schul and disc:
        out["schule"] = {"faecher": disc, "stufen": schul}
    for s in stufen:
        b = BEREICHE_STUFE.get(s)
        if b == "hochschule" and hfs:
            out[b] = {"faecher": hfs, "stufen": []}
        elif b and b != "hochschule":
            out[b] = {"faecher": disc, "stufen": []}
    return out


def _hfs_ebenen(uris: list[str]) -> dict[str, list[dict]]:
    """Hochschulfächer nach Ebene: studienfach (2), studienbereich (1), faechergruppe (0), Eltern ergänzt. Ohne
    Studienfach steht das genaueste vorhandene Fach in "studienfach" (Feld ccm:oeh_taxonid_university)."""
    v = vokabulare()[HFS]
    ebenen: dict[int, list[str]] = defaultdict(list)
    for u in uris:
        for x in [u, *v.ancestors(u)]:
            if x not in ebenen[v.by_uri[x]["level"]]:
                ebenen[v.by_uri[x]["level"]].append(x)
    fach = ebenen[2] or ebenen[1] or ebenen[0]
    return {"studienfach": [v.ref(u) for u in fach], "studienbereich": [v.ref(u) for u in ebenen[1]],
            "faechergruppe": [v.ref(u) for u in ebenen[0]]}


def zusammenfassen(rows: list[dict], bereiche=STANDARD_BEREICHE) -> tuple[dict[str, list[dict]], dict]:
    """Abstraktionen → zusammengefasste Themen je Bereich (noch ohne GND und Verknüpfungen) und Zählung."""
    gruppen: dict[str, dict] = {}
    zaehl = Counter()
    for r in rows:
        if not r.get("seed_tauglich"):
            zaehl["nicht_seed_tauglich"] += 1
            continue
        z = ziele(r)
        if not z:
            zaehl["ohne_bereich_oder_fach"] += 1
        for b, ziel in z.items():
            if b not in bereiche:
                zaehl[f"nicht_gemappt_{b}"] += 1
                continue
            key = _hash(b, norm(r["thema"]), ziel["faecher"][0])
            g = gruppen.setdefault(key, {"bereich": b, "key": key, "schreibweisen": Counter(), "node_ids": [],
                                         "faecher": [], "stufen": [], "fachbegriffe": Counter(),
                                         "allgemeiner": Counter(), "abstraktionen": 0, "vorschlag": True})
            g["schreibweisen"][r["thema"]] += 1
            g["node_ids"] += [n for n in r["node_ids"] if n not in g["node_ids"]]
            g["faecher"] += [u for u in ziel["faecher"] if u not in g["faecher"]]
            g["stufen"] += [s for s in ziel["stufen"] if s not in g["stufen"]]
            g["fachbegriffe"].update(r["fachbegriffe"])
            if r.get("thema_allgemeiner"):
                g["allgemeiner"][r["thema_allgemeiner"]] += 1
            g["abstraktionen"] += 1
            g["vorschlag"] = g["vorschlag"] and bool(r.get("vorschlag"))
            zaehl[f"abstraktionen_{b}"] += 1
    out: dict[str, list[dict]] = defaultdict(list)
    for g in gruppen.values():
        out[g["bereich"]].append(g)
    return out, dict(zaehl)


def _thema(g: dict, gnd: dict[str, dict], online: dict | None) -> dict:
    b = g["bereich"]
    label = g["schreibweisen"].most_common(1)[0][0]
    ta = g["allgemeiner"].most_common(1)[0][0] if g["allgemeiner"] else None
    fb = gnd_abgleich([f for f, _ in g["fachbegriffe"].most_common(5)], gnd, online)
    status = "wahrscheinlich" if g["vorschlag"] else "gesichert"
    stufen = [s for s in ("primar", "sek1", "sek2") if s in g["stufen"]]
    mapping = {"methode": METHODE.format(vorschlag=" (fehlende vom Modell aus dem OEH-Vokabular vorgeschlagen)"
                                         if g["vorschlag"] else ""),
               "status": status, "datum": date.today().isoformat()}
    wlo = {"node_ids": sorted(g["node_ids"]), "gewicht": len(g["node_ids"]) or g["abstraktionen"],
           "abstraktionen": g["abstraktionen"], "vorschlag": g["vorschlag"]}
    t: dict = {"id": f"{WLO_THEMA_PREFIX}{b}:{g['key']}", "label_de": label, "herkunft": "wlo_abstraktion",
               "quelle": QUELLE, "thema_allgemeiner": ta, "fachbegriffe": fb, "wlo": wlo}
    thema_gnd = gnd.get(norm(label)) or (online or {}).get(norm(label))
    if b == "schule":
        fach = vokabulare()["discipline"].by_uri[g["faecher"][0]]["label"]
        t.update(portal=fach, pfad=[ta] if ta else [], definition="",
                 keywords=list(dict.fromkeys([f["label"] for f in fb] + ([ta] if ta else []))),
                 faecher=g["faecher"], art="unterrichtsthema", stufen=stufen,
                 mapping={**mapping, "art": {"status": "wahrscheinlich"}, "fach": {"status": status}})
        if thema_gnd:
            t["gnd"] = {"uri": thema_gnd["uri"], "label": thema_gnd["label"]}
    else:
        t.update(art="wlo_abstraktion", label_en="", beschreibung_de="",
                 keywords_de=list(dict.fromkeys([f["label"] for f in fb] + ([ta] if ta else []))),
                 lehrebene=None, mapping=mapping, **_hfs_ebenen(g["faecher"]))
        if thema_gnd:
            t["gnd_treffer"] = {"uri": thema_gnd["uri"], "label": thema_gnd["label"]}
    return t


def _faecher_des_themas(t: dict) -> set[str]:
    if "faecher" in t:
        return set(t.get("faecher") or [])
    return {f["uri"] for k in ("studienfach", "studienbereich", "faechergruppe") for f in t.get(k) or []}


def _hauptfach(t: dict) -> str:
    return t["faecher"][0] if "faecher" in t else t["studienfach"][0]["uri"]


def _beste(zeile):
    """Indizes der MAX_VERKNUEPFUNGEN größten Werte (ohne die ganze Zeile zu sortieren)."""
    import numpy as np

    k = min(MAX_VERKNUEPFUNGEN, len(zeile))
    return np.argpartition(-zeile, k - 1)[:k] if k else []


def _herkunft(t: dict) -> str:
    return t.get("herkunft") or t.get("art") or "themenbaum"


def vorhandene_themen(bereich: str) -> list[dict]:
    """Themen des Bereichs ohne die der WLO-Abstraktion."""
    from .bereiche import get

    b = get(bereich)
    roh = b._topics_roh()
    return [t for t in roh if not str(t["id"]).startswith(WLO_THEMA_PREFIX)]


def verknuepfen(neu: list[dict], vorhanden: list[dict], embedder=None) -> dict:
    """Trägt "verknuepft_mit" ein: gleiche Schreibweise im selben Fach (Ähnlichkeit 1,0) und, mit embedder, nahe
    Dubletten unter den neuen Themen und Überschneidungen mit vorhandenen Themen (nur innerhalb eines Fachs)."""
    zaehl = Counter()
    je_fach_v: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(vorhanden):
        for f in _faecher_des_themas(t):
            je_fach_v[f].append(i)
    je_fach_n: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(neu):
        je_fach_n[_hauptfach(t)].append(i)
    links: dict[int, dict[str, dict]] = defaultdict(dict)

    def link(i, ziel, sim, art):
        cur = links[i].get(ziel["id"])
        if not cur or cur["aehnlichkeit"] < sim:
            links[i][ziel["id"]] = {"id": ziel["id"], "label": ziel.get("label_de") or ziel.get("label_en"),
                                    "herkunft": _herkunft(ziel), "aehnlichkeit": round(float(sim), 3), "art": art}

    vec_n = vec_v = None
    if embedder is not None:
        vec_n = embedder.encode([t["label_de"] for t in neu], art="anfrage")
        vec_v = embedder.encode([t.get("label_de") or t.get("label_en") or "" for t in vorhanden], art="anfrage") \
            if vorhanden else None
    for f, idx in je_fach_n.items():
        kand = je_fach_v.get(f, [])  # vorhandene Themen desselben Fachs (bzw. Studienbereichs)
        exakt = defaultdict(list)
        for j in kand:
            exakt[norm(vorhanden[j].get("label_de"))].append(j)
        for i in idx:
            for j in exakt.get(norm(neu[i]["label_de"]), []):
                link(i, vorhanden[j], 1.0, "vorhanden")
        if vec_n is None:
            continue
        a = vec_n[idx]
        if kand:
            for s in range(0, len(idx), 2048):
                sims = a[s:s + 2048] @ vec_v[kand].T
                for r, i in enumerate(idx[s:s + 2048]):
                    for j in _beste(sims[r]):
                        if sims[r, j] >= SCHWELLE_VORHANDEN:
                            link(i, vorhanden[kand[j]], sims[r, j], "vorhanden")
        if len(idx) > 1:
            for s in range(0, len(idx), 2048):
                sims = a[s:s + 2048] @ a.T
                for r, i in enumerate(idx[s:s + 2048]):
                    sims[r, s + r] = -1
                    for j in _beste(sims[r]):
                        if sims[r, j] >= SCHWELLE_DUBLETTE:
                            link(i, neu[idx[j]], sims[r, j], "nahe_dublette")
    for i, t in enumerate(neu):
        v = sorted(links.get(i, {}).values(), key=lambda x: -x["aehnlichkeit"])
        t["verknuepft_mit"] = [x for x in v if x["art"] == "vorhanden"][:MAX_VERKNUEPFUNGEN] + \
            [x for x in v if x["art"] == "nahe_dublette"][:MAX_VERKNUEPFUNGEN]
        arten = {x["art"] for x in t["verknuepft_mit"]}
        zaehl["mit_vorhandenem_thema"] += "vorhanden" in arten
        zaehl["mit_naher_dublette"] += "nahe_dublette" in arten
        for x in t["verknuepft_mit"]:
            if x["art"] == "vorhanden":
                zaehl[f"vorhanden_{x['herkunft']}"] += 1
    return dict(zaehl)


def bauen(abstraktion: list[Path], bereiche=STANDARD_BEREICHE, embedder=None, online: bool = False,
          on_progress=print, gnd: dict | None = None, vorhanden: dict[str, list[dict]] | None = None,
          schreiben: bool = True) -> dict:
    """Themen-Dateien je Bereich aus einer oder mehreren Abstraktions-Ausgaben; gibt die Statistik zurück."""
    rows: dict[str, dict] = {}
    for p in abstraktion:
        for r in lade_jsonl(p):
            rows[r["id"]] = r  # dieselbe Gruppe aus mehreren Läufen: der letzte zählt
    gruppen, zaehl = zusammenfassen(list(rows.values()), bereiche)
    gnd = gnd_index() if gnd is None else gnd
    on_lobid = None
    if online:
        offen = {f for gs in gruppen.values() for g in gs for f in list(g["fachbegriffe"])[:5] + list(g["schreibweisen"])
                 if norm(f) not in gnd}
        on_progress(f"lobid: {len(offen)} Begriffe ohne lokalen GND-Treffer")
        on_lobid = gnd_online(sorted(offen), on_progress=on_progress)
    stat: dict = {"abstraktionen": len(rows), "zaehlung": zaehl, "datum": date.today().isoformat(), "bereiche": {}}
    for b in bereiche:
        if b not in DATEIEN:
            stat["bereiche"][b] = {"hinweis": "kein Seed-Bau für diesen Bereich"}
            continue
        neu = [_thema(g, gnd, on_lobid) for g in gruppen.get(b, [])]
        neu.sort(key=lambda t: t["id"])
        v = vorhanden[b] if vorhanden is not None else vorhandene_themen(b)
        on_progress(f"{b}: {len(neu)} Themen, verknüpfen mit {len(v)} vorhandenen")
        vz = verknuepfen(neu, v, embedder)
        fbs = [f for t in neu for f in t["fachbegriffe"]]
        stat["bereiche"][b] = {
            "themen": len(neu), "abstraktionen": zaehl.get(f"abstraktionen_{b}", 0),
            "mehrfach_zusammengefasst": sum(t["wlo"]["abstraktionen"] > 1 for t in neu),
            "datensaetze": sum(len(t["wlo"]["node_ids"]) for t in neu),
            "vorschlag": sum(t["wlo"]["vorschlag"] for t in neu),
            "fachbegriffe": len(fbs), "fachbegriffe_gnd": sum(bool(f["gnd"]) for f in fbs),
            "thema_gnd": sum(bool(t.get("gnd") or t.get("gnd_treffer")) for t in neu),
            "verknuepfung": vz, "embeddings": getattr(embedder, "model", None)}
        if schreiben:
            p = themen_pfad(b)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in neu), encoding="utf-8")
    if schreiben:
        (ordner() / "zusammenfuehrung.json").write_text(json.dumps(stat, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    return stat
