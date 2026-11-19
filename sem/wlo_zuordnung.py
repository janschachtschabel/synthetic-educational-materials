"""Zuordnung der WLO-Themen-Abstraktion zu Erwachsenenbildung und Elementarbereich, ohne LLM.

Die Abstraktion (sem/wlo_themen.py) liefert je Thema Fach (OEH discipline) und Bildungsstufe der WLO-Datensätze.
Erwachsenen- und Elementarbildung gliedern ihre Themen anders: nach den Fachgebieten der Volkshochschul-Statistik
(DIE) bzw. nach den sieben Bildungsbereichen des Gemeinsamen Rahmens der Länder (2022). Die Zuordnung rechnet je Ziel
mit lokalen Embeddings (multilingual-e5-small) einen Wert aus

- der Ähnlichkeit zu den vorhandenen, per Abstimmung zugeordneten Themen des Bereichs (Mittel der drei ähnlichsten
  Themen des Ziels, Gewicht 0,6) und
- der Ähnlichkeit zur Beschreibung des Ziels (Gewicht 0,4)

und bevorzugt das beste Ziel, das zum Fach der WLO-Datensätze passt (Umkehrung der closeMatch-Handmappings
fachgebiete_discipline.json bzw. bildungsbereiche.json), wenn es höchstens um einen kleinen Bonus schlechter ist.
Sprachen erkennt ein Muster im Thema („Englischer Wortschatz“ → Englisch). Status: gesichert = Fach-Ziel und bestes
Ziel stimmen überein, wahrscheinlich = Fach-Ziel gewählt oder kein Fach, unsicher = bestes Ziel gegen das Fach.

Die Eignung wird markiert, nicht gelöscht: "eignung": {"status": "geeignet" | "fraglich", "gruende": [...]}.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np

from .paths import MAPPING_DIR
from .wlo_themen import CTX, norm

DISC = "http://w3id.org/openeduhub/vocabs/discipline/"
GEWICHT_KNN = 0.6
K_KNN = 3
OHNE_VORBILD = 0.02  # Abschlag für Ziele ohne vorhandene Themen (nur Beschreibung)
BONUS_FACH = 0.015  # so viel schlechter darf ein zum Fach passendes Ziel sein
BONUS_SPRACHE = 0.03  # … bei Sprach-Fachgebieten (Fach = Sprache ist ein starkes Signal)
BONUS_MUSTER = 0.05  # … wenn das Thema die Sprache nennt

# --- gemeinsame Hilfen -------------------------------------------------------------------------------


# Produkt- und Plattformnamen, die die Abstraktion stehen gelassen hat: Thema markiert („produktname“), nicht geändert
PRODUKT_RE = re.compile(r"\b(microsoft|excel|powerpoint|access|outlook|onenote|google|apple|ipad|iphone|android|"
                        r"windows|moodle|padlet|canva|scratch|minecraft|youtube|instagram|tiktok|facebook|whatsapp|"
                        r"chatgpt|openai|kahoot|mentimeter|geogebra|wikipedia|h5p|duolingo|lego|playmobil|tiptoi|"
                        r"calliope|wordwall|learningapps|national football league|nfl)\b", re.IGNORECASE)


def produktname(label: str) -> bool:
    return bool(PRODUKT_RE.search(label))


def text(label: str, allgemeiner: str | None) -> str:
    """Text für das Embedding: Thema und allgemeineres Thema („Lokaladverbien (Adverbien)“)."""
    return f"{label} ({allgemeiner})" if allgemeiner and norm(allgemeiner) != norm(label) else label


def knn_werte(v: np.ndarray, vorbilder: np.ndarray, gruppen: list[list[int]], k: int = K_KNN) -> np.ndarray:
    """(n, G): je Thema und Ziel das Mittel der k größten Ähnlichkeiten zu den Vorbildern des Ziels (nan ohne)."""
    out = np.full((len(v), len(gruppen)), np.nan, dtype=np.float32)
    if not len(vorbilder):
        return out
    for s in range(0, len(v), 4096):
        sims = v[s:s + 4096] @ vorbilder.T
        for g, idx in enumerate(gruppen):
            if idx:
                kk = min(k, len(idx))
                out[s:s + 4096, g] = -np.partition(-sims[:, idx], kk - 1, axis=1)[:, :kk].mean(axis=1)
    return out


def werte(v, vorbilder, gruppen, ziel_vec) -> np.ndarray:
    knn = knn_werte(v, vorbilder, gruppen)
    besch = v @ ziel_vec.T
    return np.where(np.isnan(knn), besch - OHNE_VORBILD, GEWICHT_KNN * knn + (1 - GEWICHT_KNN) * besch)


def waehlen(zeile: np.ndarray, ziele: list[str], prior: dict[str, float]) -> tuple[str, str, float]:
    """Ziel, Status, Wert. prior: {Ziel: Bonus} der zum Fach passenden Ziele."""
    best = int(np.argmax(zeile))
    kand = [(zeile[i] + prior[z], i) for i, z in enumerate(ziele) if z in prior]
    if not kand:
        return ziele[best], "wahrscheinlich", float(zeile[best])
    bp = max(kand)[1]
    if bp == best:
        return ziele[best], "gesichert", float(zeile[best])
    if zeile[bp] + prior[ziele[bp]] >= zeile[best]:
        return ziele[bp], "wahrscheinlich", float(zeile[bp])
    return ziele[best], "unsicher", float(zeile[best])


def _stufen(t: dict) -> set[str]:
    return {u.removeprefix(CTX) for u in t.get("stufen_uris") or []}


# --- Erwachsenenbildung ------------------------------------------------------------------------------

EB_DIR = MAPPING_DIR / "erwachsenenbildung"
SCHULABSCHLUSS = "6.07"  # Programmbereich 6 als ein Ziel (die Schulabschlüsse selbst kennt die Abstraktion nicht)
# closeMatch-Ergänzung für die Zuordnung: Naturwissenschaften und Mathematik der Schule
EB_PRIOR_PLUS = {"460": ["1.10", SCHULABSCHLUSS], "100": ["1.10", SCHULABSCHLUSS], "080": ["1.10", SCHULABSCHLUSS],
                 "04003": [SCHULABSCHLUSS], "380": [SCHULABSCHLUSS]}
SPRACHMUSTER = {"4.01": "arabisch", "4.02": "chinesisch", "4.06": "englisch", "4.08": "französisch",
                "4.09": "italienisch", "4.10": "japanisch", "4.11": "latein", "4.12": "griechisch",
                "4.14": "niederländisch", "4.17": "polnisch", "4.18": "portugiesisch", "4.19": "russisch",
                "4.20": "schwedisch", "4.22": "spanisch", "4.24": "türkisch", "4.28": "gebärdensprache",
                "4.04": r"deutsch als (fremd|zweit)sprache|\bda[fz]\b|deutschlernende"}
SPRACH_RE = {k: re.compile(v, re.IGNORECASE) for k, v in SPRACHMUSTER.items()}
# Themen für Kursleitende (Didaktik der Erwachsenenbildung, Unterricht, Lehren und Lernen)
KURSLEITUNG_RE = re.compile(r"erwachsenenbildung|weiterbildner|kursleit|didaktik|unterricht|lehrkr|lehrende|"
                            r"lernprozess|lernbegleit|lehr-?lern|lernumgebung|lernmanagement|bildungsarbeit",
                            re.IGNORECASE)
KURSLEITUNG_FG = "1.05"
GRUNDBILDUNG_RE = {"7.01": re.compile(r"alphabetis|grundbildung|lesen lernen|schreiben lernen|leseförder", re.I),
                   "7.02": re.compile(r"rechnen im alltag|grundrechenart|alltagsmathe", re.I)}
# nur über Fach oder Muster wählbar: Sprachen, Grundbildung, Schulabschluss (ihre vorhandenen Themen sind
# Formulierungen aus Rahmencurricula und Schullehrplänen, die sonst viele fachfremde Themen anziehen)
def gebunden(fg: str) -> bool:
    return fg.startswith("4.") or fg in ("7.01", "7.02", "7.03", SCHULABSCHLUSS)


# Fächer der beruflichen Bildung (OEH discipline): Thema mit nur solchen Fächern, das ESCO-Fähigkeiten näher ist als
# den vorhandenen EB-Themen (Abstand > SCHWELLE_BERUF), ist eher ein Thema der Berufsbildung
BERUFSFAECHER = {DISC + d for d in ("040", "04001", "04002", "04005", "04007", "04009", "04010", "04011", "04012",
                                    "04014", "oeh04010")}
SCHWELLE_BERUF = 0.01
# Schulfächer der Naturwissenschaften und Mathematik mit Sekundarstufe: eher Schulstoff als Kursthema
SCHULFAECHER = {DISC + d for d in ("460", "100", "080", "380")}


@lru_cache(maxsize=1)
def eb_ziele() -> list[dict]:
    s = json.loads((EB_DIR / "programmbereiche.json").read_text(encoding="utf-8"))
    pb = {p["notation"]: p for p in s["programmbereiche"]}
    out = []
    for f in s["fachgebiete"]:
        if f["uebergreifend"] or (f["programmbereich"] == "6" and f["notation"] != SCHULABSCHLUSS):
            continue
        p = pb[f["programmbereich"]]
        besch = (f"Sprachkurs {f['label']} an der Volkshochschule" if f["programmbereich"] == "4" else
                 f"Volkshochschulkurs im Fachgebiet {f['label']}, Programmbereich {p['label']}")
        out.append({"notation": f["notation"], "label": f["label"], "uri": f["uri"], "beschreibung": besch,
                    "programmbereich": {"notation": p["notation"], "uri": p["uri"], "label": p["label"]}})
    return out


@lru_cache(maxsize=1)
def eb_prior() -> dict[str, set[str]]:
    """discipline-URI → Fachgebiete (Umkehrung von fachgebiete_discipline.json plus EB_PRIOR_PLUS)."""
    cm = json.loads((EB_DIR / "fachgebiete_discipline.json").read_text(encoding="utf-8"))["closeMatch"]
    out: dict[str, set[str]] = defaultdict(set)
    for fg, uris in cm.items():
        for u in uris:
            out[u].add(SCHULABSCHLUSS if fg.startswith("6.") else fg)
    for d, fgs in EB_PRIOR_PLUS.items():
        out[DISC + d] |= set(fgs)
    return out


def _eb_fg_vorbild(t: dict) -> str | None:
    n = (t.get("fachgebiet") or [{}])[0].get("notation")
    return SCHULABSCHLUSS if n and n.startswith("6.") else n


def zuordnen_eb(themen: list[dict], vorhanden: list[dict], embedder, berufe: list[str] | None = None) -> list[dict]:
    """themen: [{"label", "allgemeiner", "faecher": [discipline-URI], "stufen_uris": [...]}] → je Thema
    {"fachgebiet", "status", "wert", "art", "eignung"}."""
    z = eb_ziele()
    codes = [x["notation"] for x in z]
    texte = [text(t["label"], t.get("allgemeiner")) for t in themen]
    v = embedder.encode(texte, art="anfrage")
    pv = embedder.encode([t.get("label_de") or "" for t in vorhanden], art="anfrage") if vorhanden else         np.zeros((0, v.shape[1]), dtype=np.float32)
    gruppen = [[i for i, t in enumerate(vorhanden) if _eb_fg_vorbild(t) == c] for c in codes]
    w = werte(v, pv, gruppen, embedder.encode([x["beschreibung"] for x in z], art="ziel"))
    beruf = None
    if berufe:
        bv = embedder.encode(berufe, art="anfrage")
        beruf = knn_werte(v, bv, [list(range(len(berufe)))])[:, 0] - knn_werte(v, pv, [list(range(len(vorhanden)))])[:, 0]
    sek = {"sekundarstufe_1", "sekundarstufe_2"}
    out = []
    for i, t in enumerate(themen):
        prior: dict[str, float] = {}
        for d in t.get("faecher") or []:
            for fg in eb_prior().get(d, ()):
                if fg == SCHULABSCHLUSS and not _stufen(t) & sek:
                    continue
                prior[fg] = max(prior.get(fg, 0.0), BONUS_SPRACHE if fg.startswith("4.") else BONUS_FACH)
        for fg, rx in SPRACH_RE.items():
            if rx.search(texte[i]):
                prior[fg] = BONUS_MUSTER
        kursleitung = bool(KURSLEITUNG_RE.search(texte[i]))
        if kursleitung:
            prior[KURSLEITUNG_FG] = max(prior.get(KURSLEITUNG_FG, 0.0), BONUS_FACH)
        for fg, rx in GRUNDBILDUNG_RE.items():
            if rx.search(texte[i]):
                prior[fg] = BONUS_MUSTER
        zeile = w[i].copy()
        for j, c in enumerate(codes):
            if gebunden(c) and c not in prior:
                zeile[j] = -1.0
        fg, status, wert = waehlen(zeile, codes, prior)
        faecher = set(t.get("faecher") or [])
        gruende = []
        if beruf is not None and faecher and faecher <= BERUFSFAECHER and beruf[i] > SCHWELLE_BERUF:
            gruende.append("eher_berufsbildung")
        if faecher and faecher <= SCHULFAECHER and _stufen(t) & sek:
            gruende.append("schulstoff")
        if produktname(t["label"]):
            gruende.append("produktname")
        out.append({"fachgebiet": fg, "status": status, "wert": round(wert, 4),
                    "art": "kursleitungsthema" if kursleitung else "kursthema",
                    "eignung": {"status": "fraglich" if gruende else "geeignet", "gruende": gruende,
                                **({"beruf_abstand": round(float(beruf[i]), 4)} if beruf is not None else {})}})
    return out


# --- Elementarbereich --------------------------------------------------------------------------------

EL_DIR = MAPPING_DIR / "elementar"
# Anker für die Kita-Nähe: Ähnlichkeit zu Kita-Angeboten minus Ähnlichkeit zu Schulunterricht
KITA_ANKER = ["Angebot im Kindergarten für Kinder von drei bis sechs Jahren",
              "Spielen, Basteln und Singen mit Kleinkindern in der Kita"]
SCHUL_ANKER = ["Unterrichtsthema in der Grundschule, Klasse 3 und 4", "Fachunterricht in der Sekundarstufe"]
SCHWELLE_KITA = -0.025
ANTEIL_FACHTHEMA = 0.7  # Art „fachthema“ ohne Muster nur, wenn die ähnlichsten Themen fast alle Fachthemen sind
SCHUL_RE = re.compile(r"schul|unterricht|klasse|lehrkr|lehrer|schüler|beschulung", re.IGNORECASE)
KITA_RE = re.compile(r"übergang|kita|kindergarten|kindertages|vorschul|frühkindlich|elementar|krippe",
                     re.IGNORECASE)
FACHTHEMA_RE = re.compile(r"pädagog|fachkr|erzieh|frühkindlich|elementar|kita|eltern|förderung|inklusion|übergang|"
                          r"beobacht|bildungsarbeit|kindheit|planung|didaktik|methodik|entwicklung (des|von) kind|spracherwerb|"
                          r"sprachentwicklung", re.IGNORECASE)
# Stufen, die für den Elementarbereich nicht sprechen: Datensatz breit über viele Stufen verschlagwortet (markiert nur
# zusammen mit einer Kita-Nähe unter 0)
FERNE_STUFEN = {"sekundarstufe_1", "sekundarstufe_2", "hochschule", "berufliche_bildung", "erwachsenenbildung",
                "fortbildung"}


@lru_cache(maxsize=1)
def el_bereiche() -> list[dict]:
    return json.loads((EL_DIR / "bildungsbereiche.json").read_text(encoding="utf-8"))["bildungsbereiche"]


@lru_cache(maxsize=1)
def el_prior() -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for b in el_bereiche():
        for u in b["closeMatch"]:
            out[u].add(b["id"])
    return out


@lru_cache(maxsize=1)
def _el_zu_allgemein() -> frozenset[str]:
    return frozenset(norm(x) for b in el_bereiche() for x in [b["label"], *re.split(r",| und ", b["label"])])


def zuordnen_elementar(themen: list[dict], vorhanden: list[dict], embedder) -> list[dict]:
    """themen wie bei zuordnen_eb → je Thema {"bildungsbereich", "status", "wert", "altersgruppe", "art",
    "eignung"}. Altersgruppe und Art aus den zehn ähnlichsten vorhandenen Themen (nach Ähnlichkeit gewichtet),
    Art „fachthema“ auch über ein Muster (Pädagogik, Fachkräfte, Eltern, Förderung …)."""
    bb = el_bereiche()
    ids = [b["id"] for b in bb]
    texte = [text(t["label"], t.get("allgemeiner")) for t in themen]
    v = embedder.encode(texte, art="anfrage")
    pv = embedder.encode([t.get("label_de") or "" for t in vorhanden], art="anfrage") if vorhanden else         np.zeros((0, v.shape[1]), dtype=np.float32)
    gruppen = [[i for i, t in enumerate(vorhanden) if t["bildungsbereich"][0]["id"] == b] for b in ids]
    w = werte(v, pv, gruppen, embedder.encode([f"Bildungsbereich in der Kita: {b['label']}. {b['beschreibung']}"
                                               for b in bb], art="ziel"))
    kita = (v @ embedder.encode(KITA_ANKER, art="ziel").T).max(axis=1) - \
        (v @ embedder.encode(SCHUL_ANKER, art="ziel").T).max(axis=1)
    out = []
    for s in range(0, len(v), 4096):
        sims = v[s:s + 4096] @ pv.T
        k = min(10, sims.shape[1])
        top = np.argpartition(-sims, k - 1, axis=1)[:, :k] if k else np.zeros((len(sims), 0), dtype=int)
        for r in range(len(sims)):
            i = s + r
            t = themen[i]
            prior = {b: BONUS_FACH for d in t.get("faecher") or [] for b in el_prior().get(d, ())}
            bid, status, wert = waehlen(w[i], ids, prior)
            alter, art = Counter(), Counter()
            for j in top[r]:
                alter[vorhanden[j]["altersgruppe"]] += float(sims[r, j])
                art[vorhanden[j]["art"]] += float(sims[r, j])
            fach = bool(FACHTHEMA_RE.search(texte[i])) or art["fachthema"] > ANTEIL_FACHTHEMA * sum(art.values())
            gruende = []
            if SCHUL_RE.search(t["label"]) and not KITA_RE.search(t["label"]):
                gruende.append("schulbezug")
            kita_wort = bool(KITA_RE.search(t["label"]))
            if float(kita[i]) < SCHWELLE_KITA and not kita_wort:
                gruende.append("kita_fern")
            if _stufen(t) & FERNE_STUFEN and float(kita[i]) < 0 and not kita_wort:
                gruende.append("breit_verschlagwortet")
            if norm(t["label"]) in _el_zu_allgemein() or norm(t["label"]) == norm("Bildung für nachhaltige Entwicklung"):
                gruende.append("zu_allgemein")
            if produktname(t["label"]):
                gruende.append("produktname")
            out.append({"bildungsbereich": bid, "status": status, "wert": round(wert, 4),
                        "altersgruppe": alter.most_common(1)[0][0] if alter else "beide",
                        "art": "fachthema" if fach else "kinderthema",
                        "eignung": {"status": "fraglich" if gruende else "geeignet", "gruende": gruende,
                                    "kita_naehe": round(float(kita[i]), 4)}})
    return out


# --- Berufsbildung -----------------------------------------------------------------------------------

# Fächer der beruflichen Bildung (OEH discipline): „Berufliche Bildung“ und ihre Berufsfelder. Ein Thema mit einem
# dieser Fächer gilt als beruflich; sonst (allgemeinbildende Schulfächer wie Religion, Politik, Geschichte, Sprachen,
# „Allgemein“ oder ohne Fach) wird es „schulstoff“, wenn es vorhandenen Schulthemen näher ist als den ESCO-Kompetenzen
# und Lernfeldern (Abstand < SCHWELLE_SCHULSTOFF)
BERUFLICHE_FAECHER = {DISC + d for d in ("040", "04001", "04002", "04003", "04005", "04006", "04007", "04009",
                                         "04010", "04011", "04012", "04013", "04014", "oeh04010")}
SCHWELLE_SCHULSTOFF = 0.0
BB_QUALIFIZIERUNG = ("ausbildung", "fortbildung")  # Berufe, denen ein Thema zugeordnet werden kann (Standardfilter)


@lru_cache(maxsize=1)
def bb_prior() -> dict[str, set[str]]:
    """discipline-URI → KldB-Berufshauptgruppen (Umkehrung von kldb_discipline.json, ohne „Berufliche Bildung“)."""
    cm = json.loads((MAPPING_DIR / "berufsbildung" / "kldb_discipline.json").read_text(encoding="utf-8"))["closeMatch"]
    out: dict[str, set[str]] = defaultdict(set)
    for hg, uris in cm.items():
        for u in uris:
            out[u].add(hg)
    return out


def _knn_zeilen(v: np.ndarray, vorbilder: np.ndarray, k: int = K_KNN, batch: int = 2048) -> np.ndarray:
    """Je Zeile das Mittel der k größten Ähnlichkeiten zu allen Vorbildern (nan ohne Vorbilder)."""
    if not len(vorbilder):
        return np.full(len(v), np.nan, dtype=np.float32)
    kk = min(k, len(vorbilder))
    out = np.empty(len(v), dtype=np.float32)
    for s in range(0, len(v), batch):
        sims = v[s:s + batch] @ vorbilder.T
        out[s:s + batch] = -np.partition(-sims, kk - 1, axis=1)[:, :kk].mean(axis=1)
    return out


def zuordnen_bb(themen: list[dict], berufe: list[dict], vorbilder: list[str], schule: list[str], embedder) -> list[dict]:
    """themen wie bei zuordnen_eb; berufe: [{"uri", "text", "hauptgruppe", "vorbilder": [Index in vorbilder]}]
    (Kompetenzen und Lernfelder des Berufs), vorbilder: Texte, schule: Labels vorhandener Schulthemen. → je Thema
    {"beruf", "status", "wert", "eignung"}. Wert je Beruf wie bei EB: Mittel der drei ähnlichsten Kompetenzen bzw.
    Lernfelder des Berufs (0,6) und Ähnlichkeit zur Berufsbeschreibung (0,4); zum Fach passende Berufe (KldB-
    Berufshauptgruppe über kldb_discipline.json) bevorzugt."""
    texte = [text(t["label"], t.get("allgemeiner")) for t in themen]
    v = embedder.encode(texte, art="anfrage")
    vv = embedder.encode(vorbilder, art="anfrage") if vorbilder else np.zeros((0, v.shape[1]), dtype=np.float32)
    uris = [b["uri"] for b in berufe]
    w = werte(v, vv, [b["vorbilder"] for b in berufe], embedder.encode([b["text"] for b in berufe], art="ziel"))
    sv = embedder.encode(schule, art="anfrage") if schule else np.zeros((0, v.shape[1]), dtype=np.float32)
    naehe = _knn_zeilen(v, vv) - _knn_zeilen(v, sv) if len(sv) else np.zeros(len(v), dtype=np.float32)
    hg = [b["hauptgruppe"] for b in berufe]
    out = []
    for i, t in enumerate(themen):
        hgs = {h for d in t.get("faecher") or [] for h in bb_prior().get(d, ())}
        prior = {u: BONUS_FACH for u, h in zip(uris, hg) if h in hgs}
        beruf, status, wert = waehlen(w[i], uris, prior)
        faecher = set(t.get("faecher") or [])
        gruende = []
        if not faecher & BERUFLICHE_FAECHER and float(naehe[i]) < SCHWELLE_SCHULSTOFF:
            gruende.append("schulstoff")
        if produktname(t["label"]):
            gruende.append("produktname")
        out.append({"beruf": beruf, "status": status, "wert": round(wert, 4),
                    "eignung": {"status": "fraglich" if gruende else "geeignet", "gruende": gruende,
                                "berufsnaehe": round(float(naehe[i]), 4)}})
    return out


# --- Schule: Stufe für Datensätze nur mit „Schule“ ------------------------------------------------------

K_STUFEN = 10
ANTEIL_STUFE = 0.3


def stufen_vorschlagen(themen: list[dict], vorhanden: list[dict], embedder) -> list[list[str]]:
    """Schulstufen (primar, sek1, sek2) für Themen, deren Datensätze nur „Schule“ tragen: die K_STUFEN ähnlichsten
    vorhandenen Schulthemen desselben Fachs (ohne Fachtreffer: aller Fächer), nach Ähnlichkeit gewichtet; jede Stufe
    mit mindestens ANTEIL_STUFE des Gewichts, sonst die stärkste. themen: [{"label", "allgemeiner", "faecher"}]."""
    vh = [t for t in vorhanden if t.get("stufen")]
    if not themen:
        return []
    if not vh:
        return [["sek1"] for _ in themen]
    v = embedder.encode([text(t["label"], t.get("allgemeiner")) for t in themen], art="anfrage")
    pv = embedder.encode([t.get("label_de") or "" for t in vh], art="anfrage")
    je_fach: dict[str, list[int]] = defaultdict(list)
    for j, t in enumerate(vh):
        for f in t.get("faecher") or []:
            je_fach[f].append(j)
    alle = list(range(len(vh)))
    out = []
    for i, t in enumerate(themen):
        kand = sorted({j for f in t.get("faecher") or [] for j in je_fach.get(f, [])}) or alle
        sims = pv[kand] @ v[i]
        k = min(K_STUFEN, len(kand))
        gew = Counter()
        for r in np.argpartition(-sims, k - 1)[:k]:
            st = vh[kand[r]]["stufen"]
            for s in st:
                gew[s] += max(float(sims[r]), 0.0) / len(st)
        summe = sum(gew.values()) or 1.0
        st = [s for s in ("primar", "sek1", "sek2") if gew[s] >= ANTEIL_STUFE * summe]
        out.append(st or [gew.most_common(1)[0][0] if gew else "sek1"])
    return out
