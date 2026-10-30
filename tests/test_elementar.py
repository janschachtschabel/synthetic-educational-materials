import pytest

from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.bereiche.elementar import F_BILDUNGSBEREICH, bildungsbereiche
from sem.prompts import load_prompts, render
from sem.vocabs import load as load_vocab


def _bereich():
    b = get("elementar")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    return b


def test_sieben_bildungsbereiche_mit_gueltigem_closematch():
    bb = bildungsbereiche()["bildungsbereiche"]
    assert len(bb) == 7
    fach = load_vocab("discipline")
    assert all(u in fach.by_uri for b in bb for u in b["closeMatch"])


def test_seeds_haben_bildungsbereich_alter_und_herkunft():
    b = _bereich()
    seeds = build_seeds(b, 300, "bildungsbereich", seed=5)
    assert len(seeds) == 300
    ids = {x["id"] for x in bildungsbereiche()["bildungsbereiche"]}
    for s in seeds:
        assert s[S.F_CONTEXT] == ["http://w3id.org/openeduhub/vocabs/educationalContext/elementarbereich"]
        assert s[F_BILDUNGSBEREICH] and all(u.rsplit("#", 1)[-1] in ids for u in s[F_BILDUNGSBEREICH])
        assert s[S.F_TAXON]
        for f in (S.F_CONTEXT, S.F_TAXON, F_BILDUNGSBEREICH, S.F_LRT, S.F_ROLE, S.F_THEMA, S.F_NIVEAU):
            assert f in s[S.F_PROVENANCE]
        if s[S.F_THEMA]["altersgruppe"] == "u3":
            assert s[S.F_NIVEAU]["id"] == "krippe"
        if s[S.F_THEMA]["art"] == "fachthema":
            assert s[S.F_ROLE][0].rsplit("/", 1)[-1] in ("teacher", "parent")
    assert build_seeds(b, 300, "bildungsbereich", seed=5) == seeds


def test_prompt_elementar():
    b = _bereich()
    s = build_seeds(b, 1, "thema", seed=2)[0]
    text = render(load_prompts("elementar")["volltext"], b.prompt_values(s))
    assert s[S.F_THEMA]["label_de"] in text and "{{" not in text
