# One image for local runs, SageMaker Processing and the live demo (Render).
FROM python:3.13-slim
RUN pip install --no-cache-dir uv==0.12.1  # from PyPI: one registry fewer to reach at build time

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

# Default model + sample inputs (training data stays out: the UI uses artifacts/drift_reference.json).
# In AWS they are overridden with --model/--movies/--consumption.
COPY artifacts artifacts
COPY data data
COPY src src
COPY .streamlit .streamlit

# Default = the UI on $PORT (hosts like Render set it; 7860 otherwise).
# Batch: `docker run <image> predict ...` (what SageMaker passes).
ENV PORT=7860
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["ui"]
