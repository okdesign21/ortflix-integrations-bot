.PHONY: help run test test-watch lint format clean install setup trivy-image

IMAGE_NAME ?= ortflix-bot-addons
DOCKER ?= docker
PYTHON ?= python
TRIVY ?= trivy

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Initial setup - copy .env.example to .env
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "✅ Created .env file. Please edit it with your credentials."; \
	else \
		echo "⚠️  .env already exists. Skipping."; \
	fi

install: ## Install dependencies in virtual environment
	@./run-local.sh test  # This will create venv and install deps

run: ## Run the bot locally
	@./run-local.sh run

test: ## Run tests
	@./run-local.sh test

test-watch: ## Run tests in watch mode
	@./run-local.sh test-watch

lint: ## Run linting
	@./run-local.sh lint

format: ## Format code
	@./run-local.sh format

clean: ## Clean up generated files and caches
	@echo "Cleaning up..."
	@rm -rf .venv .pytest_cache .ruff_cache .mypy_cache htmlcov build dist _version.py *.egg-info
	@rm -f .coverage
	@find . -maxdepth 1 -type f -name '.coverage.*' -delete
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete
	@echo "✅ Cleanup complete"

check: lint test ## Run all checks (lint + test)

# Docker-related targets
docker-build: ## Build Docker image
	$(DOCKER) build -t $(IMAGE_NAME) .

trivy-image: docker-build ## Build image and scan HIGH/CRITICAL vulnerabilities and secrets
	$(PYTHON) scripts/scan_image.py "$(IMAGE_NAME)" --trivy "$(TRIVY)"

docker-run: ## Run Docker container locally
	$(DOCKER) run --rm \
		--env-file .env \
		-p 7777:7777 \
		$(IMAGE_NAME)

# Testing helpers
test-webhook: ## Send a test webhook to running bot
	@echo "Sending test webhook to http://localhost:7777/api/v1/webhooks/overseerr"
	@if [ -z "$${WEBHOOK_TOKEN}" ]; then echo "Set WEBHOOK_TOKEN before running make test-webhook" >&2; exit 1; fi
	@curl -X POST http://localhost:7777/api/v1/webhooks/overseerr \
		-H "Content-Type: application/json" \
		-H "x-webhook-token: $${WEBHOOK_TOKEN}" \
		-d '{"notification_type":"MEDIA_AVAILABLE","subject":"Test Movie","media":{"media_type":"movie"}}'
	@echo "\n✅ Check Telegram for message"
