backup ?= yes

build-system:
	@make maybe-backup-system && docker compose -f deploy/docker-compose.yml up -d --build

start-system:
	@docker compose -f deploy/docker-compose.yml up -d

stop-system:
	@docker compose -f deploy/docker-compose.yml down

restart-system:
	@docker compose -f deploy/docker-compose.yml down && docker compose -f deploy/docker-compose.yml up -d

backup-database:
	@mkdir -p backups
	@FILE="backups/finflow-$$(date +%Y%m%d-%H%M%S).sql"; \
	docker compose -f deploy/docker-compose.yml exec -T postgres sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --no-owner --no-privileges' > "$$FILE.tmp" \
		&& mv "$$FILE.tmp" "$$FILE" || { rm -f "$$FILE.tmp"; exit 1; }

backup-media:
	@mkdir -p backups
	@FILE="backups/finflow-media-$$(date +%Y%m%d-%H%M%S).tar.gz"; \
	docker compose -f deploy/docker-compose.yml exec -T django tar czf - -C /app/media_root . > "$$FILE.tmp" \
		&& mv "$$FILE.tmp" "$$FILE" || { rm -f "$$FILE.tmp"; exit 1; }

backup-system:
	@make backup-database && make backup-media

maybe-backup-system:
	@case "$(backup)" in \
		yes|true|on|1) make backup-system ;; \
		no|false|off|0) echo "backup=$(backup): pulando o backup" ;; \
		*) echo "backup=$(backup): use yes ou no" >&2; exit 1 ;; \
	esac

prune-attachments:
	@docker compose -f deploy/docker-compose.yml exec -T django python manage.py prune_attachments $(args)

reset-system-cache:
	@docker compose -f deploy/docker-compose.yml exec redis redis-cli FLUSHDB

create-superuser:
	@docker compose -f deploy/docker-compose.yml exec django python manage.py createsuperuser
	@docker compose -f deploy/docker-compose.yml exec -T django python manage.py shell < scripts/create_totp.py

create-totp:
	@docker compose -f deploy/docker-compose.yml exec -T -e TOTP_USER="$(user)" django python manage.py shell < scripts/create_totp.py

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
