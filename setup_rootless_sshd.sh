#!/usr/bin/env bash
# setup_rootless_sshd.sh — 在一台新机器上,以普通用户(admin)搭一个跑在高位端口的
# rootless sshd,只允许指定公钥登录。用于像 172.24.201.36 这种无 root 常驻 sshd、
# 但需要用 `ssh -p <PORT> -i id_rsa_new admin@<host>` 直连的场景。
#
# 用法:在目标机上以目标用户(admin)执行:
#     bash setup_rootless_sshd.sh
# 重复执行安全(会先停掉旧实例再起)。放行防火墙那步需要 sudo(其余不需要 root)。
#
# 关键设计(照搬 172.24.201.6 的可用配置):
#   - sshd 以普通用户跑,端口 >1024(非 root 可绑),UsePAM=no(免 root),
#     只能登录该用户自己(非 root sshd 无法 setuid 到别的用户,够用)。
#   - 配置全在 $HOME/my-sshd/:空 sshd_config + 命令行 -o;独立 host key;独立 keys。
#   - StrictModes=yes:keys 及其各级父目录不能被组/他人可写,否则拒登。
set -euo pipefail

# ─── 可调参数 ──────────────────────────────────────────────────────────────
PORT="${PORT:-2222}"
DIR="$HOME/my-sshd"
# 允许登录的公钥(复用本地 ~/.ssh/id_rsa_new.pub;换机器/换钥匙改这里即可)。
# 必须是单行,别折行。
PUBKEY='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC4DX8uqdjlPuuBbXu34HeV9ZwZ38btrsW0zQw+CpVqcmQyLAXhSeIURlqwk3/Mb921T1DTZviXX9m6sMa0oTa+NdDspwN0hKt8IWUcyUgTY1njJa2aDeKkhRcp/J1zQQhzG4MFAq/YvgwP3R80Ft5deLNqdOTK4ixwb0d/4rCAHxi4utjCcTkDfaO0DScY0nowxiCXFXK/P8Kgp1JmY4ue8xvxRlpTN8jWHRZ75AWey9+kV8pr/2nNGhLFo+3VKr+IpFYsx5moOMGHMlQ7PIPWT4v2uHnoa0A5g2As8tVIKCUB8frH3iEV34hgN4i9K7g/FC5kzIGtCI6+FlKMrjZ/OjZRF9JivV5qwaqeTOl8/pylB5m+iyvZV8L6xx1KktYgvSh/YoJ4D74cRnIW0O2/T7th21wvNg0im/517FtLJ2IB+iYglNWl1fCpoze2TiioPaQ85rCdAVxY1HR0aPIbeuYiNx/JnBkqfvtooSs2QtDIdQqpSM02qLgK7q30JEfOCELP7xNIl/I1myI2+ozG4mqFonsPDB8sPiSsEbZl9Kj0Psn7fjMWIlmJ6ywkroVb+MP/zqRwuEWCpSjf8oG0PnGfdcjwsfmujXl0BnCoSaZksIb4/OnWeMcqaRYbaj7dT0fgSYlBgiIVD/elbAsfcI2dffPwJxHAATrWh4rf0w== ycui@YangdeMacBook-Pro.local'
SSHD_BIN="${SSHD_BIN:-/usr/sbin/sshd}"
# ──────────────────────────────────────────────────────────────────────────

echo "==> 目标用户=$(whoami)  home=$HOME  port=$PORT  dir=$DIR"

# 0) 前置:sshd 可执行必须在
[ -x "$SSHD_BIN" ] || { echo "缺 $SSHD_BIN,需 root 安装:  sudo yum install -y openssh-server"; exit 1; }

# 1) 配置目录(700)
mkdir -p "$DIR" && chmod 700 "$DIR"

# 2) 空 sshd_config(配置全走命令行 -o,与 .6 一致)
: > "$DIR/sshd_config"

# 3) 本机 host key(每台机器用自己的,别跨机复用)
if [ ! -f "$DIR/host_ed25519_key" ]; then
  ssh-keygen -t ed25519 -f "$DIR/host_ed25519_key" -N "" -q
  echo "==> 生成 host key: $DIR/host_ed25519_key"
fi

# 4) authorized_keys(printf 单行写入,避免粘贴折行把长 key 弄坏)
printf '%s\n' "$PUBKEY" > "$DIR/keys"

# 5) 权限:key 私密 + StrictModes 要求路径各级不被组/他人可写
chmod 600 "$DIR/keys" "$DIR/host_ed25519_key"
chmod go-w "$HOME" "$DIR"

# 6) 校验 keys 合法 + 打印指纹(和客户端 `ssh-keygen -lf ~/.ssh/id_rsa_new.pub` 对比应一致)
echo "==> keys 指纹: $(ssh-keygen -l -f "$DIR/keys" 2>&1)"

# 7) 停掉旧实例(幂等)后重启
if [ -f "$DIR/sshd.pid" ] && kill -0 "$(cat "$DIR/sshd.pid" 2>/dev/null)" 2>/dev/null; then
  echo "==> 停旧 sshd (pid=$(cat "$DIR/sshd.pid"))"; kill "$(cat "$DIR/sshd.pid")" || true; sleep 1
fi

# 8) 启动(与 .6 完全同款;nohup 后台,-D 前台交给 nohup;-e 日志进 sshd.log)
#    注:-o 里用绝对路径($DIR 已是绝对),sshd 不展开 ~。
nohup "$SSHD_BIN" \
  -f "$DIR/sshd_config" -p "$PORT" \
  -h "$DIR/host_ed25519_key" \
  -o AuthorizedKeysFile="$DIR/keys" \
  -o PidFile="$DIR/sshd.pid" \
  -o UsePAM=no -o StrictModes=yes -D -e >> "$DIR/sshd.log" 2>&1 &
sleep 1

# 9) 放行防火墙(firewalld,需 sudo;.36 上 host 入站默认 REJECT,必须显式放行本端口)
if command -v firewall-cmd >/dev/null && sudo -n firewall-cmd --state >/dev/null 2>&1; then
  sudo firewall-cmd --add-port="${PORT}/tcp" --permanent >/dev/null
  sudo firewall-cmd --reload >/dev/null
  echo "==> firewalld 已放行 ${PORT}/tcp"
else
  echo "⚠️  未自动放行防火墙(无 firewall-cmd 或无 sudo)。若外部连不上是 refused,"
  echo "    手动放行:  sudo firewall-cmd --add-port=${PORT}/tcp --permanent && sudo firewall-cmd --reload"
  echo "    (这是 firewalld,别用裸 iptables -I,reload 会冲掉)"
fi

# 10) 验证监听
echo "==> 监听检查:"
(ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | grep ":${PORT}" \
  && echo "✅ sshd 在 ${PORT} 监听" \
  || { echo "❌ 没监听,看日志:"; tail -15 "$DIR/sshd.log"; exit 1; }

echo
echo "本地连接:  ssh -p ${PORT} -i ~/.ssh/id_rsa_new $(whoami)@<本机IP>"
echo "开机自启(可选,无需 root):"
echo "  (crontab -l 2>/dev/null; echo \"@reboot $SSHD_BIN -f $DIR/sshd_config -p $PORT -h $DIR/host_ed25519_key -o AuthorizedKeysFile=$DIR/keys -o PidFile=$DIR/sshd.pid -o UsePAM=no -o StrictModes=yes -D -e >> $DIR/sshd.log 2>&1\") | crontab -"
