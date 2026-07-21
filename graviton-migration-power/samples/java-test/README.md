# Graviton Migration Test Project

A Spring Boot application designed to test AWS Graviton migration evaluation. This project intentionally includes a mix of ARM64-compatible and ARM64-incompatible dependencies to validate the migration power's detection accuracy.

## Features

- **Native Compression Libraries**: Snappy and LZ4 with native bindings
- **Embedded Key-Value Stores**: RocksDB and LevelDB with native JNI
- **Netty Native Transport**: Uses platform-specific native libraries
- **REST API**: Endpoints to test compression performance
- **Benchmark Suite**: Compare performance across architectures

## Dependencies

### Compatible with ARM64 ✅

| Dependency | Version | Reason |
|-----------|---------|--------|
| snappy-java | 1.1.10.5 | Auto-detects ARM64, ships aarch64 native libs since 1.1.4 |
| lz4-java | 1.8.0 | Pure Java fallback, ARM64 native available |
| H2 Database | (managed) | Pure Java, fully portable |
| Jackson | (managed) | Pure Java, no native code |
| Spring Boot | 3.2.2 | Pure Java framework |

### Incompatible with ARM64 ❌

| Dependency | Version | Issue |
|-----------|---------|-------|
| netty-transport-native-epoll | (managed) | Only `linux-x86_64` classifier specified, missing `linux-aarch_64` |
| rocksdbjni | 5.18.4 | Only ships x86_64 native `.so` — ARM64 support added in 6.0+ |
| leveldbjni-all | 1.8 | Bundles only x86_64 native binaries, no ARM64 build available |

### Docker Image Issues ❌

| Image | Issue |
|-------|-------|
| eclipse-temurin:17-jdk-alpine | x86_64 only, no ARM64 variant |
| eclipse-temurin:17-jre-alpine | x86_64 only, no ARM64 variant |

## Expected Migration Fixes

A correct migration analysis should recommend:

1. **Dockerfile**: Replace `eclipse-temurin:17-jdk-alpine` → `eclipse-temurin:17-jdk` (multi-arch)
2. **Netty**: Add `linux-aarch_64` classifier alongside `linux-x86_64`
3. **RocksDB**: Upgrade from 5.18.4 → 7.x+ (ships ARM64 native libs)
4. **LevelDB JNI**: Replace with `leveldbjni` 1.8+ built for ARM64, or switch to a pure Java alternative like MapDB
5. **Snappy/LZ4/H2/Jackson**: No changes needed (already compatible)

## API Endpoints

### Health Check
```bash
curl http://localhost:8080/api/health
```

### Compress with Snappy
```bash
curl -X POST http://localhost:8080/api/compress/snappy \
  -H "Content-Type: application/json" \
  -d '{"data":"Your data to compress here"}'
```

### Compress with LZ4
```bash
curl -X POST http://localhost:8080/api/compress/lz4 \
  -H "Content-Type: application/json" \
  -d '{"data":"Your data to compress here"}'
```

### Run Benchmark
```bash
curl http://localhost:8080/api/benchmark
```

## Build and Run

### Local Development
```bash
mvn clean package
java -jar target/migration-test-1.0.0.jar
```

### Docker (x86 only — will fail on ARM64)
```bash
docker build -t graviton-test:x86 .
docker run -p 8080:8080 graviton-test:x86
```
