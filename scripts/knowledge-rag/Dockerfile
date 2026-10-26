# Resolve/install ARM64 wheels on the build host; no emulation or target execution.
FROM --platform=$BUILDPLATFORM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS dependencies
ARG TARGETARCH
COPY requirements.lock /build/requirements.lock
RUN test "$TARGETARCH" = arm64 && python -m pip install --no-cache-dir \
      --only-binary=:all: --require-hashes --target=/dependencies \
      --platform=manylinux_2_28_aarch64 --platform=manylinux_2_27_aarch64 \
      --platform=manylinux_2_17_aarch64 --platform=manylinux2014_aarch64 \
      --implementation=cp --python-version=3.12 --abi=cp312 \
      -r /build/requirements.lock

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
COPY --from=dependencies /dependencies /opt/dependencies
COPY adapter.py prepare_model.py http_client.py model.lock.json model-e5.lock.json /opt/rag/
ENV PYTHONPATH=/opt/dependencies \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2
WORKDIR /opt/rag
USER 503:1000
ENTRYPOINT ["python", "/opt/rag/adapter.py"]
CMD ["--transport", "streamable-http"]
