# Provenienz: Themen aus WirLernenOnline-Datensätzen (Herkunft `wlo_abstraktion`)

*English summary: Topics of origin `wlo_abstraktion` are short subject-matter topics that an LLM derived from the
title and description of records on WirLernenOnline.de. Only the aggregated topics, key terms, subject, educational
level and node IDs are kept; titles and descriptions are never stored in outputs or seeds. Each topic links back to
the records it was derived from.*

## Quelle

- **WirLernenOnline** (https://wirlernenonline.de), Redaktionsumgebung edu-sharing (OpenEduHub). Grundlage ist ein
  Export der Datensätze (`wlo_max_dataset_slim.csv`); er gehört nicht ins Repo.
- Gelesen werden nur: Node-ID (`sys:node-uuid`), Titel (`cclom:title`, bis 200 Zeichen), Beschreibung
  (`cclom:general_description`, bis 800 Zeichen), Fach (`ccm:taxonid`, `ccm:oeh_taxonid_university`) und
  Bildungsstufe (`ccm:educationalcontext`). Alle übrigen Felder (URLs, Autor:innen, Lizenzen, Keywords …) nicht.
- Fach und Stufe werden als URIs der OEH-Vokabulare (discipline, Hochschulfächersystematik, educationalContext, CC0)
  übernommen; der WLO-Platzhalter „null“ zählt als leer.

## Verarbeitung

1. Dubletten (gleicher normalisierter Titel und gleiche normalisierte Beschreibung) werden zu einer Gruppe
   zusammengefasst; alle Node-IDs der Gruppe bleiben erhalten.
2. Ein LLM (OpenAI-Batch, Prompt `prompts/wlo_themen.md`) leitet je Gruppe das fachliche **Thema** ab (2–8 Wörter,
   Nominalphrase wie ein Lehrplan-Schlagwort), ein allgemeineres Thema und bis zu fünf Fachbegriffe. Fachlich
   bedeutsame Eigennamen (Theorien, Werke, historische Personen, Orte, Ereignisse, Gesetze) bleiben; Namen von
   Produkten, Marken, Software, Plattformen, Verlagen, Reihen, Kanälen, Sendungen sowie erfundene Figuren und nicht
   selbst behandelte Autor:innen werden weggelassen. Gespeichert wird nur der **Typ** des Weggelassenen.
3. **Kopierprüfung:** Enthält das Thema eine Folge von vier Wörtern aus Titel oder Beschreibung (Fachbegriffe
   ausgenommen) oder ähnelt es dem Titel zu stark (Zeichenähnlichkeit > 0,8), wird einmal neu gefragt. Bleibt es
   eine Kopie, wird es markiert (`seed_tauglich: false`) und nicht als Seed verwendet.
4. **Zusammenführen:** Gleiches normalisiertes Thema im selben Bereich und Fach ergibt ein Thema mit allen Node-IDs;
   deren Zahl ist das Gewicht. Fachbegriffe werden gegen GND-Sachschlagwörter abgeglichen (Labels aus den Mappings
   dieses Repos, optional lobid). Nahe Dubletten und Überschneidungen mit vorhandenen Themen (Themenbaum,
   WLO-Keywords, OpenAlex, GND, RVK, Fachprofile) werden über lokale Embeddings verknüpft (`verknuepft_mit`),
   nichts wird gelöscht.

## Was gespeichert und weitergegeben wird

| Inhalt | Abstraktion (`data/lokal/wlo_abstraktion/`) | Seeds / Export |
|---|---|---|
| Thema, allgemeineres Thema, Fachbegriffe | ja | Thema als Label (`topic`), Fachbegriffe als Keywords im Seed |
| Fach, Bildungsstufe (Vokabular-URIs) | ja | ja |
| Node-IDs | ja, alle | im Seed bis zu 10 als Link auf WirLernenOnline/edu-sharing, dazu die Zahl der Datensätze |
| Typen weggelassener Namen | ja | nein |
| Titel, Beschreibung | **nein** (nur auf Wunsch in `*.vergleich_lokal.jsonl` zur Prüfung, nie weitergeben) | **nein** |

Quelle je Seed: `synth:provenance.synth:thema` nennt WirLernenOnline mit Link, die Zahl der Datensätze, bis zu zehn
Node-Links (`https://redaktion.openeduhub.net/edu-sharing/components/render/<node-id>`) und die Verknüpfungen.
Im Hugging-Face-Export erscheint nur das Thema als Label, ohne URI und ohne Node-IDs.

## Status

Die Abstraktion und die daraus gebauten Themen liegen nur lokal (`data/lokal/`, nicht im Repo), bis entschieden ist,
ob und wie sie veröffentlicht werden. Bei den Seeds ist die Herkunft standardmäßig ausgeschaltet (Filter `herkunft`
bzw. `quellen`, Anteil über `wlo_anteil`). Datensätze ohne Bildungsstufe oder Fach werden mitverarbeitet: Fach
und Stufe schlägt dann das Modell aus dem OEH-Vokabular vor (markiert als `vorschlag: true`, im Seed-Filter
`wlo_vorschlag` getrennt abschaltbar). Berufs-, Erwachsenen- und Elementarbildung: Die Abstraktion läuft mit,
Themen für diese Bereiche werden noch nicht gebaut.
