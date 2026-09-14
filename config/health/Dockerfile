# Static HTTP probe for upstream images without curl/wget or a shell.
ARG BASE_IMAGE
FROM busybox:1.37.0-musl AS probe
FROM ${BASE_IMAGE}
COPY --from=probe /bin/busybox /usr/local/bin/busybox
USER 10001:10001
