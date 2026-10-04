# RetailKB Copilot — lệnh vận hành (F3). Chạy `make help` để xem danh sách.
SHELL := /bin/bash
COMPOSE ?= docker compose
API := $(COMPOSE) exec -T api

.DEFAULT_GOAL := help
.PHONY: help up down restart ps logs build migrate ingest ingest-status reindex kb-health graph-rebuild backup restore \
        web-dev web-build config-check config-snapshot rollback-config test test-integration lint

help: ## Danh sách lệnh
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: .env ## Build + khởi động toàn bộ (postgres, langfuse, api, worker, parser, web)
	$(COMPOSE) up -d --build
	@echo "Web: http://localhost:$${WEB_PORT:-3000} · API: http://localhost:$${API_PORT:-8000}/docs · Langfuse: http://localhost:$${LANGFUSE_PORT:-3001}"
	@echo "Đăng nhập lần đầu: BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD (superadmin) hoặc Đăng ký tài khoản mới trên web."

.env:
	@echo "Thiếu .env — tạo từ mẫu: cp .env.example .env rồi điền các biến BẮT BUỘC (xem README)"; exit 1

down: ## Dừng (giữ dữ liệu trong volume)
	$(COMPOSE) down

restart: ## Restart api + worker (sau khi đổi config / rollback config)
	$(COMPOSE) up -d --force-recreate api worker

ps: ## Trạng thái + healthcheck các service
	$(COMPOSE) ps

logs: ## Xem log (SERVICE=api|worker|parser|... để lọc)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

build: ## Build image
	$(COMPOSE) build

migrate: ## Chạy migration Alembic (tự chạy khi `make up`)
	$(COMPOSE) run --rm migrate

ingest: ## Không có FILE: nạp dữ liệu mẫu data/raw vào workspace WORKSPACE (mặc định slug `default`, pack retail). Có FILE=đường/dẫn: ingest 1 file (thêm DOC_TYPE, STORE, EFFECTIVE_DATE, TITLE, REPLACE_MODE)
ifdef FILE
	@test -f "$(FILE)" || (echo "Không thấy file $(FILE)"; exit 1)
	$(COMPOSE) cp "$(FILE)" api:/tmp/
	$(API) python -m ingest.cli upload "/tmp/$(notdir $(FILE))" --wait --workspace $(or $(WORKSPACE),default) \
	  $(if $(DOC_TYPE),--doc-type $(DOC_TYPE)) $(if $(STORE),--store $(STORE)) \
	  $(if $(EFFECTIVE_DATE),--effective-date $(EFFECTIVE_DATE)) $(if $(TITLE),--title "$(TITLE)") \
	  $(if $(REPLACE_MODE),--replace-mode $(REPLACE_MODE))
else
	$(API) python -m ingest.cli seed --wait --workspace $(or $(WORKSPACE),default) $(if $(ONLY),--only $(ONLY))
endif

ingest-status: ## Trạng thái 1 job: make ingest-status JOB=<job_id>
	$(API) python -m ingest.cli status $(JOB)

reindex: ## Reindex nền (DOC_TYPE=..., DOCUMENT=..., RECHUNK=auto|true|false, MODEL=... để embed trước model mới)
	$(API) python -m kb.cli reindex --wait --rechunk $(or $(RECHUNK),auto) $(if $(DOC_TYPE),--doc-type $(DOC_TYPE)) \
	  $(if $(DOCUMENT),--document $(DOCUMENT)) $(if $(MODEL),--model $(MODEL)) $(if $(DIM),--dim $(DIM))

kb-health: ## In KB health (record theo doc_type, file blocked, conflict, abstention 7 ngày...)
	$(API) python -m kb.cli health

graph-rebuild: ## Rebuild TOÀN BỘ graph (job nền). NO_LLM=1 để bỏ LLM extraction
	$(API) python -m graph.cli rebuild --enqueue $(if $(NO_LLM),--no-llm)

backup: ## Backup Postgres + file upload vào ./backups (cron được: xem scripts/backup.sh)
	./scripts/backup.sh

restore: ## Restore: make restore TS=<timestamp> [DB_ONLY=1]
	./scripts/restore.sh $(TS) $(if $(DB_ONLY),--db-only)

web-dev: ## Chạy web app dev (Vite, proxy /api -> localhost:8000)
	cd web && npm install --no-audit --no-fund && npm run dev

web-build: ## Build web app tĩnh vào web/dist
	cd web && npm ci --no-audit --no-fund && npm run build

config-check: ## Validate config.yaml + sha256 prompt
	$(API) python -m core.config check

config-snapshot: ## Chép config.yaml -> config/versions/<version>.yaml (sau khi bump version)
	python -m core.config snapshot

rollback-config: ## Rollback: make rollback-config VERSION=2026.10.03-1 (ghi .env + restart)
	@test -n "$(VERSION)" || (echo "Cần VERSION=..."; exit 1)
	@test -f "config/versions/$(VERSION).yaml" || (echo "Không có config/versions/$(VERSION).yaml"; ls config/versions; exit 1)
	@grep -q '^RETAILKB_CONFIG_VERSION=' .env && sed -i.bak 's/^RETAILKB_CONFIG_VERSION=.*/RETAILKB_CONFIG_VERSION=$(VERSION)/' .env \
	  || echo "RETAILKB_CONFIG_VERSION=$(VERSION)" >> .env
	$(MAKE) restart
	@echo "Đã chuyển sang config $(VERSION). Kiểm tra: curl -s localhost:8000/readyz"

test: ## Unit test (không cần DB)
	python -m pytest -q

test-integration: ## Integration test cần Postgres+pgvector+AGE: RETAILKB_TEST_PG_DSN=postgresql://... make test-integration
	RETAILKB_TEST_PG_DSN=$(RETAILKB_TEST_PG_DSN) python -m pytest -q tests/integration
