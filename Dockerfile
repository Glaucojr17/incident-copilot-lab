FROM python:3.12-slim
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app app
COPY incident_copilot/ ./incident_copilot/
USER app
EXPOSE 8080
CMD ["python", "-m", "incident_copilot.cli", "serve", "--host", "0.0.0.0"]

