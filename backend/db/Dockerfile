# PostgreSQL 18 (Alpine, as before) with the pgvector extension for
# semantic search. Built on the same base image so existing data volumes
# keep working unchanged.
FROM postgres:18-alpine

ARG PGVECTOR_VERSION=v0.8.7

RUN apk add --no-cache --virtual .build-deps git build-base \
    && git clone --branch "${PGVECTOR_VERSION}" --depth 1 \
       https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    # Portable build (no CPU-specific flags) without LLVM JIT bitcode.
    && make OPTFLAGS="" with_llvm=no \
    && make install with_llvm=no \
    && cd / && rm -rf /tmp/pgvector \
    && apk del .build-deps
