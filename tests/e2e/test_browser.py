"""O que a interface faz no navegador sem o servidor saber: contagem de alterações, aviso ao
sair, campos que aparecem e somem, chips, modo de seleção, detalhe da fila.

Cada teste diz o que a pessoa faz (`ui`, de `pages.py`) e confere o resultado na tela e no
banco. É a lista do que a interface nova tem de continuar fazendo.
"""

from conftest import Served
from pages import LegacyUi
from playwright.sync_api import expect

WENDIGO = "Supernatural - S01E02 - Wendigo.mkv"
PILOT = "Supernatural - S01E01 - Pilot.mkv"


def test_save_counts_the_changes_and_only_enables_when_there_are_some(
    ui: LegacyUi, served: Served
) -> None:
    lib = served.world.library
    ui.open("/settings/subtitles")
    ui.expect_save_label("No changes")
    assert not ui.can_save()

    ui.fill("wait_minutes", "30")
    ui.expect_save_label("Save 1 change")
    ui.switch("forced_resolves")
    ui.expect_save_label("Save 2 changes")
    ui.fill("wait_minutes", "60")  # voltou ao que estava: não conta mais
    ui.expect_save_label("Save 1 change")
    assert ui.can_save()

    assert ui.save() == "Saved."
    ui.expect_save_label("No changes")
    assert lib.config().forced_resolves and lib.config().wait_minutes == 60


def test_discard_goes_back_to_what_is_saved_without_asking(ui: LegacyUi, served: Served) -> None:
    ui.open("/settings/subtitles")
    ui.fill("wait_minutes", "5")
    ui.discard()
    assert ui.value("wait_minutes") == "60"
    assert not ui.leave_dialog().is_visible()
    assert served.world.library.config().wait_minutes == 60


def test_leaving_with_changes_asks_stay_discard_or_save(ui: LegacyUi, served: Served) -> None:
    lib = served.world.library
    ui.open("/settings/subtitles")
    ui.fill("wait_minutes", "30")

    ui.go("Queue")
    expect(ui.leave_dialog()).to_be_visible()
    expect(ui.leave_dialog()).to_contain_text("1 change")
    ui.answer_leave_dialog("stay")
    expect(ui.leave_dialog()).not_to_be_visible()
    assert ui.path() == "/settings/subtitles" and ui.value("wait_minutes") == "30"

    ui.go("Queue")
    ui.answer_leave_dialog("discard")
    ui.wait_for_path("/queue")
    assert lib.config().wait_minutes == 60

    ui.open("/settings/subtitles")
    ui.fill("wait_minutes", "45")
    ui.go("Movies")
    ui.answer_leave_dialog("save")
    ui.wait_for_path("/movies")
    assert lib.config().wait_minutes == 45


def test_leaving_without_changes_does_not_ask(ui: LegacyUi) -> None:
    ui.open("/settings/subtitles")
    ui.go("Queue")
    ui.wait_for_path("/queue")


def test_llm_fields_follow_the_provider(ui: LegacyUi) -> None:
    ui.open("/settings/integrations")
    key = "[data-provider='{}']"
    assert ui.is_shown("#llm-url-field") and not ui.is_shown("#llm-effort-field")
    assert ui.is_shown(key.format("custom")) and not ui.is_shown(key.format("openai"))

    ui.choose("llm_provider", "openai")
    assert not ui.is_shown("#llm-url-field")
    assert ui.is_shown(key.format("openai")) and not ui.is_shown(key.format("custom"))
    assert ui.value("llm_model") == "modelo-x"  # modelo escolhido à mão fica

    ui.fill("llm_model", "")
    ui.choose("llm_provider", "anthropic")  # campo vazio: entra o padrão do provedor
    assert ui.value("llm_model") == "claude-haiku-5-5" and ui.is_shown("#llm-effort-field")
    ui.choose("llm_provider", "gemini")  # era o padrão do anterior: troca pelo do novo
    assert ui.value("llm_model") == "gemini-flash-latest" and ui.is_shown("#llm-effort-field")
    ui.choose("llm_provider", "deepseek")
    assert ui.value("llm_model") == "deepseek-chat" and not ui.is_shown("#llm-effort-field")


