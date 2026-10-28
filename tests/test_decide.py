"""Decisor da pipeline: um teste por ramo do fluxo combinado com o usuário."""

from typing import Any

from c3parr.library.store import AppConfig
from c3parr.pipeline.decide import (
    Decision,
    EmbeddedTrack,
    ItemState,
    MainSub,
    OurOutput,
    decide,
    sync_key,
)

V = "/m/movies/Filme (2010)/Filme.mkv"
META_OK = {"metadados": "ok"}


def cfg(**kw: Any) -> AppConfig:
    return AppConfig.model_validate({"target_languages": ["pt-BR"], **kw})


def state(**kw: Any) -> ItemState:
    return ItemState.model_validate({"video": V, "steps": META_OK, **kw})


def kinds(d: Decision) -> list[tuple[str, str | None]]:
    return [(a.kind, a.lang) for a in d.actions]


def test_metadata_first_and_failure_blocks_until_retry() -> None:
    assert kinds(decide(state(steps={}), cfg())) == [("metadados", None)]
    failed = state(steps={"metadados": "falhou"})
    d = decide(failed, cfg())
    assert d.actions == () and d.blocked == ("metadados",)
    assert kinds(decide(failed, cfg(), retry_failed=True)) == [("metadados", None)]


def test_metadata_not_found_goes_on() -> None:
    d = decide(state(steps={"metadados": "nao_encontrado"}), cfg())
    assert kinds(d) == [("asr", None)]


def test_embedded_in_every_target_ends_the_pipeline() -> None:
    d = decide(state(embedded=[{"index": 2, "lang": "pt-BR"}]), cfg())
    assert d.actions == () and d.resolved == {"pt-BR": "embedded"}


def test_forced_does_not_resolve_nor_become_main() -> None:
    emb = [
        {"index": 2, "lang": "pt-BR", "forced": True},
        {"index": 3, "lang": "fr", "forced": True},
    ]
    d = decide(state(original_language="fr", embedded=emb), cfg())
    assert "pt-BR" not in d.resolved
    assert kinds(d) == [("asr", None)]
    assert decide(state(original_language="fr", embedded=emb), cfg(forced_resolves=True)).resolved


def test_criminal_minds_embedded_english_is_extracted_then_translated() -> None:
    emb = EmbeddedTrack(index=2, lang="en")
    first = decide(state(original_language="en", embedded=[emb]), cfg())
    assert [(a.kind, a.lang, a.arg) for a in first.actions] == [("extrair", "en", "2")]
    done = emb.model_copy(update={"extracted_path": "/m/x.embedded.en.c3parr"})
    second = decide(state(original_language="en", embedded=[done]), cfg())
    assert kinds(second) == [("traduzir", "pt-BR")]
    assert second.main == MainSub(path="/m/x.embedded.en.c3parr", lang="en", source="embedded",
                                  version=1)  # fmt: skip


def test_image_subtitles_are_ignored_with_a_note() -> None:
    d = decide(state(original_language="fr", embedded=[{"index": 2, "lang": "fr",
                                                         "text_based": False}]), cfg())  # fmt: skip
    assert kinds(d) == [("asr", None)]
    assert "OCR" in d.notes[0]


def test_incendies_asr_then_generate_then_translate() -> None:
    assert kinds(decide(state(original_language="fr"), cfg())) == [("asr", None)]
    after_asr = state(original_language="fr", asr_path="/m/x.asr.c3parr", asr_language="fr")
    assert kinds(decide(after_asr, cfg())) == [("gerar", "fr")]
    generated = after_asr.model_copy(update={"generated": {"fr": "/m/x.main.fr.c3parr"}})
    d = decide(generated, cfg())
    assert kinds(d) == [("traduzir", "pt-BR")]
    assert d.main is not None and d.main.source == "asr"


def test_fallback_language_becomes_main_when_original_has_none() -> None:
    emb = [{"index": 2, "lang": "en", "extracted_path": "/m/x.embedded.en.c3parr"}]
    d = decide(state(original_language="fr", embedded=emb), cfg())
    assert d.main is not None and d.main.lang == "en"
    assert kinds(d) == [("traduzir", "pt-BR")]


