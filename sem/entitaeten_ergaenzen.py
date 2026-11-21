"""Erfundene Namen aus den Stilvorgaben (synth:stil) in die Entitäten fertiger Materialien nachtragen, ohne LLM.

Entstanden nach der Prüfung des Schul-Datensatzes (2026-11-21): In 12 % der Materialien fehlte mindestens ein Name
aus synth:stil in synth:entitaeten, obwohl er im Volltext steht (2.142 von 2.695 Materialien ohne Entitäten hatten
solche Namen). Diese Namen sind für das Material erfunden; Typ, Fiktiv-Kennzeichen und Zeichenpositionen lassen sich
deshalb sicher bestimmen:

- Ein Name gilt als vorhanden, wenn eine Entität ihn oder seinen Nachnamen („Lehmann“ für „Herr Lehmann“) als ganzes
  Wort in name oder im_text trägt.
- Sonst wird er ergänzt, wenn er als ganzes Wort im Volltext steht, ersatzweise nur sein letztes Wort: name ist der
  Name aus synth:stil, im_text die Form im Text, typ Person, art Eigenname, fiktiv true, positionen alle Stellen.
- Ergänzte Personen stehen hinter den vorhandenen Eigennamen und vor den Begriffen; die Liste bleibt bei höchstens
  ENTITAETEN_MAX Einträgen (überzählige Begriffe am Ende fallen weg).

    python -m sem entitaeten-ergaenzen --materialien output/materialien_….jsonl [--out …]

Die Eingabedatei wird nie verändert. Ergänzte Materialien tragen synth:generierung.entitaeten_ergaenzt (Anzahl der
ergänzten Namen); der HF-Export nennt sie in der Spalte correction als „entities“.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from . import schema as S
from .generate import _ENTITAET_KATEGORIE, ENTITAETEN_MAX, _stellen

F_ERGAENZT = "entitaeten_ergaenzt"  # unter synth:generierung


def _wort_in(teil: str, text: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(teil) + r"(?!\w)", text))


def ergaenzen_rec(rec: dict) -> int:
    """Ergänzt fehlende Stilnamen in rec (in place); gibt die Zahl der ergänzten Namen zurück."""
    volltext = rec.get(S.F_FULLTEXT) or ""
    namen = [n.strip() for n in (rec.get(S.F_STIL) or {}).get("namen") or [] if n and n.strip()]
    if not volltext or not namen:
        return 0
    ents = list(rec.get(S.F_ENTITAETEN) or [])
    formen = [f for e in ents for f in (e.get("name") or "", e.get("im_text") or "") if f]
    neu = []
    for name in namen:
        nach = name.split()[-1]
        if any(_wort_in(name, f) or _wort_in(nach, f) for f in formen):
            continue
        for im_text in (name, nach) if nach != name else (name,):
            if stellen := _stellen(volltext, im_text):
                neu.append({"name": name, "typ": "Person", "kategorie": _ENTITAET_KATEGORIE["Person"],
                            "art": "Eigenname", "im_text": im_text, "fiktiv": True, "positionen": stellen})
                formen += [name, im_text]
                break
    if not neu:
        return 0
    eigen = [e for e in ents if e.get("art") == "Eigenname"]
    rest = [e for e in ents if e.get("art") != "Eigenname"]
    rec[S.F_ENTITAETEN] = (eigen + neu + rest)[:ENTITAETEN_MAX]
    rec[S.F_GENERATION] = {**(rec.get(S.F_GENERATION) or {}), F_ERGAENZT: len(neu)}
    return len(neu)


def standard_ausgabe(materialien: Path) -> Path:
    return materialien.with_name(materialien.stem + "_entitaeten.jsonl")


def ergaenzen(materialien: Path, out_path: Path) -> dict:
    materialien, out_path = Path(materialien), Path(out_path)
    if out_path.resolve() == materialien.resolve():
        raise ValueError("Ausgabe muss eine neue Datei sein")
    z = Counter()
    with materialien.open(encoding="utf-8") as f, out_path.open("w", encoding="utf-8") as o:
        for zeile in f:
            if not zeile.strip():
                continue
            rec = json.loads(zeile)
            z["materialien"] += 1
            leer = not rec.get(S.F_ENTITAETEN)
            if n := ergaenzen_rec(rec):
                z["materialien_ergaenzt"] += 1
                z["namen_ergaenzt"] += n
                z["vorher_ohne_entitaeten"] += leer
            o.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return dict(z)
