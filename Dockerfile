# Build stage - uses Python to compile with PyInstaller
FROM 1202971/ubpy:3.13 AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY src/ ./src/

# Install dependencies and PyInstaller
RUN python3.13 -m ensurepip --upgrade && \
    python3.13 -m pip install --no-cache-dir -e . && \
    python3.13 -m pip install --no-cache-dir pyinstaller

# Build the standalone binary
RUN python3.13 -m PyInstaller \
    --name=migrator \
    --onefile \
    --console \
    --clean \
    --noconfirm \
    --paths=src \
    --hidden-import=typer \
    --hidden-import=rich \
    --hidden-import=pydantic \
    --hidden-import=httpx \
    --hidden-import=tenacity \
    --hidden-import=yaml \
    --hidden-import=click \
    --collect-all=typer \
    --collect-all=rich \
    src/main.py

# Verify the binary was created
RUN ls -la dist/ && ./dist/migrator --help

# Runtime stage - minimal image to just copy out the binary
FROM scratch AS export

# Copy the binary from the builder
COPY --from=builder /app/dist/migrator /migrator
