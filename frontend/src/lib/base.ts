// Onde a interface está sendo servida. O servidor escreve isto na página (Settings → General →
// Host): atrás de um proxy reverso num subcaminho, tudo parte de `/c3parr` em vez da raiz.
declare global {
  interface Window {
    __C3PARR__?: { base: string; name: string }
  }
}

/** `/c3parr`, ou vazio na raiz (e no `npm run dev`). */
export const BASE = window.__C3PARR__?.base ?? ''

// Aberto sem a URL base (direto pela porta, para consertar um prefixo errado, por exemplo): vai
// para o mesmo endereço com ela, que é onde as rotas da interface valem. O servidor atende os dois.
const { pathname, search, hash } = window.location
if (BASE && pathname !== BASE && !pathname.startsWith(`${BASE}/`)) {
  window.location.replace(BASE + pathname + search + hash)
}

/** O nome desta instalação: vai no título da aba. */
export const INSTANCE = window.__C3PARR__?.name ?? 'c3parr'

/** Um caminho do servidor (`/logo.png`, `/api/v1/…`) com a URL base na frente. */
export const withBase = (path: string) => (path.startsWith('/') ? BASE + path : path)

/** A URL base como o servidor a guarda: `c3parr/` → `/c3parr`; vazio → vazio. */
export function cleanBase(text: string): string {
  const path = text.trim().replace(/^\/+|\/+$/g, '')
  return path ? `/${path}` : ''
}
