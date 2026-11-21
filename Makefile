MAKEFLAGS += --no-print-directory

backup ?= yes

build-system:
	@$(MAKE) maybe-backup-system \
		&& docker compose -f deploy/docker-compose.yml pull --ignore-buildable \
		&& docker compose -f deploy/docker-compose.yml build --pull \
		&& docker compose -f deploy/docker-compose.yml up -d \
		&& docker compose -f deploy/docker-compose.yml up -d --force-recreate nginx

start-system:
	@docker compose -f deploy/docker-compose.yml up -d

stop-system:
	@docker compose -f deploy/docker-compose.yml down

restart-system:
	@docker compose -f deploy/docker-compose.yml down && docker compose -f deploy/docker-compose.yml up -d

backup-database:
	@mkdir -p -m 700 backups
	@umask 077; FILE="backups/finflow-$$(date +%Y%m%d-%H%M%S).sql"; \
	docker compose -f deploy/docker-compose.yml exec -T postgres sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --no-owner --no-privileges' > "$$FILE.tmp" \
		&& mv "$$FILE.tmp" "$$FILE" || { rm -f "$$FILE.tmp"; exit 1; }

backup-media:
	@mkdir -p -m 700 backups
	@umask 077; FILE="backups/finflow-media-$$(date +%Y%m%d-%H%M%S).tar.gz"; \
	docker compose -f deploy/docker-compose.yml exec -T django tar czf - -C /app/media_root . > "$$FILE.tmp" \
		&& mv "$$FILE.tmp" "$$FILE" || { rm -f "$$FILE.tmp"; exit 1; }

backup-system:
	@$(MAKE) backup-database && $(MAKE) backup-media

# Restaura o backup num Postgres descartável, sem rede e sem tocar no banco de
# produção, e mostra o que voltou. Sem file=, usa o .sql mais recente de backups/;
# um .gpg (cópia do remoto) é decifrado antes, e pede a chave privada.
check-backup:
	@FILE="$${file:-$$(ls -t backups/finflow-2*.sql 2>/dev/null | head -1)}"; \
	[ -s "$$FILE" ] || { echo "nenhum backup .sql em $(CURDIR)/backups (use file=...)" >&2; exit 1; }; \
	case "$$FILE" in *.gpg) READ="gpg --quiet --decrypt" ;; *) READ="cat" ;; esac; \
	NAME="finflow-check-backup-$$$$"; \
	docker run -d --rm --name "$$NAME" --network none -e POSTGRES_PASSWORD=check postgres:18 >/dev/null || exit 1; \
	trap 'docker stop "$$NAME" >/dev/null' EXIT; \
	until docker exec "$$NAME" pg_isready -q -h 127.0.0.1 -U postgres; do sleep 1; done; \
	$$READ "$$FILE" | docker exec -i "$$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d postgres >/dev/null \
		|| { echo "falha ao restaurar $$FILE" >&2; exit 1; }; \
	echo "$$FILE restaurado:"; \
	docker exec "$$NAME" psql -U postgres -d postgres -c "SELECT \
		(SELECT count(*) FROM app_user) AS usuarios, \
		(SELECT count(*) FROM app_transaction) AS transacoes, \
		(SELECT max(occurred_at)::date FROM app_transaction) AS ultima_transacao, \
		(SELECT max(applied) FROM django_migrations) AS ultima_migration"
	@MEDIA="$$(ls -t backups/finflow-media-*.tar.gz 2>/dev/null | head -1)"; \
	[ -z "$$MEDIA" ] || [ -n "$(file)" ] || { tar tzf "$$MEDIA" >/dev/null && echo "$$MEDIA íntegro: $$(tar tzf "$$MEDIA" | grep -vc '/$$') arquivos"; }

upload-backups:
	@scripts/upload_backups.sh

maybe-backup-system:
	@case "$(backup)" in \
		yes|true|on|1) $(MAKE) backup-system ;; \
		no|false|off|0) echo "backup=$(backup): pulando o backup" ;; \
		*) echo "backup=$(backup): use yes ou no" >&2; exit 1 ;; \
	esac

prune-attachments:
	@docker compose -f deploy/docker-compose.yml exec -T django python manage.py prune_attachments $(args)

create-superuser:
	@docker compose -f deploy/docker-compose.yml exec django python manage.py createsuperuser
	@docker compose -f deploy/docker-compose.yml exec -T django python manage.py shell < scripts/create_totp.py

create-totp:
	@docker compose -f deploy/docker-compose.yml exec -T -e TOTP_USER="$(user)" django python manage.py shell < scripts/create_totp.py

protect-login:
	@docker compose -f deploy/docker-compose.yml run --rm --no-deps -T -v "$(PWD)/scripts:/app/scripts" tools python scripts/cloudflare_login_rule.py $(args)

create-app-role:
	@docker compose -f deploy/docker-compose.yml run --rm --no-deps -T -v "$(PWD)/scripts:/app/scripts" tools python scripts/create_app_role.py

make-migrations:
	@docker compose -f deploy/docker-compose.yml run --rm --no-deps -v "$(PWD)/app:/app/app" -v "$(PWD)/assistant:/app/assistant" django python manage.py makemigrations $(app)

run-tests:
	@docker compose -f deploy/docker-compose.yml run --rm tests python -m pytest -vv $(args)

django-shell:
	@docker compose -f deploy/docker-compose.yml exec django python manage.py shell

container-terminal:
	@docker compose -f deploy/docker-compose.yml exec $(container) sh

containers-logs:
	@docker compose -f deploy/docker-compose.yml logs -f $(container)
