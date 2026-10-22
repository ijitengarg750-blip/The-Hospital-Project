FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends novnc websockify \
    && rm -rf /var/lib/apt/lists/*
USER nobody
EXPOSE 6080
ENTRYPOINT ["websockify", "--web=/usr/share/novnc/", "6080"]
