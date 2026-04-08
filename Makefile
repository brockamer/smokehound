.PHONY: install dev test lint format clean report doctor

PYTHON := python3
PIP := $(PYTHON) -m pip
VENV := .venv
BIN := $(VENV)/bin

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[speedtest]"
	@echo ""
	@echo "Installation complete. Run: source $(VENV)/bin/activate"
	@echo "Then: smokehound doctor"

dev:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev,speedtest]"
	@echo ""
	@echo "Dev environment ready. Run: source $(VENV)/bin/activate"

test:
	$(BIN)/pytest tests/ -v

test-cov:
	$(BIN)/pytest tests/ --cov=smokehound --cov-report=html --cov-report=term-missing

lint:
	$(BIN)/ruff check src/ tests/
	$(BIN)/ruff format --check src/ tests/

format:
	$(BIN)/ruff check --fix src/ tests/
	$(BIN)/ruff format src/ tests/

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

report:
	smokehound report

doctor:
	smokehound doctor
