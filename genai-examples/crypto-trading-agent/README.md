# Crypto Trading Agent on Amazon EKS — Implementation Guide

## Overview

Build a production-grade crypto trading assistant using [Strands Agents SDK](https://github.com/strands-agents/sdk-python) on Amazon EKS. The agent performs portfolio analysis, options pricing, and risk calculations in air-gapped gVisor sandboxes — combining LLM reasoning with safe code execution.

**Key components:**

| Component | Role |
|-----------|------|
| **Strands Agents SDK** | Orchestrates the trading assistant's reasoning loop |
| **agentgateway** | Unified gateway for LLM (Bedrock) and MCP tool calls |
| **gVisor (runsc)** | Kernel-level sandbox isolation for untrusted code |
| **Agent Sandbox Controller** | Manages sandbox lifecycle, warm pools, and claims |
| **Amazon Cognito** | Per-user, per-tool authorization via JWT |
| **Karpenter** | Right-sizes Graviton nodes for sandbox workloads |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Amazon EKS 1.32 (Graviton arm64)                  │
│                                                                             │
│  ┌──────────┐    ┌─────────────┐    ┌──────────────────────────────────┐   │
│  │  Chat UI │───▶│Strands Agent│───▶│         agentgateway             │   │
│  └──────────┘    └─────────────┘    │  ┌────────────┐ ┌─────────────┐ │   │
│       ▲                             │  │ LLM Route  │ │  MCP Route  │ │   │
│       │                             │  └─────┬──────┘ └──────┬──────┘ │   │
│       │                             └────────┼───────────────┼────────┘   │
│       │                                      │               │            │
│  ┌────┴─────┐                       ┌────────▼──────┐  ┌────▼────────┐   │
│  │ Cognito  │                       │Amazon Bedrock │  │ MCP Servers │   │
│  │(JWT Auth)│                       │(Claude Sonnet)│  │             │   │
│  └──────────┘                       └───────────────┘  │ ┌─────────┐ │   │
│                                                        │ │ trading │ │   │
│  ┌───────────────────────────────────┐                 │ │  -mcp   │ │   │
│  │       gVisor Sandbox Pool         │                 │ └────┬────┘ │   │
│  │  ┌─────────┐ ┌─────────┐         │                 │      │      │   │
│  │  │ sandbox │ │ sandbox │  (warm)  │◀────────────────│ ┌────▼────┐ │   │
│  │  │ pandas  │ │ numpy   │         │                 │ │  code   │ │   │
│  │  │ scipy   │ │ ta-lib  │         │                 │ │executor │ │   │
│  │  └─────────┘ └─────────┘         │                 │ │  -mcp   │ │   │
│  │  RuntimeClass: gvisor             │                 │ └─────────┘ │   │
│  │  Network: air-gapped (no egress)  │                 └─────────────┘   │
│  └───────────────────────────────────┘                                    │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Data flow:**
1. User authenticates via Cognito, receives JWT
2. Chat UI forwards request + JWT to the Strands Agent
3. Agent reasons and calls tools via agentgateway
4. agentgateway routes LLM calls to Bedrock, tool calls to MCP servers
5. `trading-mcp-server` returns market data / positions from in-memory sample data
6. `code-executor-mcp` claims a warm gVisor sandbox, injects data, executes calculations
7. Results flow back through the agent to the user

---

## Prerequisites

- AWS account with Bedrock model access for `us.anthropic.claude-sonnet-4-20250514-v1:0`
- `kubectl`, `helm`, `aws` CLI, `eksctl` installed
- Terraform (optional, for infrastructure-as-code)

```bash
# Set environment variables used throughout
export AWS_REGION=us-west-2
export CLUSTER_NAME=crypto-trading-cluster
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPO=${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
export SANDBOX_VERSION=v0.4.6
```

---

## Module 1: Cluster Setup (EKS Standard + Karpenter)

### Why EKS Standard Mode (not Auto Mode)?

EKS Auto Mode does not support custom RuntimeClasses or node user-data scripts. Since gVisor requires installing the `runsc` binary via node bootstrap, we need **EKS Standard Mode** with Karpenter for:

- Custom EC2NodeClass with user-data to install gVisor runtime
- RuntimeClass registration for `gvisor` 
- Full control over kubelet flags (`--container-runtime-endpoint`)
- Ability to use `containerd` runtime handler configuration

### Create the cluster

```bash
cat <<'EOF' > cluster-config.yaml
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: crypto-trading-cluster
  region: us-west-2
  version: "1.32"

managedNodeGroups:
  - name: system
    instanceType: m7g.large
    desiredCapacity: 2
    minSize: 1
    maxSize: 3
    amiFamily: AmazonLinux2023

iam:
  withOIDC: true

addons:
  - name: vpc-cni
    version: latest
  - name: coredns
    version: latest
  - name: kube-proxy
    version: latest
  - name: eks-pod-identity-agent
    version: latest

karpenter:
  version: "1.4.0"
  createServiceAccount: true
EOF

eksctl create cluster -f cluster-config.yaml
```

### Install Karpenter 1.4.0

```bash
helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter \
  --version "1.4.0" \
  --namespace kube-system \
  --set "settings.clusterName=${CLUSTER_NAME}" \
  --set "settings.interruptionQueue=${CLUSTER_NAME}" \
  --set controller.resources.requests.cpu=1 \
  --set controller.resources.requests.memory=1Gi \
  --set controller.resources.limits.cpu=1 \
  --set controller.resources.limits.memory=1Gi \
  --wait
```

### Verification

```bash
kubectl get nodes -L kubernetes.io/arch
# Expect: arm64 nodes from m7g.large

kubectl get pods -n kube-system -l app.kubernetes.io/name=karpenter
# Expect: karpenter-controller pods Running
```

---

## Module 2: Install Agent Sandbox Controller

The Agent Sandbox Controller manages the lifecycle of ephemeral sandboxes: creation, warm pooling, claiming, and garbage collection. It watches `SandboxTemplate` and `SandboxWarmPool` CRDs to maintain a ready supply of pre-warmed sandbox pods.

### Install the controller

```bash
# Add the agent-sandbox Helm repo
helm repo add agent-sandbox https://agent-sandbox.github.io/charts
helm repo update

# Install the controller
helm upgrade --install agent-sandbox-controller agent-sandbox/agent-sandbox-controller \
  --version ${SANDBOX_VERSION} \
  --namespace agent-sandbox-system \
  --create-namespace \
  --set controller.image.tag=${SANDBOX_VERSION} \
  --set controller.resources.requests.cpu=500m \
  --set controller.resources.requests.memory=512Mi \
  --wait
```

### Install CRDs

```bash
kubectl apply -f https://github.com/agent-sandbox/agent-sandbox-controller/releases/download/${SANDBOX_VERSION}/agent-sandbox-crds.yaml
```

### Verification

```bash
kubectl get pods -n agent-sandbox-system
# Expect: agent-sandbox-controller-* Running

kubectl get crd | grep sandbox
# Expect:
# sandboxtemplates.agents.x-k8s.io
# sandboxwarmpools.agents.x-k8s.io
# sandboxclaims.agents.x-k8s.io
```

---

## Module 3: gVisor Runtime Isolation

gVisor (`runsc`) intercepts all system calls from sandboxed containers, providing kernel-level isolation without the overhead of full VMs. This protects the host from untrusted code executed by the LLM agent.

### 3.1 Karpenter NodePool with gVisor user-data

The EC2NodeClass installs `runsc` on Graviton arm64 nodes at boot time and configures containerd to use the `runsc` runtime handler.

```yaml
# manifests/nodepool-gvisor.yaml
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: gvisor-sandboxes
spec:
  template:
    metadata:
      labels:
        sandbox-runtime: gvisor
    spec:
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["arm64"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand"]
        - key: node.kubernetes.io/instance-type
          operator: In
          values: ["c7g.xlarge", "c7g.2xlarge", "m7g.xlarge", "m7g.2xlarge"]
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: gvisor-arm64
  limits:
    cpu: "64"
    memory: 128Gi
  disruption:
    consolidationPolicy: WhenEmptyOrUnderutilized
    consolidateAfter: 60s
---
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: gvisor-arm64
spec:
  role: "KarpenterNodeRole-${CLUSTER_NAME}"
  amiSelectorTerms:
    - alias: al2023@latest
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: "${CLUSTER_NAME}"
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: "${CLUSTER_NAME}"
  userData: |
    #!/bin/bash
    set -euo pipefail

    # Install gVisor (runsc) for arm64
    ARCH=arm64
    URL="https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}"
    wget -q "${URL}/runsc" -O /usr/local/bin/runsc
    wget -q "${URL}/containerd-shim-runsc-v1" -O /usr/local/bin/containerd-shim-runsc-v1
    chmod +x /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1

    # Configure containerd to use runsc handler
    cat >> /etc/containerd/config.toml <<CONTAINERD

    [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
      runtime_type = "io.containerd.runsc.v1"
    CONTAINERD

    systemctl restart containerd
```

```bash
envsubst < manifests/nodepool-gvisor.yaml | kubectl apply -f -
```

### 3.2 RuntimeClass

Register the `gvisor` RuntimeClass so pods can request gVisor isolation:

```yaml
# manifests/runtimeclass-gvisor.yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
scheduling:
  nodeSelector:
    sandbox-runtime: gvisor
```

```bash
kubectl apply -f manifests/runtimeclass-gvisor.yaml
```

### 3.3 SandboxTemplate

Defines what each sandbox pod looks like — image, resources, runtime, and security context:

```yaml
# manifests/sandboxtemplate-trading.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: trading-python
  namespace: trading
spec:
  podTemplate:
    metadata:
      labels:
        app: trading-sandbox
        isolation: gvisor
    spec:
      runtimeClassName: gvisor
      terminationGracePeriodSeconds: 10
      automountServiceAccountToken: false
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      containers:
        - name: sandbox
          image: ${ECR_REPO}/python-runtime-sandbox:latest
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          ports:
            - containerPort: 8888
              name: http
          readinessProbe:
            httpGet:
              path: /health
              port: 8888
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "1"
              memory: "1Gi"
```

### 3.4 SandboxWarmPool

Keeps pre-warmed sandboxes ready for instant claiming:

```yaml
# manifests/sandboxwarmpool-trading.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: trading-python-pool
  namespace: trading
spec:
  replicas: 2
  sandboxTemplateRef:
    name: trading-python
```

```bash
kubectl create namespace trading
envsubst < manifests/sandboxtemplate-trading.yaml | kubectl apply -f -
```

### 3.5 Build the sandbox runtime image

The warm pool pods will pull this image, so it must exist in ECR before creating the pool.

```bash
# Authenticate Docker to ECR
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REPO}

# Create repository and build/push
aws ecr create-repository --repository-name python-runtime-sandbox --region ${AWS_REGION} || true
cd code-executor-mcp/python-runtime-sandbox/
docker buildx build --platform linux/arm64 \
  -t ${ECR_REPO}/python-runtime-sandbox:latest \
  -f Dockerfile --push .
cd ../..
```

### 3.6 Create the SandboxWarmPool

Once the image is available, create the warm pool to pre-warm sandbox pods:

```yaml
# manifests/sandboxwarmpool-trading.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: trading-python-pool
  namespace: trading
spec:
  replicas: 2
  sandboxTemplateRef:
    name: trading-python
```

```bash
kubectl apply -f manifests/sandboxwarmpool-trading.yaml
```

### Verification

```bash
# Check warm pool pods
kubectl get pods -n trading -l agents.x-k8s.io/pool=trading-python-pool
# Expect: 2 pods in Running state

# Verify gVisor runtime
kubectl get pods -n trading -o jsonpath='{.items[0].spec.runtimeClassName}'
# Expect: gvisor

# Test sandbox isolation (gVisor intercepts syscalls)
kubectl exec -n trading <sandbox-pod> -- cat /proc/version
# Expect: "Linux version 4.19.0-gvisor" (gVisor sentry kernel, not the host)

kubectl exec -n trading <sandbox-pod> -- uname -r
# Expect: "4.19.0-gvisor"

kubectl exec -n trading <sandbox-pod> -- python -c "import socket; socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)" 2>&1
# Expect: "PermissionError: [Errno 1] Operation not permitted"
```

---

## Module 4: Configure Cognito + Per-Tool Authorization

Cognito issues JWTs containing group claims. agentgateway evaluates these with CEL (Common Expression Language) policies to enforce per-tool authorization — traders can execute calculations, viewers can only read data.

### 4.1 Create Cognito User Pool

```bash
# Create the user pool
aws cognito-idp create-user-pool \
  --pool-name crypto-trading-pool \
  --auto-verified-attributes email \
  --admin-create-user-config AllowAdminCreateUserOnly=true \
  --schema '[{"Name":"email","Required":true,"Mutable":true}]' \
  --region ${AWS_REGION}

export USER_POOL_ID=$(aws cognito-idp list-user-pools --max-results 10 \
  --query "UserPools[?Name=='crypto-trading-pool'].Id" --output text)

# Create app client
aws cognito-idp create-user-pool-client \
  --user-pool-id ${USER_POOL_ID} \
  --client-name crypto-trading-app \
  --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
  --supported-identity-providers COGNITO

export APP_CLIENT_ID=$(aws cognito-idp list-user-pool-clients \
  --user-pool-id ${USER_POOL_ID} \
  --query "UserPoolClients[?ClientName=='crypto-trading-app'].ClientId" --output text)

# Create groups
aws cognito-idp create-group --user-pool-id ${USER_POOL_ID} --group-name traders
aws cognito-idp create-group --user-pool-id ${USER_POOL_ID} --group-name viewers

# Create test users
aws cognito-idp admin-create-user \
  --user-pool-id ${USER_POOL_ID} \
  --username trader1 \
  --user-attributes Name=email,Value=trader1@example.com \
  --message-action SUPPRESS

aws cognito-idp admin-set-user-password \
  --user-pool-id ${USER_POOL_ID} \
  --username trader1 \
  --password 'Workshop1!' \
  --permanent

aws cognito-idp admin-add-user-to-group \
  --user-pool-id ${USER_POOL_ID} \
  --username trader1 \
  --group-name traders

aws cognito-idp admin-create-user \
  --user-pool-id ${USER_POOL_ID} \
  --username viewer1 \
  --user-attributes Name=email,Value=viewer1@example.com \
  --message-action SUPPRESS

aws cognito-idp admin-set-user-password \
  --user-pool-id ${USER_POOL_ID} \
  --username viewer1 \
  --password 'Workshop1!' \
  --permanent

aws cognito-idp admin-add-user-to-group \
  --user-pool-id ${USER_POOL_ID} \
  --username viewer1 \
  --group-name viewers
```

---

## Module 5: Deploy agentgateway

[agentgateway](https://github.com/agentgateway/agentgateway) is a unified gateway that routes both LLM and MCP (Model Context Protocol) traffic. It provides a single endpoint for the Strands agent to reach Bedrock and all MCP tool servers.

### 5.1 Create Pod Identity for Bedrock access

```bash
# Create the IAM policy
cat <<EOF > bedrock-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "arn:aws:bedrock:${AWS_REGION}::foundation-model/us.anthropic.claude-sonnet-4-20250514-v1:0"
    }
  ]
}
EOF

aws iam create-policy \
  --policy-name AgentgatewayBedrockAccess \
  --policy-document file://bedrock-policy.json

# Create the IAM role with EKS Pod Identity trust policy
cat <<EOF > trust-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "pods.eks.amazonaws.com"
      },
      "Action": [
        "sts:AssumeRole",
        "sts:TagSession"
      ]
    }
  ]
}
EOF

aws iam create-role \
  --role-name AgentgatewayBedrockRole \
  --assume-role-policy-document file://trust-policy.json

aws iam attach-role-policy \
  --role-name AgentgatewayBedrockRole \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/AgentgatewayBedrockAccess

# Create Pod Identity association
aws eks create-pod-identity-association \
  --cluster-name ${CLUSTER_NAME} \
  --namespace gateway \
  --service-account agentgateway-sa \
  --role-arn arn:aws:iam::${ACCOUNT_ID}:role/AgentgatewayBedrockRole
```

### 5.2 Deploy agentgateway

agentgateway is deployed in its own `gateway` namespace, separate from the application workloads in `trading`.

```yaml
# manifests/agentgateway.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: gateway
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: agentgateway-sa
  namespace: gateway
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentgateway-config
  namespace: gateway
data:
  config.yaml: |
    llm:
      port: 8080
      policies:
        jwtAuth:
          mode: optional
          issuer: https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}
          audiences:
            - ${APP_CLIENT_ID}
          jwks:
            url: https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/jwks.json
      models:
        - name: "*"
          provider: bedrock
          params:
            awsRegion: ap-southeast-1
    mcp:
      port: 8081
      policies:
        mcpAuthentication:
          mode: optional
          issuer: https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}
          audiences:
            - ${APP_CLIENT_ID}
          jwks:
            url: https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/jwks.json
        mcpAuthorization:
          rules:
            # Traders get full access to all tools
            - '"trader" in jwt["cognito:groups"]'
            # Viewers can only access read-only tools
            - '"viewer" in jwt["cognito:groups"] && mcp.tool.name in ["get_market_data", "get_positions", "get_order_history", "get_portfolio_summary"]'
      targets:
        - name: trading-mcp-server
          mcp:
            host: http://trading-mcp-server.trading.svc.cluster.local:8000/mcp/
        - name: code-executor-mcp
          mcp:
            host: http://code-executor-mcp.trading.svc.cluster.local:8000/mcp/
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentgateway
  namespace: gateway
spec:
  replicas: 1
  selector:
    matchLabels:
      app: agentgateway
  template:
    metadata:
      labels:
        app: agentgateway
    spec:
      serviceAccountName: agentgateway-sa
      nodeSelector:
        kubernetes.io/arch: arm64
      containers:
        - name: agentgateway
          image: cr.agentgateway.dev/agentgateway:v1.4.1
          args: ["--file", "/etc/agentgateway/config.yaml"]
          ports:
            - containerPort: 8080
              name: llm
            - containerPort: 8081
              name: mcp
          volumeMounts:
            - name: config
              mountPath: /etc/agentgateway
              readOnly: true
          resources:
            requests:
              cpu: "250m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
      volumes:
        - name: config
          configMap:
            name: agentgateway-config
---
apiVersion: v1
kind: Service
metadata:
  name: agentgateway
  namespace: gateway
spec:
  selector:
    app: agentgateway
  ports:
    - name: llm
      port: 8080
      targetPort: 8080
    - name: mcp
      port: 8081
      targetPort: 8081
  type: ClusterIP
```

```bash
kubectl apply -f manifests/agentgateway.yaml
```

### Verification

```bash
kubectl get pods -n gateway -l app=agentgateway
# Expect: 2/2 Running
```

---

## Module 6: Deploy Trading MCP Server

The trading MCP server exposes market data, portfolio positions, and order history as MCP tools. It uses **in-memory sample data** (no external database dependencies), making it self-contained and fast to deploy.

### 6.1 What the server provides

The MCP server exposes four tools:

| Tool | Description |
|------|-------------|
| `get_market_data` | OHLCV candlestick data for crypto pairs (BTC/USD, ETH/USD, SOL/USD, AVAX/USD) |
| `get_positions` | Current portfolio positions with unrealized PnL |
| `get_order_history` | Past buy/sell orders within a timeframe |
| `get_portfolio_summary` | Total value, PnL, and allocation breakdown |

Sample data includes realistic prices and positions for BTC, ETH, SOL, and AVAX.

### 6.2 Build and push the container image

```bash
# Authenticate Docker to ECR
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REPO}

# Create ECR repository
aws ecr create-repository --repository-name trading-mcp-server --region ${AWS_REGION} || true

# Build and push for arm64
cd trading-mcp-server/
docker buildx build --platform linux/arm64 \
  -t ${ECR_REPO}/trading-mcp-server:latest \
  -f Dockerfile --push .
cd ..
```

### 6.3 Deploy the MCP server

```bash
envsubst < manifests/trading-mcp-server.yaml | kubectl apply -f -
```

The deployment runs on Graviton:

```yaml
spec:
  nodeSelector:
    kubernetes.io/arch: arm64
  containers:
    - name: trading-mcp-server
      image: ${ECR_REPO}/trading-mcp-server:latest
      ports:
        - containerPort: 8000
```

### 6.4 Verify the MCP server

```bash
kubectl get pods -n trading -l app=trading-mcp-server
kubectl get svc -n trading trading-mcp-server
```

---

## Module 7: Deploy Code Executor MCP Broker

The code executor MCP broker manages gVisor sandbox lifecycle for secure code execution. When the agent calls `run_calculation`, the broker:

1. Claims a warm sandbox from the pool
2. Injects the agent-provided input data and code into the sandbox
3. Executes the Python code
4. Returns the result (stdout/stderr)
5. Releases the sandbox (auto-cleaned by TTL)

The agent is responsible for fetching trading data (via `get_market_data`, `get_positions`, etc.) and passing it to `run_calculation` as `input_data`. This keeps the broker focused on sandbox lifecycle only.

### 7.1 Build and push the broker image

```bash
# Authenticate Docker to ECR (skip if already logged in)
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REPO}

# Build and push the code-executor-mcp broker
aws ecr create-repository --repository-name code-executor-mcp --region ${AWS_REGION} || true
cd code-executor-mcp/
docker buildx build --platform linux/arm64 \
  -t ${ECR_REPO}/code-executor-mcp:latest \
  -f Dockerfile --push .
cd ..
```

### 7.2 Deploy the broker

```yaml
# manifests/code-executor-mcp.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: code-executor-mcp-sa
  namespace: trading
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: sandbox-claim-creator
  namespace: trading
rules:
  - apiGroups: ["extensions.agents.x-k8s.io"]
    resources: ["sandboxclaims"]
    verbs: ["create", "get", "list", "watch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: code-executor-sandbox-claim
  namespace: trading
subjects:
  - kind: ServiceAccount
    name: code-executor-mcp-sa
    namespace: trading
roleRef:
  kind: Role
  name: sandbox-claim-creator
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: code-executor-mcp
  namespace: trading
spec:
  replicas: 1
  selector:
    matchLabels:
      app: code-executor-mcp
  template:
    metadata:
      labels:
        app: code-executor-mcp
    spec:
      serviceAccountName: code-executor-mcp-sa
      nodeSelector:
        kubernetes.io/arch: arm64
      containers:
        - name: code-executor-mcp
          image: ${ECR_REPO}/code-executor-mcp:latest
          ports:
            - containerPort: 8000
              name: http
          env:
            - name: SANDBOX_WARMPOOL
              value: "trading-python-pool"
            - name: SANDBOX_NAMESPACE
              value: "trading"
          readinessProbe:
            tcpSocket:
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            tcpSocket:
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          resources:
            requests:
              cpu: "250m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: code-executor-mcp
  namespace: trading
spec:
  selector:
    app: code-executor-mcp
  ports:
    - name: http
      port: 8000
      targetPort: 8000
  type: ClusterIP
```

```bash
envsubst < manifests/code-executor-mcp.yaml | kubectl apply -f -
```

### 7.3 Network Policy — Air-gap sandboxes

Sandboxes must have no outbound network access. Data is injected via HTTP from the code-executor-mcp broker (which communicates with the sandbox's internal server on port 8888):

```yaml
# manifests/sandbox-networkpolicy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-sandbox-egress
  namespace: trading
spec:
  podSelector:
    matchLabels:
      agents.x-k8s.io/pool: trading-python-pool
  policyTypes:
    - Egress
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: code-executor-mcp
      ports:
        - port: 8888
          protocol: TCP
  egress: []    # No outbound — completely air-gapped
```

```bash
kubectl apply -f manifests/sandbox-networkpolicy.yaml
```

### Exposed MCP Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `run_calculation` | Execute Python code in gVisor sandbox | `code` (str), `input_data` (dict) |

### Execution flow detail

```
Agent calls get_market_data("BTC/USD") → receives price data
Agent calls get_positions("trader1") → receives positions
Agent writes Python code for the calculation
Agent calls run_calculation(code="...", input_data={...})
  │
  ▼
Code Executor MCP claims a warm sandbox via k8s-agent-sandbox SDK:
  - Creates SandboxClaim referencing trading-python-pool
  - Controller assigns a pre-warmed gVisor pod
  │
  ▼
Broker injects files via HTTP (POST /upload to sandbox:8888):
  - /app/input_data.json   (the agent-provided data)
  - /app/main.py           (the agent's Python code)
  │
  ▼
Broker executes code via HTTP (POST /exec to sandbox:8888):
  - Runs: python3 /app/main.py
  - Sandbox subprocess captures stdout/stderr
  │
  ▼
Returns stdout (result) or stderr (error) to agent
  │
  ▼
Optionally reads /app/chart.png if generated (GET /download?path=/app/chart.png)
  │
  ▼
SandboxClaim TTL expires → pod terminated → pool replenishes
```

### Verification

```bash
kubectl get pods -n trading -l app=code-executor-mcp
# Expect: 2/2 Running
```

---

## Module 8: Deploy the Strands Agent

The Strands Agent is the crypto trading assistant. It uses `strands-agents` SDK with a Streamlit UI, connecting to agentgateway for LLM inference and MCP tool calls.

### 8.1 Agent application code

The agent connects to agentgateway for both LLM (Bedrock) and MCP tools (trading data + code executor):

```python
# trading-agent/agent.py
from strands import Agent
from strands.models.openai import OpenAIModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

def create_agent(authorization_token=None):
    mcp_headers = {}
    if authorization_token:
        mcp_headers["Authorization"] = f"Bearer {authorization_token}"

    mcp_client = MCPClient(
        lambda: streamablehttp_client(MCP_ENDPOINT, headers=mcp_headers),
    )

    with mcp_client:
        tools = mcp_client.list_tools_sync()

        model = OpenAIModel(
            model_id=MODEL_ID,
            client_args={"base_url": LLM_ENDPOINT, "api_key": "not-needed"},
            params={"max_tokens": 16384},
        )

        agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=tools)
        return agent, mcp_client
```

The Streamlit app (`app.py`) provides login via Cognito and a chat interface with streaming responses.

### 8.2 Dockerfile

```dockerfile
# trading-agent/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py app.py sample_prompts.md ./
EXPOSE 8501
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
```

```
# trading-agent/requirements.txt
strands-agents[mcp,otel]==1.50.2
openai==2.46.0
streamlit==1.41.1
boto3==1.35.86
requests==2.32.3
```

### 8.3 Build and push the agent image

```bash
# Authenticate Docker to ECR (skip if already logged in)
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REPO}

# Create repository and build/push
aws ecr create-repository --repository-name strands-trading-agent --region ${AWS_REGION} || true
cd trading-agent/
docker buildx build --platform linux/arm64 \
  -t ${ECR_REPO}/strands-trading-agent:latest \
  -f Dockerfile --push .
cd ..
```

### 8.4 Deploy to EKS

```bash
envsubst < manifests/agent-deployment.yaml | kubectl apply -f -
```

The deployment configures:
- `MODEL_ID` — Bedrock model via agentgateway (`global.anthropic.claude-sonnet-4-6`)
- `LLM_ENDPOINT` — agentgateway LLM port with `/v1` path
- `MCP_ENDPOINT` — agentgateway MCP port with `/mcp` path
- `COGNITO_USER_POOL_ID` / `COGNITO_CLIENT_ID` — for login authentication
- Readiness/liveness probes on Streamlit's `/_stcore/health` endpoint

### Verification

```bash
kubectl get pods -n trading -l app=strands-trading-agent
# Expect: 1/1 Running

# Port-forward to access the agent UI
kubectl port-forward -n trading svc/strands-trading-agent 8501:80 &
echo "Agent UI: http://localhost:8501"
```
---

## Module 9: Test Scenarios

These scenarios validate end-to-end agent behavior through the Streamlit chat UI.

### Access the UI

```bash
kubectl port-forward -n trading svc/strands-trading-agent 8501:80 &
echo "Open http://localhost:8501"
```

Login with `trader1` / `Workshop1!`

### 9.1 Portfolio Positions

In the chat UI, type:

> Show my current portfolio positions

**Expected:** Agent calls `get_positions` and displays your BTC, ETH, SOL, AVAX holdings with unrealized PnL.

### 9.2 Value-at-Risk Calculation

> Calculate the 1-day 95% VaR for my portfolio using historical simulation

**Expected:**
1. Agent calls `get_market_data` for each asset
2. Agent calls `get_positions` for holdings
3. Agent writes Python VaR code and calls `run_calculation` in the sandbox
4. Returns VaR estimate with methodology explanation

### 9.3 Black-Scholes Options Pricing

> Price a European call option on BTC with strike 70000, expiry 30 days, 60% vol, 5.25% risk-free rate. Execute in the sandbox.

**Expected:** Agent writes Black-Scholes code, executes in sandbox, returns option premium and Greeks.

### 9.4 Authorization Test — Viewer

Logout and login as `viewer1` / `Workshop1!`

> Calculate VaR for my portfolio

**Expected:** Agent cannot access `run_calculation` tool (filtered by agentgateway mcpAuthorization). It can still show positions but cannot execute code.

### 9.5 Observe tool calls

```bash
# After sending a prompt, view the MCP tool calls through agentgateway
kubectl logs -n gateway deploy/agentgateway | grep "tools/call"
```

Example output showing the agent fetching data then executing in the sandbox:
```
mcp.method.name=tools/call mcp.target=trading-mcp-server gen_ai.tool.name=get_positions duration=11ms
mcp.method.name=tools/call mcp.target=trading-mcp-server gen_ai.tool.name=get_market_data duration=14ms
mcp.method.name=tools/call mcp.target=code-executor-mcp gen_ai.tool.name=run_calculation duration=3522ms
```

---

## Module 10: Cleanup

Remove all resources created in this workshop.

### 10.1 Delete application resources

```bash
# Delete all trading namespace resources
kubectl delete namespace trading

# Delete the gateway namespace (agentgateway)
kubectl delete namespace gateway

# Wait for namespaces to terminate
kubectl wait --for=delete namespace/trading --timeout=120s
kubectl wait --for=delete namespace/gateway --timeout=120s
```

### 10.2 Delete gVisor infrastructure

```bash
kubectl delete runtimeclass gvisor
kubectl delete nodepool gvisor-sandboxes
kubectl delete ec2nodeclass gvisor-arm64
```

### 10.3 Uninstall Agent Sandbox Controller

```bash
helm uninstall agent-sandbox-controller -n agent-sandbox-system
kubectl delete namespace agent-sandbox-system
kubectl delete -f https://github.com/agent-sandbox/agent-sandbox-controller/releases/download/${SANDBOX_VERSION}/agent-sandbox-crds.yaml
```

### 10.4 Delete Cognito resources

```bash
aws cognito-idp delete-user-pool-client \
  --user-pool-id ${USER_POOL_ID} \
  --client-id ${APP_CLIENT_ID}

aws cognito-idp delete-user-pool --user-pool-id ${USER_POOL_ID}
```

### 10.5 Delete IAM resources

```bash
# Detach and delete policies
aws iam delete-policy \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/AgentgatewayBedrockAccess

# Delete Pod Identity associations
aws eks delete-pod-identity-association \
  --cluster-name ${CLUSTER_NAME} \
  --association-id $(aws eks list-pod-identity-associations \
    --cluster-name ${CLUSTER_NAME} \
    --query 'associations[0].associationId' --output text)
```

### 10.6 Delete ECR repositories

```bash
aws ecr delete-repository --repository-name python-runtime-sandbox --force --region ${AWS_REGION}
aws ecr delete-repository --repository-name trading-mcp-server --force --region ${AWS_REGION}
aws ecr delete-repository --repository-name code-executor-mcp --force --region ${AWS_REGION}
aws ecr delete-repository --repository-name strands-trading-agent --force --region ${AWS_REGION}
```

### 10.7 Delete the EKS cluster

```bash
eksctl delete cluster --name ${CLUSTER_NAME} --region ${AWS_REGION}
```

> ⚠️ **Note:** Cluster deletion takes 10-15 minutes. Ensure all LoadBalancers and EBS volumes are cleaned up by checking the AWS Console.

---

## Summary

You've built a production-grade crypto trading agent with:

| Feature | Implementation |
|---------|---------------|
| **LLM Reasoning** | Strands Agents SDK + Claude Sonnet via Bedrock |
| **Unified Gateway** | agentgateway routing LLM + MCP traffic |
| **Safe Code Execution** | gVisor sandboxes with air-gapped networking |
| **Warm Pool** | Agent Sandbox Controller pre-warms pods for instant startup |
| **Per-Tool AuthZ** | Cognito JWT + CEL policies in agentgateway |
| **Observability** | agentgateway admin UI for LLM + MCP traffic visibility |
| **Cost Efficient** | Graviton arm64 instances throughout via Karpenter |
