# OpenClaw on Amazon EKS with Agent Sandbox — Lab Guide

This hands-on lab walks you through deploying OpenClaw AI agents on Amazon EKS (Standard) with Karpenter and the [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) project. You'll use the Sandbox CRD for lifecycle management, gVisor and Kata Containers for tiered runtime isolation, Pod Identity for keyless Bedrock access, Kubernetes network policies for egress control, and Karpenter for intelligent node autoscaling with dedicated isolation-capable nodes.

## Why Agent Sandbox?

The [Agent Sandbox](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/) project (SIG Apps) provides a Kubernetes-native abstraction purpose-built for AI agent workloads:

- **Sandbox CRD** — a declarative, single-container environment with stable identity and persistent storage
- **SandboxTemplate** — reusable runtime templates (runc, gVisor, Kata) that decouple workload config from isolation policy
- **SandboxWarmPool** — pre-provisioned sandbox pods that eliminate cold-start latency for agents

## Why Standard EKS + Karpenter (not Auto Mode)?

gVisor requires installing the `runsc` containerd shim on nodes at boot. Kata Containers requires bare metal instances with the `kata-containers` runtime installed. EKS Auto Mode doesn't allow custom AMIs or user-data, making both impossible. Standard EKS with Karpenter gives you:

- **Custom node configuration** — AL2023 user-data installs gVisor or Kata at boot
- **Dedicated NodePools** — tainted nodes ensure only sandbox workloads land on isolation-capable nodes
- **Instance type control** — gVisor runs on any instance; Kata requires `.metal` for hardware virtualization
- **Karpenter autoscaling** — nodes provision in seconds based on pending pod demand
- **Full runtime control** — you own the AMI, containerd config, and node lifecycle

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Amazon EKS Standard Cluster + Karpenter                                 │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  agent-sandbox-system namespace                                    │  │
│  │  ┌──────────────────────────────────────────────────────────────┐  │  │
│  │  │  agent-sandbox-controller-manager                            │  │  │
│  │  │  (manages Sandbox, SandboxTemplate, SandboxWarmPool)         │  │  │
│  │  └──────────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  openclaw-agents namespace                                         │  │
│  │                                                                    │  │
│  │  SandboxTemplate: openclaw-standard (runc)                         │  │
│  │  SandboxTemplate: openclaw-gvisor (gVisor + Sentry)                │  │
│  │  SandboxTemplate: openclaw-kata (Kata + Cloud Hypervisor)          │  │
│  │  SandboxWarmPool: openclaw-pool (3 runc)                           │  │
│  │  SandboxWarmPool: openclaw-gvisor-pool (3 gVisor)                  │  │
│  │  SandboxWarmPool: openclaw-kata-pool (2 Kata)                      │  │
│  │                                                                    │  │
│  │  ┌──────────────────────────────────────────────────────────────┐  │  │
│  │  │  Sandbox pod (claimed from warm pool)                        │  │  │
│  │  │  ┌────────────────────────────────────────────────────────┐  │  │  │
│  │  │  │  OpenClaw container                                    │  │  │  │
│  │  │  │  • Claude on Bedrock (Pod Identity — no keys)          │  │  │  │
│  │  │  │  • gVisor Sentry OR Kata micro-VM (tiered isolation)   │  │  │  │
│  │  │  │  • Network Policy (egress allowlist)                   │  │  │  │
│  │  │  └────────────────────────────────────────────────────────┘  │  │  │
│  │  │  PVC: 10Gi gp3 (encrypted, via volumeClaimTemplate)          │  │  │
│  │  └──────────────────────────────────────────────────────────────┘  │  │
│  │                                                                    │  │
│  │  NetworkPolicy: deny-all + allow DNS/HTTPS                         │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  Karpenter-Managed Nodes                                                 │
│  ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────┐  │
│  │  NodePool: general   │ │  NodePool: gvisor    │ │  NodePool: kata  │  │
│  │  AL2023, Graviton    │ │  AL2023 + runsc shim │ │  AL2023 + kata   │  │
│  │  Controller, system  │ │  Taint: gvisor       │ │  .metal instances│  │
│  │                      │ │                      │ │  Taint: kata     │  │
│  └──────────────────────┘ └──────────────────────┘ └──────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│  Amazon Bedrock             │
│  Claude Sonnet 4            │
│  (Pod Identity, no keys)    │
└─────────────────────────────┘
```

## Security Layers

```
┌───────────────────────────────────────────────────────┐
│  Application (OpenClaw agent)                         │
├───────────────────────────────────────────────────────┤
│  Network Policy (egress allowlist)                    │  ← only approved ports
├───────────────────────────────────────────────────────┤
│  Runtime Isolation (choose one per tier):             │
│  • gVisor Sentry (user-space kernel)                  │  ← syscalls stop here
│  • Kata micro-VM (Cloud Hypervisor)                   │  ← full kernel boundary
├───────────────────────────────────────────────────────┤
│  Container securityContext (drop ALL caps)            │  ← no privilege escalation
├───────────────────────────────────────────────────────┤
│  Host kernel (AL2023, minimal)                        │  ← unreachable by workload
├───────────────────────────────────────────────────────┤
│  EC2 instance (Karpenter-managed, auto-scaled)        │
│  • gVisor: standard Graviton instances                │
│  • Kata: bare metal (.metal) instances                │
└───────────────────────────────────────────────────────┘
```

## Prerequisites

- An AWS account with permissions to create EKS clusters, IAM roles, and S3 buckets
- AWS CLI v2 configured with appropriate credentials
- `kubectl` v1.36+
- `eksctl` v0.195.0+
- Helm 3

## Lab Modules

| Module | Topic | Time Estimate |
|--------|-------|---------------|
| 1 | [Cluster Setup with Karpenter](#module-1-cluster-setup-with-karpenter) | 15 min |
| 2 | [Install Agent Sandbox Controller](#module-2-install-agent-sandbox-controller) | 5 min |
| 3 | [Configure Bedrock Access via Pod Identity](#module-3-configure-bedrock-access-via-pod-identity) | 10 min |
| 4 | [Deploy OpenClaw with a Sandbox CRD](#module-4-deploy-openclaw-with-a-sandbox-crd) | 10 min |
| 5 | [Scale with SandboxTemplate and SandboxWarmPool](#module-5-scale-with-sandboxtemplate-and-sandboxwarmpool) | 15 min |
| 6 | [Harden with gVisor Runtime Isolation](#module-6-harden-with-gvisor-runtime-isolation) | 20 min |
| 7 | [Isolate with Kata Containers (VM-level)](#module-7-isolate-with-kata-containers-vm-level) | 20 min |
| 8 | [Restrict Egress with Network Policy](#module-8-restrict-egress-with-network-policy) | 10 min |
| 9 | [Cleanup](#module-9-cleanup) | 5 min |

---

## Module 1: Cluster Setup with Karpenter

### 1.1 Create an EKS cluster with Karpenter

eksctl handles all Karpenter prerequisites — IAM roles, SQS interruption queue, Helm install — via a cluster config file:

```bash
export CLUSTER_NAME="openclaw-sandbox-lab"
export AWS_REGION="us-east-1"
```

```yaml
# manifests/cluster-config.yaml
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: openclaw-sandbox-lab
  region: us-east-1
  version: "1.36"
  tags:
    karpenter.sh/discovery: openclaw-sandbox-lab

