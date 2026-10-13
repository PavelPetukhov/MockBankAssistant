FROM python:3.13-slim

WORKDIR /app

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.3

# Copy dependency manifests first for layer caching
COPY pyproject.toml poetry.lock* ./

# Install dependencies (no dev deps, no venv — we're in a container)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

COPY . .

# Third-party mock service (port 8001) and agent CLI run on the same container.
# Override CMD to run just the mock server if you need it standalone.
EXPOSE 8001

CMD ["python", "run.py"]
