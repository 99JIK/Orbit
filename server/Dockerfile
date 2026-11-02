FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY orbit_server ./orbit_server
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 orbit \
    && mkdir /data && chown orbit /data

USER orbit
ENV ORBIT_HOME=/data
VOLUME /data
EXPOSE 8787

CMD ["orbit-server", "serve", "--host", "0.0.0.0", "--port", "8787"]
