Nenne die benannten Entitäten, die im Volltext vorkommen: Eigennamen und feste Bezeichnungen, die ein bestimmtes Einzelnes meinen (eine Person, einen Ort, ein Werk, eine Epoche, eine benannte Theorie …).
- Nur was wörtlich im Volltext steht. Allgemeine Fachbegriffe, Gattungsbegriffe und Konzepte („Photosynthese“, „Demokratie“, „Ballade“, „Gruppenarbeit“) sind keine Entitäten; sie gehören in die Schlagworte. Theorien und Methoden nur, wenn sie einen Eigennamen tragen oder fest benannt sind („Satz des Pythagoras“, „Ohmsches Gesetz“, „Gruppenpuzzle“, „Montessori-Pädagogik“).
- "im_text": die Stelle genau so, wie sie im Volltext steht, Zeichen für Zeichen (gleiche Schreibweise, Beugung und Abkürzung, z. B. „Goethes“, „BGB“). Nimm die erste Stelle, an der die Entität vorkommt.
- "name": die vollständige, gebräuchliche Form im Nominativ („Johann Wolfgang von Goethe“, „Bürgerliches Gesetzbuch“). Ist sie nicht sicher bestimmbar, wiederhole "im_text".
- "typ": genau einer dieser Werte:
  "Person" (reale Menschen, auch für das Material erfundene Personen in Beispielen und Aufgaben),
  "Figur" (Figuren aus Literatur, Film, Mythologie, Religion und Märchen, z. B. Faust, Zeus, Rotkäppchen),
  "Gruppe" (Völker, Dynastien, Religionsgemeinschaften, Bewegungen, z. B. Römer, Habsburger, Reformierte),
  "Organisation" (Institutionen, Behörden, Unternehmen, Parteien, Vereine, Schulen, Hochschulen),
  "Ort" (Länder, Städte, Regionen, Gewässer, Gebirge, Himmelskörper),
  "Bauwerk" (Gebäude, Denkmäler, Brücken, Anlagen, z. B. Kölner Dom, Chinesische Mauer),
  "Ereignis" (Kriege, Revolutionen, Konferenzen, Katastrophen, Feste, z. B. Westfälischer Friede),
  "Epoche" (Zeitalter, Kunst- und Literaturepochen, Strömungen, z. B. Barock, Aufklärung, Bronzezeit),
  "Werk" (Bücher, Gedichte, Musikstücke, Bilder, Filme, Schriften),
  "Regelwerk" (Gesetze, Verordnungen, Verträge, Normen, Lehrpläne, Bildungsstandards),
  "Theorie" (benannte Theorien, Modelle, Sätze, Naturgesetze, Effekte, z. B. Bohrsches Atommodell, Maslowsche Bedürfnispyramide),
  "Methode" (benannte Unterrichts-, Lern-, Fach- und Forschungsmethoden, pädagogische Konzepte, z. B. Think-Pair-Share, PCR),
  "Abschluss" (Bildungsabschlüsse, Prüfungen, Zertifikate, Niveaustufen, z. B. Abitur, Bachelor, GER-Niveau B1),
  "Produkt" (Software, Programmiersprachen, Geräte, Medien, Marken, z. B. GeoGebra, Python, Scratch),
  "Sonstiges" (andere Eigennamen, etwa Schiffe, Sprachen, Preise).
- "fiktiv": true für Personen und Figuren, die nur für dieses Material erfunden sind (etwa Namen in Beispielen, Dialogen oder Aufgaben); sonst false, auch für Figuren aus bekannten Werken.
- Jede Entität nur einmal, auch wenn sie mehrmals oder in verschiedenen Formen vorkommt. Höchstens 15, die wichtigsten zuerst.
- Kommen keine Entitäten vor, gib eine leere Liste zurück.
Ausgabe: Ein JSON-Objekt in genau dieser Form, ohne Codeblock: {"entitaeten": [{"name": "...", "typ": "...", "im_text": "...", "fiktiv": false}]}
{{#ausgabesprache}}- "name" und "im_text" in der Sprache des Volltexts; "typ" bleibt deutsch (Beispiele zeigen nur das Format).{{/ausgabesprache}}
