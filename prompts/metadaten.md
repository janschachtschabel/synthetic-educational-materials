Erzeuge alle folgenden Metadatenfelder für dieses Material in einer Antwort, in der angegebenen Reihenfolge. Die Felder entstehen nacheinander: Jedes spätere Feld kennt die früheren, passt zu ihnen und wiederholt sie nicht wörtlich.
Die Abschnitte unten sind die Aufträge für die einzelnen Felder. Was dort unter „Ausgabe“ steht, beschreibt nur den Wert des jeweiligen Schlüssels, nicht die ganze Antwort.
Jedes Feld bekommt dieselbe Sorgfalt wie in einer eigenen Antwort: Halte Längen und Anzahlen jedes Abschnitts voll ein und kürze spätere Felder nicht ab (die Beschreibung hat 450 bis 900 Zeichen, die Suchanfragen mischen alle genannten Arten).

## titel
{{auftrag_titel}}

## beschreibung
{{auftrag_beschreibung}}

## keywords
{{auftrag_keywords}}

## lernziele
{{auftrag_lernziele}}

## suchanfragen
{{auftrag_suchanfragen}}

## paare
{{auftrag_fragen_antworten}}

## entitaeten
{{auftrag_entitaeten}}

## Ausgabe
Ein einziges JSON-Objekt ohne Codeblock, mit genau diesen Schlüsseln in dieser Reihenfolge: "titel" (Text), "beschreibung" (Text), "keywords" (Liste von Texten), "lernziele" (Liste wie im Abschnitt lernziele), "suchanfragen" (Liste von Texten), "paare" (Liste wie im Abschnitt paare), "entitaeten" (Liste wie im Abschnitt entitaeten, auch leer).
{"titel": "...", "beschreibung": "...", "keywords": ["...", "..."], "lernziele": [{"lernziel": "...", "operator": "...", "stufe": "..."}], "suchanfragen": ["...", "..."], "paare": [{"frage": "...", "antwort": "...", "typ": "..."}], "entitaeten": [{"name": "...", "typ": "...", "art": "...", "im_text": "...", "fiktiv": false}]}
