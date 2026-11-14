"""Kommandozeile.

    python -m sem info
    python -m sem seeds --anzahl hochschule=30000 [--gleichverteilung hochschule=thema] [--seed 42] [--out …]
                        [--auswahl hochschule.fach_status=gesichert,wahrscheinlich hochschule.belege=lehrbuch,wlo]
    python -m sem auswahl hochschule   (Filter, Standardwerte und Themenzahl anzeigen)
    python -m sem materialien --seeds output/seeds.jsonl [--out …] [--limit N] [--workers 20] [--model …] [--flex]
    python -m sem materialien --seeds output/seeds.jsonl --batch [--warten]   (Batch: halber Preis, Runden bis 24 h)
    python -m sem materialien --seeds output/seeds.jsonl --anbieter anthropic [--batch]   (Claude, Standard Haiku 5.5)
    python -m sem laeufe                          (Läufe, die ihr Ziel nicht erreicht haben)
    python -m sem materialien --fortsetzen NAME   (unfertigen Lauf mit seinen Einstellungen fortsetzen)
    python -m sem titel-neu --materialien output/materialien_….jsonl [--out …] [--limit N]   (nur Titel neu)
    python -m sem beschreibung-einstieg --materialien output/materialien_….jsonl   (nur Schablonen-Anfang neu)
    python -m sem faecher-ergaenzen --materialien output/materialien_….jsonl   (OEH-Fach für Berufs- und EB nachtragen)
    python -m sem mappings hochschule [alle|openalex|gnd|rvk|schritt …] [--limit N] [--stimmen 3] [--wlo export.csv …]
                                     [--rvk data/lokal/rvk/rvko_marcxml_2026_3.xml.gz]
    python -m sem mappings hochschule --liste
    python -m sem wlo-themen --csv wlo_max_dataset_slim.csv [--limit 2000] [--ohne-stufe vorschlag|nur-thema|weglassen]
                             [--warten]   (Themen-Abstraktion aus WLO-Datensätzen, Batch, fortsetzbar)
    python -m sem wlo-themen --zusammenfuehren [--gnd-online] [--ohne-embeddings]   (Themen für die Seeds)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .paths import ausgabe_pfad


def _pairs(values: list[str], cast=str) -> dict:
    out = {}
    for v in values or []:
        k, _, x = v.partition("=")
        if not x:
            raise SystemExit(f"Erwartet bereich=wert, nicht {v!r}")
        out[k.strip()] = cast(x.strip())
    return out


def _auswahl(values: list[str]) -> dict:
    """bereich.filter=w1,w2 → {bereich: {filter: [w1, w2]}}; leerer Wert = keine Werte."""
    out: dict = {}
    for v in values or []:
        k, eq, x = v.partition("=")
        bereich, dot, name = k.partition(".")
        if not eq or not dot:
            raise SystemExit(f"Erwartet bereich.filter=wert1,wert2, nicht {v!r}")
        out.setdefault(bereich.strip(), {})[name.strip()] = [w.strip() for w in x.split(",") if w.strip()]
    return out


def _llm_args(p):
    g = p.add_argument_group("LLM (Standard aus LLM_ANBIETER, OPENAI_BASE_URL, OPENAI_API_KEY bzw. ANTHROPIC_BASE_URL, "
                             "ANTHROPIC_API_KEY, LLM_MODEL, LLM_WORKERS)")
    g.add_argument("--anbieter", choices=["openai", "anthropic"],
                   help="openai = jede OpenAI-kompatible API (Standard), anthropic = Claude über die Anthropic-API "
                        "(Standardmodell claude-haiku-5-5, Key aus ANTHROPIC_API_KEY); Flex gibt es dort nicht, "
                        "halber Preis über --batch")
    g.add_argument("--base-url")
    g.add_argument("--api-key")
    g.add_argument("--model")
    g.add_argument("--workers", type=int)
    g.add_argument("--retries", type=int, dest="max_retries")
    g.add_argument("--ausfall-minuten", type=float, dest="outage_minutes",
                   help="so lange wird bei nicht erreichbarer API gewartet, dann pausiert der Lauf (Standard 10)")
    g.add_argument("--reasoning-effort", choices=["standard", "minimal", "low", "medium", "high", "xhigh", "max"],
                   help="nur für Reasoning-Modelle: weniger Denkaufwand = schneller; Standard low "
                        "(LLM_REASONING_EFFORT), „standard“ = Parameter nicht senden; bei Anthropic als effort "
                        "gesendet (minimal = low; xhigh und max nur dort)")
    g.add_argument("--flex", action="store_const", const="flex", dest="service_tier",
                   help="Flex-Verarbeitung (OpenAI): halber Preis, langsamere Antworten, bei Engpässen Ablehnungen "
                        "(werden wiederholt)")


def _stand_arg(p):
    p.add_argument("--stand", type=Path, metavar="DATEI",
                   help="Fortschritt als JSON in DATEI (NAME.stand.json) schreiben und anhalten, sobald NAME.stop "
                        "existiert; so startet die Oberfläche Läufe als eigenen Prozess (sem/prozess.py)")


def _mit_stand(a, fn, **kwargs):
    """fn(on_progress=…, stop_event=…, **kwargs) mit Fortschritt im Terminal oder, mit --stand, in der Stand-Datei."""
    from .llm import print_progress

    if not a.stand:
        return fn(on_progress=print_progress, **kwargs)
    from .prozess import StandSchreiber, stop_beobachten

    schreiber = StandSchreiber(a.stand)
    try:
        prog = fn(on_progress=schreiber, stop_event=stop_beobachten(a.stand), **kwargs)
    except BaseException as e:
        schreiber.ende(None, fehler=f"{type(e).__name__}: {e}")
        raise
    schreiber.ende(prog)
    return prog


def _cfg(a):
    from .llm import LLMConfig

    try:
        cfg = LLMConfig.from_env(anbieter=a.anbieter, base_url=a.base_url, api_key=a.api_key, model=a.model,
                                 workers=a.workers, max_retries=a.max_retries, outage_minutes=a.outage_minutes,
                                 reasoning_effort=a.reasoning_effort, service_tier=getattr(a, "service_tier", None))
        cfg.pruefen()
    except ValueError as e:
        raise SystemExit(str(e)) from e
    return cfg


def main(argv=None):
    # Windows-Konsole/Umleitung nutzt sonst cp1252 und bricht bei „→“ oder Umlauten in Fremdtexten ab
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m sem", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="Bildungsbereiche, Themenzahl und Gleichverteilungsfelder anzeigen")

    p = sub.add_parser("seeds", help="Seeds erzeugen (ohne LLM)")
    p.add_argument("--anzahl", nargs="+", required=True, metavar="BEREICH=N")
    p.add_argument("--gleichverteilung", nargs="*", metavar="BEREICH=FELD")
    p.add_argument("--seed", type=int, default=42, help="Zufalls-Seed (gleicher Wert = gleiche Seeds)")
    p.add_argument("--auswahl", nargs="*", metavar="BEREICH.FILTER=W1,W2",
                   help="welche Themen einbezogen werden (Status, Quellen, Belege); fehlende Filter = Standard")
    p.add_argument("--konfidenz", metavar="W1,W2",
                   help="Status aller Zuordnungen in allen Bereichen, z. B. gesichert oder gesichert,wahrscheinlich "
                        "(Standard: gesichert,wahrscheinlich,redaktionell); --auswahl hat Vorrang")
    p.add_argument("--out", type=Path, help="Dateiname; das Präfix seeds_ wird ergänzt")
    _stand_arg(p)

    p = sub.add_parser("auswahl", help="Filter eines Bildungsbereichs mit Standardwerten und Themenzahl")
    p.add_argument("bereich")
    p.add_argument("--auswahl", nargs="*", metavar="BEREICH.FILTER=W1,W2")

    p = sub.add_parser("materialien", help="Materialien aus Seeds erzeugen (LLM, fortsetzbar)")
    p.add_argument("--seeds", type=Path, help="Seed-Datei (entfällt mit --fortsetzen)")
    p.add_argument("--ohne-flex", action="store_true",
                   help="mit --fortsetzen: zum Standardpreis weiter, auch wenn der Lauf mit Flex lief")
    p.add_argument("--fortsetzen", metavar="NAME",
                   help="unfertigen Lauf fortsetzen (Name der Ausgabe, siehe python -m sem laeufe): Seed-Datei, Anzahl, "
                        "Sprache, Modell und Verarbeitung kommen aus dem Lauf")
    p.add_argument("--out", type=Path, help="Dateiname; das Präfix materialien_ wird ergänzt "
                                            "(Standard: Name der Seed-Datei)")
    p.add_argument("--limit", type=int)
    p.add_argument("--ohne-inhaltstyp-pruefung", action="store_true",
                   help="Inhaltstyp nicht am fertigen Material prüfen (spart einen Aufruf je Material)")
    p.add_argument("--sprache", default="de", metavar="CODE",
                   help="Ausgabesprache als ISO-Code (de, en, fr, es, …; Standard de). Der Hinweis an das Modell "
                        "steht in prompts/ausgabesprache.md")
    p.add_argument("--batch", action="store_true",
                   help="über die Batch-Schnittstelle (OpenAI): halber Preis, Ergebnis je Runde bis 24 h; jeder Aufruf "
                        "holt Fertiges ab und schickt die nächste Runde (Stand in <ausgabe>.batch/)")
    p.add_argument("--warten", action="store_true", help="mit --batch: wiederholen, bis alles fertig ist")
    p.add_argument("--intervall", type=float, default=60, help="mit --warten: Sekunden zwischen den Abfragen")
    _stand_arg(p)
    _llm_args(p)

    p = sub.add_parser("titel-neu", help="Titel fertiger Materialien mit dem aktuellen Titel-Prompt neu erzeugen "
                                         "(neue Datei, übrige Felder unverändert, fortsetzbar)")
    p.add_argument("--materialien", type=Path, required=True, help="fertige Materialien (JSONL)")
    p.add_argument("--out", type=Path, help="Ausgabe (Standard: <eingabe>_titel_neu.jsonl daneben)")
    p.add_argument("--limit", type=int, help="nur die ersten N Materialien")
    _llm_args(p)

    p = sub.add_parser("beschreibung-einstieg",
                       help="Schablonen-Anfang der Beschreibungen fertiger Materialien („Im Mittelpunkt …“, „Das "
                            "Material …“) ersetzen: nur der erste Satz neu (neue Datei, fortsetzbar)")
    p.add_argument("--materialien", type=Path, required=True, help="fertige Materialien (JSONL)")
    p.add_argument("--out", type=Path, help="Ausgabe (Standard: <eingabe>_einstieg.jsonl daneben)")
    p.add_argument("--limit", type=int, help="nur die ersten N Materialien")
    _llm_args(p)

    p = sub.add_parser("faecher-ergaenzen",
                       help="OEH-Fach (ccm:taxonid) in fertigen Materialien der Berufs- und Erwachsenenbildung "
                            "nachtragen (Handmapping, ohne LLM, neue Datei)")
    p.add_argument("--materialien", type=Path, required=True, help="fertige Materialien (JSONL)")
    p.add_argument("--out", type=Path, help="Ausgabe (Standard: <eingabe>_faecher.jsonl daneben)")

    p = sub.add_parser("gnd-ergaenzen", help="GND-Themen aller Bereiche ergänzen: Ersatz für Motiv-Sätze und "
                                             "Kurzbeschreibungen (GND, Wikidata) → data/mappings/gnd_ergaenzungen.jsonl")
    p.add_argument("--limit", type=int, help="nur die ersten N Themen (zum Testen)")

    sub.add_parser("laeufe", help="Materialläufe, die ihr Ziel nicht erreicht haben (abgebrochen oder mit Fehlern)")

    p = sub.add_parser("mappings", help="Mappings eines Bildungsbereichs neu bauen (Schritte siehe --liste)")
    p.add_argument("bereich")
    p.add_argument("schritte", nargs="*", default=["alle"],
                   help="Schritte oder Folgen (z. B. openalex, gnd) in dieser Reihenfolge, Standard: alle")
    p.add_argument("--liste", action="store_true", help="Schritte des Bereichs anzeigen")
    p.add_argument("--limit", type=int, help="nur die ersten N Themen (zum Testen)")
    p.add_argument("--stimmen", type=int, default=3, help="LLM-Stimmen je Zuordnung")
    p.add_argument("--embedding-modell", help="lokales Embedding-Modell (Standard intfloat/multilingual-e5-small)")
    p.add_argument("--wlo", nargs="+", metavar="CSV", help="WLO-Exporte für Keyword-Fach-Belege (bleiben lokal)")
    p.add_argument("--rlp", metavar="ORDNER", help="Markdown-Fassungen der KMK-Rahmenlehrpläne (Berufsbildung, "
                                                  "Schritt lernfelder; Standard RLP_MARKDOWN_DIR)")
    p.add_argument("--rvk", metavar="DATEI", help="RVK-Gesamtabzug MarcXML (.xml.gz; Hochschule, Schritt rvk_klassen; "
                                                 "Standard: neueste Datei unter data/lokal/rvk/)")
    p.add_argument("--digcomp", metavar="DATEI", help="DigComp 3.0 Data Supplement (.jsonld; Erwachsenenbildung, "
                                                     "Schritt digcomp; Standard: data/lokal/digcomp/)")
    _stand_arg(p)
    _llm_args(p)

    p = sub.add_parser("wlo-themen", help="Themen-Abstraktion aus WLO-Datensätzen (Batch, fortsetzbar) und Themen für "
                                          "die Seeds (Herkunft wlo_abstraktion, nur lokal in data/lokal/wlo_abstraktion/)")
    p.add_argument("--csv", type=Path, help="WLO-Export (;-getrennt, Standard: WLO_CSV)")
    p.add_argument("--limit", type=int, help="geschichtete Stichprobe aus N Gruppen (nach Stufe und Fach)")
    p.add_argument("--ohne-stufe", choices=["vorschlag", "nur-thema", "weglassen"], default="vorschlag",
                   help="Datensätze ohne Bildungsstufe oder Fach: Thema mit Fach- und Stufenvorschlag aus dem "
                        "OEH-Vokabular (Standard, markiert als vorschlag), nur Thema, oder (ohne Stufe) weglassen")
    p.add_argument("--pool", choices=["alle", "ohne_stufe", "maskiert"], default="alle",
                   help="Stichprobe nur aus Datensätzen ohne Stufe, oder maskiert: Datensätze mit Fach und Stufe "
                        "ohne beides fragen und die Vorschläge mit den echten Werten vergleichen (nie als Seed)")
    p.add_argument("--zufall", type=int, default=42, help="Startwert der Stichprobe")
    p.add_argument("--out", type=Path, help="Ausgabe (Standard: data/lokal/wlo_abstraktion/abstraktion[_nN].jsonl)")
    p.add_argument("--vergleich-lokal", action="store_true",
                   help="zusätzlich <ausgabe>.vergleich_lokal.jsonl mit Titel zur Prüfung (nie weitergeben)")
    p.add_argument("--warten", action="store_true", help="wiederholen, bis alles fertig ist")
    p.add_argument("--intervall", type=float, default=60, help="mit --warten: Sekunden zwischen den Abfragen")
    p.add_argument("--sofort", action="store_true",
                   help="ohne Batch: Anfragen sofort parallel stellen (--workers), etwa wenn die Batch-API hängt; "
                        "zum Standardpreis (doppelt), mit --flex zum Batch-Preis; laufende Batches werden "
                        "weiter abgeholt")
    p.add_argument("--max-kosten", type=float, metavar="USD",
                   help="erst einen Prüfabschnitt (--pruefabschnitt Gruppen, sofort über Flex) abfragen, Cache-Quote "
                        "und hochgerechnete Gesamtkosten (Batch-Preis) melden und nur weiterlaufen, wenn die "
                        "Hochrechnung höchstens USD beträgt")
    p.add_argument("--pruefabschnitt", type=int, default=3000, metavar="N",
                   help="mit --max-kosten: Gruppen im Prüfabschnitt (Standard 3000)")
    p.add_argument("--nur-zaehlen", action="store_true",
                   help="nur lesen und zählen (Gruppen, aussortierte Platzhalter- und Testdatensätze), ohne API")
    p.add_argument("--zusammenfuehren", nargs="*", type=Path, metavar="ABSTRAKTION",
                   help="Themen für die Seeds bauen (ohne LLM) aus den genannten Ausgaben, Standard: alle "
                        "abstraktion*.jsonl in data/lokal/wlo_abstraktion/")
    p.add_argument("--bereiche", default="schule,hochschule",
                   help="mit --zusammenfuehren: Bereiche, für die Themen gebaut werden (Standard schule,hochschule)")
    p.add_argument("--gnd-online", action="store_true", help="mit --zusammenfuehren: Fachbegriffe ohne lokalen "
                                                             "GND-Treffer bei lobid suchen")
    p.add_argument("--ohne-embeddings", action="store_true", help="mit --zusammenfuehren: nur exakte Verknüpfungen")
    p.add_argument("--embedding-modell", help="Standard intfloat/multilingual-e5-small")
    _llm_args(p)

    a = ap.parse_args(argv)

    if a.cmd == "info":
        from .bereiche import BEREICHE

        for key, cls in BEREICHE.items():
            print(json.dumps(cls().describe(), ensure_ascii=False, indent=1))

    elif a.cmd == "seeds":
        from . import seeds

        from .bereiche import get

        counts = _pairs(a.anzahl, int)
        balance = _pairs(a.gleichverteilung)
        roh = _auswahl(a.auswahl)
        konf = [w.strip() for w in a.konfidenz.split(",") if w.strip()] if a.konfidenz else None
        auswahl = {k: get(k).auswahl_pruefen(get(k).mit_konfidenz(roh.get(k), konf)) for k in set(roh) | set(counts)}
        auswahl = {k: auswahl[k] for k in counts}
        out = ausgabe_pfad("seeds", a.out or "_".join(f"{k}{v}" for k, v in counts.items()) + f"_s{a.seed}")
        st = seeds.write(seeds.build(counts, balance, a.seed, auswahl), out,
                         {"anzahl": counts, "gleichverteilung": balance, "zufalls_seed": a.seed, "auswahl": auswahl})
        print(f"{st['anzahl']} Seeds → {out}")
        if a.stand:  # Oberfläche: Abschlussmeldung samt Ausgabedatei
            from .llm import Progress
            from .prozess import StandSchreiber

            StandSchreiber(a.stand).ende(Progress(total=st["anzahl"], done=st["anzahl"], status="fertig"),
                                         ausgabe=str(out))
        print(json.dumps({k: st[k] for k in ("themen_abgedeckt", "seeds_je_gruppe", "seeds_je_thema", "niveau",
                                             "status", "belege")}, ensure_ascii=False))
        fehlt = [k for k, v in st.get("lokale_daten", {}).items() if v is None]
        if fehlt:
            print(f"Hinweis: lokale Dateien fehlen ({', '.join(fehlt)}); die Seeds weichen von einem Lauf mit "
                  "diesen Dateien ab (siehe data/README.md, data/lokal/)", file=sys.stderr)

    elif a.cmd == "auswahl":
        from .bereiche import get

        b = get(a.bereich)
        wahl = b.auswahl_pruefen(_auswahl(a.auswahl).get(a.bereich))
        for k, o in b.auswahl_optionen().items():
            print(f"{k} – {o['label']}: {o['beschreibung']}")
            for w, txt in o["werte"].items():
                print(f"  [{'x' if w in wahl[k] else ' '}] {w:16} {txt}")
        print(f"{len(b.themen_auswahl(wahl))} von {len(b.topics())} Themen")

    elif a.cmd == "laeufe":
        from .laeufe import unfertige_laeufe

        laeufe = unfertige_laeufe()
        for L in laeufe:
            print(f"{L['name']}: {L['fertig']} von {L['ziel']} fertig, {L['fehlgeschlagen']} fehlgeschlagen · "
                  f"{L['verarbeitung']} · zuletzt {L['zuletzt']}" + (" · gesperrt (läuft oder abgebrochen)"
                                                                       if L["gesperrt"] else ""))
        print(f"{len(laeufe)} unfertige Läufe. Fortsetzen: python -m sem materialien --fortsetzen NAME"
              if laeufe else "Keine unfertigen Läufe.")

    elif a.cmd == "materialien":
        from .generate import generate

        if a.fortsetzen:
            from .laeufe import lauf_stand

            lauf = lauf_stand(ausgabe_pfad("materialien", a.fortsetzen))
            if not lauf:
                raise SystemExit(f"Kein Lauf {a.fortsetzen!r}; siehe python -m sem laeufe")
            a.seeds, a.out, a.limit, a.sprache = Path(lauf["seeds"]), Path(lauf["ausgabe"]), lauf["limit"],                 lauf["ausgabesprache"]
            a.ohne_inhaltstyp_pruefung = not lauf["inhaltstyp_pruefen"]
            a.batch = lauf["verarbeitung"] == "batch"
            a.anbieter = a.anbieter or lauf["anbieter"]
            a.model = a.model or lauf["modell"]
            a.reasoning_effort = a.reasoning_effort or lauf["reasoning_effort"] or "standard"
            if getattr(a, "ohne_flex", False):
                a.service_tier = None
            elif not a.service_tier:  # --flex wählt Flex auch für einen Lauf, der ohne lief
                a.service_tier = "flex" if lauf["verarbeitung"] == "flex" else None
        elif not a.seeds:
            raise SystemExit("--seeds oder --fortsetzen angeben")
        out = ausgabe_pfad("materialien", a.out or a.seeds)
        if a.batch:
            from .batch import BatchLauf, status_zeile

            lauf = BatchLauf(a.seeds, out, _cfg(a), limit=a.limit, inhaltstyp_pruefen=not a.ohne_inhaltstyp_pruefung,
                             ausgabesprache=a.sprache)
            show = lambda st: print(time.strftime("%H:%M:%S"), status_zeile(st), flush=True)  # noqa: E731
            st = lauf.warten(a.intervall, on_status=show) if a.warten else lauf.weiter()
            if not a.warten:
                show(st)
            print(f"→ {out}" + ("" if st["abgeschlossen"] else "  (später erneut aufrufen, um fortzusetzen)"))
            if a.stand:  # Oberfläche: Stand der Batch-Runde
                from .llm import Progress
                from .prozess import StandSchreiber

                p = Progress(total=st["materialien"], done=st["fertig"], failed=st["fehlgeschlagen"],
                             status="fertig" if st["abgeschlossen"] else "Runde verschickt")
                StandSchreiber(a.stand).ende(p, batch=status_zeile(st), abgeschlossen=st["abgeschlossen"])
            return
        prog = _mit_stand(a, lambda **kw: generate(a.seeds, out, _cfg(a), **kw), limit=a.limit,
                          inhaltstyp_pruefen=not a.ohne_inhaltstyp_pruefung, ausgabesprache=a.sprache)
        print(f"{prog.done} neu, {prog.skipped} schon vorhanden, {prog.failed} fehlgeschlagen → {out}")
        if prog.status != "fertig":
            sys.exit(2)

    elif a.cmd == "titel-neu":
        from .titel_neu import standard_ausgabe, titel_neu

        out = a.out or standard_ausgabe(a.materialien)
        prog = titel_neu(a.materialien, out, _cfg(a), limit=a.limit)
        print(f"{prog.done} neu, {prog.skipped} schon vorhanden, {prog.failed} fehlgeschlagen → {out}")
        if prog.status != "fertig":
            sys.exit(2)

    elif a.cmd == "beschreibung-einstieg":
        from .beschreibung_einstieg import beschreibung_einstieg, standard_ausgabe

        out = a.out or standard_ausgabe(a.materialien)
        prog = beschreibung_einstieg(a.materialien, out, _cfg(a), limit=a.limit)
        print(f"{prog.done} neu, {prog.skipped} schon vorhanden, {prog.failed} fehlgeschlagen → {out}")
        if prog.status != "fertig":
            sys.exit(2)

    elif a.cmd == "faecher-ergaenzen":
        from .faecher import ergaenzen, standard_ausgabe

        out = a.out or standard_ausgabe(a.materialien)
        print(json.dumps(ergaenzen(a.materialien, out), ensure_ascii=False), "→", out)

    elif a.cmd == "gnd-ergaenzen":
        from .gnd_ergaenzung import DATEI, bauen

        print(json.dumps(bauen(limit=a.limit, on_progress=print), ensure_ascii=False), "→", DATEI)

    elif a.cmd == "mappings":
        from .bereiche import get

        b = get(a.bereich)
        schritte = {x.key: x for x in b.mapping_schritte()}
        if a.liste or not schritte:
            for x in schritte.values():
                print(f"{x.key:16} {'LLM ' if x.llm else '    '}{x.beschreibung}")
            for name, folge in b.mapping_folgen.items():
                print(f"{name} = " + " → ".join(folge))
            print("alle = " + " + ".join(b.mapping_folgen))
            if b.mapping_abschluss:
                print("zuletzt immer: " + ", ".join(b.mapping_abschluss))
            return
        keys = b.schritte_aufloesen(a.schritte)
        unbekannt = [k for k in keys if k not in schritte]
        if unbekannt:
            raise SystemExit(f"Unbekannte Schritte {unbekannt}. Möglich: {', '.join(schritte)}, "
                             f"{', '.join(b.mapping_folgen)}, alle")
        prog = _mit_stand(a, lambda **kw: b.mapping_ausfuehren(keys, _cfg(a), **kw), limit=a.limit, stimmen=a.stimmen,
                          embedding_modell=a.embedding_modell, wlo_dateien=a.wlo, rlp_ordner=a.rlp,
                          rvk_datei=a.rvk, digcomp_datei=a.digcomp)
        print("\n" + prog.line())
        if "fertig" not in prog.status:
            sys.exit(2)

    elif a.cmd == "wlo-themen":
        _wlo_themen(a)


def _wlo_themen(a):
    import os

    from . import wlo_seeds, wlo_themen as W

    if a.zusammenfuehren is not None:
        quellen = a.zusammenfuehren or sorted(p for p in W.ordner().glob("abstraktion*.jsonl")
                                              if not p.name.endswith((".fehler.jsonl", ".vergleich_lokal.jsonl")))
        if not quellen:
            raise SystemExit(f"Keine Abstraktion in {W.ordner()}; erst python -m sem wlo-themen --csv … ausführen")
        emb = None
        if not a.ohne_embeddings:
            from .mapping.embeddings import DEFAULT_EMBEDDING_MODEL, Embedder

            emb = Embedder(a.embedding_modell or DEFAULT_EMBEDDING_MODEL)
        st = wlo_seeds.bauen(quellen, [b.strip() for b in a.bereiche.split(",") if b.strip()], embedder=emb,
                             online=a.gnd_online)
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return
    csv_pfad = a.csv or (Path(os.environ["WLO_CSV"]) if os.environ.get("WLO_CSV") else None)
    if not csv_pfad:
        raise SystemExit("--csv angeben (oder WLO_CSV setzen)")
    t0 = time.time()
    from collections import Counter

    aussortiert = Counter()
    gruppen = W.gruppieren(W.lesen(csv_pfad, aussortiert=aussortiert))
    print(f"{len(gruppen)} Gruppen nach Dublettenabgleich ({time.time() - t0:.0f} s); aussortiert: "
          + (", ".join(f"{k} {v}" for k, v in aussortiert.most_common()) or "nichts"), flush=True)
    if a.nur_zaehlen:
        print(json.dumps({"gruppen": len(gruppen), "aussortiert": dict(aussortiert),
                          "gruppen_mit_vorschlag": sum(W.braucht_vorschlag(g, a.ohne_stufe)
                                                       for g in gruppen.values())}, ensure_ascii=False))
        return
    show = lambda st: print(time.strftime("%H:%M:%S"), W.status_zeile(st), flush=True)  # noqa: E731
    cfg = _cfg(a)
    if a.max_kosten is not None:
        _wlo_pruefabschnitt(a, W, gruppen, cfg, show)
    lauf = W.WloThemenLauf(gruppen, cfg, a.out, limit=a.limit, ohne_stufe=a.ohne_stufe, zufall=a.zufall,
                           vergleich=a.vergleich_lokal, pool=a.pool, sofort=a.sofort)
    st = lauf.warten(a.intervall, on_status=show) if a.warten else lauf.weiter()
    if not a.warten:
        show(st)
    if st["abgeschlossen"]:
        kz = W.kennzahlen(lauf.out_path, lauf.stand)
        lauf.out_path.with_suffix(".kennzahlen.json").write_text(json.dumps(kz, ensure_ascii=False, indent=1),
                                                                 encoding="utf-8")
        print(json.dumps(kz, ensure_ascii=False, indent=1))
    print(f"→ {lauf.out_path}" + ("" if st["abgeschlossen"] else "  (später erneut aufrufen, um fortzusetzen)"))


def _wlo_pruefabschnitt(a, W, gruppen, cfg, show):
    """Prüfabschnitt vor einem großen Lauf: a.pruefabschnitt Gruppen (geschichtet) sofort über Flex abfragen, dann
    Cache-Quote und Hochrechnung melden; weiter nur, wenn die Hochrechnung unter a.max_kosten liegt. Die Ergebnisse
    stehen in derselben Ausgabe und zählen für den Lauf."""
    from dataclasses import replace

    lauf = W.WloThemenLauf(gruppen, replace(cfg, service_tier="flex"), a.out, limit=a.limit,
                           ohne_stufe=a.ohne_stufe, zufall=a.zufall, vergleich=a.vergleich_lokal, pool=a.pool,
                           sofort=True)
    alle = lauf.ids
    lauf.ids = W.auswaehlen({gid: gruppen[gid] for gid in alle}, a.ohne_stufe, a.pruefabschnitt, a.zufall + 1,
                            a.pool)
    print(f"Prüfabschnitt: {len(lauf.ids)} von {len(alle)} Gruppen, sofort über Flex", flush=True)
    st = lauf.warten(min(a.intervall, 20), on_status=show)
    hr = W.hochrechnung(W.lade_jsonl(lauf.out_path), gruppen, alle, a.ohne_stufe, cfg.model)
    hr.update(max_kosten_usd=a.max_kosten, pruefabschnitt=len(lauf.ids), kosten_pruefabschnitt_usd=st["kosten_usd"])
    lauf.out_path.with_suffix(".pruefung.json").write_text(json.dumps(hr, ensure_ascii=False, indent=1),
                                                           encoding="utf-8")
    print(json.dumps(hr, ensure_ascii=False, indent=1), flush=True)
    if hr["kosten_usd"] is None or hr["kosten_usd"] > a.max_kosten:
        raise SystemExit(f"Hochrechnung {hr['kosten_usd']} $ über der Grenze {a.max_kosten} $ (oder nicht "
                         "berechenbar): Lauf angehalten. Die Ergebnisse des Prüfabschnitts bleiben in "
                         f"{lauf.out_path}.")
    print(f"Hochrechnung {hr['kosten_usd']} $ ≤ {a.max_kosten} $: weiter mit dem ganzen Lauf", flush=True)


if __name__ == "__main__":
    main()
