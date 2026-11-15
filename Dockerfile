# syntax=docker/dockerfile:1
#
# The installed product: the engine's API and the screen, in one image.
#
# Build it through `make package`, which also checks and stages the license verification
# key. Built by hand, the verification key comes in as a second build context named
# `license`: a directory holding `license_public_key.pem`, or an empty directory for an
# image that enforces no license keys.
#
#   docker build --build-context license=/path/to/keydir -t wri:0.3.0 .
#
# Behind a proxy that inspects TLS, pass its certificate so the dependency install can
# verify it. It is used for the build only and is not kept in the image:
#
#   docker build --secret id=ca,src=/path/to/ca.pem --build-context license=... .

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    python -m venv /opt/wri \
    && /opt/wri/bin/pip install --no-cache-dir --disable-pip-version-check ".[ui]" \
    && /opt/wri/bin/pip freeze --exclude wri-engine > /opt/wri/requirements.lock

# ---------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim
ARG WRI_VERSION=0+unknown
ARG WRI_LICENSED=no
LABEL org.opencontainers.image.title="Workforce Risk Intelligence" \
      org.opencontainers.image.version="${WRI_VERSION}" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary" \
      wri.license-keys-enforced="${WRI_LICENSED}"

# An unprivileged account with a fixed id, so the data volume's ownership is predictable.
RUN groupadd --system --gid 10001 wri \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /tmp wri \
    && mkdir -p /data \
    && chown wri:wri /data

COPY --from=build /opt/wri /opt/wri
WORKDIR /app
COPY LICENSE ./
COPY config ./config
COPY app ./app
COPY .streamlit ./.streamlit
# The verification key, if this build has one. It arrives only through the `license` build
# context: `.dockerignore` keeps every .pem out of the main one, so a key lying in a
# checkout can never be baked in by accident.
COPY --from=license . ./config/
COPY --chmod=0755 deploy/entrypoint.sh /usr/local/bin/wri-entrypoint

ENV PATH=/opt/wri/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    WRI_CONFIG_DIR=/app/config \
    WRI_DATA_DIR=/data \
    WRI_IMAGE_VERSION=${WRI_VERSION}

USER wri
VOLUME ["/data"]
EXPOSE 8000 8501
ENTRYPOINT ["wri-entrypoint"]
CMD ["api"]
