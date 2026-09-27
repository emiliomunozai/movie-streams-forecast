# One image for local runs, SageMaker Processing and HF Spaces.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

# Default model + sample inputs; in AWS they are overridden with --model/--movies/--consumption.
COPY artifacts artifacts
COPY data/inference_*.csv data/
COPY src src

ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["predict"]
