"""Provider `srt` da interface de escrita de legenda."""

from pathlib import Path

from c3parr.core.models import Subtitle
from c3parr.interfaces import subtitle_writer
from c3parr.providers.base import Health, ProviderInfo
from c3parr.subtitle.srt import read_srt, write_srt

INFO = ProviderInfo(
    id="srt",
    interface=subtitle_writer.INTERFACE,
    version="1",
    description="SubRip in UTF-8, numbered from 1",
)


class SrtWriter:
    info = INFO
    extension = "srt"

    def health(self) -> Health:
        return Health(ok=True)

    def write(self, subtitle: Subtitle, dest: Path) -> Path:
        path = dest.with_name(f"{dest.name}.{self.extension}")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_srt(subtitle, path)
        if len(read_srt(path).cues) != len(subtitle.cues):
            raise RuntimeError(f"re-reading {path} does not match what was written")
        return path


def register() -> None:
    subtitle_writer.FACTORY.register(INFO, lambda _config: SrtWriter())
