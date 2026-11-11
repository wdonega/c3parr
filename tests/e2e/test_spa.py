"""A interface nova no navegador de verdade, com a biblioteca de exemplo.

É a mesma lista de comportamentos que `test_browser.py` fixou na interface antiga (contagem de
alterações, aviso ao sair, campos que seguem uma escolha, chips, seleção, detalhe da fila), mais
o que a nova corrige: subpáginas alcançáveis no celular, botão só onde falta legenda, título por
página. Precisa do build do frontend: `npm run build` em `frontend/`.
"""

import dataclasses
import re
from collections.abc import Iterator

import pytest
from conftest import Served, serve
from playwright.sync_api import Browser, Page, expect
from uikit import seed_world

from c3parr.web.app import DIST

WENDIGO = "Supernatural S01E02"  # o Sonarr do exemplo não deu título a este episódio

pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").exists(), reason="build the frontend first: npm run build"
)


@pytest.fixture
def spa(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    world = seed_world(tmp_path_factory.mktemp("spa"), ui="spa")
    service = world.app.state.service  # sem internet: nenhum pôster vem, fica o bloco de cor
    service.clients = dataclasses.replace(service.clients, fetch_poster=lambda kind, url: None)
    yield from serve(world)


def _open(browser: Browser, served: Served, path: str, width: int = 1280) -> Page:
    context = browser.new_context(viewport={"width": width, "height": 844})
    page = context.new_page()
    page.set_default_timeout(8000)
    page.goto(served.url + path)
    page.get_by_role("heading", level=1).first.wait_for()
    return page


@pytest.fixture
def desktop(browser: Browser, spa: Served) -> Iterator[Page]:
    page = _open(browser, spa, "/dashboard")
    yield page
    page.context.close()


@pytest.fixture
def phone(browser: Browser, spa: Served) -> Iterator[Page]:
    page = _open(browser, spa, "/dashboard", width=390)
    yield page
    page.context.close()


def go(page: Page, label: str) -> None:
    """Clica num item do menu lateral."""
    page.get_by_role("navigation", name="Main").first.get_by_role("link", name=label).click()


def save_bar(page: Page):  # type: ignore[no-untyped-def]
    return page.get_by_role("region", name="Unsaved changes")


def said(page: Page, text: str) -> None:
    """Espera o aviso (toast) com este texto."""
    expect(page.locator(".toast", has_text=text).first).to_be_visible()


# --- casca ---


def test_every_page_has_its_own_title_and_the_menu_shows_what_runs(desktop: Page) -> None:
    expect(desktop).to_have_title("Overview · c3parr")
    go(desktop, "Movies")
    expect(desktop).to_have_title("Movies · c3parr")
    go(desktop, "Queue")
    expect(desktop).to_have_title("Queue · c3parr")
    running = desktop.get_by_role("link", name=re.compile("Running now"))
    expect(running).to_contain_text("Oldboy")
    expect(running).to_contain_text("47%")
    expect(desktop.get_by_label("2 unfinished tasks")).to_be_visible()


def test_the_top_search_finds_a_show_and_opens_it(desktop: Page) -> None:
    desktop.keyboard.press("Control+k")
    box = desktop.get_by_role("dialog").get_by_role("searchbox")
    box.fill("super")
    desktop.get_by_role("dialog").get_by_role("button", name=re.compile("Supernatural")).click()
    expect(desktop).to_have_title("Supernatural · c3parr")
    expect(desktop.get_by_role("navigation", name="Breadcrumb")).to_contain_text("TV Shows")


def test_a_phone_reaches_every_settings_and_system_page(phone: Page) -> None:
    tabs = phone.get_by_role("navigation", name="Main").last
    tabs.get_by_role("link", name=re.compile("More")).click()
    phone.get_by_role("link", name="Settings").click()
    for name in ("Integrations", "Languages", "Engines", "Subtitles", "Library"):
        phone.get_by_role("navigation", name="Settings pages").get_by_role(
            "link", name=name
        ).click()
        expect(phone).to_have_url(re.compile(f"/settings/{name.lower()}"))
    tabs.get_by_role("link", name=re.compile("More")).click()
    phone.get_by_role("link", name=re.compile("System")).click()
    for name in ("Alerts", "Logs", "Status", "Tasks"):
        phone.get_by_role("navigation", name="System pages").get_by_role("link", name=name).click()
        expect(phone).to_have_url(re.compile(f"/system/{name.lower()}"))


# --- Settings ---


def test_the_save_bar_counts_the_changes_and_shows_only_when_there_are_some(
    desktop: Page, spa: Served
) -> None:
    lib = spa.world.library
    desktop.goto(spa.url + "/settings/subtitles")
    wait = desktop.get_by_label("Wait time (minutes)")
    expect(wait).to_have_value("60")
    expect(save_bar(desktop)).to_have_count(0)

    wait.fill("30")
    expect(save_bar(desktop)).to_contain_text("1 unsaved change")
    desktop.get_by_role("switch", name="Forced subtitles cover their language").click()
    expect(save_bar(desktop)).to_contain_text("2 unsaved changes")
    wait.fill("60")  # voltou ao que estava: não conta mais
    expect(save_bar(desktop)).to_contain_text("1 unsaved change")

    save_bar(desktop).get_by_role("button", name="Save 1 change").click()
    said(desktop, "Saved.")
    expect(save_bar(desktop)).to_have_count(0)
    assert lib.config().forced_resolves and lib.config().wait_minutes == 60


def test_discard_goes_back_to_what_is_saved(desktop: Page, spa: Served) -> None:
    desktop.goto(spa.url + "/settings/subtitles")
    wait = desktop.get_by_label("Wait time (minutes)")
    wait.fill("5")
    save_bar(desktop).get_by_role("button", name="Discard").click()
    expect(wait).to_have_value("60")
    expect(save_bar(desktop)).to_have_count(0)
    assert spa.world.library.config().wait_minutes == 60


def test_leaving_settings_with_changes_asks_but_changing_tab_does_not(
    desktop: Page, spa: Served
) -> None:
    lib = spa.world.library
    desktop.goto(spa.url + "/settings/subtitles")
    desktop.get_by_label("Wait time (minutes)").fill("30")

    # outra aba de Settings: o rascunho é um só, nada a perguntar, e a alteração continua
    desktop.get_by_role("navigation", name="Settings pages").get_by_role(
        "link", name="Languages"
    ).click()
    expect(desktop).to_have_url(re.compile("/settings/languages"))
    expect(save_bar(desktop)).to_contain_text("1 unsaved change")

    leave = desktop.get_by_role("dialog").filter(has_text="Leave without saving?")
    go(desktop, "Queue")
    expect(leave).to_be_visible()
    expect(leave).to_contain_text("1 change")
    leave.get_by_role("button", name="Stay").click()
    expect(leave).not_to_be_visible()
    expect(desktop).to_have_url(re.compile("/settings/languages"))

    go(desktop, "Queue")
    leave.get_by_role("button", name="Discard").click()
    expect(desktop).to_have_url(re.compile("/queue"))
    assert lib.config().wait_minutes == 60

    desktop.goto(spa.url + "/settings/subtitles")
    desktop.get_by_label("Wait time (minutes)").fill("45")
    go(desktop, "Movies")
    leave.get_by_role("button", name="Save and leave").click()
    expect(desktop).to_have_url(re.compile("/movies"))
    assert lib.config().wait_minutes == 45


def test_llm_fields_follow_the_provider(desktop: Page, spa: Served) -> None:
    desktop.goto(spa.url + "/settings/integrations")
    section = desktop.get_by_role("region", name="Translation model")
    provider, model = section.get_by_label("Provider"), section.get_by_label("Model")
    url = section.get_by_label("Server URL")
    expect(url).to_be_visible()
    expect(model).to_have_value("modelo-x")
    expect(section.get_by_text("Reasoning effort")).to_have_count(0)

    provider.select_option("openai")
    expect(url).to_have_count(0)
    expect(model).to_have_value("modelo-x")  # modelo escolhido à mão fica
    expect(section.get_by_text("platform.openai.com")).to_be_visible()

    model.fill("")
    expect(model).to_have_value("gpt-4.1-mini")  # campo vazio: o padrão do provedor
    provider.select_option("anthropic")
    expect(model).to_have_value("claude-haiku-5-5")
    expect(section.get_by_role("group", name="Reasoning effort")).to_be_visible()
    provider.select_option("deepseek")
    expect(model).to_have_value("deepseek-chat")
    expect(section.get_by_text("Reasoning effort")).to_have_count(0)


def test_transcription_fields_follow_where_it_runs(desktop: Page, spa: Served) -> None:
    desktop.goto(spa.url + "/settings/integrations")
    section = desktop.get_by_role("region", name="Transcription")
    where = section.get_by_role("group", name="Where it runs")
    expect(section.get_by_label("Server URL")).to_have_count(0)

    where.get_by_role("button", name="OpenAI-compatible server").click()
    expect(section.get_by_label("Server URL")).to_be_visible()
    expect(section.get_by_label("Model")).to_be_visible()
    where.get_by_role("button", name="whisper-asr-webservice").click()
    expect(section.get_by_label("Server URL")).to_be_visible()
    expect(section.get_by_label("Model")).to_have_count(0)

    # servidor sem endereço: salvar é recusado e a alteração fica na tela
    save_bar(desktop).get_by_role("button", name=re.compile("Save")).click()
    said(desktop, "Transcription: put the server URL.")
    expect(save_bar(desktop)).to_be_visible()
    assert spa.world.library.config().asr_mode == "local"


def test_a_connection_test_answers_next_to_the_button(desktop: Page, spa: Served) -> None:
    desktop.goto(spa.url + "/settings/integrations")
    section = desktop.get_by_role("region", name="TMDb")
    expect(section.get_by_text("Not tested yet")).to_be_visible()
    section.get_by_role("button", name="Test connection").click()
    expect(section.get_by_role("status")).to_contain_text("TMDb: no read access token.")
    assert spa.world.library.secrets() == {"RADARR_API_KEY": "radarr-key"}  # testar não salva


def test_target_languages_are_chips_with_a_first_one(desktop: Page, spa: Served) -> None:
    desktop.goto(spa.url + "/settings/languages")
    section = desktop.get_by_role("region", name="Target languages")
    chips = section.get_by_role("listitem")
    expect(chips).to_have_count(2)
    add = section.get_by_label("Add a language")
    assert add.locator("option[value=es]").count() == 0  # o que já está escolhido sai da lista

    add.select_option("fr")
    expect(chips).to_have_count(3)
    section.get_by_role("button", name="Remove es").click()
    section.get_by_role("button", name="Make fr the first").click()
    expect(chips.first).to_contain_text("French")
    expect(save_bar(desktop)).to_contain_text("1 unsaved change")  # a lista é um campo só

    save_bar(desktop).get_by_role("button", name="Save 1 change").click()
    said(desktop, "Saved.")
    assert spa.world.library.config().target_languages == ("fr", "pt-BR")


def test_path_mappings_are_rows_to_add_and_remove(desktop: Page, spa: Served) -> None:
    desktop.goto(spa.url + "/settings/library/radarr")
    section = desktop.get_by_role("region", name="Path mappings")
    expect(section.get_by_label("Radarr path 1")).to_have_value("/data/movies")
    section.get_by_role("button", name="Add a mapping").click()
    section.get_by_label("Radarr path 2").fill("/remote/new")
    section.get_by_label("c3parr path 2").fill("/local/new")
    section.get_by_role("button", name="Remove mapping 1").click()
    expect(section.get_by_label("Radarr path 1")).to_have_value("/remote/new")

    save_bar(desktop).get_by_role("button", name=re.compile("Save")).click()
    said(desktop, "Saved.")
    assert spa.world.library.config().radarr.path_mappings == (("/remote/new", "/local/new"),)
    assert spa.world.library.secrets()["RADARR_API_KEY"] == "radarr-key"  # a chave não foi tocada


# --- biblioteca ---


def test_the_make_button_is_only_where_a_subtitle_is_missing(desktop: Page, spa: Served) -> None:
    desktop.goto(spa.url + "/movies")
    card = desktop.locator("article.media")
    expect(card).to_have_count(4)
    complete = card.filter(has_text="Incendies")
    expect(complete.get_by_role("button", name=re.compile("Make"))).to_have_count(0)
    working = card.filter(has_text="Oldboy")
    expect(working).to_contain_text("Translate → pt-BR · 47%")  # já tem tarefa: nada de botão
    expect(working.get_by_role("button", name=re.compile("Make"))).to_have_count(0)
    expect(card.filter(has_text="Parasite").get_by_role("button", name="Make es")).to_be_visible()

    # o filtro mostra a contagem e muda o endereço
    desktop.get_by_role("group", name="Filter").get_by_role(
        "button", name=re.compile("Complete")
    ).click()
    expect(desktop).to_have_url(re.compile("show=complete"))
    expect(card).to_have_count(1)


def test_selection_sends_every_ticked_item_to_the_queue(desktop: Page, spa: Served) -> None:
    catalog = spa.world.catalog
    desktop.goto(spa.url + "/movies")
    expect(desktop.get_by_role("checkbox")).to_have_count(0)
    desktop.get_by_role("button", name="Select", exact=True).click()
    desktop.get_by_role("checkbox", name="Select Incendies").check()
    desktop.get_by_role("checkbox", name="Select Parasite").check()
    bulk = desktop.get_by_role("region", name="Selection")
    expect(bulk).to_contain_text("2 selected")
    before = len(catalog.alerts(include_resolved=False))
    bulk.get_by_role("button", name="Add to the queue").click()
    # os arquivos do exemplo não são vídeos de fato: cada item marcado vira um alerta de leitura
    said(desktop, "2 item(s) could not be read")
    assert len(catalog.alerts(include_resolved=False)) == before + 2


def test_the_bookmark_toggles_monitoring(desktop: Page, spa: Served) -> None:
    world = spa.world
    desktop.goto(spa.url + "/movies")
    desktop.get_by_role("button", name="Not monitored: Roma").click()
    expect(desktop.get_by_role("button", name="Monitored: Roma", exact=True)).to_be_visible()
    assert world.library.movie(world.ids["Roma"]).monitored


def test_a_show_opens_the_seasons_with_work_and_names_the_episodes(
    desktop: Page, spa: Served
) -> None:
    desktop.goto(f"{spa.url}/tv/{spa.world.ids['Supernatural']}")
    seasons = desktop.get_by_role("region", name="Seasons")
    expect(seasons.get_by_text("In My Time of Dying")).to_be_visible()  # título, não o arquivo
    expect(seasons.get_by_text("Queued · next")).to_be_visible()
    first = seasons.get_by_role("button", name=re.compile("Season 1"))
    expect(first).to_have_attribute("aria-expanded", "true")
    first.click()
    expect(first).to_have_attribute("aria-expanded", "false")
    expect(seasons.get_by_text("Pilot")).to_have_count(0)


# --- fila ---


def test_the_queue_opens_details_and_filters_without_reloading(desktop: Page, spa: Served) -> None:
    desktop.goto(spa.url + "/queue")
    tasks = desktop.get_by_role("region", name="Tasks")
    details = desktop.get_by_role("region", name="Task details")
    expect(details).to_contain_text("Select a task")
    tasks.get_by_role("button", name=re.compile(WENDIGO)).click()
    expect(desktop).to_have_url(re.compile(f"job={spa.world.ids['failed']}"))
    expect(details.get_by_role("alert")).to_contain_text("LLM fora do ar")
    expect(details.get_by_role("button", name="Run again")).to_be_visible()

    filters = desktop.get_by_role("group", name="Filter")
    filters.get_by_role("button", name=re.compile("Failed")).click()
    expect(desktop).to_have_url(re.compile("status=failed"))
    expect(tasks.get_by_role("button", pressed=True)).to_have_count(1)
    expect(tasks.locator(".row-btn")).to_have_count(1)

    filters.get_by_role("button", name=re.compile("All")).click()
    desktop.get_by_role("searchbox", name="Search the queue").fill("pilot")
    expect(desktop).to_have_url(re.compile("q=pilot"))
    expect(tasks.locator(".row-btn")).to_have_count(1)
    expect(tasks).to_contain_text("Pilot")


def test_cancelling_everything_waiting_asks_first(desktop: Page, spa: Served) -> None:
    world = spa.world
    desktop.goto(spa.url + "/queue")
    desktop.get_by_role("button", name="Cancel 1 queued").click()
    question = desktop.get_by_role("dialog").filter(has_text="Cancel 1 queued task?")
    question.get_by_role("button", name="Cancel", exact=True).click()  # desistiu
    assert world.queue.get(world.ids["queued"]).status == "na_fila"

    desktop.get_by_role("button", name="Cancel 1 queued").click()
    question.get_by_role("button", name="Cancel them").click()
    said(desktop, "1 task(s) cancelled.")
    assert world.queue.get(world.ids["queued"]).status == "cancelada"
    assert world.queue.get(world.ids["running"]).status == "processando"


def test_on_a_phone_the_task_details_open_over_the_list(phone: Page, spa: Served) -> None:
    phone.goto(spa.url + "/queue")
    phone.get_by_role("region", name="Tasks").get_by_role(
        "button", name=re.compile("Oldboy")
    ).click()
    sheet = phone.get_by_role("dialog", name="Task details")
    expect(sheet).to_be_visible()
    expect(sheet).to_contain_text("block 44 of 94")
    sheet.get_by_role("button", name="Close").click()
    expect(sheet).not_to_be_visible()
    expect(phone).not_to_have_url(re.compile("job="))
