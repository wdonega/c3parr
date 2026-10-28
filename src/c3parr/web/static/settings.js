// Formulário de Settings no jeito do Radarr/Sonarr: o Save mostra quantas alterações há,
// sair da página com alteração pendente pede confirmação e o Discard volta ao que está salvo.
(() => {
  const form = document.querySelector("form[data-settings]");
  if (!form) return;
  const save = form.querySelector("button[type=submit]");
  const label = save.querySelector("span");

  // um valor por nome de campo; campos repetidos (chips) viram a lista em ordem
  function snapshot() {
    const out = {};
    for (const el of form.elements) {
      if (!el.name || el.disabled) continue;
      if ((el.type === "checkbox" || el.type === "radio") && !el.checked) {
        out[el.name] ??= [];
        continue;
      }
      (out[el.name] ??= []).push(el.value);
    }
    return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, v.join("\n")]));
  }

  let saved = snapshot();
  const changes = () => {
    const now = snapshot();
    const names = new Set([...Object.keys(saved), ...Object.keys(now)]);
    return [...names].filter(n => (saved[n] ?? "") !== (now[n] ?? "")).length;
  };
  function refresh() {
    const n = changes();
    const text = n === 0 ? "No changes" : n === 1 ? "Save 1 change" : `Save ${n} changes`;
    save.disabled = n === 0;
    if (label.textContent !== text) label.textContent = text;
  }

  form.addEventListener("input", refresh);
  form.addEventListener("change", refresh);
  // chips entram e saem do DOM sem evento de input; mudanças no próprio botão não contam
  new MutationObserver(records => {
    if (records.some(r => !save.contains(r.target))) refresh();
  }).observe(form, { childList: true, subtree: true });

  // gravou (2xx): o que está na tela passa a ser o salvo
  form.addEventListener("htmx:afterRequest", e => {
    if (e.detail.elt === form && e.detail.successful) { saved = snapshot(); refresh(); }
  });
  // Discard relê do banco: chips e campos montados por script voltam junto
  form.addEventListener("reset", e => {
    e.preventDefault();
    saved = snapshot();  // não pergunta ao sair: o usuário pediu para descartar
    location.reload();
  });
  let leaving = false;  // saída confirmada no nosso diálogo: o do browser não aparece de novo
  window.addEventListener("beforeunload", e => {
    if (!leaving && changes() > 0) e.preventDefault();
  });

  // link dentro do app (menu lateral, sub-abas): o nosso diálogo no lugar do aviso do browser,
  // que fica só para fechar a aba e recarregar (esse não dá para personalizar)
  const dialog = document.getElementById("unsaved-dialog");
  let pending = null;
  const go = () => { leaving = true; location.href = pending; };
  document.addEventListener("click", e => {
    const a = e.target.closest("a[href]");
    if (!a || !dialog || a.target === "_blank" || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || url.pathname === location.pathname || changes() === 0) return;
    e.preventDefault();
    pending = url.href;
    const n = changes();
    dialog.querySelector("[data-count]").textContent = n === 1 ? "1 change" : `${n} changes`;
    dialog.showModal();
  }, true);
  dialog?.querySelector("[data-stay]").addEventListener("click", () => dialog.close());
  dialog?.querySelector("[data-discard]").addEventListener("click", go);
  dialog?.querySelector("[data-save]").addEventListener("click", () => {
    dialog.close();
    // grava pelo próprio formulário (htmx); só sai se gravou, senão fica com o aviso de erro
    form.addEventListener("htmx:afterRequest", e => {
      if (e.detail.elt === form && e.detail.successful) go();
    }, { once: true });
    form.requestSubmit();
  });

  // o olho dos campos de chave é do próprio BeerCSS: ícone ao lado de input password alterna o tipo
  refresh();
})();
