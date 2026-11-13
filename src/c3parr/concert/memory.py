"""Quanta memória a legenda de show pode usar.

Os programas da família *arr rodam em máquina pequena, e um show é raro: melhor devagar do que
travar o que mais roda ali. A separação de voz e o encaixe da letra trabalham em pedaços; pedaço
menor pede menos memória e leva o mesmo tempo. Medido num M3 Pro (pico do processo):

| faixa | separação: pedaço | pico | encaixe: janela | pico |
|---|---|---|---|---|
| 2 GB | 1 min | 2,1 GB | 10 s | 2,3 GB |
| 3 GB | 5 min | 3,1 GB | 10 s | 2,3 GB |
| mais | 15 min | 4,1 GB | 30 s | 3,5 GB |

A janela de 10 s muda um pouco a escuta nas emendas: num show de 2 h, 444 das 491 linhas oficiais
saíram idênticas às da janela de 30 s, com 13 linhas aceitas a mais e a mesma conferência
independente (93% dos intervalos iguais aos do disco).

No encaixe, 1,3 GB do pico é o arquivo do modelo lido direto do disco (não precisa de troca: o
sistema pode soltar e ler de novo). A faixa é o teto escolhido em Settings; dentro dele vale o que
estiver livre na hora, com uma folga. Com bem menos de 2 GB livres a etapa não começa: numa placa
de 8 GB com 0,8 GB livre, sem esta conferência, a máquina passou a trocar memória com o disco e
parou de responder.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from c3parr.core.errors import Unavailable

Limit = Literal["auto", "2", "3"]
"""O teto em GB (`2`, `3`) ou `auto`: tudo o que estiver livre, com a folga."""


@dataclass(frozen=True)
class Tier:
    need_gb: float
    piece_s: float
    """Quanto áudio a separação de voz carrega de uma vez."""
    window_s: float
    """Quanto áudio o modelo de encaixe escuta de uma vez."""


TIERS = (Tier(4.5, 900.0, 30.0), Tier(3.0, 300.0, 10.0), Tier(2.0, 60.0, 10.0))
CAP = {"auto": float("inf"), "3": 3.0, "2": 2.0}
MARGIN_GB = 0.5  # fica livre para o resto da máquina
FLOOR_GB = 1.5  # com menos que isso livre, a etapa não começa


def free_gb() -> float | None:
    """A memória disponível, pelo `MemAvailable` do Linux; None onde não dá para saber."""
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    except (OSError, ValueError, IndexError):
        pass
    return None


def plan(limit: str, free: float | None, provider: str | None = None) -> Tier:
    """A faixa de trabalho: a maior que cabe no teto e no que está livre (menos a folga). Entre
    `FLOOR_GB` e a menor faixa ela ainda roda, na menor, contando com um pouco de troca."""
    budget = float("inf") if free is None else free - MARGIN_GB
    ceiling = min(CAP.get(limit, CAP["auto"]), budget)
    for tier in TIERS:
        if tier.need_gb <= ceiling:
            return tier
    if free is not None and free < FLOOR_GB:
        raise Unavailable(
            f"a concert needs about 2 GB of free memory and this machine has {free:.1f} GB "
            "available",
            provider=provider,
        )
    return TIERS[-1]
