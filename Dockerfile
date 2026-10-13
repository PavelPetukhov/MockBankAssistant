FROM python:3.13-slim

WORKDIR /app

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.4  # match the version that wrote poetry.lock

# Copy dependency manifests first for layer caching
COPY pyproject.toml poetry.lock* ./

# Install dependencies only (no dev deps, no venv — we're in a container). --no-root
# because the project source isn't copied yet; it runs straight from /app.
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-root --no-interaction --no-ansi

COPY . .

# One image, two roles (see docker-compose.yml): `python -m thirdparty.server` runs the
# mock sink on 8001; the default CMD runs the agent CLI.
EXPOSE 8001

CMD ["python", "run.py"]
