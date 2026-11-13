"""Legenda de show: a letra oficial encaixada no canto (♪) e a fala entre as músicas.

O caminho, uma parte por módulo:
voz isolada → transcrição sem detector de voz (`words`) → catálogo de letras do artista
(`catalogue`) → quais músicas tocam e onde (`songs`) → cada linha no seu instante e a prova de
cada uma (`fit`) → o que se canta de novo (`again`) → a fala fora das músicas (`speech`) → a
legenda (`build`).

O Whisper erra canto com certeza alta, por isso a letra vem de fora e cada linha só entra com
prova. Os números por trás de cada limiar estão na decisão 0007.
"""