iam:
  withOIDC: true

karpenter:
  version: "1.12.0"
  withSpotInterruptionQueue: true

managedNodeGroups:
  - name: system
    instanceType: m7g.large
    minSize: 1
    maxSize: 3
    desiredCapacity: 2

addons:
  - name: eks-pod-identity-agent
  - name: aws-ebs-csi-driver
```

```bash
eksctl create cluster -f manifests/cluster-config.yaml
```

This creates:
- An EKS 1.36 cluster with OIDC provider
- Karpenter controller IAM role + node IAM role
- SQS interruption queue for spot/maintenance events
- Karpenter installed and running in `kube-system`
- EBS CSI driver and Pod Identity Agent as managed addons
- A small Graviton managed node group for system workloads (Karpenter, CoreDNS, etc.)

### 1.2 Create a general-purpose NodePool

```yaml
# manifests/nodepool-general.yaml
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: general
spec:
  template:
    spec:
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: general
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["arm64"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand", "spot"]
  limits:
    cpu: "100"
    memory: 200Gi
  disruption:
    consolidationPolicy: WhenEmpty
    consolidateAfter: 60s
---
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: general
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
```

```bash
envsubst < manifests/nodepool-general.yaml | kubectl apply -f -
```

### 1.3 Create the StorageClass

```yaml
# manifests/storageclass.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
volumeBindingMode: WaitForFirstConsumer
parameters:
  type: gp3
  encrypted: "true"
```

```bash
kubectl apply -f manifests/storageclass.yaml
```

### 1.4 Verify the cluster

```bash
kubectl get nodes
kubectl get pods -n kube-system
kubectl get pods -n karpenter
```

---

## Module 2: Install Agent Sandbox Controller

The agent-sandbox controller manages `Sandbox`, `SandboxTemplate`, `SandboxClaim`, and `SandboxWarmPool` resources.

### 2.1 Install the controller

```bash
export SANDBOX_VERSION="v0.4.6"

# Core controller
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${SANDBOX_VERSION}/manifest.yaml

# Extensions (SandboxTemplate, SandboxWarmPool, SandboxClaim)
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${SANDBOX_VERSION}/extensions.yaml
```

### 2.2 Verify the controller is running

```bash
kubectl get pods -n agent-sandbox-system
```

### 2.3 Confirm CRDs are installed

```bash
kubectl get crds | grep agents.x-k8s.io
```

You should see `sandboxes.agents.x-k8s.io`, `sandboxtemplates.agents.x-k8s.io`, `sandboxclaims.agents.x-k8s.io`, and `sandboxwarmpools.agents.x-k8s.io`.

---

## Module 3: Configure Bedrock Access via Pod Identity

### 3.1 Create the namespace

```bash
kubectl create namespace openclaw-agents
```

### 3.2 Create the Bedrock IAM policy and role

```bash
cat > openclaw-bedrock-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
        "arn:aws:bedrock:*:*:inference-profile/global.anthropic.claude-sonnet-4-6"
      ]
    }
  ]
}
EOF

