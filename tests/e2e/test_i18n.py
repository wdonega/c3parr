"""Os idiomas da interface no navegador de verdade: qual abre, o seletor, o que fica guardado e
o que vem do servidor escrito no idioma escolhido.
Precisa do build do frontend: `npm run build` em `frontend/`.
"""

import pytest
from conftest import Served, open_page
from playwright.sync_api import Browser, Page, expect

from c3parr.web.app import DIST

pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").exists(), reason="build the frontend first: npm run build"
)

SAVED = "window.localStorage.getItem('c3parr.locale')"


def menu(page: Page, name: str):  # type: ignore[no-untyped-def]
    return page.get_by_role("navigation", name=name).first


def picker(page: Page, name: str):  # type: ignore[no-untyped-def]
    return page.get_by_label(name, exact=True)


@pytest.mark.parametrize(
    ("browser_language", "locale", "title", "queue"),
    [
        ("pt-BR", "pt-BR", "Visão geral · c3parr", "Fila"),
        ("pt-PT", "pt-BR", "Visão geral · c3parr", "Fila"),
        ("es-MX", "es-MX", "Resumen · c3parr", "Cola"),
        ("es-AR", "es-MX", "Resumen · c3parr", "Cola"),
        ("en-GB", "en-US", "Overview · c3parr", "Queue"),
        ("fr-FR", "en-US", "Overview · c3parr", "Queue"),
    ],
)
def test_the_interface_opens_in_the_language_of_the_browser_and_saves_it(
    browser: Browser, spa: Served, browser_language: str, locale: str, title: str, queue: str
) -> None:
    page = open_page(browser, spa, "/dashboard", locale=browser_language)
    expect(page).to_have_title(title)
    expect(page.locator("nav.side").get_by_role("link", name=queue).first).to_be_visible()
    assert page.evaluate(SAVED) == locale
    assert page.evaluate("document.documentElement.lang") == locale
    page.context.close()


def test_the_picker_changes_the_language_at_once_and_the_choice_stays(
    browser: Browser, spa: Served
) -> None:
    page = open_page(browser, spa, "/dashboard")
    side = menu(page, "Main")
    expect(side.get_by_role("link", name="Overview")).to_be_visible()

    picker(page, "Language").select_option("es-MX")
    expect(menu(page, "Principal").get_by_role("link", name="Resumen")).to_be_visible()
    expect(page).to_have_title("Resumen · c3parr")
    expect(page.get_by_role("heading", level=1)).to_have_text("Resumen")
    assert page.evaluate(SAVED) == "es-MX"

    # o navegador continua em inglês: vale o que a pessoa escolheu
    page.reload()
    expect(page).to_have_title("Resumen · c3parr")
    page.goto(spa.url + "/queue")
    expect(page.get_by_role("heading", level=1)).to_have_text("Cola")

    picker(page, "Idioma").select_option("pt-BR")
    expect(page.get_by_role("heading", level=1)).to_have_text("Fila")
    picker(page, "Idioma").select_option("en-US")
    expect(page.get_by_role("heading", level=1)).to_have_text("Queue")
    assert page.evaluate(SAVED) == "en-US"
    page.context.close()


def test_changing_the_language_keeps_what_was_being_edited(browser: Browser, spa: Served) -> None:
    page = open_page(browser, spa, "/settings/subtitles")
    page.get_by_role("switch", name="Use embedded subtitles").click()
    expect(page.get_by_role("region", name="Unsaved changes")).to_contain_text("1 unsaved change")

    picker(page, "Language").select_option("pt-BR")
    bar = page.get_by_role("region", name="Alterações não salvas")
    expect(bar).to_contain_text("1 alteração não salva")
    expect(page.get_by_role("switch", name="Usar legendas embutidas")).to_have_attribute(
        "aria-checked", "false"
    )
    bar.get_by_role("button", name="Salvar 1 alteração").click()
    expect(page.locator(".toast", has_text="Salvo.").first).to_be_visible()
    assert spa.world.library.config().use_embedded is False
    page.context.close()


def test_what_the_server_says_comes_in_the_chosen_language(browser: Browser, spa: Served) -> None:
    page = open_page(browser, spa, "/queue", locale="pt-BR")
    # o passo e o andamento da tarefa que roda: o servidor manda `traduzir` e "block 44 of 94"
    running = page.get_by_role("region", name="Tarefas").locator(".running-card")
    expect(running).to_contain_text("Traduzir → pt-BR · bloco 44 de 94")
    expect(running.get_by_role("progressbar")).to_have_attribute("aria-label", "Traduzir → pt-BR")

    running.click()
    details = page.get_by_role("region", name="Detalhes da tarefa")
    expect(details).to_contain_text("Processando")
    expect(details).to_contain_text("Tradução")  # a etapa de agora

    # a frase de uma ação
    page.get_by_role("button", name="Cancelar 1 da fila").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("Cancelar 1 tarefa da fila?")
    dialog.get_by_role("button", name="Cancelar todas").click()
    expect(page.locator(".toast", has_text="1 tarefa cancelada.").first).to_be_visible()

    # o que falta configurar e as rotinas
    page.goto(spa.url + "/system/tasks")
    expect(page.get_by_role("region", name="Rotinas")).to_contain_text("Sincronizar com o Radarr")
    page.context.close()


def test_spanish_reaches_settings_and_the_names_of_the_languages(
    browser: Browser, spa: Served
) -> None:
    page = open_page(browser, spa, "/settings/languages", locale="es-ES")
    expect(page.get_by_role("heading", level=1)).to_have_text("Configuración")
    targets = page.get_by_role("list", name="Idiomas de destino")
    expect(targets).to_contain_text("Portugués (Brasil)")
    expect(targets).to_contain_text("primero")
    page.context.close()


def test_on_a_phone_the_picker_is_in_the_more_page(browser: Browser, spa: Served) -> None:
    page = open_page(browser, spa, "/more", width=390, locale="en-US")
    page.get_by_label("Language").last.select_option("pt-BR")
    expect(page.get_by_role("heading", level=1)).to_have_text("Mais")
    expect(menu(page, "Principal").get_by_role("link", name="Filmes")).to_be_visible()
    page.context.close()
