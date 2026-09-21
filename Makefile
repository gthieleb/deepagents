.DEFAULT_GOAL := help

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- hyfe-devops-agent testing ---
.PHONY: test-hyfe-devops
test-hyfe-devops: ## Run hyfe-devops-agent unit tests (no API keys needed)
	cd examples/hyfe-devops-agent && uv sync --group test && uv run --group test pytest tests/unit_tests/ -v

.PHONY: eval-hyfe-devops
eval-hyfe-devops: ## Run hyfe-devops-agent integration evals (requires ZAI_API_KEY)
	cd examples/hyfe-devops-agent && [ -n "$$ZAI_API_KEY" ] && uv run --group test pytest tests/integration_tests/ -m eval -v || echo "SKIP: ZAI_API_KEY not set"

.PHONY: deepeval-gate
deepeval-gate: ## Run DeepEval CI gate (metrics, synthetic data checks)
	cd examples/hyfe-devops-agent && uv run --group test deepeval test run tests/integration_tests/ -v && uv run --group test pytest tests/datasets/ -v
