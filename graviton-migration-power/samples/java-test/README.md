# Graviton Migration Test Project

A Spring Boot application designed to test AWS Graviton migration with native x86 dependencies.

## Features

- **Native Compression Libraries**: Snappy and LZ4 with x86_64 native bindings
- **Netty Native Transport**: Uses platform-specific native libraries
- **REST API**: Endpoints to test compression performance
- **Benchmark Suite**: Compare performance across architectures

## Dependencies with Native Components

1. **snappy-java** (1.1.10.5) - Native compression library
2. **lz4-java** (1.8.0) - Native compression library  
3. **netty-transport-native-epoll** - Linux x86_64 native transport

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

### Docker (x86)
```bash
docker build -t graviton-test:x86 .
docker run -p 8080:8080 graviton-test:x86
```

## Migration Testing Workflow

1. **Baseline on x86**: Build and benchmark on x86_64
2. **Analyze Dependencies**: Use Kiro Power to identify architecture-specific dependencies
3. **Update for ARM64**: Modify pom.xml to include ARM64 native libraries
4. **Build for Graviton**: Create ARM64 Docker image
5. **Compare Performance**: Run benchmarks on both architectures

## Migration Changes Applied ✓

The following changes have been made for Graviton (ARM64) compatibility:

### Docker Images
- **Before**: `eclipse-temurin:17-jdk-alpine` and `eclipse-temurin:17-jre-alpine` (x86 only)
- **After**: `eclipse-temurin:17-jdk` and `eclipse-temurin:17-jre` (multi-arch support)

### Dependencies
- **Netty Native Transport**: Added `linux-aarch_64` classifier alongside `linux-x86_64`
  - Both classifiers are now included for multi-architecture support
- **Snappy-java (1.1.10.5)**: Compatible - auto-detects ARM64 ✓
- **LZ4-java (1.8.0)**: Compatible - auto-detects ARM64 ✓

### Build Instructions

#### Multi-Architecture Docker Build
```bash
# Build for both x86 and ARM64
docker buildx create --name multiarch --use --bootstrap
docker buildx build -t graviton-test:latest \
  --platform linux/amd64,linux/arm64 \
  --push .
```

#### Architecture-Specific Builds
```bash
# For x86_64
docker build -t graviton-test:x86 .

# For ARM64/Graviton
docker build -t graviton-test:arm64 --platform linux/arm64 .
```

## Performance Metrics

The benchmark endpoint measures:
- Compression throughput (ops/sec)
- Latency (ms per operation)
- CPU architecture detection
- Compression ratios

Compare these metrics between x86 and Graviton instances to validate migration success.
