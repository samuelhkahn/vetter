FROM python:3.12-slim-trixie 
# install binaries
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
# run everyhing in app
WORKDIR /app
# only true dependencies are installed
COPY pyproject.toml uv.lock README.md ./
#install dependencies with uv
RUN uv sync --no-dev --frozen --no-install-project
COPY src ./src
RUN uv sync --no-dev --frozen
#Default command: import vetter and print ok.
CMD ["uv", "run", "python",  "-c", "import vetter; print('ok')"]