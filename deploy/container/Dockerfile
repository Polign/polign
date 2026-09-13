# Binaries are compiled in the private server release pipeline. This packaging
# context contains only the public, verified release binaries and license.
FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
ARG TARGETARCH
ARG VERSION
LABEL org.opencontainers.image.title="Polign" \
      org.opencontainers.image.source="https://github.com/Polign/polign" \
      org.opencontainers.image.version="${VERSION}"
COPY --chown=65532:65532 bin/linux/${TARGETARCH}/polign-server /usr/local/bin/polign-server
COPY --chown=65532:65532 bin/linux/${TARGETARCH}/polign-apikey /usr/local/bin/polign-apikey
COPY --chown=65532:65532 bin/linux/${TARGETARCH}/polign /usr/local/bin/polign
COPY bin/LICENSE /LICENSE
USER 65532:65532
EXPOSE 23000 23001
ENTRYPOINT ["/usr/local/bin/polign-server"]