def test_transcription_fields_follow_where_it_runs(ui: LegacyUi) -> None:
    ui.open("/settings/integrations")
    help_for = ".asr-help[data-mode='{}']"
    assert not ui.is_shown(".asr-remote") and ui.is_shown(help_for.format("local"))

    ui.choose("asr_mode", "openai")
    assert ui.is_shown("[name=asr_url]") and ui.is_shown("[name=asr_model]")
    assert ui.is_shown(help_for.format("openai")) and not ui.is_shown(help_for.format("local"))

    ui.choose("asr_mode", "asr-webservice")
    assert ui.is_shown("[name=asr_url]") and not ui.is_shown("[name=asr_model]")
    assert ui.is_shown(help_for.format("asr-webservice"))


def test_target_languages_are_chips_and_a_picked_one_leaves_the_list(
    ui: LegacyUi, served: Served
) -> None:
    ui.open("/settings/languages")
    name = "target_languages"
    assert ui.language_chips(name) == ["pt-BR", "es"]
    assert not ui.language_can_be_picked(name, "es") and ui.language_can_be_picked(name, "fr")

    ui.add_language(name, "fr")
    assert ui.language_chips(name) == ["pt-BR", "es", "fr"]
    assert not ui.language_can_be_picked(name, "fr")
    ui.remove_language(name, "es")
    assert ui.language_chips(name) == ["pt-BR", "fr"] and ui.language_can_be_picked(name, "es")
    ui.expect_save_label("Save 1 change")  # a lista de alvos é um campo só

    assert ui.save() == "Saved."
    assert served.world.library.config().target_languages == ("pt-BR", "fr")


def test_path_mappings_are_rows_to_add_and_remove(ui: LegacyUi, served: Served) -> None:
    ui.open("/settings/library/radarr")
    assert ui.mapping_rows() == 1
    ui.add_mapping("/remote/new", "/local/new")
    assert ui.mapping_rows() == 2
    ui.remove_mapping(0)
    assert ui.mapping_rows() == 1
    assert ui.save() == "Saved."
    assert served.world.library.config().radarr.path_mappings == (("/remote/new", "/local/new"),)


def test_selection_mode_shows_the_boxes_and_sends_every_ticked_item(
    ui: LegacyUi, served: Served
) -> None:
    catalog = served.world.catalog
    ui.open("/movies")
    assert not ui.selection_boxes_shown()
    ui.start_selecting()
    assert ui.selection_boxes_shown()
    ui.tick("Incendies")
    ui.tick("Parasite")
    before = len(catalog.alerts(include_resolved=False))
    # os arquivos do exemplo não são vídeos de fato: cada item marcado vira um alerta de leitura
    assert "2 item(s) could not be read" in ui.add_selected()
    assert len(catalog.alerts(include_resolved=False)) == before + 2


def test_the_bookmark_toggles_monitoring(ui: LegacyUi, served: Served) -> None:
    world = served.world
    ui.open("/movies")
    assert ui.monitor_label("Roma") == "Not monitored"
    ui.toggle_monitor("Roma")
    assert ui.monitor_label("Roma") == "Monitored"
    assert world.library.movie(world.ids["Roma"]).monitored


def test_queue_opens_details_and_filters_without_reloading(ui: LegacyUi) -> None:
    ui.open("/queue")
    ui.expect_queue_rows(6)
    assert "LLM fora do ar" in ui.open_task(WENDIGO)

    ui.filter_queue("Failed")
    ui.wait_for_path("status=failed")
    ui.expect_queue_rows(1)
    assert ui.queue_rows() == [WENDIGO]

    ui.filter_queue("All")
    ui.search_queue("pilot")
    ui.wait_for_path("q=pilot")
    ui.expect_queue_rows(1)
    assert ui.queue_rows() == [PILOT]
