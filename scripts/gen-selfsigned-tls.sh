#!/bin/bash
# 生成自签 CA + memoryd 服务端证书（Plan B：无 DNS 时的 TLS 加密方案）。
# 用法：bash /opt/memoryd/scripts/gen-selfsigned-tls.sh
# 产物：/etc/memoryd/tls/ca.pem（CA，分发给各客户端信任）
#       /etc/memoryd/tls/server.pem / server.key（nginx 用）
set -euo pipefail
DIR=/etc/memoryd/tls
mkdir -p "$DIR"
cd "$DIR"

# 若已存在则不覆盖
[ -f ca.pem ] && { echo "✓ 已存在 $DIR（跳过生成）"; exit 0; }

openssl genrsa -out ca.key 2048 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/CN=memoryd-selfsigned-ca" -out ca.pem

openssl genrsa -out server.key 2048 2>/dev/null
openssl req -new -key server.key -sha256 \
  -subj "/CN=memoryd-server" -out server.csr
cat > san.cnf <<EOF
subjectAltName = IP:203.0.113.10, IP:10.0.0.2, DNS:localhost, IP:127.0.0.1
EOF
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out server.pem -days 3650 -sha256 -extfile san.cnf 2>/dev/null
rm -f server.csr san.cnf ca.srl
chmod 600 server.key ca.key

echo "✓ 生成完成："
echo "    CA:     $DIR/ca.pem（分发给客户端，用作信任根）"
echo "    证书:   $DIR/server.pem"
echo "    私钥:   $DIR/server.key（600）"
echo ""
echo "启用：nginx 仿照 nginx-memoryd-selfsigned.conf.example（listen 2043 ssl）"
echo "客户端：dsh-web-v2 或其他服务器设 NODE_EXTRA_CA_CERTS=$DIR/ca.pem"