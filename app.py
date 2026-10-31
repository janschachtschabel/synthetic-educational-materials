"""Oberfläche im Browser: python app.py, dann http://127.0.0.1:8501 öffnen (Menüsprache Deutsch oder Englisch).

Nur auf diesem Rechner erreichbar: Die Oberfläche hat keine Anmeldung und kann LLM-Läufe mit dem API-Key aus der
Umgebung starten. Port mit --port oder SEM_PORT; die Adresse bleibt bewusst 127.0.0.1.
"""

import argparse
import os
import webbrowser

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("SEM_PORT", 8501)))
    ap.add_argument("--ohne-browser", action="store_true", help="Browser nicht automatisch öffnen")
    a = ap.parse_args()
    adresse = f"http://127.0.0.1:{a.port}"
    print(f"Oberfläche: {adresse}  (beenden mit Strg+C; laufende Läufe arbeiten weiter)")
    if not a.ohne_browser:
        webbrowser.open(adresse)
    uvicorn.run("sem.web.app:app", host="127.0.0.1", port=a.port, log_level="warning")
