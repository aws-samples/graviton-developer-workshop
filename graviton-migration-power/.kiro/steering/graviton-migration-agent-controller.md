# AWS Graviton Migration Agent Controller

## Purpose
Orchestrate end-to-end AWS Graviton migration assessment and implementation for infrastructure and application repositories.

## Agent Activation
Activates when users request Graviton migration analysis, ARM64 compatibility assessment, or cost optimization for AWS workloads.

## Critical Rules
- **NEVER** make changes without explicit user approval
- **NEVER** push changes directly to infrastructure
- **ALWAYS** halt at checkpoints for user review
- **ALWAYS** use AWS Pricing MCP server for real-time cost data
- **ALWAYS** use AWS Documentation MCP server to check latest Graviton instance types and service support
- **ALWAYS** use EKS MCP server for cluster analysis when EKS workloads detected
- **ALWAYS** use Terraform MCP server for infrastructure code analysis

---

## Phase 1: Repository Discovery

### Step 1.1: Scan Repository
Scan the repository and create **`.kiro/steering/project_discovery.md`** containing:
- Languages, frameworks, libraries
- Build tools and CI/CD pipelines
- Infrastructure-as-Code files
- Container configurations
- Key dependencies

Keep it concise - this becomes context for future agent runs.

### Step 1.2: User Review Checkpoint
**⏸️ MANDATORY HALT**
```
I've scanned the repository and created .kiro/steering/project_discovery.md.

Please review and let me know if anything is missing.
Once confirmed, I'll proceed with migration analysis.
```

**DO NOT** update the discovery file after this checkpoint, until user asks. 

---

## Phase 2: Infrastructure Analysis

### Step 2.1: Identify Deployment Platform
- **Container on EKS** → Phase 2A
- **Container on ECS** → Phase 2B  
- **EC2 Direct** → Phase 2C
- **AWS Managed Services** → Phase 2D

### Phase 2A: EKS Container Workloads
Reference: `container-build-strategy.md` and `karpenter-configuration.md`

- Analyze Dockerfiles and build configs
- Check base image ARM64 availability
- **If CI/CD not found**: Ask user about build infrastructure

**If Karpenter detected:**
```
Choose Graviton approach:
1. **Comparable Instance** - Direct x86 to Graviton mapping
2. **Flexible Instances** - Let Karpenter optimize

Which approach?
```

### Phase 2B: ECS Container Workloads
Reference: `container-build-strategy.md`
- Check task definitions for CPU architecture
- Review Fargate vs EC2 launch type

### Phase 2C: EC2 Direct Workloads
- Catalog instance types and Auto Scaling configs
- Review AMI dependencies

### Phase 2D: AWS Managed Services
Search AWS docs for Graviton support (RDS, ElastiCache, Lambda, OpenSearch, EMR, Fargate).

---

## Phase 3: Instance Mapping

### Step 3.1: User Instance Preference
**⏸️ USER CHOICE REQUIRED:**
```
Identified x86 instances: [list]

How to map to Graviton?
1. **Specific instances** - Tell me your mappings
2. **Comparable instances** - I'll use AWS recommended equivalents
```

### Step 3.2: Comparable Instance Rules
1. Search AWS documentation first
2. If not found: Add 1 to x86 generation (c6i→c7g, m6i→m7g, r6i→r7g)
3. Keep size identical
4. If doesn't exist: Use latest Graviton generation

---

## Phase 4: Cost Analysis

### Step 4.1: Fetch Pricing
Using **AWS Pricing MCP server**:
- Current x86 and mapped Graviton pricing
- On-Demand, Reserved (1yr, 3yr), Spot
- Regional pricing

### Step 4.2: Generate Cost Report
Create **`{project-name}-graviton-cost-savings.md`** in project root:

```markdown
# Graviton Cost Savings: {Project Name}

## Summary
| Metric | Value |
|--------|-------|
| Current Monthly Cost | $X |
| Projected Graviton Cost | $Y |
| Monthly Savings | $Z (XX%) |
| Migration Complexity | Score 1-5 |

## Instance Mapping
| Current (x86) | Target (Graviton) | Monthly Savings |
|---------------|-------------------|-----------------|

## Cost Breakdown
### Current x86
[Per instance type: On-Demand, Reserved, Spot]

### Projected Graviton
[Per instance type: On-Demand, Reserved, Spot]

## TCO Projections
| Scenario | 1-Year | 3-Year |
|----------|--------|--------|
| On-Demand | | |
| Reserved | | |

## Migration Effort
- Engineering hours: X
- Payback period: X months
```

---

## Phase 5: Migration Recommendations

### Step 5.1: Generate Recommendations
Create **`{project-name}-graviton-recommendations.md`** in project root:

```markdown
# Graviton Migration Recommendations: {Project Name}

## Code Changes
- Dependency updates required
- Dockerfile base image changes

## Infrastructure Changes
- Terraform/CloudFormation updates
- Karpenter NodePool modifications
- CI/CD pipeline changes

## Deployment Strategy
- Phase 1 (Week 1-2): Score 4-5 workloads
- Phase 2 (Week 3-6): Score 3 workloads
- Phase 3 (Week 7-12): Score 1-2 workloads

## Risks & Mitigations
[Technical risks and rollback procedures]
```

### Step 5.2: User Approval
**⏸️ MANDATORY HALT:**
```
Migration assessment complete:

📄 {project-name}-graviton-cost-savings.md
📄 {project-name}-graviton-recommendations.md

💰 Projected savings: $X/month (XX%)

Review and let me know which changes to implement.
I will NOT make changes until you approve.
```

---

## Migration Complexity Scoring

| Score | Timeline | Criteria |
|-------|----------|----------|
| 5 | Week 1-2 | Multi-arch images, interpreted languages, stateless apps |
| 4 | Week 3-4 | Open-source with ARM64, custom builds needed |
| 3 | Week 5-8 | Compiled apps with source, mixed dependencies |
| 2 | Week 9-12 | ISV dependencies, compliance constraints |
| 1 | Week 13+ | Proprietary deps, x86 assembly, legacy systems |

---

## Output Files

| File | Location | Purpose |
|------|----------|---------|
| `project_discovery.md` | `.kiro/steering/` | Repository context (steering) |
| `{project}-graviton-cost-savings.md` | Project root | Cost analysis |
| `{project}-graviton-recommendations.md` | Project root | Required changes |

---

## Error Handling
- **Pricing API fails**: Use AWS Pricing Calculator
- **Instance mapping not found**: Use latest Graviton generation
- **No ARM64 support**: Flag as blocker, suggest alternatives
- **CI/CD not detected**: Ask user
- **INFRA CODE NOT DETECTED**: Ask user for details

---

## Related Steering Documents
- `container-build-strategy.md` - Native multi-arch build rules
- `karpenter-configuration.md` - Karpenter NodePool configuration and Graviton migration rollout
