# syntax=docker/dockerfile:1.7
# whisper.cpp server for running c3parr's transcription on another machine with a GPU.
# Speaks the OpenAI API at /v1/audio/transcriptions, which is what c3parr calls.
# Two variants, for x86 (amd64) and ARM (arm64), the same as c3parr's own image:
#   VARIANT=cuda    NVIDIA: RTX 20xx–50xx on x86; Jetson Orin, Thor and DGX Spark on ARM (default)
#   VARIANT=vulkan  AMD, Intel and others through Mesa
# Without a GPU both fall back to the CPU (whisper.cpp picks the best CPU code at start).
ARG VARIANT=cuda
ARG CUDA_VERSION=13.2.0
ARG UBUNTU_VERSION=24.04

# ---------- whisper.cpp ----------
FROM nvcr.io/nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION} AS whisper-cuda
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git gcc-14 g++-14 \
 && rm -rf /var/lib/apt/lists/*
# the build machine needs no GPU: the linker finds libcuda in the CUDA stubs
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
# CUDA only. Empty = every GPU of the architecture: Turing, Ampere, Ada and Blackwell on x86;
# Orin (87), Thor (110) and DGX Spark (121) on ARM. Just yours makes the build much shorter.
ARG CUDA_ARCHS=""
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
 && cmake --build build -j"$(nproc)" --target whisper-server \
 && mkdir -p /out \
 && cp -a build/bin/whisper-server /out/ \
 && find build -name "*.so*" -exec cp -a {} /out/ \;

# ---------- runtime ----------
FROM nvcr.io/nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${UBUNTU_VERSION} AS base-cuda
# on a Jetson the driver comes from the host, and the toolkit's cudacompat hook panics on these
# libraries from the image; on x86 the folder doesn't exist
RUN rm -rf /usr/local/cuda-*/compat_orin
ENV NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility

FROM ubuntu:${UBUNTU_VERSION} AS base-vulkan
RUN apt-get update && apt-get install -y --no-install-recommends libvulkan1 mesa-vulkan-drivers \
 && rm -rf /var/lib/apt/lists/*

FROM base-${VARIANT}
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=whisper /out /opt/whisper
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENV PATH=/opt/whisper:$PATH LD_LIBRARY_PATH=/opt/whisper \
    WHISPER_MODEL=large-v3-turbo-q8_0 MODELS_DIR=/models
VOLUME ["/models"]
EXPOSE 9000
# the first start downloads ~900 MB of model before the port opens
HEALTHCHECK --interval=30s --timeout=5s --start-period=10m \
  CMD curl -fs http://127.0.0.1:9000/ > /dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