aws iam create-policy \
  --policy-name OpenClawBedrockPolicy \
  --policy-document file://openclaw-bedrock-policy.json

cat > pod-identity-trust.json << 'EOF'
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
  --role-name OpenClawAgentRole \
  --assume-role-policy-document file://pod-identity-trust.json

aws iam attach-role-policy \
  --role-name OpenClawAgentRole \
  --policy-arn arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/OpenClawBedrockPolicy
```

### 3.3 Create the ServiceAccount and Pod Identity association

```bash
kubectl create serviceaccount openclaw-agent-sa -n openclaw-agents

aws eks create-pod-identity-association \
  --cluster-name ${CLUSTER_NAME} \
  --role-arn arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/OpenClawAgentRole \
  --namespace openclaw-agents \
  --service-account openclaw-agent-sa
```

> **No API keys needed.** The Pod Identity Agent injects temporary credentials automatically.

---

## Module 4: Deploy OpenClaw with a Sandbox CRD

### 4.1 Create the OpenClaw configuration

```yaml
# manifests/openclaw-config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: openclaw-config
  namespace: openclaw-agents
data:
  openclaw.json: |
    {
      "agents": {
        "defaults": {
          "model": {
            "primary": "bedrock/global.anthropic.claude-sonnet-4-6"
          }
        }
      },
      "gateway": {
        "controlUi": {
          "dangerouslyAllowHostHeaderOriginFallback": true
        }
      }
    }
```

```bash
kubectl apply -f manifests/openclaw-config.yaml
```

### 4.2 Deploy the Sandbox

```yaml
# manifests/openclaw-sandbox.yaml
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: openclaw-agent
  namespace: openclaw-agents
spec:
  podTemplate:
    metadata:
      labels:
        app: openclaw
        sandbox: openclaw-agent
    spec:
      serviceAccountName: openclaw-agent-sa
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      initContainers:
        - name: copy-config
          image: busybox
          command: ["sh", "-c", "cp /config-src/openclaw.json /etc/openclaw/openclaw.json"]
          volumeMounts:
            - mountPath: /config-src
              name: config-source
            - mountPath: /etc/openclaw
              name: config-writable
      containers:
        - name: openclaw
          image: ghcr.io/openclaw/openclaw:2026.5.4-slim
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              memory: "1Gi"
          env:
            - name: AWS_REGION
              value: "us-east-1"
            - name: OPENCLAW_CONFIG_PATH
              value: "/etc/openclaw/openclaw.json"
            - name: OPENCLAW_GATEWAY_TOKEN
              value: "REPLACE_ME"
          command:
            - node
            - dist/index.js
            - gateway
            - --bind=lan
            - --port
            - "18789"
            - --allow-unconfigured
            - --verbose
          ports:
            - containerPort: 18789
            - containerPort: 18790
          volumeMounts:
            - mountPath: /home/node/.openclaw/workspace
              name: workspace-pvc
            - mountPath: /etc/openclaw
              name: config-writable
      volumes:
        - name: config-source
          configMap:
            name: openclaw-config
        - name: config-writable
          emptyDir: {}
  volumeClaimTemplates:
    - metadata:
        name: workspace-pvc
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 10Gi
```

```bash
export OPENCLAW_GATEWAY_TOKEN="$(openssl rand -hex 32)"
sed "s/REPLACE_ME/$OPENCLAW_GATEWAY_TOKEN/g" manifests/openclaw-sandbox.yaml | kubectl apply -f -
```

### 4.3 Verify the Sandbox

```bash
kubectl get sandbox openclaw-agent -n openclaw-agents
kubectl get pods -n openclaw-agents

kubectl wait --for=jsonpath='{.status.phase}'=Running \
  sandbox/openclaw-agent -n openclaw-agents --timeout=180s
```

### 4.4 Test Bedrock connectivity (Pod Identity)

```bash
kubectl exec -n openclaw-agents openclaw-agent -c openclaw -- \
  node -e "
    const { BedrockRuntimeClient, InvokeModelCommand } = require('@aws-sdk/client-bedrock-runtime');
    const client = new BedrockRuntimeClient({ region: 'ap-southeast-1' });
    client.send(new InvokeModelCommand({
      modelId: 'global.anthropic.claude-sonnet-4-6',
      contentType: 'application/json',
      body: JSON.stringify({
        anthropic_version: 'bedrock-2023-05-31',
        max_tokens: 50,
        messages: [{ role: 'user', content: 'Say hello' }]
      })
    })).then(r => console.log('Bedrock OK:', r.\$metadata.httpStatusCode))
      .catch(e => console.error('Bedrock FAIL:', e.message));
  "
```

### 4.5 Access the Web UI

```bash
kubectl port-forward pod/openclaw-agent -n openclaw-agents 18789:18789
```

Open http://localhost:18789 in your browser.

---

## Module 5: Scale with SandboxTemplate and SandboxWarmPool

### 5.1 Create a SandboxTemplate

```yaml
# manifests/openclaw-template.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: openclaw-standard
  namespace: openclaw-agents
