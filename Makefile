build-system:
	@make backup-system && docker compose -f deploy/docker-compose.yml up -d --build

start-system:
	@docker compose -f deploy/docker-compose.yml up -d

stop-system:
	@docker compose -f deploy/docker-compose.yml down

restart-system:
	@docker compose -f deploy/docker-compose.yml down && docker compose -f deploy/docker-compose.yml up -d

reset-system:
	@make backup-system && docker compose -f deploy/docker-compose.yml down -v && docker compose -f deploy/docker-compose.yml up -d --build

backup-system:
	@make backup-database && make backup-media

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

prune-attachments:
	@docker compose -f deploy/docker-compose.yml exec -T django python manage.py prune_attachments $(args)

reset-system-cache:
	@docker compose -f deploy/docker-compose.yml exec redis redis-cli FLUSHDB

clean-system:
	@make backup-system && docker compose -f deploy/docker-compose.yml down -v && docker system prune -a --volumes --force

make-migrations:
	@docker compose -f deploy/docker-compose.yml run --rm --no-deps -v "$(PWD)/app:/app/app" django python manage.py makemigrations $(app)

create-superuser:
	@docker compose -f deploy/docker-compose.yml exec django python manage.py createsuperuser
	@docker compose -f deploy/docker-compose.yml exec -T django python manage.py shell < app/utils/create_totp.py

create-totp:
	@docker compose -f deploy/docker-compose.yml exec -T -e TOTP_USER="$(user)" django python manage.py shell < app/utils/create_totp.py

container-terminal:
	@docker compose -f deploy/docker-compose.yml exec $(container) sh

containers-logs:
	@docker compose -f deploy/docker-compose.yml logs -f $(container)

django-shell:
	@docker compose -f deploy/docker-compose.yml exec django python manage.py shell
