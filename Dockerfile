# One image for local runs, SageMaker Processing and HF Spaces.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

# Default model + sample inputs (+ training inputs as the drift reference for the UI).
# In AWS they are overridden with --model/--movies/--consumption.
COPY artifacts artifacts
COPY data data
COPY src src

# Default = the UI on 7860 (HF Spaces). Batch: `docker run <image> predict ...` (what SageMaker passes).
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["ui", "--port", "7860"]