spec:
  podTemplate:
    metadata:
      labels:
        app: openclaw
    spec:
      serviceAccountName: openclaw-agent-sa
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      initContainers:
        - name: copy-config
          image: busybox
          command: ["sh", "-c", "cp /config-src/openclaw.json /etc/openclaw/openclaw.json"]
          volumeMounts:
            - mountPath: /config-src
              name: config-source
            - mountPath: /etc/openclaw
              name: config-writable
      containers:
        - name: openclaw
          image: ghcr.io/openclaw/openclaw:2026.5.4-slim
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              memory: "1Gi"
          env:
            - name: AWS_REGION
              value: "us-east-1"
            - name: OPENCLAW_CONFIG_PATH
              value: "/etc/openclaw/openclaw.json"
          command:
            - node
            - dist/index.js
            - gateway
            - --bind=lan
            - --port
            - "18789"
            - --allow-unconfigured
          ports:
            - containerPort: 18789
          volumeMounts:
            - mountPath: /home/node/.openclaw/workspace
              name: workspace-pvc
            - mountPath: /etc/openclaw
              name: config-writable
      volumes:
        - name: config-source
          configMap:
            name: openclaw-config
        - name: config-writable
          emptyDir: {}
  volumeClaimTemplates:
    - metadata:
        name: workspace-pvc
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 10Gi
```

```bash
kubectl apply -f manifests/openclaw-template.yaml
```

### 5.2 Create a SandboxWarmPool

```yaml
# manifests/openclaw-warmpool.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: openclaw-pool
  namespace: openclaw-agents
spec:
  sandboxTemplateRef:
    name: openclaw-standard
  replicas: 3
```

```bash
kubectl apply -f manifests/openclaw-warmpool.yaml
```

### 5.3 Claim a pre-warmed sandbox

```yaml
# manifests/openclaw-claim.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxClaim
metadata:
  name: user-session-001
  namespace: openclaw-agents
spec:
  sandboxTemplateRef:
    name: openclaw-standard
