# Deploy NVIDIA GPU Operator to Private EKS Cluster

The instructions here shares how to deploy Nvidia GPU Operator v25.3.4 to a private EKS cluster (no internet access)

## Prerequisites

- Private EKS cluster with GPU nodes (g4dn, g5, p3, p4, etc.)
- Bastion host with access to EKS Cluster
- kubectl configured
- Helm 3.x installed
- VPC endpoints pulling images

## 1. Prepare Container Images

Since the cluster is private, mirror required images to ECR using the provided script:

```bash
# All required images are listed in gpu-operator-v25.3.4-images.txt. If you are using a different version, please adjust the images.
# Use the push-to-ecr.sh script to automatically push all images to ECR

./push-to-ecr.sh
```

The script will:
- Create ECR repositories automatically
- Pull all images from NVIDIA Container Registry
- Tag and push them to your ECR registry

## 2. Add NVIDIA Helm Repository

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
```

## 3. Configure GPU Operator Values

```bash
helm fetch https://helm.ngc.nvidia.com/nvidia/charts/gpu-operator-v25.3.4.tgz
```

Update the `values-v25.3.4.yaml` file with your ECR registry. The file has been updated to use precompiled container for Nvidia driver.

## 4. Install GPU Operator

```bash
helm install gpu-operator gpu-operator-v25.3.4.tgz \
  -n gpu-operator \
  --create-namespace \
  -f values-v25.3.4.yaml
```

## 5. Verify Installation

```bash
# Check operator pods
kubectl get pods -n gpu-operator

# Wait for all pods to be running
kubectl wait --for=condition=ready pod -l app=nvidia-operator-validator -n gpu-operator --timeout=600s

# Check GPU nodes are labeled
kubectl get nodes -l nvidia.com/gpu.present=true

# Verify runtime class
kubectl get runtimeclass
```

## 6. Test GPU Access

```bash
kubectl apply -f cuda-test.yaml
```

```bash
kubectl logs cuda-vectoradd
```

You should see the following output:

[Vector addition of 50000 elements]
Copy input data from the host memory to the CUDA device
CUDA kernel launch with 196 blocks of 256 threads
Copy output data from the CUDA device to the host memory
Test PASSED
Done



## Troubleshooting

### Pods stuck in ImagePullBackOff
- Verify ECR repositories exist
- Check IAM roles for node groups have ECR pull permissions
- Ensure VPC endpoints for ECR are configured

### Driver installation fails
- Check node OS compatibility with driver version
- Check driver pod logs: `kubectl logs -n gpu-operator -l app=nvidia-driver-daemonset`

### GPU not detected
- Verify GPU instance type
- Check node labels: `kubectl describe node <node-name>`
- Review validator logs: `kubectl logs -n gpu-operator -l app=nvidia-operator-validator`

## Cleanup

```bash
helm uninstall gpu-operator -n gpu-operator
kubectl delete namespace gpu-operator
```
