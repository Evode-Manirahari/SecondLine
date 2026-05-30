FROM dailyco/pipecat-base:latest

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies. Resolves from pyproject at build time so no committed
# uv.lock is required; run `uv sync` locally if you want a pinned lockfile.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-install-project --no-dev

# Application code — the full SecondLine agent + business brain.
COPY ./bot.py bot.py
COPY ./agent.py agent.py
COPY ./backend.py backend.py
COPY ./policy.py policy.py
COPY ./sms.py sms.py
COPY ./nemotron_llm.py nemotron_llm.py
COPY ./nvidia_stt.py nvidia_stt.py
COPY ./agent_policy.json agent_policy.json
