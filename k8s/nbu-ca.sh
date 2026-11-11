#!/usr/bin/env bash
# Lấy CA của NetBackup primary server và tạo/cập nhật ConfigMap `nbu-ca` cho app.
#
#   ./k8s/nbu-ca.sh [nbu-host] [namespace]
#
# Bundle = CA gốc "nbatd" (lấy qua API /netbackup/security/cacert, không cần đăng nhập)
#        + CA trung gian "broker" (lấy từ chuỗi chứng chỉ máy chủ trả về).
# Sau khi chạy: kubectl -n <namespace> rollout restart deploy/vco-app
set -euo pipefail
HOST="${1:-nbu-primary.hsc.com.vn}"
NS="${2:-cattle-monitoring-system}"
PORT=1556
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "1/4 Lấy CA trung gian (broker) từ $HOST:$PORT"
openssl s_client -connect "$HOST:$PORT" -servername "$HOST" -showcerts </dev/null 2>/dev/null \
  | awk '/BEGIN CERT/{n++} n==2,/END CERT/' > "$WORK/broker.crt"
openssl x509 -in "$WORK/broker.crt" -noout -subject >/dev/null

echo "2/4 Lấy CA gốc (nbatd) qua /netbackup/security/cacert"
curl -sf --cacert "$WORK/broker.crt" "https://$HOST:$PORT/netbackup/security/cacert" \
  -H "Accept: application/vnd.netbackup+json;version=4.0" -o "$WORK/cacert.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("".join(c["webRootCert"].strip()+"\n" for c in d["webRootCertificateData"]),end="")' \
  "$WORK/cacert.json" > "$WORK/root.crt"
cat "$WORK/root.crt" "$WORK/broker.crt" > "$WORK/ca.pem"

echo "3/4 Kiểm tra chứng chỉ máy chủ với bundle (cả hostname)"
for f in "$WORK/root.crt" "$WORK/broker.crt"; do openssl x509 -in "$f" -noout -subject -enddate; done
openssl s_client -connect "$HOST:$PORT" -CAfile "$WORK/ca.pem" -verify_hostname "$HOST" </dev/null 2>&1 \
  | grep -q "Verify return code: 0 (ok)" || { echo "LỖI: bundle CA không xác thực được $HOST" >&2; exit 1; }
echo "   OK — fingerprint CA gốc: $(openssl x509 -in "$WORK/root.crt" -noout -fingerprint -sha256 | cut -d= -f2)"
echo "   Đối chiếu với lệnh trên primary: /usr/openv/netbackup/bin/nbcertcmd -displayCACertDetail"

echo "4/4 Tạo/cập nhật ConfigMap nbu-ca trong namespace $NS"
kubectl -n "$NS" create configmap nbu-ca --from-file=ca.pem="$WORK/ca.pem" --dry-run=client -o yaml | kubectl apply -f -
echo "Xong. Áp dụng cho app: kubectl -n $NS rollout restart deploy/vco-app"
