# syntax=docker/dockerfile:1.7
# c3parr com whisper.cpp acelerado. Duas variantes, cada uma para x86 (amd64) e ARM (arm64):
#   VARIANT=cuda    NVIDIA: RTX 20xx–50xx no x86; Jetson Orin, Thor e DGX Spark no ARM (padrão)
#   VARIANT=vulkan  AMD, Intel e NVIDIA pelo Mesa no x86; Mali e afins no ARM
# Sem GPU, as duas transcrevem na CPU (o whisper.cpp escolhe a melhor variante de CPU na hora).
ARG VARIANT=cuda
ARG CUDA_VERSION=13.2.0
ARG UBUNTU_VERSION=24.04

# ---------- whisper.cpp ----------
FROM nvcr.io/nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION} AS whisper-cuda
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git gcc-14 g++-14 \
 && rm -rf /var/lib/apt/lists/*
# o build não tem GPU nem driver: o linker resolve a libcuda pelo stub do CUDA
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64/stubs
ENV GGML_ACCEL="-DGGML_CUDA=ON"

FROM ubuntu:${UBUNTU_VERSION} AS whisper-vulkan
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates cmake git gcc-14 g++-14 glslc libvulkan-dev spirv-headers \
 && rm -rf /var/lib/apt/lists/*
ENV GGML_ACCEL="-DGGML_VULKAN=ON"

FROM whisper-${VARIANT} AS whisper
ARG WHISPER_VERSION=v1.9.5
ARG TARGETARCH
# vazio = as GPUs de cada arquitetura: Turing, Ampere, Ada e Blackwell no x86; Orin (87),
# Thor (110) e DGX Spark (121) no ARM
ARG CUDA_ARCHS=""
# GCC 14: o 13 do Ubuntu 24.04 não conhece o `+sme` da variante de CPU armv9.2
ENV CC=gcc-14 CXX=g++-14 CUDAHOSTCXX=g++-14
RUN git clone --depth 1 --branch "${WHISPER_VERSION}" https://github.com/ggml-org/whisper.cpp /src
WORKDIR /src
RUN archs="${CUDA_ARCHS}"; \
    if [ -z "$archs" ]; then \
      if [ "$TARGETARCH" = "arm64" ]; then archs="87;110;121"; else archs="75;86;89;120"; fi; \
    fi; \
    cmake -B build ${GGML_ACCEL} -DCMAKE_CUDA_ARCHITECTURES="$archs" -DCMAKE_BUILD_TYPE=Release \
      -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_SDL2=OFF \
 && cmake --build build -j"$(nproc)" --target whisper-cli \
 && mkdir -p /out \
 && cp -a build/bin/whisper-cli /out/ \
 && find build -name "*.so*" -exec cp -a {} /out/ \;

# ---------- base de runtime ----------
FROM nvcr.io/nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${UBUNTU_VERSION} AS base-cuda
# no Jetson o driver vem do host, e o hook cudacompat do nvidia-container-toolkit dá panic com
# estas libs da imagem 13.2 (visto no Orin Nano); no x86 a pasta não existe
RUN rm -rf /usr/local/cuda-*/compat_orin
ENV NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility

FROM ubuntu:${UBUNTU_VERSION} AS base-vulkan
RUN apt-get update && apt-get install -y --no-install-recommends libvulkan1 mesa-vulkan-drivers \
 && rm -rf /var/lib/apt/lists/*

# ---------- c3parr ----------
FROM base-${VARIANT}
ARG VARIANT
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates ffmpeg libgomp1 python3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_PYTHON=python3 UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# dependências antes do código: mudar o código não reinstala tudo
COPY pyproject.toml uv.lock LICENSE ./
RUN uv sync --frozen --no-dev --extra web --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra web --no-editable

COPY --from=whisper /out /opt/whisper
# no fim: mudar a versão a cada build não invalida o cache das camadas de cima
ARG C3PARR_VERSION=""
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

ENV PATH=/app/.venv/bin:/opt/whisper:$PATH \
    LD_LIBRARY_PATH=/opt/whisper:$LD_LIBRARY_PATH \
    PYTHONUNBUFFERED=1 \
    C3PARR_HOST=0.0.0.0 \
    C3PARR_PORT=8484 \
    C3PARR_CONFIG_DIR=/config \
    C3PARR_CACHE_DIR=/config/cache \
    HOME=/config \
    PUID=1000 \
    PGID=1000 \
    C3PARR_IMAGE_VARIANT=${VARIANT} \
    C3PARR_VERSION=${C3PARR_VERSION}

EXPOSE 8484
VOLUME ["/config"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/health', timeout=4)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["c3parr-web"]
