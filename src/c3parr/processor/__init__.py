"""c3parr-cuda-processor: o trabalho pesado do c3parr (Whisper, separação de voz, encaixe da letra)
numa máquina com GPU NVIDIA, chamado pela rede.

O c3parr manda o áudio (nunca o vídeo) e o pedido; o processador roda o mesmo provider que rodaria
no c3parr, num processo filho por tarefa, e devolve o resultado. Os arquivos ficam guardados pelo
sha256: o que já subiu não sobe de novo, e a voz isolada que ele fez já está lá para o encaixe.
"""
