FROM postgres:16

# Install pgvector extension
RUN apt-get update && apt-get install -y postgresql-16-pgvector && \
    rm -rf /var/lib/apt/lists/*

# Copy init script
COPY init-pgvector.sh /docker-entrypoint-initdb.d/init-pgvector.sh
RUN chmod +x /docker-entrypoint-initdb.d/init-pgvector.sh