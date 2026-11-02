FROM python:3.13.18-alpine3.24@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8
RUN apk add --no-cache openssh-client-default && addgroup -g 10001 collector && adduser -D -u 10001 -G collector collector
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["python3", "/app/nas_nfs_collector.py"]
