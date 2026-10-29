"""Lädt die OpenEduHub-Vokabulare (SKOS/Turtle) und schreibt sie aufbereitet nach data/vocabs/.

    python scripts/fetch_vocabs.py
"""

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sem.paths import VOCAB_DIR  # noqa: E402
from sem.vocabs import OEH_VOCABS, OEH_VOCABS_RAW, skos_to_json  # noqa: E402


def main():
    VOCAB_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name in OEH_VOCABS:
            url = OEH_VOCABS_RAW.format(name=name)
            ttl = Path(tmp) / f"{name}.ttl"
            urllib.request.urlretrieve(url, ttl)
            data = skos_to_json(ttl, name, url)
            (VOCAB_DIR / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{name}: {len(data['concepts'])} Konzepte")


if __name__ == "__main__":
    main()
