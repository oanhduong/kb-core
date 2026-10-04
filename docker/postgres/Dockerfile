# Postgres 16 + pgvector (image chính thức pgvector) + Apache AGE build từ source (tag cố định) —
# 1 hệ Postgres duy nhất cho KB, vector, graph và Langfuse.
FROM pgvector/pgvector:pg16

ARG AGE_REF=PG16/v1.6.0-rc0
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential git ca-certificates bison flex postgresql-server-dev-16; \
    git clone --depth 1 --branch "${AGE_REF}" https://github.com/apache/age.git /tmp/age; \
    cd /tmp/age && make && make install; \
    rm -rf /tmp/age; \
    apt-get purge -y build-essential git bison flex postgresql-server-dev-16; \
    apt-get autoremove -y; rm -rf /var/lib/apt/lists/*

# AGE cần được nạp sẵn để mọi session dùng được cypher() sau LOAD 'age'
RUN echo "shared_preload_libraries = 'age'" >> /usr/share/postgresql/postgresql.conf.sample

COPY init/ /docker-entrypoint-initdb.d/
