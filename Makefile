build-system:
	@cd deploy/ && docker compose up -d --build

start-system:
	@cd deploy/ && docker compose up -d

stop-system:
	@cd deploy/ && docker compose down

restart-system:
	@cd deploy/ && docker compose down && docker compose up -d

reset-system:
	@cd deploy/ && docker compose down -v && rm -rf ../database/ && docker compose up -d --build

reset-system-cache:
	@docker compose -f deploy/docker-compose.yml exec redis redis-cli FLUSHDB

clean-system:
	@cd deploy/ && docker compose down -v && docker system prune -a --volumes --force && rm -rf ../database/

make-migrations:
	@cd deploy/ && docker compose run --rm --no-deps -v "$(PWD)/app:/app/app" django python manage.py makemigrations $(app)

create-superuser:
	@cd deploy/ && \
	docker compose exec django python manage.py shell -c "from app.models import User; User.objects.filter(username='admin').exists() or User.objects.create_superuser(username='admin', password='1234')"

container-terminal:
	@cd deploy/ && docker compose exec $(container) sh

containers-logs:
	@cd deploy/ && docker compose logs -f $(container)

django-shell:
	@cd deploy/ && docker compose exec django python manage.py shell
