# EmberForge site — deployable anywhere that runs containers.
FROM python:3.12-slim

WORKDIR /app

# No third-party dependencies: the server uses only the standard library.
COPY server.py index.html ./
RUN mkdir -p downloads uploads

ENV PORT=8080
EXPOSE 8080

# Bind 0.0.0.0 so the platform's router can reach it.
CMD ["sh", "-c", "python3 server.py --bind 0.0.0.0 --port ${PORT}"]
