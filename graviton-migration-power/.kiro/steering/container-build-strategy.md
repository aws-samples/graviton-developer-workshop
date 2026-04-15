# Container Build Strategy

## AWS Recommended Approach
Native builds are AWS recommended for optimal Graviton performance.

## Prohibited (Performance Issues)
- QEMU emulation
- Docker buildx with emulation
- Cross-compilation without native hardware

## Required: Native Build Strategy
- Build ARM64 images on Graviton EC2 instances (native)
- Build AMD64 images on x86 EC2 instances (native)
- Use `arm64` compute type in CI/CD for ARM64 builds
- Separate build pipelines for each architecture

## Image Strategy
- Create architecture-specific images (not single multi-arch build)
- Push ARM64 image with `-arm64` tag
- Push AMD64 image with `-amd64` tag
- Create manifest file combining both architectures
- Push manifest to container registry as the main tag

## CI/CD Detection
- Scan repository for: buildspec.yaml, .gitlab-ci.yml, .github/workflows, Jenkinsfile, Dockerfile
- If CI/CD not found: Ask user about their build infrastructure
- Identify current compute types and recommend native alternatives
