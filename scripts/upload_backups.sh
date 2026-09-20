#!/usr/bin/env bash
# Criptografa os backups locais e envia para o armazenamento remoto.
# Cada arquivo sobe uma única vez: o que já existe no remoto é ignorado.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-backups}"
REMOTE="${RCLONE_REMOTE:-gdrive:finflow-backups}"
RECIPIENT="${GPG_RECIPIENT:-finflow-backup@pagniz.com}"

for binary in rclone gpg; do
	command -v "$binary" >/dev/null || { echo "$binary não está instalado" >&2; exit 1; }
done

gpg --list-keys "$RECIPIENT" >/dev/null 2>&1 \
	|| { echo "chave pública de $RECIPIENT não encontrada no keyring" >&2; exit 1; }

umask 077
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT

remote_files="$(rclone lsf "$REMOTE" 2>/dev/null || true)"

pending=0
for file in "$BACKUP_DIR"/finflow-*; do
	[ -f "$file" ] && [ -s "$file" ] || continue
	case "$file" in *.tmp | *.gpg) continue ;; esac

	name="$(basename "$file").gpg"
	printf '%s\n' "$remote_files" | grep -qxF "$name" && continue

	gpg --batch --yes --trust-model always --encrypt \
		--recipient "$RECIPIENT" --output "$staging/$name" "$file"
	pending=$((pending + 1))
done

if [ "$pending" -eq 0 ]; then
	echo "$(date '+%Y-%m-%d %H:%M:%S') nenhum backup novo para enviar"
	exit 0
fi

rclone copy "$staging" "$REMOTE" --no-traverse
echo "$(date '+%Y-%m-%d %H:%M:%S') $pending arquivo(s) enviados para $REMOTE"
