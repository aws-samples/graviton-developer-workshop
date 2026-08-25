# Disaggregated LLM Inference on Amazon EKS with NVIDIA Dynamo — Lab Guide

This hands-on lab deploys **Qwen3-8B** with **disaggregated prefill/decode serving** on Amazon EKS using **NVIDIA Dynamo v1.4.0** and the **vLLM** backend. You'll run the compute-bound prefill phase and the memory-bandwidth-bound decode phase on *separate* GPUs, connected by NVIDIA's NIXL KV-cache transfer library, and provision the GPU capacity just-in-time with EKS Auto Mode.

Target region: **us-east-2**. GPUs: **2× g6e.12xlarge** (4× NVIDIA L40S 45 GB per node; one GPU used per worker), with prefill and decode on separate nodes connected by **EFA** for cross-node KV-cache transfer.

> This lab follows the official [Dynamo EKS Setup Guide](https://docs.nvidia.com/dynamo/dev/kubernetes/installation/managed-kubernetes/eks/eks-setup) and the [Disaggregated Serving Guide](https://docs.nvidia.com/dynamo/dev/kubernetes/disaggregated-serving/overview). No Terraform wrapper or `ai-on-eks` clone required — just `eksctl`, `helm`, and `kubectl`.

---

## What is disaggregated inference?

LLM inference has two phases with opposite hardware profiles:

| Phase | What it does | Bottleneck | Wants |
|-------|--------------|-----------|-------|
| **Prefill** | Processes the whole prompt, produces the first token + KV cache | **Compute** (GEMM-heavy, token-parallel) | High FLOPs |
| **Decode** | Generates tokens one at a time, autoregressively | **Memory bandwidth** (reads KV cache every step) | Fast HBM, high concurrency |

Traditional ("aggregated") serving co-locates both phases on the same GPU, so they contend for the same resources and a long prompt can head-of-line-block ongoing decodes. **Disaggregation** splits them onto separate GPUs/pods that scale independently. NVIDIA Dynamo orchestrates this: a **Smart Router** does KV-aware routing, the request hits a **prefill worker** first, the KV cache is transferred to a **decode worker** over **NIXL**, and the decode worker streams the response.

### Why bother?

- **No head-of-line blocking** — long prefills don't stall active decodes.
- **Independent scaling** — add prefill workers for prompt-heavy (RAG) traffic, add decode workers for generation-heavy (reasoning) traffic.
- **Right-sized parallelism** — each phase can use a different tensor-parallel degree.

---

## Architecture

```
                          Amazon EKS Auto Mode (us-east-2)
┌───────────────────────────────────────────────────────────────────────┐
│  dynamo-system namespace                                               │
│                                                                        │
│   ┌─────────────────────────────┐                                      │
│   │  Frontend + Smart Router     │  (CPU — Auto Mode general node)     │
│   │  OpenAI-compatible :8000     │                                      │
│   └──────────────┬──────────────┘                                      │
│                  │                                                      │
│          ┌───────┴───────┐         same AZ (EFA can't cross AZ)        │
│          ▼               ▼                                             │
│   ┌──────────────┐   ┌──────────────┐                                  │
│   │  Prefill     │   │  Decode      │                                  │
│   │  Worker      │   │  Worker      │                                  │
│   │  g6e node A   │══▶│  g6e node B  │  NIXL / LIBFABRIC over EFA       │
│   │  L40S 45GB    │KV │  L40S 45GB   │  (GPUDirect RDMA?, cross-node)   │
│   └──────────────┘   └──────────────┘                                  │
│  g6e.12xlarge (4 GPU) g6e.12xlarge     ← anti-affinity forces 2 nodes  │
│                                                                        │
│   Dynamo Platform: Operator · NATS                                     │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

- **AWS CLI**, **kubectl** (v1.30+), **eksctl**, and **Helm** (v3+) installed.
- An AWS account in **us-east-2** with quota for **G-instance vCPUs** — 2× g6e.12xlarge = **96 vCPUs** of "Running On-Demand G and VT instances". Request an increase via Service Quotas if needed.
- A **Hugging Face token** — Qwen3-8B is public but the runtime reads `HF_TOKEN` for model access.

> **EFA note.** g6e.12xlarge is a multi-GPU node (4× L40S). Each worker takes one GPU, and **pod anti-affinity forces prefill and decode onto separate nodes** so their KV cache crosses the network over EFA. EFA traffic stays within one AZ, so both nodes are pinned to the same AZ (us-east-2b) via the NodePool. EKS Auto Mode ships the EFA device plugin, so `vpc.amazonaws.com/efa` is available without extra setup. Whether multi-GPU g6e actually reaches GPUDirect-RDMA line rate over EFA is what this lab measures (see verification).

> **Cost warning.** This lab runs **two on-demand g6e.12xlarge instances** plus general-purpose Auto Mode nodes. Check the [AWS Pricing Calculator](https://calculator.aws/) for current rates, and always run the cleanup step (Step 7) when done.

---

## Step 1 — Create the EKS Auto Mode cluster

EKS Auto Mode bundles Karpenter, GPU drivers, EBS CSI, and node management — no GPU Operator needed.

```bash
export AWS_REGION="us-east-2"
export CLUSTER_NAME="dynamo-lab"

cat <<EOF > eksctl-config.yaml
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: ${CLUSTER_NAME}
  region: ${AWS_REGION}
  version: "1.32"
autoModeConfig:
  enabled: true
addons:
  - name: amazon-efs-csi-driver
EOF

eksctl create cluster -f eksctl-config.yaml
```

This takes ~15 minutes. When done, `kubectl` is automatically configured:

```bash
kubectl get nodes   # Initially 0 nodes — Auto Mode provisions on demand
```

---

## Step 2 — Create the StorageClass and GPU NodePool

The Dynamo Helm chart deploys a NATS StatefulSet that needs a PVC. EKS Auto Mode requires an explicit StorageClass. Also apply the GPU NodePool so Auto Mode knows it can provision g6e instances:

```bash
kubectl apply -f manifests/auto-ebs-sc.yaml
kubectl apply -f manifests/automode-np-gpu.yaml
```

Verify:

```bash
kubectl get storageclass auto-ebs-sc
kubectl get nodepool gpu
```

---

## Step 3 — Install NVIDIA Dynamo platform

Single Helm chart from NGC — installs the Dynamo operator, NATS, and CRDs:

```bash
export DYNAMO_VERSION="1.4.0"
export DYNAMO_NAMESPACE="dynamo-system"

helm fetch https://helm.ngc.nvidia.com/nvidia/ai-dynamo/charts/dynamo-platform-${DYNAMO_VERSION}.tgz

helm install dynamo-platform dynamo-platform-${DYNAMO_VERSION}.tgz \
  --namespace ${DYNAMO_NAMESPACE} \
  --create-namespace
```

Verify the platform is running:

```bash
kubectl get pods -n ${DYNAMO_NAMESPACE}
# Expected: dynamo-platform-dynamo-operator-controller-manager  1/1  Running
#           dynamo-platform-nats-0                              2/2  Running

kubectl get crds | grep dynamo
# Expected: dynamographdeployments.nvidia.com, dynamocomponentdeployments.nvidia.com, etc.
```

---

## Step 4 — Create the Hugging Face token secret

```bash
export HF_TOKEN="hf_xxxxxxxxxxxxxxxxxxxxx"  # your token

kubectl create secret generic hf-token-secret \
  --from-literal=HF_TOKEN=${HF_TOKEN} \
  -n ${DYNAMO_NAMESPACE}
```

---

## Step 5 — Deploy the disaggregated inference graph

```bash
kubectl apply -f manifests/qwen3-8b-vllm-disagg.yaml
```

Watch the pods come up. EKS Auto Mode provisions two g6e.12xlarge nodes (one for prefill, one for decode) in the same AZ. First start is slow — node boot + ~16 GB model download:

```bash
# Watch nodes appear
kubectl get nodes -w

# Watch pods
kubectl get pods -n ${DYNAMO_NAMESPACE} -w

# Follow worker logs (model download progress)
kubectl logs -n ${DYNAMO_NAMESPACE} \
  -l nvidia.com/dynamo-graph-deployment-name=qwen3-8b-disagg \
  --all-containers=true --max-log-requests=5 -f
```

Wait until the frontend and both workers are `Running` and `Ready`.

---

## Step 6 — Test inference

```bash
kubectl port-forward svc/qwen3-8b-disagg-frontend 8000:8000 -n ${DYNAMO_NAMESPACE} &

# Health check
curl -s http://localhost:8000/health

# Short prompt — measure TTFT
curl -s -o /dev/null -w "TTFT: %{time_starttransfer}s\nTotal: %{time_total}s\n" \
  -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen3-8B","messages":[{"role":"user","content":"Explain disaggregated inference in one sentence."}],"max_tokens":128,"stream":true}'

# Long prompt (~1800 tokens) — triggers full prefill → NIXL transfer → decode path
LONG_PROMPT=$(python3 -c "print('Summarize the following quarterly report: ' + 'Revenue grew 12 percent quarter over quarter driven by strong demand in cloud services. ' * 200)")
curl -s -o /dev/null -w "TTFT: %{time_starttransfer}s\nTotal: %{time_total}s\n" \
  -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"Qwen/Qwen3-8B\",\"messages\":[{\"role\":\"user\",\"content\":\"$LONG_PROMPT\"}],\"max_tokens\":50,\"stream\":true}"
```

`time_starttransfer` = time until the first SSE token arrives ≈ **TTFT** (includes prefill + KV transfer + first decode step).

### Verify EFA + disaggregation are working

**1. Confirm NIXL brought up the LIBFABRIC (EFA) backend** — grep a worker's startup logs:

```bash
kubectl logs -n ${DYNAMO_NAMESPACE} \
  -l nvidia.com/dynamo-sub-component-type=prefill --tail=200 | grep -i "nixl\|libfabric\|efa"
```

You want to see NIXL instantiate the LIBFABRIC backend (not a UCX/TCP fallback).

**2. Check the decode worker logs for KV Transfer metrics** — this confirms NIXL moved the KV cache from prefill to decode over EFA:

```bash
kubectl logs -n ${DYNAMO_NAMESPACE} \
  -l nvidia.com/dynamo-sub-component-type=decode --tail=20
```

You should see output like:

```
KV Transfer metrics: Num successful transfers=1, Avg xfer time (ms)=9.4, Avg MB per transfer=256.5, Throughput (MB/s)=~27000
Engine 000: ... External prefix cache hit rate: 100.0%
```

Key indicators:
- **`Avg xfer time (ms)` in single digits** — GPUDirect RDMA over EFA (vs ~844ms over TCP on g5). This is the whole point of EFA.
- **`Num successful transfers=1`** — NIXL moved KV cache from prefill to decode
- **`External prefix cache hit rate: 100.0%`** — decode is receiving ALL KV from prefill, not computing any locally
- **Prefill worker shows `component="prefill"`** and decode worker shows `component="backend"` — requests are split across workers

Watch both workers in real time:

```bash
kubectl logs -n ${DYNAMO_NAMESPACE} \
  -l nvidia.com/dynamo-graph-deployment-name=qwen3-8b-disagg \
  --all-containers=true --max-log-requests=5 -f \
  | grep -Ei "KV Transfer|request received|request completed|prefix cache"
```

**3. (Optional) Confirm the EFA device is attached** to a worker node:

```bash
POD=$(kubectl get pod -n ${DYNAMO_NAMESPACE} -l nvidia.com/dynamo-sub-component-type=prefill -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n ${DYNAMO_NAMESPACE} $POD -- fi_info -p efa 2>/dev/null | head
```

---

## Step 6b (optional) — Scale independently

The core advantage of disaggregation — scale each phase independently:

```bash
# More prefill workers for prompt-heavy traffic (RAG, long docs)
kubectl patch dgd qwen3-8b-disagg -n ${DYNAMO_NAMESPACE} --type merge \
  -p '{"spec":{"services":{"VLLMPrefillWorker":{"replicas":2}}}}'

# More decode workers for generation-heavy traffic (reasoning, long outputs)
kubectl patch dgd qwen3-8b-disagg -n ${DYNAMO_NAMESPACE} --type merge \
  -p '{"spec":{"services":{"VLLMDecodeWorker":{"replicas":2}}}}'
```

EKS Auto Mode provisions additional g6e nodes automatically.

---

## Step 7 — Cleanup (do not skip)

```bash
# Delete the inference graph → GPU nodes deprovision automatically
kubectl delete -f manifests/qwen3-8b-vllm-disagg.yaml

# Uninstall Dynamo platform
helm uninstall dynamo-platform -n ${DYNAMO_NAMESPACE}

# Clean up NATS PVC
kubectl delete pvc -n ${DYNAMO_NAMESPACE} --all

# Delete NodePool and StorageClass
kubectl delete -f manifests/automode-np-gpu.yaml
kubectl delete -f manifests/auto-ebs-sc.yaml

# Delete the EKS cluster
eksctl delete cluster --name ${CLUSTER_NAME} --region ${AWS_REGION}
```

Confirm in the EC2 console that no g6e instances remain running.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Worker pod `Pending` forever | No GPU quota, NodePool GPU limit too low, or no single AZ with capacity for 2 nodes | Check `kubectl describe pod`; verify 96 vCPU G-instance quota; confirm NodePool `limits` fit 2× g6e.12xlarge; g6e.12xlarge is in us-east-2a/2b/2c |
| Worker `CrashLoopBackOff`, OOM | KV cache exceeds GPU memory | Unlikely on 96 GB GPU; if so lower `--gpu-memory-utilization` or `--max-model-len` |
| `nixlNotFoundError: NIXL_ERR_NOT_FOUND` | EFA device not present but LIBFABRIC requested | Confirm `vpc.amazonaws.com/efa: "1"` in the DGD and that nodes are g6e (EFA-capable) |
| NIXL falls back to UCX/TCP | EFA image or resource missing | Confirm the `:1.4.0-efa` image and the `vpc.amazonaws.com/efa` resource request |
| Startup probe fails (~10 min) | Model still downloading | Increase model download timeout; check egress to huggingface.co |
| 401/403 pulling model | Invalid HF token | Recreate `hf-token-secret` |
| No NIXL transfers visible | Short prompts handled locally (by design) | Send a long prompt (see Step 6) |
| NIXL initialization failure | `sharedMemory.size` too small or missing | Confirm `sharedMemory.size: 16Gi` in the DGD |
| Pods scheduled to different AZs | Missing/failed pod affinity | Confirm the `podAffinity` block on both workers (EFA can't cross AZ) |

---

## Files in this lab

```
dynamo-disaggregated/
├── README.md                                   # this guide
└── manifests/
    ├── auto-ebs-sc.yaml                        # StorageClass for NATS PVC
    ├── automode-np-gpu.yaml                    # EKS Auto Mode GPU NodePool (g6e, EFA)
    └── qwen3-8b-vllm-disagg.yaml              # disaggregated DGD (prefill + decode, EFA)
```

---

## References

- [NVIDIA Dynamo — EKS Setup Guide](https://docs.nvidia.com/dynamo/dev/kubernetes/installation/managed-kubernetes/eks/eks-setup)
- [NVIDIA Dynamo — Disaggregated Serving](https://docs.nvidia.com/dynamo/dev/kubernetes/disaggregated-serving/overview)
- [NVIDIA Dynamo — Kubernetes Quickstart](https://docs.nvidia.com/dynamo/dev/kubernetes/getting-started/quickstart)
- [NIXL — GPU-to-GPU Transfer Library](https://github.com/ai-dynamo/nixl)
- [Dynamo GitHub](https://github.com/ai-dynamo/dynamo)
