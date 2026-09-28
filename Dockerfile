# One image for local runs, SageMaker Processing and the live demo (Render).
FROM python:3.13-slim
RUN pip install --no-cache-dir uv==0.12.1  # from PyPI: one registry fewer to reach at build time

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

# Same layout as the S3 bucket: model v1, the sample month, the accuracy history for the dashboard.
# In AWS the inputs are overridden with --model/--movies/--consumption (S3 mounted as folders).
COPY models models
COPY data data
COPY output/performance output/performance
COPY src src
COPY .streamlit .streamlit

# Default = the UI on $PORT (hosts like Render set it; 7860 otherwise).
# Batch: `docker run <image> predict ...` (what SageMaker passes).
ENV PORT=7860
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["ui"]
