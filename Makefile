.PHONY: bench test lint conventions

bench:
	PYTHONPATH=. uv run python bench/status.py

test:
	uv run pytest -q

lint:
	uv run ruff check .

conventions:
	uv run python scripts/check_conventions.py
