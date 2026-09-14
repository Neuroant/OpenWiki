# OpenWiki (owiki) — the tool image. It contains the Python app + its deps (PyMuPDF,
# NumPy, Kuzu) but NOT Ollama or any models: bring your own Ollama (on the host or a
# sibling container) and point `--host` at it, and mount a project's data as a volume.
#
#   docker build -t owiki .
#   docker run --rm owiki --version
#   # serve a project's wiki (Ollama on the host):
#   docker run --rm -p 8137:8137 -v "$PWD:/data" \
#     --add-host host.docker.internal:host-gateway owiki \
#     serve --bind 0.0.0.0 --port 8137 --wiki /data/output/wiki \
#     -i /data/output/index --graph /data/output/graph \
#     --host http://host.docker.internal:11434
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Targeted copy so the build context that matters is just the package + metadata
# (the committed sample PDF, tests, docs, and output/ never enter the image).
COPY pyproject.toml README.md ./
COPY openwiki ./openwiki
RUN pip install .

# Projects mount here; the CLI is the entrypoint, so `docker run owiki <subcommand> …`.
WORKDIR /data
EXPOSE 8000
ENTRYPOINT ["owiki"]
CMD ["--help"]
