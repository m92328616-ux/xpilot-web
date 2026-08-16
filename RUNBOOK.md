# xpilot-webnet: full setup runbook

Pure commands, in order -- see `DEPLOYMENT.md` for the "why" behind each
step. Placeholders: `<LB_OCID>`, `<SEC_LIST_OCID>`, `<COMPARTMENT_OCID>`.

## 0. Prerequisites check
```bash
kubectl config current-context
kubectl get nodes -o wide
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"  "}{.status.nodeInfo.architecture}{"\n"}{end}'
kubectl get deployment traefik -n kube-system -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl get ingressclass
```

## 1. Container image (multi-arch)
```bash
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create --use --name xpilot-builder
docker buildx inspect --bootstrap
echo <DOCKERHUB_TOKEN> | docker login -u omnom62 --password-stdin
docker buildx build --platform linux/amd64,linux/arm64 -t omnom62/xpilot-webnet:manual --push .
```

## 2. Kubernetes deploy
```bash
cd k8s
kustomize edit set image omnom62/xpilot-webnet=omnom62/xpilot-webnet:manual
kubectl apply -k .
kubectl rollout status deployment/ws-server -n xpilot
kubectl rollout status deployment/web-server -n xpilot
kubectl get pods -n xpilot -o wide
```

## 3. OCI Load Balancer -- port 8888
```bash
oci lb load-balancer list --compartment-id <COMPARTMENT_OCID> --output table \
  --query "data[].{name:\"display-name\", id:id, ip:\"ip-addresses\"[0].\"ip-address\"}"

oci lb backend-set create --load-balancer-id <LB_OCID> \
  --name bs_xpilot_traefik --policy ROUND_ROBIN \
  --health-checker-protocol HTTP --health-checker-port 80 \
  --health-checker-url-path /status --health-checker-return-code 200 \
  --backends '[{"ipAddress":"10.0.0.82","port":80},{"ipAddress":"10.0.0.104","port":80},{"ipAddress":"10.0.0.251","port":80}]'

oci lb listener create --load-balancer-id <LB_OCID> \
  --name listener_xpilot --port 8888 --protocol HTTP \
  --default-backend-set-name bs_xpilot_traefik

curl http://150.230.10.165:8888/status
```

## 4. Flannel VXLAN fix (UDP 8472)
```bash
oci network security-list get --security-list-id <SEC_LIST_OCID> \
  --query "data.\"ingress-security-rules\"[?protocol=='17']"
# if empty: read full list, append {protocol:17, source:10.0.0.0/24, udpOptions.destinationPortRange:{min:8472,max:8472}}, write back complete list
curl -v --max-time 3 http://<a-pod-ip-on-node-02>:8000/status   # verify from another node
```

## 5. DNS
```bash
dig +short xpilot.spdns.eu   # -> 150.230.10.165
```

## 6. Host-based routing on the existing port-80 listener
```bash
cat > /tmp/xpilot-routing-policy.json <<'JSON'
{"name":"xpilot_host_routing","conditionLanguageVersion":"V1","rules":[{"name":"xpilot_rule","condition":"any(http.request.headers[(i 'host')] eq (i 'xpilot.spdns.eu'))","actions":[{"name":"FORWARD_TO_BACKENDSET","backendSetName":"bs_xpilot_traefik"}]}]}
JSON
oci lb routing-policy create --load-balancer-id <LB_OCID> --name xpilot_host_routing \
  --condition-language-version V1 --rules file:///tmp/xpilot-routing-policy.json --wait-for-state SUCCEEDED
oci lb listener update --load-balancer-id <LB_OCID> \
  --listener-name <EXISTING_PORT_80_LISTENER_NAME> --routing-policy-name xpilot_host_routing --force
curl -H "Host: xpilot.spdns.eu" http://150.230.10.165/status
curl http://150.230.10.165/   # unaffected default traffic
```

## 7. Open port 80/443 publicly (safe read-then-append pattern only)
```bash
oci network security-list get --security-list-id <SEC_LIST_OCID> \
  --query "data.\"ingress-security-rules\"" > /tmp/full-rules.json
# append 0.0.0.0/0 TCP 80, then later 0.0.0.0/0 TCP 443, to the complete list
oci network security-list update --security-list-id <SEC_LIST_OCID> \
  --ingress-security-rules file:///tmp/full-rules.json --force
ssh -i node-02.key ubuntu@<node-02-public-ip> "echo still-works"   # verify SSH survives
```

## 8. cert-manager (pinned)
```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.12.15/cert-manager.yaml
kubectl wait --for=condition=Available --timeout=120s deployment --all -n cert-manager
```

## 9. CoreDNS hairpin fix
```bash
kubectl get svc traefik -n kube-system -o jsonpath='{.spec.clusterIP}'
kubectl apply -f k8s/cluster-infra/coredns-custom.yaml
kubectl rollout restart deployment coredns -n kube-system
```

## 10. ClusterIssuers + TLS (staging then prod)
```bash
kubectl apply -k k8s/
kubectl get certificate -n xpilot -w
sed -i 's/letsencrypt-staging/letsencrypt-prod/' k8s/ingress.yaml
kubectl delete secret xpilot-tls -n xpilot
kubectl delete certificaterequest -n xpilot --all
kubectl delete certificate xpilot-tls -n xpilot
kubectl apply -k k8s/
kubectl get certificate -n xpilot -w
```

## 11. Port 443 (TCP passthrough)
```bash
oci lb backend-set create --load-balancer-id <LB_OCID> \
  --name bs_xpilot_traefik_tls --policy ROUND_ROBIN \
  --health-checker-protocol TCP --health-checker-port 443 \
  --backends '[{"ipAddress":"10.0.0.82","port":443},{"ipAddress":"10.0.0.104","port":443},{"ipAddress":"10.0.0.251","port":443}]'
oci lb listener create --load-balancer-id <LB_OCID> \
  --name listener_xpilot_tls --port 443 --protocol TCP \
  --default-backend-set-name bs_xpilot_traefik_tls
curl -v https://xpilot.spdns.eu/status
```

## 12. Switch to wss://
```bash
sed -i 's|value: "ws://150.230.10.165:8888/ws"|value: "wss://xpilot.spdns.eu/ws"|' k8s/web-server-deployment.yaml
kubectl apply -k k8s/
kubectl rollout status deployment/web-server -n xpilot
```

## 13. CI runner
```bash
kubectl apply -f k8s/ci-runner/namespace.yaml
kubectl apply -f k8s/ci-runner/rbac.yaml
kubectl create secret generic github-runner-pat -n ci --from-literal=access-token=<GITHUB_PAT>
kubectl apply -f k8s/ci-runner/runner-deployment.yaml
kubectl get pods -n ci
kubectl logs -n ci deployment/github-runner
```
