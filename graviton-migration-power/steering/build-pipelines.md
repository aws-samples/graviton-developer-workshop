# Build Pipeline Migration to Graviton (ARM64)

This steering file guides the detection and migration of CI/CD build pipelines when moving workloads to AWS Graviton (ARM64) instances.

## Detection

When analyzing a workspace for build pipeline configurations, look for:

- CI/CD config files: `.github/workflows/*.yml`, `.gitlab-ci.yml`, `Jenkinsfile`, `buildspec.yml`, `Dockerfile`, `.circleci/config.yml`, `bitbucket-pipelines.yml`
- Build scripts referencing specific architectures (`x86_64`, `amd64`, `linux/amd64`)
- Docker build commands without `--platform` flags or with hardcoded `linux/amd64`
- Container image tags using architecture-specific suffixes (e.g., `:latest-amd64`)
- Build matrix configurations that only include x86 targets
- Terraform or CloudFormation templates provisioning build infrastructure on x86 instance types
- Makefile or shell scripts with architecture-conditional logic

## Migration Strategy

### 1. Multi-Architecture Container Builds

The most common change is updating Docker builds to produce multi-arch images. This ensures images work on both x86 and Graviton nodes during the migration period. Always use native ARM64 build runners rather than QEMU emulation, which is significantly slower (5-10x) and can produce unreliable builds.

#### Using Docker Buildx with Native Builders (GitHub Actions)

Use separate native runners for each architecture and merge the results into a single multi-arch manifest:

```yaml
jobs:
  build-amd64:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64
          push: true
          tags: ${{ env.REGISTRY }}/${{ env.IMAGE }}:${{ env.TAG }}-amd64

  build-arm64:
    runs-on: [self-hosted, linux, arm64]
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/arm64
          push: true
          tags: ${{ env.REGISTRY }}/${{ env.IMAGE }}:${{ env.TAG }}-arm64

  manifest:
    needs: [build-amd64, build-arm64]
    runs-on: ubuntu-latest
    steps:
      - uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
      - run: |
          docker manifest create ${{ env.REGISTRY }}/${{ env.IMAGE }}:${{ env.TAG }} \
            ${{ env.REGISTRY }}/${{ env.IMAGE }}:${{ env.TAG }}-amd64 \
            ${{ env.REGISTRY }}/${{ env.IMAGE }}:${{ env.TAG }}-arm64
          docker manifest push ${{ env.REGISTRY }}/${{ env.IMAGE }}:${{ env.TAG }}
```

#### Using Docker Buildx with Remote Native Builders (CLI / Jenkinsfile / Shell Scripts)

Create a buildx builder that uses remote native nodes instead of emulation:

```bash
# Create a builder using remote native ARM64 and x86 Docker hosts
docker buildx create --name multiarch-builder --platform linux/amd64 --node builder-amd64 unix:///var/run/docker.sock
docker buildx create --name multiarch-builder --append --platform linux/arm64 --node builder-arm64 ssh://user@arm64-host
docker buildx use multiarch-builder

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag ${REGISTRY}/${IMAGE}:${TAG} \
  --push .
```

#### Using AWS CodeBuild

Use a Graviton-based CodeBuild environment for native ARM64 builds:

```json
{
  "environment": {
    "type": "ARM_CONTAINER",
    "image": "aws/codebuild/amazonlinux2-aarch64-standard:3.0",
    "computeType": "BUILD_GENERAL1_LARGE"
  }
}
```

For multi-arch images, use separate CodeBuild projects (one x86, one Graviton) and combine with a manifest creation step:

```yaml
version: 0.2
env:
  variables:
    IMAGE_REPO: "my-repo"
phases:
  pre_build:
    commands:
      - aws ecr get-login-password | docker login --username AWS --password-stdin ${ECR_REGISTRY}
  build:
    commands:
      - docker build --tag ${ECR_REGISTRY}/${IMAGE_REPO}:${IMAGE_TAG}-arm64 .
      - docker push ${ECR_REGISTRY}/${IMAGE_REPO}:${IMAGE_TAG}-arm64
```

### 2. Dockerfile Adjustments

Ensure Dockerfiles are architecture-agnostic or handle multi-arch properly.

#### Use Multi-Arch Base Images

Prefer official images that already support multiple architectures:

```dockerfile
# Good: official images are typically multi-arch
FROM python:3.12-slim
FROM node:20-alpine
FROM amazoncorretto:21

# Avoid: architecture-specific tags
# FROM amd64/python:3.12-slim
```

#### Handle Architecture-Specific Dependencies

When a Dockerfile installs native binaries or libraries, use build args or runtime detection:

