# Deploying xpilot-webnet

Live at **https://xpilot.spdns.eu**. This covers the one-time cluster
setup; after that, every push to `main` builds, tests, and deploys
automatically via `.github/workflows/deploy.yml` (once the CI runner in
section 5 is live).

Cluster facts: k3s v1.22.7+k3s1, 3 nodes (`node-02` arm64 control-plane,
`node-03`/`node-04` amd64), Traefik 2.6.1 bundled ingress controller,
fronted by an existing shared OCI Load Balancer (`150.230.10.165`) that
already served other traffic before this project.

## 0. OCI Load Balancer

```bash
oci lb backend-set create \
  --load-balancer-id <LB_OCID> \
  --name bs_xpilot_traefik \
  --policy ROUND_ROBIN \
  --health-checker-protocol HTTP \
  --health-checker-port 80 \
  --health-checker-url-path /status \
  --health-checker-return-code 200 \
  --backends '[
    {"ipAddress":"10.0.0.82","port":80},
    {"ipAddress":"10.0.0.104","port":80},
    {"ipAddress":"10.0.0.251","port":80}
  ]'

oci lb listener create \
  --load-balancer-id <LB_OCID> \
  --name listener_xpilot \
  --port 8888 \
  --protocol HTTP \
  --default-backend-set-name bs_xpilot_traefik
```

The health check is HTTP (not TCP) hitting `/status` directly -- a TCP
check only proves Traefik is listening, not that a request reaches a
working pod, which was the actual failure mode hit during setup (see
`ARCHITECTURE.md` section 2.3).

A **routing policy** lets xpilot share the LB's pre-existing port-80
listener (which already served unrelated traffic) rather than repointing
it:

```bash
cat > /tmp/xpilot-routing-policy.json <<'JSON'
{
  "name": "xpilot_host_routing",
  "conditionLanguageVersion": "V1",
  "rules": [{
    "name": "xpilot_rule",
    "condition": "any(http.request.headers[(i 'host')] eq (i 'xpilot.spdns.eu'))",
    "actions": [{"name": "FORWARD_TO_BACKENDSET", "backendSetName": "bs_xpilot_traefik"}]
  }]
}
JSON

oci lb routing-policy create --load-balancer-id <LB_OCID> \
  --name xpilot_host_routing --condition-language-version V1 \
  --rules file:///tmp/xpilot-routing-policy.json --wait-for-state SUCCEEDED

oci lb listener update --load-balancer-id <LB_OCID> \
  --listener-name <EXISTING_PORT_80_LISTENER_NAME> \
  --routing-policy-name xpilot_host_routing --force
```

## 1. Docker Hub

`omnom62/xpilot-webnet` -- create an access token (Account Settings ->
Personal access tokens -> Read & Write), add as GitHub repo secrets
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. Repo is public by default on
first push, which is what the manifests assume (no `imagePullSecrets`).

## 2. Security lists -- flannel + public access

**flannel VXLAN (UDP 8472)** must be allowed between nodes, or cross-node
pod networking silently fails (this was a real, years-old fault on this
cluster -- see `ARCHITECTURE.md` 2.3):

```bash
oci network security-list get --security-list-id <SEC_LIST_OCID> \
  --query "data.\"ingress-security-rules\"[?protocol=='17']"
```

**Public HTTP (80) and HTTPS (443)** must be open to `0.0.0.0/0` -- Let's
Encrypt's validation servers come from arbitrary IPs worldwide, can't be
individually allowlisted.

**Critical safety note**: `oci network security-list update` *replaces
the entire rule set*. Never pass a partial rule list -- always read the
full current list, append in code, write the complete list back. This
list also holds SSH/API-server access rules that a naive update would
silently destroy.

## 3. cert-manager (pinned to v1.12.15)

The *latest* cert-manager release needs a CRD field this cluster's
Kubernetes (1.22.7) doesn't support. v1.12 is the last LTS line
documented as supporting Kubernetes 1.22-1.31:

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.12.15/cert-manager.yaml
kubectl wait --for=condition=Available --timeout=120s deployment --all -n cert-manager
```

## 4. CoreDNS hairpin-NAT fix

cert-manager's HTTP-01 self-check runs from inside the cluster and tries
to reach the domain's public IP, which must then route back in -- many
networks (this one included) can't do that. Fix:

```bash
kubectl apply -f k8s/cluster-infra/coredns-custom.yaml
kubectl rollout restart deployment coredns -n kube-system
```

## 5. Deploy + TLS

```bash
cd k8s
kustomize edit set image omnom62/xpilot-webnet=omnom62/xpilot-webnet:manual
kubectl apply -k .
kubectl get certificate -n xpilot -w   # should reach READY=True
```

`k8s/ingress.yaml`'s annotation is already `letsencrypt-prod` -- validate
against `letsencrypt-staging` first if making changes, to avoid burning
production rate limits on a misconfiguration.

## 6. Port 443 (TCP passthrough)

```bash
oci lb backend-set create --load-balancer-id <LB_OCID> \
  --name bs_xpilot_traefik_tls --policy ROUND_ROBIN \
  --health-checker-protocol TCP --health-checker-port 443 \
  --backends '[
    {"ipAddress":"10.0.0.82","port":443},
    {"ipAddress":"10.0.0.104","port":443},
    {"ipAddress":"10.0.0.251","port":443}
  ]'

oci lb listener create --load-balancer-id <LB_OCID> \
  --name listener_xpilot_tls --port 443 --protocol TCP \
  --default-backend-set-name bs_xpilot_traefik_tls
```

TLS terminates at Traefik using the cert-manager certificate, not at the
LB -- the LB does plain TCP passthrough.

## 7. CI runner

```bash
kubectl apply -f k8s/ci-runner/namespace.yaml
kubectl apply -f k8s/ci-runner/rbac.yaml
kubectl create secret generic github-runner-pat -n ci --from-literal=access-token=<GITHUB_PAT>
kubectl apply -f k8s/ci-runner/runner-deployment.yaml
```

RBAC scopes the runner's ServiceAccount to the `xpilot` namespace only --
it cannot touch anything else on this shared cluster.

## Known limitations

- `ws-server` is single-replica by design (in-memory game state).
- Docker Hub `:manual` tag is a manually-managed floating tag today; the
  CI workflow already uses immutable git-SHA tags once the runner is live.
- No autoscaling, no persistent storage.
