"""Themen aus der WLO-Themen-Abstraktion für die Seeds (Herkunft „wlo_abstraktion“).

`python -m sem wlo-themen --zusammenfuehren` liest die Abstraktion (sem/wlo_themen.py) und schreibt je
Bildungsbereich eine Themen-Datei nach data/lokal/wlo_abstraktion/ (themen_schule.jsonl, themen_hochschule.jsonl,
themen_erwachsenenbildung.jsonl, themen_elementar.jsonl). Die Bereiche laden sie, wenn vorhanden, als weitere
Themenquelle; über die Filter herkunft/quellen ist sie an- und abschaltbar, über wlo_anteil der Anteil der Seeds
steuerbar.

- Schule: Primarstufe, Sekundarstufe I und II aus der Bildungsstufe der WLO-Datensätze, Fach = OEH discipline.
- Hochschule: Fach = OEH-Hochschulfächersystematik (Destatis), Lehrebene offen.
  Datensätze nur mit „Schule“ (ohne Schulstufe): Stufe aus den ähnlichsten vorhandenen Schulthemen desselben Fachs
  vorgeschlagen (wlo.vorschlag, Filter wlo_vorschlag).
- Erwachsenenbildung (auch Stufen „Informelles Lernen“ und „Fortbildung“): Fachgebiet der Volkshochschul-Statistik,
  Elementarbereich: Bildungsbereich, Altersgruppe und Themenart, Berufsbildung: ESCO-Beruf (mit KldB), alle ohne LLM
  zugeordnet (sem/wlo_zuordnung.py); Eignung als "wlo.eignung" markiert (Filter wlo_eignung), gleiche Themen über
  alle Fächer zusammengefasst.

Dubletten werden verknüpft, nicht gelöscht: gleiches normalisiertes Thema im selben Bereich und Fach → ein Thema mit
allen Node-IDs und deren Zahl als Gewicht. Nahe Dubletten (Embedding-Ähnlichkeit) und Überschneidungen mit den
vorhandenen Themen des Bereichs (Themenbaum, Keywords, OpenAlex, GND, RVK, Fachprofile) stehen in "verknuepft_mit".
Fachbegriffe werden gegen die GND-Labels der vorhandenen Mappings abgeglichen, auf Wunsch der Rest gegen lobid
(--gnd-online, derselbe Weg wie gnd-ergaenzen). Quelltexte stehen in keiner Datei, nur Node-IDs.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from . import paths
from .bereiche.base import WLO_THEMA_PREFIX
from .wlo_themen import CTX, HFS, lade_jsonl, norm, ordner, vokabulare

GND_PREFIX = "https://d-nb.info/gnd/"
SCHULSTUFEN = {CTX + "grundschule": "primar", CTX + "sekundarstufe_1": "sek1", CTX + "sekundarstufe_2": "sek2"}
# Stufe „Schule“ ohne Schulstufe: Stufe wird aus den vorhandenen Schulthemen vorgeschlagen (stufen_vorschlagen)
SCHULE_ALLGEMEIN = CTX + "schule"
BEREICHE_STUFE = {CTX + "berufliche_bildung": "berufsbildung", CTX + "erwachsenenbildung": "erwachsenenbildung",
                  CTX + "informelles_lernen": "erwachsenenbildung", CTX + "fortbildung": "erwachsenenbildung",
                  CTX + "elementarbereich": "elementar", CTX + "hochschule": "hochschule"}
STANDARD_BEREICHE = ("schule", "hochschule", "erwachsenenbildung", "elementar", "berufsbildung")
QUELLE = {"name": "WirLernenOnline (Themen-Abstraktion aus Titel und Beschreibung der Datensätze)",
          "uri": "https://wirlernenonline.de",
          "lizenz": "CC0-1.0 (vom Projekt abstrahiertes Thema; Titel und Beschreibungen nicht übernommen)"}
METHODE = ("LLM-Themen-Abstraktion aus Titel und Beschreibung von WLO-Datensätzen (Kopierprüfung bestanden); Fach "
           "und Bildungsstufe aus den Metadaten der Datensätze{vorschlag}; gleiche Themen je Fach zusammengefasst")
METHODE_ZUORDNUNG = ("LLM-Themen-Abstraktion aus Titel und Beschreibung von WLO-Datensätzen (Kopierprüfung bestanden); "
                     "Bildungsstufe aus den Metadaten der Datensätze{vorschlag}; gleiche Themen zusammengefasst; {ziel} "
                     "ohne LLM aus lokalen Embeddings (Ähnlichkeit zu den vorhandenen Themen des Bereichs und zur "
                     "Beschreibung) und dem Fach der Datensätze (sem/wlo_zuordnung.py)")
# Ähnlichkeit (multilingual-e5-small, beide Seiten mit „query:“) ab der verknüpft wird; gemessen im 2.000er-Testlauf
SCHWELLE_DUBLETTE = 0.93
SCHWELLE_VORHANDEN = 0.92
MAX_VERKNUEPFUNGEN = 3
DATEIEN_FACH = {"schule": "themen_schule.jsonl", "hochschule": "themen_hochschule.jsonl"}
DATEIEN = {**DATEIEN_FACH, "erwachsenenbildung": "themen_erwachsenenbildung.jsonl",
           "elementar": "themen_elementar.jsonl", "berufsbildung": "themen_berufsbildung.jsonl"}


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


def gnd_online(begriffe: list[str], workers: int = 8, on_progress=None,
               max_je_sekunde: float = 5.0) -> dict[str, dict | None]:
    """Exakte Suche bei lobid (zwischengespeichert in data/cache, ein abgebrochener Lauf setzt dort fort), wie bei
    gnd-ergaenzen; höchstens max_je_sekunde Netzanfragen pro Sekunde."""
    from concurrent.futures import ThreadPoolExecutor

    from .quellen import gnd

    gnd.MIN_ABSTAND = 1.0 / max_je_sekunde if max_je_sekunde else 0.0

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
    if disc and (schul or SCHULE_ALLGEMEIN in stufen):
        out["schule"] = {"faecher": disc, "stufen": schul}  # leer: Stufe wird beim Bau vorgeschlagen
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


# Ausweich-Antworten statt eines Themas („Unklarer Lerngegenstand“, „Nicht erkennbar“): kein Seed
AUSWEICH_THEMA = re.compile(r"(unbekannt|unklar|nicht erkennbar|nicht näher bestimmbar)\w*( \w+er)?"
                            r"( (inhalt|gegenstand|thema))?", re.IGNORECASE)


@lru_cache(maxsize=1)
def _vokabular_labels() -> frozenset[str]:
    return frozenset(norm(c["label"]) for v in vokabulare().values() for c in v.concepts)


def kein_thema(thema: str) -> str | None:
    """Grund, warum eine Abstraktion kein brauchbares Thema ist: Ausweich-Antwort oder nur der Name eines Fachs bzw.
    einer Stufe (z. B. „Mathematik“), was als Seed nichts über den Gegenstand sagt. None = brauchbar."""
    if AUSWEICH_THEMA.fullmatch(thema.strip()) or "lerngegenstand" in thema.casefold():
        return "ausweich_thema"
    if norm(thema) in _vokabular_labels():
        return "thema_nur_fach"
    return None


def zusammenfassen(rows: list[dict], bereiche=STANDARD_BEREICHE) -> tuple[dict[str, list[dict]], dict]:
    """Abstraktionen → zusammengefasste Themen je Bereich (noch ohne GND und Verknüpfungen) und Zählung."""
    gruppen: dict[str, dict] = {}
    zaehl = Counter()
    for r in rows:
        if not r.get("seed_tauglich"):
            zaehl["nicht_seed_tauglich"] += 1
            continue
        grund = kein_thema(r["thema"])
        if grund:
            zaehl[grund] += 1
            continue
        z = ziele(r)
        if not z:
            zaehl["ohne_bereich_oder_fach"] += 1
        for b, ziel in z.items():
            if b not in bereiche:
                zaehl[f"nicht_gemappt_{b}"] += 1
                continue
            # Schule/Hochschule: je Fach ein Thema; EB/Elementar: Fachgebiet bzw. Bildungsbereich kommt erst aus der
            # Zuordnung (sem/wlo_zuordnung.py), gleiche Themen werden über alle Fächer zusammengefasst
            key = _hash(b, norm(r["thema"]), ziel["faecher"][0] if b in DATEIEN_FACH else "")
            g = gruppen.setdefault(key, {"bereich": b, "key": key, "schreibweisen": Counter(), "node_ids": [],
                                         "faecher": [], "stufen": [], "fachbegriffe": Counter(),
                                         "allgemeiner": Counter(), "abstraktionen": 0, "vorschlag": True})
            g["schreibweisen"][r["thema"]] += 1
            g["node_ids"] += [n for n in r["node_ids"] if n not in g["node_ids"]]
            g["faecher"] += [u for u in ziel["faecher"] if u not in g["faecher"]]
            g["stufen"] += [s for s in ziel["stufen"] if s not in g["stufen"]]
            g.setdefault("stufen_uris", [])
            g["stufen_uris"] += [s["uri"] for s in r["stufe"] if s["uri"] not in g["stufen_uris"]]
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


def _thema(g: dict, gnd: dict[str, dict], online: dict | None, zuordnung: dict | None = None) -> dict:
    b = g["bereich"]
    label = g["schreibweisen"].most_common(1)[0][0]
    ta = g["allgemeiner"].most_common(1)[0][0] if g["allgemeiner"] else None
    fb = gnd_abgleich([f for f, _ in g["fachbegriffe"].most_common(5)], gnd, online)
    status = "wahrscheinlich" if g["vorschlag"] else "gesichert"
    stufen = [s for s in ("primar", "sek1", "sek2") if s in g["stufen"]]
    vorschlag = (" (Schulstufe aus den ähnlichsten vorhandenen Schulthemen desselben Fachs vorgeschlagen, lokale "
                 "Embeddings; Datensätze nur mit Bildungsstufe „Schule“)" if g.get("stufe_vorgeschlagen") else
                 " (fehlende vom Modell aus dem OEH-Vokabular vorgeschlagen)" if g["vorschlag"] else "")
    mapping = {"methode": METHODE.format(vorschlag=vorschlag),
               "status": status, "datum": date.today().isoformat()}
    wlo = {"node_ids": sorted(g["node_ids"]), "gewicht": len(g["node_ids"]) or g["abstraktionen"],
           "abstraktionen": g["abstraktionen"], "vorschlag": g["vorschlag"]}
    t: dict = {"id": f"{WLO_THEMA_PREFIX}{b}:{g['key']}", "label_de": label, "herkunft": "wlo_abstraktion",
               "quelle": QUELLE, "thema_allgemeiner": ta, "fachbegriffe": fb, "wlo": wlo}
    thema_gnd = gnd.get(norm(label)) or (online or {}).get(norm(label))
    if b in ZUORDNUNG:
        return _thema_zuordnung(t, g, fb, ta, zuordnung, thema_gnd)
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


def _thema_zuordnung(t: dict, g: dict, fb: list, ta: str | None, z: dict, thema_gnd: dict | None) -> dict:
    """Thema für Erwachsenenbildung bzw. Elementarbereich im Format der vorhandenen Themen des Bereichs; Fachgebiet
    bzw. Bildungsbereich, Art, Altersgruppe und Eignung aus sem/wlo_zuordnung.py."""
    from . import wlo_zuordnung as Z

    b = g["bereich"]
    vorschlag = " (fehlende vom Modell aus dem OEH-Vokabular vorgeschlagen)" if g["vorschlag"] else ""
    t["wlo"]["faecher"] = g["faecher"]
    t["wlo"]["eignung"] = z["eignung"]
    kw = list(dict.fromkeys([f["label"] for f in fb] + ([ta] if ta else [])))
    mapping = {"status": z["status"], "wert": z["wert"], "datum": date.today().isoformat(),
               "embedding_modell": z.get("embedding_modell")}
    if b == "berufsbildung":
        # Herkunft als Themenquelle des Bereichs (Filter quellen); Beruf wird beim Laden aus berufe.jsonl aufgelöst
        t.update(quelle="wlo_abstraktion", quelle_wlo=QUELLE, beruf_uri=z["beruf"], berufsfeld=z["berufsfeld"],
                 keywords=kw, mapping={"methode": METHODE_ZUORDNUNG.format(vorschlag=vorschlag, ziel="ESCO-Beruf "
                                                                         "(mit KldB-Berufsgruppe)"), **mapping})
    elif b == "erwachsenenbildung":
        fg = next(x for x in Z.eb_ziele() if x["notation"] == z["fachgebiet"])
        t.update(art=z["art"], fachgebiet=[{k: fg[k] for k in ("notation", "uri", "label")}],
                 programmbereich=[fg["programmbereich"]], synonyme=[], definition="", oberbegriffe=[], keywords=kw,
                 mapping={"methode": METHODE_ZUORDNUNG.format(vorschlag=vorschlag, ziel="Fachgebiet der "
                                                              "Volkshochschul-Statistik"), **mapping})
    else:
        bb = next(x for x in Z.el_bereiche() if x["id"] == z["bildungsbereich"])
        basis = json.loads((Z.EL_DIR / "bildungsbereiche.json").read_text(encoding="utf-8"))["uri_basis"]
        t.update(herkunft=["wlo_abstraktion"], art=z["art"], altersgruppe=z["altersgruppe"],
                 bildungsbereich=[{"id": bb["id"], "uri": basis + bb["id"], "label": bb["label"]}],
                 synonyme=kw[:4], definition="", oberbegriffe=[],
                 mapping={"methode": METHODE_ZUORDNUNG.format(vorschlag=vorschlag, ziel="Bildungsbereich, "
                                                              "Altersgruppe und Themenart"), **mapping})
    if thema_gnd:
        t["gnd"] = {"uri": thema_gnd["uri"], "label": thema_gnd["label"]}
    return t


def _faecher_des_themas(t: dict) -> set[str]:
    """Gruppe, innerhalb der verknüpft wird: Fach (Schule), Hochschulfächer, VHS-Fachgebiet, Bildungsbereich,
    KldB-Berufshauptgruppe (Berufsbildung)."""
    if "berufsfeld" in t:
        return {f["notation"] for f in t.get("berufsfeld") or []}
    if "fachgebiet" in t:
        return {f["notation"] for f in t.get("fachgebiet") or []}
    if "bildungsbereich" in t:
        return {x["id"] for x in t.get("bildungsbereich") or []}
    if "faecher" in t:
        return set(t.get("faecher") or [])
    return {f["uri"] for k in ("studienfach", "studienbereich", "faechergruppe") for f in t.get(k) or []}


def _hauptfach(t: dict) -> str:
    if "berufsfeld" in t:
        return t["berufsfeld"][0]["notation"]
    if "fachgebiet" in t:
        return t["fachgebiet"][0]["notation"]
    if "bildungsbereich" in t:
        return t["bildungsbereich"][0]["id"]
    return t["faecher"][0] if "faecher" in t else t["studienfach"][0]["uri"]


def _beste(zeile):
    """Indizes der MAX_VERKNUEPFUNGEN größten Werte (ohne die ganze Zeile zu sortieren)."""
    import numpy as np

    k = min(MAX_VERKNUEPFUNGEN, len(zeile))
    return np.argpartition(-zeile, k - 1)[:k] if k else []


def _herkunft(t: dict) -> str:
    h = t.get("herkunft") or t.get("art") or "themenbaum"
    return "+".join(h) if isinstance(h, list) else h


def vorhandene_themen(bereich: str) -> list[dict]:
    """Themen des Bereichs ohne die der WLO-Abstraktion."""
    from .bereiche import get

    b = get(bereich)
    if bereich == "berufsbildung":
        return vorhandene_bb(b)
    roh = b._topics_roh()
    return [t for t in roh if not str(t["id"]).startswith(WLO_THEMA_PREFIX)]


def vorhandene_bb(b) -> list[dict]:
    """Themen der Berufsbildung als Vergleichsthemen: Kompetenz- bzw. Lernfeld-Label je KldB-Berufshauptgruppe des
    Berufs (gleiche Labels in derselben Hauptgruppe einmal); DigComp ohne Beruf fehlt."""
    out, gesehen = [], set()
    for t in b.topics():
        if str(t["id"]).startswith(WLO_THEMA_PREFIX) or "beruf" not in t:
            continue
        label = t["kompetenz"]["label"] if t.get("kompetenz") else t["lernfeld"]["titel"]
        hg = t["beruf"]["berufshauptgruppe"][0]
        key = (norm(label), hg["notation"])
        if key not in gesehen:
            gesehen.add(key)
            out.append({"id": t["id"], "label_de": label, "herkunft": t.get("quelle", "esco"),
                        "berufsfeld": [{k: hg[k] for k in ("notation", "uri", "label")}]})
    return out


def verknuepfen(neu: list[dict], vorhanden: list[dict], embedder=None) -> dict:
    """Trägt "verknuepft_mit" ein: gleiche Schreibweise im selben Fach (Ähnlichkeit 1,0) und, mit embedder, nahe
    Dubletten unter den neuen Themen und Überschneidungen mit vorhandenen Themen (nur innerhalb eines Fachs)."""
    if not neu:
        return {}
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
          online_min: int = 2, on_progress=print, gnd: dict | None = None, vorhanden: dict[str, list[dict]] | None = None,
          schreiben: bool = True, berufe: list[str] | None = None) -> dict:
    """Themen-Dateien je Bereich aus einer oder mehreren Abstraktions-Ausgaben; gibt die Statistik zurück.
    Erwachsenenbildung und Elementarbereich brauchen Embeddings (Zuordnung); berufe = Labels der
    ESCO-Fähigkeiten für die Markierung „eher_berufsbildung“ (Standard: aus data/mappings/berufsbildung)."""
    rows: dict[str, dict] = {}
    for p in abstraktion:
        for r in lade_jsonl(p):
            rows[r["id"]] = r  # dieselbe Gruppe aus mehreren Läufen: der letzte zählt
    gruppen, zaehl = zusammenfassen(list(rows.values()), bereiche)
    gnd = gnd_index() if gnd is None else gnd
    on_lobid = None
    if online:
        # nur Fachbegriffe (ganze Themen treffen die GND fast nie) und nur solche, die in mindestens online_min
        # Themen vorkommen: Beim Volllauf sind das rund 63 000 statt 400 000 Anfragen, für 84 % der Vorkommen
        anzahl = Counter(norm(f) for gs in gruppen.values() for g in gs
                         for f, _ in g["fachbegriffe"].most_common(5) if norm(f) not in gnd)
        offen = {f for gs in gruppen.values() for g in gs for f, _ in g["fachbegriffe"].most_common(5)
                 if anzahl.get(norm(f), 0) >= online_min}
        on_progress(f"lobid: {len(offen)} Begriffe ohne lokalen GND-Treffer (in mindestens {online_min} Themen)")
        on_lobid = gnd_online(sorted(offen), on_progress=on_progress)
    stat: dict = {"abstraktionen": len(rows), "zaehlung": zaehl, "datum": date.today().isoformat(), "bereiche": {}}
    for b in bereiche:
        if b not in DATEIEN:
            stat["bereiche"][b] = {"hinweis": "kein Seed-Bau für diesen Bereich"}
            continue
        gs = gruppen.get(b, [])
        v = vorhanden[b] if vorhanden is not None and b in vorhanden else (vorhandene_themen(b) if gs else [])
        zu = [None] * len(gs)
        if b == "schule":
            ohne = [g for g in gs if not g["stufen"]]
            if ohne:
                from . import wlo_zuordnung as Z

                if any(t.get("stufen") for t in v):  # ohne vorhandene Schulthemen: Sek I, kein Modell nötig
                    embedder = embedder or _standard_embedder()
                on_progress(f"schule: Schulstufe für {len(ohne)} Themen vorschlagen")
                for g, st in zip(ohne, Z.stufen_vorschlagen(_zuordnung_eingabe(ohne), v, embedder)):
                    g.update(stufen=st, vorschlag=True, stufe_vorgeschlagen=True)
                zaehl["schule_stufe_vorgeschlagen"] = len(ohne)
        if b in ZUORDNUNG and gs:
            embedder = embedder or _standard_embedder()
            zu = zuordnen(b, gs, v, embedder, berufe=berufe)
        neu = [_thema(g, gnd, on_lobid, z) for g, z in zip(gs, zu)]
        neu.sort(key=lambda t: t["id"])
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
        stat["bereiche"][b].update(kennzahlen_verknuepfung(neu))
        if b in ZUORDNUNG:
            stat["bereiche"][b].update(kennzahlen_zuordnung(neu))
        if schreiben:
            p = themen_pfad(b)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in neu), encoding="utf-8")
    if schreiben:
        p = ordner() / "zusammenfuehrung.json"
        alt = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        # Bereiche, die dieser Lauf nicht gebaut hat, behalten ihre Kennzahlen aus dem vorigen Lauf
        stat = {**alt, **stat, "zaehlung": {**alt.get("zaehlung", {}), **zaehl},
                "bereiche": {**alt.get("bereiche", {}), **stat["bereiche"]}}
        for b2, x in stat["bereiche"].items():
            if "themen" in x:
                stat["zaehlung"].pop(f"nicht_gemappt_{b2}", None)
        p.write_text(json.dumps(stat, ensure_ascii=False, indent=1), encoding="utf-8")
    return stat


ZUORDNUNG = ("erwachsenenbildung", "elementar", "berufsbildung")


def _standard_embedder():
    from .mapping.embeddings import DEFAULT_EMBEDDING_MODEL, Embedder

    return Embedder(DEFAULT_EMBEDDING_MODEL)


def zuordnen(bereich: str, gruppen: list[dict], vorhanden: list[dict], embedder,
             berufe: list[str] | None = None) -> list[dict]:
    """Fachgebiet (EB) bzw. Bildungsbereich (Elementar) je zusammengefasster Gruppe, siehe sem/wlo_zuordnung.py."""
    from . import wlo_zuordnung as Z

    themen_ = _zuordnung_eingabe(gruppen)
    if bereich == "berufsbildung":
        out = _zuordnen_bb(themen_, embedder)
    elif bereich == "erwachsenenbildung":
        if berufe is None:
            p = paths.MAPPING_DIR / "berufsbildung" / "kompetenzen.jsonl"
            berufe = [x["label"] for x in lade_jsonl(p)] if p.exists() else []
        out = Z.zuordnen_eb(themen_, vorhanden, embedder, berufe)
    else:
        out = Z.zuordnen_elementar(themen_, vorhanden, embedder)
    for z in out:
        z["embedding_modell"] = getattr(embedder, "model", None)
    return out


def _zuordnung_eingabe(gruppen: list[dict]) -> list[dict]:
    return [{"label": g["schreibweisen"].most_common(1)[0][0],
             "allgemeiner": g["allgemeiner"].most_common(1)[0][0] if g["allgemeiner"] else None,
             "faecher": g["faecher"], "stufen_uris": g.get("stufen_uris", [])} for g in gruppen]


def _zuordnen_bb(themen_: list[dict], embedder) -> list[dict]:
    """Beruf je Thema (sem/wlo_zuordnung.zuordnen_bb): Kandidaten sind die Berufe mit Qualifizierungsweg Ausbildung
    oder Fortbildung, Vorbilder ihre essenziellen Kompetenzen und Lernfelder; Schulthemen für die Eignung."""
    from . import wlo_zuordnung as Z
    from .bereiche import get

    bb = get("berufsbildung")
    vorbilder: dict[str, int] = {}
    je_beruf: dict[str, list[int]] = defaultdict(list)
    for t in bb.topics():
        if str(t["id"]).startswith(WLO_THEMA_PREFIX) or "beruf" not in t or t.get("relation") == "optional":
            continue
        label = t["kompetenz"]["label"] if t.get("kompetenz") else t["lernfeld"]["titel"]
        je_beruf[t["beruf"]["uri"]].append(vorbilder.setdefault(label, len(vorbilder)))
    berufe = [b for b in bb.berufe if b.get("qualifizierung") in Z.BB_QUALIFIZIERUNG]
    eingabe = [{"uri": b["uri"], "text": f"Beruf {b['label']}: {b.get('beschreibung', '')}",
                "hauptgruppe": b["berufshauptgruppe"][0]["notation"], "vorbilder": je_beruf.get(b["uri"], [])}
               for b in berufe]
    try:
        schule = [t["label_de"] for t in vorhandene_themen("schule") if t.get("label_de")]
    except FileNotFoundError:
        schule = []
    out = Z.zuordnen_bb(themen_, eingabe, list(vorbilder), schule, embedder)
    by = {b["uri"]: b for b in berufe}
    for z in out:
        hg = by[z["beruf"]]["berufshauptgruppe"][0]
        z["berufsfeld"] = [{k: hg[k] for k in ("notation", "uri", "label")}]
    return out


def kennzahlen_verknuepfung(neu: list[dict]) -> dict:
    """alle, mit naher Dublette, einzigartig (ohne nahe Dublette), einzigartig ohne vorhandenes Thema; je mit
    Zahl der Themen mit vorgeschlagener Stufe/Fach."""
    def arten(t):
        return {x["art"] for x in t.get("verknuepft_mit") or []}

    gruppen = {"alle": neu, "nahe_dublette": [t for t in neu if "nahe_dublette" in arten(t)],
               "einzigartig": [t for t in neu if "nahe_dublette" not in arten(t)],
               "einzigartig_ohne_vorhandene": [t for t in neu if not arten(t)]}
    return {"uebersicht": {k: {"themen": len(v), "vorschlag": sum(t["wlo"]["vorschlag"] for t in v)}
                           for k, v in gruppen.items()}}


def kennzahlen_zuordnung(neu: list[dict]) -> dict:
    if neu and "berufsfeld" in neu[0]:
        def ziel(t):
            return t["berufsfeld"][0]["notation"]
    elif neu and "fachgebiet" in neu[0]:
        def ziel(t):
            return t["fachgebiet"][0]["notation"]
    else:
        def ziel(t):
            return t["bildungsbereich"][0]["id"]
    return {"status": dict(Counter(t["mapping"]["status"] for t in neu)),
            **({"art": dict(Counter(t["art"] for t in neu))} if neu and "art" in neu[0] else {}),
            "eignung": dict(Counter(t["wlo"]["eignung"]["status"] for t in neu)),
            "eignung_gruende": dict(Counter(g for t in neu for g in t["wlo"]["eignung"]["gruende"])),
            "ziel": dict(sorted(Counter(ziel(t) for t in neu).items())),
            **({"altersgruppe": dict(Counter(t["altersgruppe"] for t in neu))} if neu and "altersgruppe" in neu[0]
               else {})}