```dockerfile
ARG TARGETARCH
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      curl -L https://example.com/tool-arm64.tar.gz -o tool.tar.gz; \
    else \
      curl -L https://example.com/tool-amd64.tar.gz -o tool.tar.gz; \
    fi && \
    tar -xzf tool.tar.gz -C /usr/local/bin/
```

Docker Buildx automatically sets `TARGETARCH` (`amd64` or `arm64`) during multi-platform builds.

### 3. Build Agent / Runner Configuration

If your CI system uses self-hosted runners or build agents, you may need ARM64 runners.

#### GitHub Actions (Self-Hosted ARM64 Runner)

```yaml
jobs:
  build-arm64:
    runs-on: [self-hosted, linux, arm64]
    steps:
      - uses: actions/checkout@v4
      - run: make build
```

#### GitLab CI (ARM64 Runner Tag)

```yaml
build:
  tags:
    - arm64
  script:
    - make build
```

#### Jenkins (ARM64 Agent Label)

```groovy
pipeline {
  agent { label 'arm64' }
  stages {
    stage('Build') {
      steps {
        sh 'make build'
      }
    }
  }
}
```

### 4. Build Matrix for Dual-Architecture Testing

During migration, run tests on both architectures to catch issues early.

#### GitHub Actions Matrix

```yaml
jobs:
  test:
    strategy:
      matrix:
        arch: [amd64, arm64]
        include:
          - arch: amd64
            runner: ubuntu-latest
          - arch: arm64
            runner: [self-hosted, linux, arm64]
    runs-on: ${{ matrix.runner }}
    steps:
      - uses: actions/checkout@v4
      - run: make test
```

### 5. Native Dependency and Compilation Flags

When build scripts compile native code (C/C++, Rust, Go CGO), check for:

- Hardcoded `-march=x86-64` or `-mtune` flags targeting x86 microarchitectures
- x86-specific SIMD intrinsics (`SSE`, `AVX`) that need ARM equivalents (`NEON`, `SVE`)
- Assembly files (`.s`, `.asm`) with x86 instructions
- Go builds with `GOARCH=amd64` hardcoded

#### Go (Native Build on ARM64 Runner)

Build natively on an ARM64 runner for best performance:

```bash
# On an ARM64 runner, GOARCH defaults to arm64
go build -o app
```

For pipelines that still need both architectures, build each on its native runner:

```bash
# On x86 runner
GOOS=linux GOARCH=amd64 go build -o app-amd64

# On ARM64 runner
GOOS=linux GOARCH=arm64 go build -o app-arm64
```

#### Rust (Native Build on ARM64 Runner)

Build natively on an ARM64 runner rather than cross-compiling:

```bash
# On an ARM64 runner, the default target is aarch64
cargo build --release
```

### 6. Post-Migration Cleanup

After all workloads are running on Graviton:

- Remove `linux/amd64` from multi-platform build targets if x86 is no longer needed
- Remove architecture-conditional logic from Dockerfiles and build scripts
- Update build infrastructure (CodeBuild projects, self-hosted runners) to use Graviton instance types for cost savings
- Consolidate separate per-architecture build jobs into single ARM64-native jobs

## Key Checks

- Verify all base images in Dockerfiles support `linux/arm64` using the `check_image` or `skopeo` tools
- Run `migrate_ease_scan` on application source to detect architecture-specific code patterns
- Search the knowledge base for any native dependencies that may lack ARM64 support
- Test build output on ARM64 before deploying to production
- Check that build caches are separated by architecture to avoid cross-arch cache poisoning

## Common Pitfalls

| Issue | Symptom | Fix |
|-------|---------|-----|
| Missing multi-arch manifest | `exec format error` at runtime | Build per-arch on native runners and create a manifest |
| x86-only binary downloads in Dockerfile | Build fails on ARM64 | Use `TARGETARCH` to select correct binary |
| Hardcoded `amd64` in image tags | Wrong image pulled on Graviton nodes | Use multi-arch tags or manifest lists |
| Native extensions not compiled for ARM64 | Import errors or segfaults | Rebuild native deps on ARM64 build runner |
| Shared build cache across architectures | Corrupt or wrong-arch artifacts | Separate build caches by architecture |

## References

- [AWS Graviton Getting Started — CI/CD](https://github.com/aws/aws-graviton-getting-started/blob/main/containers.md)
- [Docker Multi-Platform Builds](https://docs.docker.com/build/building/multi-platform/)
- [AWS CodeBuild ARM Support](https://docs.aws.amazon.com/codebuild/latest/userguide/build-env-ref-compute-types.html)
- [GitHub Actions Runner Images](https://github.com/actions/runner-images)
