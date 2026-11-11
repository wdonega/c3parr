"""Como dirigir a interface no navegador, dito em termos do que a pessoa faz.

`LegacyUi` é a interface de hoje (BeerCSS + htmx). Os testes só usam estes métodos: a
interface nova ganha a classe dela com os mesmos métodos e roda os mesmos testes.
"""

from playwright.sync_api import Locator, Page, expect


class LegacyUi:
    def __init__(self, page: Page, base: str) -> None:
        self.page = page
        self.base = base

    # --- navegar ---

    def open(self, path: str) -> None:
        self.page.goto(self.base + path)
        self.page.wait_for_function("window.htmx !== undefined")

    def path(self) -> str:
        return self.page.evaluate("location.pathname + location.search")

    def wait_for_path(self, fragment: str) -> None:
        self.page.wait_for_url(f"**{fragment}**")

    def go(self, label: str) -> None:
        """Clica num item do menu lateral."""
        self.page.locator("nav.left.max a", has_text=label).first.click()

    def toast(self) -> str:
        toast = self.page.locator("#toast.active")
        toast.wait_for(state="visible")
        return toast.inner_text().strip()

    # --- formulário de Settings ---

    def field(self, name: str) -> Locator:
        return self.page.locator(f'form[data-settings] [name="{name}"]').first

    def fill(self, name: str, value: str) -> None:
        self.field(name).fill(value)

    def value(self, name: str) -> str:
        return self.field(name).input_value()

    def choose(self, name: str, value: str) -> None:
        self.field(name).select_option(value)

    def switch(self, name: str) -> None:
        """Liga ou desliga (o input fica escondido atrás do desenho do BeerCSS)."""
        self.page.locator(f'label:has(input[name="{name}"])').first.click()

    def is_shown(self, selector: str) -> bool:
        return self.page.locator(selector).first.is_visible()

    def save_label(self) -> str:
        return self.page.locator("form[data-settings] button[type=submit] span").inner_text()

    def can_save(self) -> bool:
        return self.page.locator("form[data-settings] button[type=submit]").is_enabled()

    def expect_save_label(self, text: str) -> None:
        expect(self.page.locator("form[data-settings] button[type=submit] span")).to_have_text(text)

    def save(self) -> str:
        self.page.locator("form[data-settings] button[type=submit]").click()
        return self.toast()

    def discard(self) -> None:
        with self.page.expect_navigation():
            self.page.locator("form[data-settings] button[type=reset]").click()
        self.page.wait_for_function("window.htmx !== undefined")

    # --- aviso de alterações não salvas ---

    def leave_dialog(self) -> Locator:
        return self.page.locator("#unsaved-dialog")

    def answer_leave_dialog(self, answer: str) -> None:
        """`stay`, `discard` ou `save`."""
        self.leave_dialog().locator(f"[data-{answer}]").click()

    # --- idiomas e mapeamentos ---

    def language_chips(self, name: str) -> list[str]:
        chips = self.page.locator(f"#{name}-chips .chip")
        return [chips.nth(i).get_attribute("data-code") or "" for i in range(chips.count())]

    def add_language(self, name: str, code: str) -> None:
        self.page.locator(f"#{name}-pick").select_option(code)
        card = self.page.locator("article", has=self.page.locator(f"#{name}-pick"))
        card.locator("button", has_text="Add").click()

    def remove_language(self, name: str, code: str) -> None:
        self.page.locator(f'#{name}-chips .chip[data-code="{code}"] i').click()

    def language_can_be_picked(self, name: str, code: str) -> bool:
        option = self.page.locator(f'#{name}-pick option[value="{code}"]')
        return not option.is_disabled()

    def mapping_rows(self) -> int:
        return self.page.locator(".map-row").count()

    def add_mapping(self, remote: str, local: str) -> None:
        card = self.page.locator("article", has_text="Path Mappings")
        card.locator("button", has_text="Add").click()
        row = self.page.locator(".map-row").last
        row.locator("input").nth(0).fill(remote)
        row.locator("input").nth(1).fill(local)

    def remove_mapping(self, index: int) -> None:
        self.page.locator(".map-row").nth(index).locator("button").click()

    # --- biblioteca ---

    def row(self, text: str) -> Locator:
        return self.page.locator("#library tbody tr", has_text=text).first

    def selection_boxes_shown(self) -> bool:
        return self.page.locator("#library tbody input[name=item]").first.locator("..").is_visible()

    def start_selecting(self) -> None:
        self.page.locator("button", has_text="Select").first.click()

    def tick(self, text: str) -> None:
        self.row(text).locator("label.checkbox").click()

    def add_selected(self) -> str:
        self.page.locator("button", has_text="Add selected to queue").click()
        return self.toast()

    def monitor_label(self, text: str) -> str:
        return self.row(text).locator("button[hx-post$='/monitor']").get_attribute("title") or ""

    def toggle_monitor(self, text: str) -> None:
        before = self.monitor_label(text)
        self.row(text).locator("button[hx-post$='/monitor']").click()
        expect(self.row(text).locator("button[hx-post$='/monitor']")).not_to_have_attribute(
            "title", before
        )

    # --- fila ---

    def queue_rows(self) -> list[str]:
        rows = self.page.locator("#rows li.wave h6")
        return [rows.nth(i).inner_text() for i in range(rows.count())]

    def expect_queue_rows(self, count: int) -> None:
        expect(self.page.locator("#rows li.wave")).to_have_count(count)

    def open_task(self, text: str) -> str:
        self.page.locator("#rows li.wave", has_text=text).first.click()
        detail = self.page.locator("#detail")
        expect(detail.locator("h5")).to_contain_text(text)
        return detail.inner_text()

    def filter_queue(self, label: str) -> None:
        self.page.locator("#queue-filter label.chip", has_text=label).click()

    def search_queue(self, text: str) -> None:
        self.page.locator("#queue-filter input[name=q]").fill(text)
