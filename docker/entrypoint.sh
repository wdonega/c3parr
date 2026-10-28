#!/bin/sh
# Roda o c3parr com o usuário PUID:PGID (como as imagens linuxserver), para as legendas saírem com o
# mesmo dono dos arquivos do Radarr/Sonarr. Mantém os grupos dos dispositivos de GPU (/dev/dri do
# Vulkan, /dev/nvidia* do CUDA), senão o processo perde o acesso a eles ao trocar de usuário.
set -e
mkdir -p "$C3PARR_CONFIG_DIR" "$C3PARR_CACHE_DIR"

if [ "$(id -u)" != "0" ] || [ "${PUID:-0}" = "0" ]; then
  exec "$@"
fi

if [ "$(stat -c %u "$C3PARR_CONFIG_DIR")" != "$PUID" ]; then
  chown -R "$PUID:$PGID" "$C3PARR_CONFIG_DIR"
fi

groups=$(stat -c %g /dev/dri/* /dev/nvidia* 2>/dev/null | sort -un | paste -sd, -)
if [ -n "$groups" ]; then
  exec setpriv --reuid="$PUID" --regid="$PGID" --groups="$groups" "$@"
fi
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
