import random
from collections import Counter

from sem.bereiche.base import allocate
from sem.prompts import render


def test_allocate_gleichverteilt_und_deckt_alles_ab():
    groups = {f"g{i}": [{"id": f"g{i}-t{j}"} for j in range(i + 1)] for i in range(10)}
    out = allocate(1000, groups, random.Random(1))
    per_group = Counter(g for g, _ in out)
    assert len(out) == 1000 and set(per_group.values()) == {100}
    # innerhalb einer Gruppe: erst jedes Thema einmal, bevor eins doppelt kommt
    for g, topics in groups.items():
        c = Counter(t["id"] for gg, t in out if gg == g)
        assert max(c.values()) - min(c.values()) <= 1 and len(c) == len(topics)


def test_allocate_weniger_seeds_als_gruppen():
    groups = {f"g{i}": [{"id": i}] for i in range(50)}
    out = allocate(7, groups, random.Random(3))
    assert len(out) == 7 and len({g for g, _ in out}) == 7


def test_render_platzhalter_und_abschnitte():
    t = "A {{x}}{{#y}} Y={{y}}{{/y}}{{#z}} Z{{/z}}"
    assert render(t, {"x": 1, "y": "ja", "z": ""}) == "A 1 Y=ja"


def test_ausgabe_pfad_festes_praefix(tmp_path):
    from sem.paths import ausgabe_pfad, ausgabedateien

    assert ausgabe_pfad("seeds", "test", tmp_path).name == "seeds_test.jsonl"
    assert ausgabe_pfad("materialien", "seeds_s42.jsonl", tmp_path).name == "materialien_s42.jsonl"
    for n in ("seeds_a.jsonl", "materialien_a.jsonl", "materialien_a.fehler.jsonl", "alt.jsonl"):
        (tmp_path / n).write_text("{}\n")
    assert [p.name for p in ausgabedateien("materialien", tmp_path)] == ["materialien_a.jsonl"]
