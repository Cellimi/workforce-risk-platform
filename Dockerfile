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

# The base image, fixed to one exact build of Python 3.12 on Debian. A tag such as
# `3.12-slim` moves whenever it is rebuilt; the digest does not, so two builds of the same
# commit start from the same bytes. Refresh it deliberately, before a release, to take in
# operating-system security fixes:
#   docker pull python:3.12-slim
#   docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}'
ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# ---------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build
WORKDIR /src
# Every third-party library at the exact version in requirements.lock, and each download
# checked against the fingerprint recorded there. A library that is not in the lock, or
# whose file has changed since the lock was made, stops the build.
COPY requirements.lock ./
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    python -m venv /opt/wri \
    && /opt/wri/bin/pip install --no-cache-dir --disable-pip-version-check \
       --require-hashes -r requirements.lock
# Then the product itself, with nothing allowed to come along with it. `pip check` stops
# the build if the lock is missing something the product declares it needs. pip is removed
# last: nothing installs anything into a running installation.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    /opt/wri/bin/pip install --no-cache-dir --disable-pip-version-check --no-deps . \
    && /opt/wri/bin/pip check \
    && /opt/wri/bin/pip freeze --exclude wri-engine > /opt/wri/requirements.lock \
    && /opt/wri/bin/pip uninstall --yes --quiet pip

# ---------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
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
