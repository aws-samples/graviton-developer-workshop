# Graviton Migration Report

## Summary

The **java-test** project is a Spring Boot 3.2.2 application (Java 17) that uses native compression libraries, embedded key-value stores, and Netty native transport. The scan identified **5 total issues**: 2 critical Docker image incompatibilities, 2 dependency incompatibilities detected by the migrate-ease scanner, and 1 Netty classifier issue detected via manual inspection. The Java source code (4 files, 162 LOC) is architecture-neutral with no native calls or platform-specific code.

## Docker Images

| Image | Source File | ARM64 Support | Action Required |
|-------|------------|---------------|-----------------|
| `eclipse-temurin:17-jdk-alpine` | Dockerfile (build stage) | ❌ | Replace with `eclipse-temurin:17-jdk` |
| `eclipse-temurin:17-jre-alpine` | Dockerfile (runtime stage) | ❌ | Replace with `eclipse-temurin:17-jre` |

## Dependencies

| Package | Version | File | ARM64 Compatible | Action |
|---------|---------|------|------------------|--------|
| `spring-boot-starter-web` | 3.2.2 | pom.xml | ✅ | None |
| `snappy-java` | 1.1.10.5 | pom.xml | ✅ | None |
| `lz4-java` | 1.8.0 | pom.xml | ✅ | None |
| `h2` | (managed) | pom.xml | ✅ | None |
| `jackson-databind` | (managed) | pom.xml | ✅ | None |
| `spring-boot-starter-data-jpa` | (managed) | pom.xml | ✅ | None |
| `netty-transport-native-epoll` | (managed) | pom.xml:59 | ❌ | Add `linux-aarch_64` classifier |
| `rocksdbjni` | 5.18.4 | pom.xml:65 | ❌ | Upgrade to 6.2.2+ |
| `leveldbjni-all` | 1.8 | pom.xml:71 | ❌ | Remove or replace with pure Java alternative |

## Source Code Scan Results

**Scanner used**: Java (migrate-ease)
**Architecture target**: armv8-a
**Files scanned**: 5 (1 pom.xml + 4 .java source files)
**Lines of code**: 162 (Java) + 85 (POM)
**Issues found**: 2 PomIssues, 0 JavaSourceIssues, 0 JarIssues

### Issue 1: rocksdbjni 5.18.4 (pom.xml, line 65)

**Severity**: Critical
**Type**: PomIssue — artifact does not support target architecture

RocksDB JNI provides a Java interface to the RocksDB C++ library. Version 5.18.4 only ships x86_64 native libraries. The minimum version with aarch64 support is **v6.2.2**.

**Fix**: Upgrade to a recent stable release:
```xml
<dependency>
    <groupId>org.rocksdb</groupId>
    <artifactId>rocksdbjni</artifactId>
    <version>9.7.3</version>
</dependency>
```

### Issue 2: leveldbjni-all 1.8 (pom.xml, line 71)

**Severity**: Critical
**Type**: PomIssue — artifact does not support target architecture

LevelDB JNI provides a Java interface to the LevelDB C++ library. It does not provide an official release for aarch64.

**Fix options**:
1. Build from source using [this commit](https://github.com/fusesource/leveldbjni/commit/e401863b264652a665d402d176b30b56166ef6f1)
2. Replace with a pure Java alternative (e.g., [leveldb-java by dain](https://github.com/dain/leveldb))
3. Remove if not actively used

### Issue 3: netty-transport-native-epoll with x86_64-only classifier (pom.xml, line 59)

**Severity**: Critical
**Type**: Architecture-specific classifier (not flagged by scanner — detected via manual review)

The dependency specifies only `linux-x86_64` classifier, meaning the native epoll transport will not load on ARM64.

**Fix**: Add the aarch64 classifier alongside the existing one:
```xml
<dependency>
    <groupId>io.netty</groupId>
    <artifactId>netty-transport-native-epoll</artifactId>
    <classifier>linux-x86_64</classifier>
</dependency>
<dependency>
    <groupId>io.netty</groupId>
    <artifactId>netty-transport-native-epoll</artifactId>
    <classifier>linux-aarch_64</classifier>
</dependency>
```

## Recommendations (Priority Order)

1. **[Critical]** Replace Docker base images: `eclipse-temurin:17-jdk-alpine` → `eclipse-temurin:17-jdk`, `eclipse-temurin:17-jre-alpine` → `eclipse-temurin:17-jre`
2. **[Critical]** Add Netty `linux-aarch_64` classifier for native epoll transport
3. **[Critical]** Upgrade `rocksdbjni` from 5.18.4 to 6.2.2+ (recommend latest stable)
4. **[Critical]** Replace `leveldbjni-all` with a pure Java LevelDB implementation or remove
5. **[Info]** Update Dockerfile `RUN apk add --no-cache maven` to `RUN apt-get update && apt-get install -y maven` (non-alpine base uses apt)

## Dockerfile Changes

### Current
```dockerfile
FROM eclipse-temurin:17-jdk-alpine AS build
WORKDIR /app
COPY pom.xml .
COPY src ./src
RUN apk add --no-cache maven && mvn clean package -DskipTests

FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY --from=build /app/target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
```

### Recommended
```dockerfile
FROM eclipse-temurin:17-jdk AS build
WORKDIR /app
COPY pom.xml .
COPY src ./src
RUN apt-get update && apt-get install -y maven && mvn clean package -DskipTests

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /app/target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
```

## Build Instructions

### Multi-Architecture Docker Build
```bash
docker buildx create --name multiarch --use --bootstrap
docker buildx build -t your-registry/migration-test:latest \
  --platform linux/amd64,linux/arm64 \
  --push .
```

### Verify Image Architectures
```bash
docker manifest inspect your-registry/migration-test:latest
```

## Scan Metadata

- **Tool**: migrate-ease Java scanner
- **Target architecture**: armv8-a (AWS Graviton)
- **Scan date**: 2026-07-16
- **Scanner checkpoint database**: check_points.yaml
