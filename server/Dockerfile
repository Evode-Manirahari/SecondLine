FROM dailyco/pipecat-base:latest

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies from the lockfile
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Application code — the full SecondLine agent + business brain.
COPY ./bot.py bot.py
COPY ./agent.py agent.py
COPY ./backend.py backend.py
COPY ./policy.py policy.py
COPY ./sms.py sms.py
COPY ./nemotron_llm.py nemotron_llm.py
COPY ./nvidia_stt.py nvidia_stt.py
COPY ./agent_policy.json agent_policy.json
