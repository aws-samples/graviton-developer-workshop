---
name: "graviton-migration-power"
displayName: "Graviton Migration Power"
description: "Analyzes source code to identify compatibilities with Graviton processors(Arm64 architecture). Generates reports with incompatibilities and provides suggestions for minimal required and recommended versions for language runtimes and dependency libraries."
keywords: ["ec2", "graviton", "arm", "migration", "porting", "dependencies", "compatibilities", "arm64", "aarch64", "karpenter", "eks", "containers"]
author: "AWS"
---

# Graviton Migration Power

## Overview

The Graviton Migration Power helps developers migrate workloads to AWS Graviton processors (Arm64 architecture). It analyzes source code for known code patterns and dependency libraries to identify compatibilities with Graviton processors, generates reports highlighting detected compatibility issues (manual review recommended), and provides actionable suggestions for minimal required and recommended versions for both language runtimes and dependency libraries.

## CRITICAL: Mandatory Migration Process

**You MUST follow the steering files included with this power.** These contain AWS recommended guidelines for Graviton migration and define the required process for every migration assessment.

### Primary Controller

The file `graviton-migration-agent-controller.md` is the **main orchestration steering file**. It defines the end-to-end migration process across 5 mandatory phases:

1. **Phase 1: Repository Discovery** — Scan the repo, create `project_discovery.md`, halt for user review
2. **Phase 2: Infrastructure Analysis** — Identify deployment platform (EKS/ECS/EC2/Managed Services) and analyze accordingly
3. **Phase 3: Instance Mapping** — Map x86 instances to Graviton equivalents with user input
4. **Phase 4: Cost Analysis** — Fetch real-time pricing and generate cost savings report
5. **Phase 5: Migration Recommendations** — Generate recommendations, halt for user approval before any changes

**You MUST execute these phases in order. You MUST halt at every checkpoint marked with ⏸️. You MUST NOT make changes without explicit user approval.**

### Supporting Steering Files

The controller references these specialized steering files at specific phases. You MUST follow them when the controller directs you to:

- **`container-build-strategy.md`** — AWS recommended native build strategy for multi-arch containers. Referenced during Phase 2A (EKS) and Phase 2B (ECS). Key rules: native builds only (no QEMU emulation), architecture-specific images with manifest lists, native CI/CD compute types.

- **`karpenter-configuration.md`** — Complete Karpenter guidance for Graviton migration. Referenced during Phase 2A when Karpenter is detected. Covers NodePool configuration rules, "comparable instance" and "flexible instances" approaches, instance category requirements, generation constraints, x86-to-Graviton instance family mappings, gradual rollout strategy with dedicated Graviton NodePool using taints/tolerations, workload scheduling, and post-migration cleanup.

### How the Steering Files Work Together

```
graviton-migration-agent-controller.md  (main process — always followed)
  ├── Phase 2A/2B → container-build-strategy.md  (container build rules)
  └── Phase 2A    → karpenter-configuration.md   (NodePool config + migration rollout)
```

### MCP Servers

This power bundles four MCP servers. They are configured in `mcp.json` and start automatically when the power is activated.

#### arm-mcp (Docker-based)
The core migration assessment server. Provides:
- **migrate_ease_scan** — Scans codebases for Arm compatibility issues (C++, Python, Go, JS, Java)
- **skopeo** — Inspects container images remotely for architecture support
- **knowledge_base_search** — Searches Arm documentation for migration guidance
- **check_image** — Quick Docker image architecture verification
- **mca (Machine Code Analyzer)** — Analyzes assembly code performance predictions
- Requires Docker Desktop running locally

#### awslabs.terraform-mcp-server
Analyzes Terraform and Terragrunt infrastructure code. Used during Phase 2 to inspect instance types, Auto Scaling configurations, and infrastructure definitions. Also provides AWS provider documentation search for validating Graviton instance mappings.

#### awslabs.eks-mcp-server
Provides EKS cluster analysis capabilities. Used during Phase 2A to inspect Karpenter NodePools, EC2NodeClasses, and workload configurations. Most tools are disabled by default to keep the power focused on migration-relevant operations.

#### awslabs.aws-pricing-mcp-server
Fetches real-time AWS pricing data. Used during Phase 4 to compare x86 vs Graviton instance costs across On-Demand, Reserved, and Spot pricing models. Requires AWS credentials with pricing API access.

### When to Use Each Server

| Phase | MCP Server | Purpose |
|-------|-----------|---------|
| Phase 1: Discovery | arm-mcp | `migrate_ease_scan` on codebase |
| Phase 2: Infrastructure | arm-mcp | `check_image`, `skopeo` for container ARM64 support |
| Phase 2: Infrastructure | arm-mcp | `knowledge_base_search` for dependency compatibility |
| Phase 2: Infrastructure | terraform-mcp | Analyze Terraform/IaC for instance types and configs |
| Phase 2A: EKS | eks-mcp | Inspect Karpenter NodePools and workload configs |
| Phase 3: Instance Mapping | terraform-mcp | Validate Graviton instance equivalents via AWS docs |
| Phase 4: Cost Analysis | aws-pricing-mcp | Real-time x86 vs Graviton pricing comparison |

---

## Onboarding

### Prerequisites

Before using the Graviton Migration Power, ensure the following are installed and running:

#### Required Tools

1. **Docker Desktop**: Required for running the Arm MCP server and migration assessment tools
   - Verify installation: `docker --version`
   - Ensure Docker daemon is running: `docker ps`
   - **CRITICAL**: If Docker is not installed or not running, DO NOT proceed with migration assessment

2. **uv/uvx**: Required for running the AWS MCP servers (Terraform, EKS, Pricing)
   - Install: `pip install uv` or `brew install uv`
   - Verify installation: `uvx --version`

3. **AWS Credentials**: Required for EKS cluster analysis and pricing data
   - Ensure valid credentials are configured: `aws sts get-caller-identity`

4. **Git** (optional but recommended): For scanning remote repositories
   - Verify installation: `git --version`

### Validate MCP Server Connection

The power uses the Arm MCP server running in a Docker container. Test the connection:

```bash
# The MCP server should auto-start when you use the power
# If you encounter issues, verify Docker is running
docker ps
```

---

## Additional Resources

- AWS Graviton Technical Guide: https://github.com/aws/aws-graviton-getting-started
- Arm Architecture Reference: Available through knowledge base search
- Migration Tools Documentation: Included in MCP server responses

---

## Power Metadata

**Version**: 1.2
**Author**: AWS
**Supported Languages**: C++, Python, Go, JavaScript, Java
**Container Runtime**: Docker required
**MCP Servers**: arm-mcp (Docker-based), awslabs.terraform-mcp-server, awslabs.eks-mcp-server, awslabs.aws-pricing-mcp-server
**MCP Server Licenses**: arm-mcp — see: https://github.com/arm/mcp/blob/main/LICENSE