```

```bash
kubectl apply -f manifests/openclaw-claim.yaml
```

The controller immediately assigns a pre-warmed sandbox — no cold start.

### 5.4 Verify the warm pool

```bash
kubectl get sandboxwarmpool openclaw-pool -n openclaw-agents
kubectl get sandboxes -n openclaw-agents
```

---

## Module 6: Harden with gVisor Runtime Isolation

gVisor interposes a user-space kernel (Sentry) that re-implements syscalls without passing them to the host kernel. This is critical for AI agents that execute arbitrary, model-generated code.

### 6.1 Why gVisor for AI agents?

| Threat | How gVisor helps |
|--------|-----------------|
| Agent-generated code runs a kernel exploit | Syscalls never reach the real kernel — Sentry re-implements them in user space |
| Container escape via kernel CVE (e.g., dirty pipe) | The exploit targets Sentry, not the host kernel — blast radius is contained |
| Compromised dependency attempts privilege escalation | Capabilities are meaningless inside the gVisor sandbox |

### 6.2 Create a gVisor-capable Karpenter NodePool

This NodePool uses AL2023 user-data to install the `runsc` containerd shim at boot:

```yaml
# manifests/nodepool-gvisor.yaml
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: gvisor
spec:
  template:
    metadata:
      labels:
        runtime: gvisor
    spec:
      taints:
        - key: runtime
          value: gvisor
          effect: NoSchedule
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: gvisor
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["arm64"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand", "spot"]
        - key: karpenter.k8s.aws/instance-category
          operator: In
          values: ["m", "c", "r"]
        - key: karpenter.k8s.aws/instance-memory
          operator: Gt
          values: ["3072"]
  limits:
    cpu: "64"
    memory: 128Gi
  disruption:
    consolidationPolicy: WhenEmpty
    consolidateAfter: 30m
---
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: gvisor
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
    MIME-Version: 1.0
    Content-Type: multipart/mixed; boundary="//"

    --//
    Content-Type: text/x-shellscript; charset="us-ascii"

    #!/bin/bash
    set -euxo pipefail

    # Install gVisor runsc binary + containerd shim
    ARCH=$(uname -m)
    mkdir -p /tmp/gvisor && cd /tmp/gvisor
    curl -fsSL -o runsc "https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}/runsc"
    curl -fsSL -o containerd-shim-runsc-v1 "https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}/containerd-shim-runsc-v1"
    chmod +x runsc containerd-shim-runsc-v1
    mv runsc containerd-shim-runsc-v1 /usr/local/bin/

    # Register runsc with containerd (v2 plugin path for EKS AL2023)
    cat <<'EOT' >> /etc/containerd/config.toml

    [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runsc]
      runtime_type = "io.containerd.runsc.v1"

    [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runsc.options]
      TypeUrl = "io.containerd.runsc.v1.options"
      ConfigPath = "/etc/containerd/runsc.toml"
    EOT

    # gVisor runtime options
    cat <<'EOT' > /etc/containerd/runsc.toml
    [runsc_config]
      platform = "systrap"
      network = "sandbox"
    EOT

    systemctl restart containerd
    --//
```

```bash
envsubst '$CLUSTER_NAME' < manifests/nodepool-gvisor.yaml | kubectl apply -f -
```

### 6.3 Create the RuntimeClass

```yaml
# manifests/runtimeclass-gvisor.yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
scheduling:
  nodeSelector:
    runtime: gvisor
  tolerations:
    - key: runtime
      value: gvisor
      effect: NoSchedule
```

```bash
kubectl apply -f manifests/runtimeclass-gvisor.yaml
```

The `scheduling` block ensures pods using this RuntimeClass automatically land on gVisor-capable nodes and tolerate their taint.

### 6.4 Create a gVisor SandboxTemplate

```yaml
# manifests/openclaw-template-gvisor.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: openclaw-gvisor
  namespace: openclaw-agents
spec:
  podTemplate:
    metadata:
      labels:
        app: openclaw
        isolation: gvisor
    spec:
      runtimeClassName: gvisor
      terminationGracePeriodSeconds: 10
      serviceAccountName: openclaw-agent-sa
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      initContainers:
        - name: copy-config
          image: busybox
          command: ["sh", "-c", "cp /config-src/openclaw.json /etc/openclaw/openclaw.json"]
          volumeMounts:
            - mountPath: /config-src
              name: config-source
            - mountPath: /etc/openclaw
              name: config-writable
      containers:
        - name: openclaw
          image: ghcr.io/openclaw/openclaw:2026.5.4-slim
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              memory: "1Gi"
          env:
            - name: AWS_REGION
              value: "us-east-1"
            - name: OPENCLAW_CONFIG_PATH
              value: "/etc/openclaw/openclaw.json"
          command:
            - node
            - dist/index.js
            - gateway
            - --bind=lan
            - --port
            - "18789"
            - --allow-unconfigured
          ports:
            - containerPort: 18789
          volumeMounts:
            - mountPath: /home/node/.openclaw/workspace
              name: workspace-pvc
            - mountPath: /etc/openclaw
              name: config-writable
      volumes:
        - name: config-source
          configMap:
            name: openclaw-config
        - name: config-writable
          emptyDir: {}
  volumeClaimTemplates:
    - metadata:
        name: workspace-pvc
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 10Gi
```

```bash
kubectl apply -f manifests/openclaw-template-gvisor.yaml
```

### 6.5 Create a gVisor warm pool

```yaml
# manifests/openclaw-warmpool-gvisor.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: openclaw-gvisor-pool
  namespace: openclaw-agents
spec:
  sandboxTemplateRef:
    name: openclaw-gvisor
  replicas: 3
```

```bash
kubectl apply -f manifests/openclaw-warmpool-gvisor.yaml
```

This triggers Karpenter to provision gVisor-capable nodes automatically (since the warm pool pods need `runtimeClassName: gvisor` → node selector → gVisor NodePool).

> **Note:** We set `terminationGracePeriodSeconds: 10` because gVisor's Sentry intercepts SIGTERM but the Node.js process may not handle it cleanly, causing pods to hang in `Terminating` state. A shorter grace period ensures stuck pods are force-killed promptly.

### 6.6 Claim a gVisor-isolated sandbox

```yaml
# manifests/openclaw-claim-gvisor.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxClaim
metadata:
  name: secure-agent-001
  namespace: openclaw-agents
spec:
  sandboxTemplateRef:
    name: openclaw-gvisor
```

```bash
kubectl apply -f manifests/openclaw-claim-gvisor.yaml
```

### 6.7 Verify gVisor isolation

```bash
# Get the sandbox name assigned to the claim
SANDBOX_NAME=$(kubectl get sandboxclaim secure-agent-001 -n openclaw-agents -o jsonpath='{.status.sandbox.name}')
echo "Claimed sandbox: $SANDBOX_NAME"

# Check the pod's runtimeClassName
kubectl get pod $SANDBOX_NAME -n openclaw-agents -o jsonpath='{.spec.runtimeClassName}'
# Expected: gvisor

# Verify the node has the gVisor label
kubectl get pod $SANDBOX_NAME -n openclaw-agents -o jsonpath='{.spec.nodeName}' | \
  xargs kubectl get node -o jsonpath='{.metadata.labels.runtime}'
# Expected: gvisor

# Verify from inside the container — dmesg shows gVisor's Sentry kernel
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- dmesg 2>&1 | head -5
# Expected: "Starting gVisor..." or similar Sentry output

# Confirm gVisor kernel (not the host kernel)
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- uname -r
# Expected: 4.19.0-gvisor (NOT the real host kernel version)

# Attempt to change system time — blocked by gVisor
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- date -s "2020-01-01"
# Expected: "date: cannot set date: Operation not permitted"
```

---


## Module 7: Isolate with Kata Containers (VM-level)

Kata Containers runs each pod inside a lightweight virtual machine using Cloud Hypervisor (CLH). Unlike gVisor's user-space kernel, Kata provides a **full hardware-enforced kernel boundary** — each agent gets its own Linux kernel running in a micro-VM. This is the strongest isolation tier, ideal for executing completely untrusted or hostile code.

### 7.1 Why Kata for AI agents?

| Threat | How Kata helps |
|--------|---------------|
| Kernel zero-day exploit | Each pod has its own kernel — exploit is contained within the micro-VM |
| Container escape | There is no shared kernel to escape to — the host kernel is behind a VMM boundary |
| Cross-tenant data leakage | Hardware-enforced memory isolation between VMs |
| Compromised agent pivoting to node | The agent sees a virtual machine, not the real node |

### 7.2 gVisor vs Kata — when to use which

| | gVisor | Kata |
|---|---|---|
| Isolation mechanism | User-space kernel (Sentry) | Hardware VM (Cloud Hypervisor) |
| Instance requirement | Any instance | `.metal` only |
| Cold start overhead | ~0ms | ~1-3s (hidden by warm pool) |
| Memory overhead | ~20-50MB | ~128-256MB per VM |
| Syscall compatibility | Subset implemented by Sentry | Full Linux kernel |
| Best for | General untrusted code | Hostile workloads, compliance requirements |

### 7.3 Create a Kata-capable Karpenter NodePool

Kata requires bare metal instances for hardware virtualization. This NodePool provisions Graviton `.metal` instances with Kata + Cloud Hypervisor installed at boot:

```yaml
# manifests/nodepool-kata.yaml
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: kata
spec:
  template:
    metadata:
      labels:
        runtime: kata
    spec:
      taints:
        - key: runtime
          value: kata
          effect: NoSchedule
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: kata
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["arm64"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand"]
        - key: node.kubernetes.io/instance-type
          operator: In
          values: ["m7g.metal", "c7g.metal", "m6g.metal"]
  limits:
    cpu: "192"
    memory: 512Gi
  disruption:
    consolidationPolicy: WhenEmpty
    consolidateAfter: 60m
---
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: kata
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
    MIME-Version: 1.0
    Content-Type: multipart/mixed; boundary="//"

    --//
    Content-Type: text/x-shellscript; charset="us-ascii"

    #!/bin/bash
    set -euxo pipefail

    # Install Kata Containers with Cloud Hypervisor VMM
    KATA_VERSION="3.32.0"
    ARCH=$(uname -m)
    # Kata release assets use 'arm64' not 'aarch64'
    [ "$ARCH" = "aarch64" ] && DL_ARCH="arm64" || DL_ARCH="amd64"

    # Download and install (3.32+ uses .tar.zst)
    yum install -y zstd
    curl -fsSL -o /tmp/kata-static.tar.zst \
      "https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/kata-static-${KATA_VERSION}-${DL_ARCH}.tar.zst"
    tar --use-compress-program=unzstd -xf /tmp/kata-static.tar.zst -C /
    rm -f /tmp/kata-static.tar.zst

    # Link binaries
    ln -sf /opt/kata/bin/kata-runtime /usr/local/bin/kata-runtime
    ln -sf /opt/kata/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2

    # Register kata runtime with containerd using CLH configuration
    cat <<'EOT' >> /etc/containerd/config.toml

    [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.kata-clh]
      runtime_type = "io.containerd.kata.v2"
      privileged_without_host_devices = true
      pod_annotations = ["io.katacontainers.*"]

    [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.kata-clh.options]
      ConfigPath = "/opt/kata/share/defaults/kata-containers/configuration-clh.toml"
    EOT

    systemctl restart containerd
    --//
```

```bash
envsubst '$CLUSTER_NAME' < manifests/nodepool-kata.yaml | kubectl apply -f -
```

> **Note:** `.metal` instances are significantly more expensive than standard instances (~$6.50/hr for `m7g.metal`). The `consolidateAfter: 60m` gives ample time before Karpenter removes idle metal nodes.

### 7.4 Create the RuntimeClass

```yaml
# manifests/runtimeclass-kata.yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: kata
handler: kata-clh
scheduling:
  nodeSelector:
    runtime: kata
  tolerations:
    - key: runtime
      value: kata
      effect: NoSchedule
```

```bash
kubectl apply -f manifests/runtimeclass-kata.yaml
```

Like gVisor, the `scheduling` block ensures Kata pods land only on `.metal` nodes with the Kata runtime installed.

### 7.5 Create a Kata SandboxTemplate

```yaml
# manifests/openclaw-template-kata.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: openclaw-kata
  namespace: openclaw-agents
spec:
  podTemplate:
    metadata:
      labels:
        app: openclaw
        isolation: kata
    spec:
      runtimeClassName: kata
      terminationGracePeriodSeconds: 10
      serviceAccountName: openclaw-agent-sa
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      initContainers:
        - name: copy-config
          image: busybox
          command: ["sh", "-c", "cp /config-src/openclaw.json /etc/openclaw/openclaw.json"]
          volumeMounts:
            - mountPath: /config-src
              name: config-source
            - mountPath: /etc/openclaw
              name: config-writable
      containers:
        - name: openclaw
          image: ghcr.io/openclaw/openclaw:2026.5.4-slim
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              memory: "1Gi"
          env:
            - name: AWS_REGION
              value: "us-east-1"
            - name: OPENCLAW_CONFIG_PATH
              value: "/etc/openclaw/openclaw.json"
          command:
            - node
            - dist/index.js
            - gateway
            - --bind=lan
            - --port
            - "18789"
            - --allow-unconfigured
          ports:
            - containerPort: 18789
          volumeMounts:
            - mountPath: /home/node/.openclaw/workspace
              name: workspace-pvc
            - mountPath: /etc/openclaw
              name: config-writable
      volumes:
        - name: config-source
          configMap:
            name: openclaw-config
        - name: config-writable
          emptyDir: {}
  volumeClaimTemplates:
    - metadata:
        name: workspace-pvc
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 10Gi
```

```bash
kubectl apply -f manifests/openclaw-template-kata.yaml
```

### 7.6 Create a Kata warm pool

The warm pool is critical for Kata — it pre-boots micro-VMs so that claiming a sandbox is instant despite the ~1-3s VM boot time. We use 2 replicas (vs 3 for gVisor) to manage `.metal` cost:

```yaml
# manifests/openclaw-warmpool-kata.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: openclaw-kata-pool
  namespace: openclaw-agents
spec:
  sandboxTemplateRef:
    name: openclaw-kata
  replicas: 2
```

```bash
kubectl apply -f manifests/openclaw-warmpool-kata.yaml
```

This triggers Karpenter to provision a `.metal` node. Watch the node come up:

```bash
kubectl get nodes -w -l runtime=kata
```

### 7.7 Claim a Kata-isolated sandbox

```yaml
# manifests/openclaw-claim-kata.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxClaim
metadata:
  name: vm-agent-001
  namespace: openclaw-agents
spec:
  sandboxTemplateRef:
    name: openclaw-kata
```

```bash
kubectl apply -f manifests/openclaw-claim-kata.yaml
```

### 7.8 Verify Kata VM isolation

```bash
# Get the sandbox name assigned to the claim
SANDBOX_NAME=$(kubectl get sandboxclaim vm-agent-001 -n openclaw-agents -o jsonpath='{.status.sandbox.name}')
echo "Claimed sandbox: $SANDBOX_NAME"

# Verify runtimeClassName
kubectl get pod $SANDBOX_NAME -n openclaw-agents -o jsonpath='{.spec.runtimeClassName}'
# Expected: kata

# Verify the node is a .metal instance
NODE=$(kubectl get pod $SANDBOX_NAME -n openclaw-agents -o jsonpath='{.spec.nodeName}')
kubectl get node $NODE -o jsonpath='{.metadata.labels.node\.kubernetes\.io/instance-type}'
# Expected: m7g.metal (or c7g.metal / m6g.metal)

# Check kernel — Kata runs its own guest kernel, NOT the host's
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- uname -r
# Expected: 6.18.x (Kata's guest kernel, different from the host's 6.18.x-amzn2023)

# Check dmesg — shows a full Linux boot sequence inside the micro-VM
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- dmesg 2>&1 | head -5
# Expected:
#   [0.000000] Booting Linux on physical CPU 0x0000000000 [0x411fd401]
#   [0.000000] Linux version 6.18.35 ...
#   [0.000000] KASLR enabled
#   [0.000000] Machine model: linux,dummy-virt
#   [0.000000] efi: UEFI not found.

# Check nproc — shows only the allocated vCPU, not the host's 64 cores
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- nproc
# Expected: 1

# /proc/cpuinfo — on ARM/Graviton, Cloud Hypervisor passes through the
# host CPU features via VHE (Virtualization Host Extensions), so you'll
# see the real Graviton3 CPU info. The isolation is still VM-level
# (separate kernel, memory, PID namespace) — just not visible via cpuinfo on ARM.
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- cat /proc/cpuinfo | head -10
```

### 7.9 Compare isolation tiers

Run this on all three tiers to see the difference:

```bash
GVISOR_POD=$(kubectl get sandboxclaim secure-agent-001 -n openclaw-agents -o jsonpath='{.status.sandbox.name}')

echo "=== runc (standard) ==="
kubectl exec -n openclaw-agents openclaw-agent -c openclaw -- uname -r
kubectl exec -n openclaw-agents openclaw-agent -c openclaw -- nproc
# Expected: 6.18.30-61.116.amzn2023.aarch64 (host kernel), 2 (cgroup limit)

echo "=== gVisor ==="
kubectl exec -n openclaw-agents $GVISOR_POD -c openclaw -- uname -r
kubectl exec -n openclaw-agents $GVISOR_POD -c openclaw -- nproc
# Expected: 4.19.0-gvisor (synthetic kernel), 1

echo "=== Kata (VM) ==="
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- uname -r
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- nproc
kubectl exec -n openclaw-agents $SANDBOX_NAME -c openclaw -- dmesg 2>&1 | head -3
# Expected: 6.18.35 (guest kernel), 1, full Linux boot log
```

| | **runc** | **gVisor** | **Kata (VM)** |
|---|---|---|---|
| `uname -r` | `6.18.30-61.116.amzn2023` (host) | `4.19.0-gvisor` (synthetic) | `6.18.35` (guest VM kernel) |
| `nproc` | 2 (cgroup limit) | 1 | 1 (allocated vCPU) |
| `dmesg` | Host log (or denied) | gVisor Sentry log | Full Linux boot inside micro-VM |
| Isolation | Linux namespaces | User-space kernel | Hardware VM boundary |

---

## Module 8: Restrict Egress with Network Policy

Without an egress allowlist, a compromised agent can exfiltrate data or probe internal services. EKS supports native Kubernetes `NetworkPolicy` enforced by VPC CNI.

### 8.1 Default deny all egress

```yaml
# manifests/deny-all-egress.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: openclaw-deny-all-egress
  namespace: openclaw-agents
spec:
  podSelector:
    matchLabels:
      app: openclaw
  policyTypes:
    - Egress
```

```bash
kubectl apply -f manifests/deny-all-egress.yaml
```

### 8.2 Allow only DNS and HTTPS egress

```yaml
# manifests/allow-agent-egress.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: openclaw-allow-egress
  namespace: openclaw-agents
spec:
  podSelector:
    matchLabels:
      app: openclaw
  policyTypes:
    - Egress
  egress:
    # DNS resolution (kube-dns)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # HTTPS egress (Bedrock, STS, package registries)
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
      ports:
        - protocol: TCP
          port: 443
```

```bash
kubectl apply -f manifests/allow-agent-egress.yaml
```

### 8.3 Verify egress enforcement

```bash
# Should succeed — HTTPS to Bedrock
kubectl exec -n openclaw-agents openclaw-agent -c openclaw -- \
  curl -s -o /dev/null -w '%{http_code}' https://bedrock-runtime.ap-southeast-1.amazonaws.com
# Expected: 403 (AWS auth error, but connection succeeded)

# Should fail — non-443 port
kubectl exec -n openclaw-agents openclaw-agent -c openclaw -- \
  curl -s --connect-timeout 5 http://example.com:80 2>&1 || echo "BLOCKED"
# Expected: timeout or BLOCKED
```

---

---

## Module 9: Cleanup

```bash
# Delete all sandbox resources
kubectl delete sandboxclaims --all -n openclaw-agents
kubectl delete sandboxwarmpools --all -n openclaw-agents
kubectl delete sandboxtemplates --all -n openclaw-agents
kubectl delete sandboxes --all -n openclaw-agents
kubectl delete networkpolicies --all -n openclaw-agents
kubectl delete configmap openclaw-config -n openclaw-agents
kubectl delete namespace openclaw-agents

# Remove the agent-sandbox controller
kubectl delete -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${SANDBOX_VERSION}/extensions.yaml
kubectl delete -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${SANDBOX_VERSION}/manifest.yaml

# Remove RuntimeClasses and isolation NodePools
kubectl delete runtimeclass gvisor kata
kubectl delete nodepool gvisor kata
kubectl delete ec2nodeclass gvisor kata

# Scale down Karpenter to avoid node replacement during destroy
kubectl scale deployment karpenter -n kube-system --replicas=0

# Patch finalizers to avoid deadlock
kubectl get nodepool -o name | xargs -I{} kubectl patch {} --type merge -p '{"metadata":{"finalizers":null}}'
kubectl get ec2nodeclass -o name | xargs -I{} kubectl patch {} --type merge -p '{"metadata":{"finalizers":null}}'

# Delete the cluster
eksctl delete cluster --name ${CLUSTER_NAME} --region ${AWS_REGION}

# Clean up IAM resources
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

aws eks delete-pod-identity-association \
  --cluster-name ${CLUSTER_NAME} \
  --association-id $(aws eks list-pod-identity-associations \
    --cluster-name ${CLUSTER_NAME} \
    --query 'associations[0].associationId' --output text) 2>/dev/null || true

aws iam detach-role-policy \
  --role-name OpenClawAgentRole \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/OpenClawBedrockPolicy
aws iam delete-role --role-name OpenClawAgentRole
aws iam delete-policy --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/OpenClawBedrockPolicy
```

---

---

---

## Additional Resources

- [Running Agents on Kubernetes with Agent Sandbox (Kubernetes Blog)](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/)
- [kubernetes-sigs/agent-sandbox GitHub](https://github.com/kubernetes-sigs/agent-sandbox)
- [Agent Sandbox on EKS (AI on EKS)](https://awslabs.github.io/ai-on-eks/docs/infra/agents/agent-sandbox)
- [OpenClaw Sandbox Example](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/examples/openclaw-sandbox)
- [Karpenter Documentation](https://karpenter.sh/docs/)
- [gVisor on Kubernetes](https://gvisor.dev/docs/user_guide/quick_start/kubernetes/)
- [Kata Containers Documentation](https://katacontainers.io/docs/)
- [Kata with Cloud Hypervisor](https://github.com/kata-containers/kata-containers/blob/main/docs/hypervisors.md#cloud-hypervisor)
- [Kubernetes Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
