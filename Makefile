# Convenience targets; the app itself needs no build step.
PY ?= python3

.PHONY: help chat web ask test lint doctor clean

help:
	@echo "make chat            interactive terminal assistant"
	@echo "make web             web UI + API on http://localhost:8000"
	@echo "make ask Q='17*23'   one-shot question"
	@echo "make test            run the pytest suite"
	@echo "make lint            ruff + compileall"
	@echo "make doctor          check configuration"

chat:
	$(PY) -m jarvis chat

web:
	$(PY) -m jarvis serve

ask:
	$(PY) -m jarvis ask "$(Q)"

test:
	$(PY) -m pytest -q

lint:
	-$(PY) -m ruff check jarvis tests
	$(PY) -m compileall -q jarvis

doctor:
	$(PY) -m jarvis doctor

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__ data
