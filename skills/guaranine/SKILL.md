---
name: guaranine
description: Reference context for Kian's Guaranine K3s cluster (Kairos nodes over Tailscale, kubeconfig location, PodSecurity restricted rules, disabled components, and networking constraints). Use when deploying workloads, configuring services, debugging cluster issues, or answering questions about the Guaranine cluster, or when invoked with /guaranine.
---

# Guaranine Cluster Reference

Guaranine is Kian's personal K3s cluster: a single cloud control-plane VM plus Linux laptop worker nodes connected exclusively over Tailscale. This skill provides cluster context so you can work with it accurately from any application repository.

## Cluster Topology & Versions

- **Distribution:** K3s (`v1.37.1+k3s1`) on immutable Kairos/Hadron (`v4.4.0`).
- **Control plane:** Single K3s server on an Azure VM (`Standard_B2ls_v2`, 2 vCPU, 4 GiB RAM) using SQLite on a separate retained ext4 disk (`GUARANINE_DATA` at `/var/lib/rancher/k3s`). It carries `node-role.kubernetes.io/control-plane=true:NoSchedule`, so application pods schedule on workers.
- **Workers:** Wi-Fi laptops running Kairos + K3s agent (`systemd-networkd`, `systemd-resolved`, `wpa_supplicant`). Laptops may disconnect or sleep, and local disks do not follow rescheduled pods.
- **Cluster repo:** `/home/kian/guaranine` owns the Azure Terraform (`infra/azure/`), Kairos node configs (`kairos/`), tailnet ACLs (`tailscale/policy.json`), and `default` namespace baseline (`kubernetes/network-policy.yaml`). Per-app deployments belong in their own app repos, not `/home/kian/guaranine`.

## Operator Access & Secrets Boundary

- **Operator directory:** `${GUARANINE_OPERATOR_DIR:-$HOME/.local/state/guaranine-kairos}`
- **Kubeconfig:** `$GUARANINE_OPERATOR_DIR/kubeconfig` (default `~/.local/state/guaranine-kairos/kubeconfig`)
- **Connectivity:** The operator machine must be connected to Tailscale (`100.64.0.0/10` routes preserved). The K3s API is only reachable over Tailscale (TCP `6443`).
- **Privacy rule:** Never copy `kubeconfig`, SSH keys, Tailscale/K3s tokens, `operator.yaml`, `.tfvars`, or `.tfstate` into any git repository or public output. Never commit app secrets (API keys, bot tokens) to Git; load them from Kubernetes `Secret` resources (K3s has `secrets-encryption: true` enabled).

## Disabled K3s Components

`kairos/controlplane.yaml` explicitly disables:
- `traefik` (no preinstalled Ingress controller)
- `servicelb` (no `LoadBalancer` IP provisioning)
- `local-storage` (no default dynamic `StorageClass` / PVC provisioner)
- `metrics-server` (no `kubectl top` unless installed)
- `gateway-api-crd`

Implications:
- Do not use `Service` type `LoadBalancer` or assume a preinstalled Ingress controller or PVC storage class exists unless you install one.
- There is no in-cluster container registry; workers pull images from a registry such as `ghcr.io` (use `imagePullSecrets` for private packages).

## Security & Networking Rules

- **PodSecurity (`restricted`):** Enforced (`enforce`, `audit`, `warn` at `latest`) across all namespaces except `kube-system`. Every workload container must:
  - Run as non-root (`runAsNonRoot: true`, and build Dockerfiles with a non-root user)
  - Set `allowPrivilegeEscalation: false`
  - Drop all capabilities (`capabilities: { drop: ["ALL"] }`)
  - Set `seccompProfile: { type: RuntimeDefault }`
  - Avoid privileged mode, hostPath, and host namespaces (`hostNetwork`, `hostPID`, `hostIPC`)
- **Namespace `NetworkPolicy`:** The `default` namespace has `default-deny` (Ingress + Egress) with only DNS (`kube-dns` TCP/UDP `53` in `kube-system`) allowed. Create a dedicated `Namespace` per app with its own `NetworkPolicy` allowing DNS plus only the specific ingress/egress that app requires.
- **Host & cloud firewalls:**
  - Azure NSG enforces `deny-all-inbound` and `deny-vnet-outbound`.
  - Host `nftables` drops unsolicited physical inbound traffic and blocks pod/host egress to private/LAN/CGNAT/link-local ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`, `169.254.0.0/16`, plus dynamic DHCP `lan4`/`lan6` subnets).
  - Inter-node traffic flows over `tailscale0` (Flannel VXLAN UDP `8472`, API TCP `6443`, kubelet TCP `10250`); ordinary public internet egress (e.g., HTTPS/WSS `443`) is allowed.
- **Exposing services:**
  - Outbound-only workloads (e.g., bots, workers, cron jobs) need no `Service` or exposure.
  - Private tailnet exposure or public HTTP domains must work without inbound host firewall ports—for example via Tailscale (`tailscale/policy.json` grants `"attr": ["funnel"]` to `autogroup:member`) or a Cloudflare Tunnel (`cloudflared`) pod, or `kubectl port-forward` for temporary local testing.