def test_main_in_a_target_language_is_published() -> None:
    s = state(original_language="fr", asr_path="/a", generated={"fr": "/m/x.main.fr.c3parr"})
    d = decide(s, cfg(target_languages=["fr", "pt-BR"]))
    assert kinds(d) == [("publicar", "fr"), ("traduzir", "pt-BR")]


def test_human_subtitle_appears_ours_is_removed_and_it_is_synced() -> None:
    main = MainSub(path="/m/x.embedded.en.c3parr", lang="en", source="embedded", version=1)
    s = state(original_language="en",
              embedded=[{"index": 2, "lang": "en", "extracted_path": main.path}], main=main,
              externals=[{"path": "/m/x.pt-BR.srt", "lang": "pt-BR"}],
              outputs={"pt-BR": {"path": "/m/x.pt-BR.c3parr.srt", "main_version": 1}})  # fmt: skip
    d = decide(s, cfg())
    assert kinds(d) == [("sincronizar", "pt-BR"), ("remover", "pt-BR")]
    assert d.resolved == {"pt-BR": "external"}
    keep = decide(s, cfg(delete_ours_on_human=False, sync_new_external=False))
    assert keep.actions == ()


def test_better_main_appears_and_derived_translations_follow_the_option() -> None:
    old = MainSub(path="/m/x.main.fr.c3parr", lang="fr", source="asr", version=1)
    s = state(original_language="fr", asr_path="/a", main=old,
              generated={"fr": old.path}, externals=[{"path": "/m/x.fr.srt", "lang": "fr"}],
              steps={**META_OK, sync_key("/m/x.fr.srt"): "ok"},
              outputs={"pt-BR": OurOutput(path="/m/x.pt-BR.c3parr.srt", main_version=1)})  # fmt: skip
    d = decide(s, cfg())
    assert d.main is not None and d.main.source == "external" and d.main.version == 2
    assert kinds(d) == [("traduzir", "pt-BR")]
    assert decide(s, cfg(derived_on_main_change="mark")).resolved == {"pt-BR": "outdated"}
    assert decide(s, cfg(derived_on_main_change="keep")).actions == ()
    stay = decide(s, cfg(new_external_becomes_main=False))
    assert stay.main == old and stay.actions == ()


def test_edited_output_is_left_alone() -> None:
    main = MainSub(path="/m/x.embedded.en.c3parr", lang="en", source="embedded", version=2)
    s = state(original_language="en", main=main,
              embedded=[{"index": 2, "lang": "en", "extracted_path": main.path}],
              outputs={"pt-BR": {"path": "/o", "main_version": 1, "modified": True}})  # fmt: skip
    d = decide(s, cfg())
    assert d.actions == () and d.resolved == {"pt-BR": "edited"}


def test_failed_translation_waits_for_the_routine() -> None:
    emb = [{"index": 2, "lang": "en", "extracted_path": "/m/x.embedded.en.c3parr"}]
    s = state(original_language="en", embedded=emb,
              steps={**META_OK, "traduzir:pt-BR": "falhou"})  # fmt: skip
    d = decide(s, cfg(target_languages=["pt-BR", "es"]))
    assert kinds(d) == [("traduzir", "es")] and d.blocked == ("traduzir:pt-BR",)
    assert len(decide(s, cfg(target_languages=["pt-BR", "es"]), retry_failed=True).actions) == 2


def test_asr_disabled_and_nothing_to_use_stops_quietly() -> None:
    d = decide(state(original_language="fr"), cfg(asr_enabled=False))
    assert d.actions == () and "ASR is disabled" in d.notes[-1]


def test_fallback_matches_the_base_language_and_extracts_with_the_track_language() -> None:
    emb = [{"index": 3, "lang": "pt-BR"}]
    d = decide(state(original_language="fr", embedded=emb), cfg(target_languages=["es"],
                                                                  fallback_languages=["pt"]))  # fmt: skip
    assert [(a.kind, a.lang) for a in d.actions] == [("extrair", "pt-BR")]
    done = [{"index": 3, "lang": "pt-BR", "extracted_path": "/m/x.embedded.pt-BR.c3parr"}]
    d2 = decide(state(original_language="fr", embedded=done),
                cfg(target_languages=["es"], fallback_languages=["pt"]))  # fmt: skip
    assert d2.main is not None and d2.main.lang == "pt-BR"
    assert kinds(d2) == [("traduzir", "es")]
