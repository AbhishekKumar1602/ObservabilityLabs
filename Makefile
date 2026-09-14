SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
TEST_IMAGE := fastapi-observability-test:local
.PHONY: help bootstrap up down build rebuild restart logs ps test lint health load migrate migration validate clean
help:
	@echo "bootstrap up down build rebuild restart logs ps test lint health load migrate migration validate clean"
bootstrap:
	./scripts/bootstrap.sh
up:
	$(COMPOSE) up --build -d
down:
	$(COMPOSE) down
build:
	$(COMPOSE) build
rebuild:
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d --force-recreate
restart:
	$(COMPOSE) restart
logs:
	$(COMPOSE) logs --follow --tail=100
ps:
	$(COMPOSE) ps -a
test:
	docker build --target test -t $(TEST_IMAGE) ./app
	docker run --rm --network none --read-only --tmpfs /tmp $(TEST_IMAGE)
lint:
	docker build --target test -t $(TEST_IMAGE) ./app
	docker run --rm --network none --read-only --tmpfs /tmp $(TEST_IMAGE) ruff check --no-cache app tests migrations
	docker run --rm --network none --read-only --tmpfs /tmp $(TEST_IMAGE) ruff format --check --no-cache app tests migrations
	bash -n scripts/bootstrap.sh scripts/healthcheck.sh scripts/load-test.sh
health:
	./scripts/healthcheck.sh
load:
	./scripts/load-test.sh
migrate:
	$(COMPOSE) run --rm migrate
migration:
	@test -n "$(m)" || { echo 'Usage: make migration m="add item field"'; exit 2; }
	$(COMPOSE) run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR)/app/migrations:/srv/app/migrations" migrate alembic revision --autogenerate -m "$(m)"
	@echo "Review the generated migration, then make build and make migrate."
validate: build
	$(COMPOSE) config --quiet
	$(COMPOSE) run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yml
	$(COMPOSE) run --rm --no-deps --entrypoint amtool alertmanager check-config /etc/alertmanager/alertmanager.yml
	$(COMPOSE) run --rm --no-deps otel-collector validate --config=/etc/otelcol-contrib/config.yaml
	$(COMPOSE) run --rm --no-deps loki -config.file=/etc/loki/config.yaml -verify-config=true
clean:
	@test "$(CONFIRM)" = delete-local-data || { echo 'Deletes ALL project volumes. Use make clean CONFIRM=delete-local-data'; exit 2; }
	$(COMPOSE) down --volumes --remove-orphans
