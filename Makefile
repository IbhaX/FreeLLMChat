.PHONY: install dev run test lint typecheck docker

install:
	pip install -r requirements-dev.txt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000

test:
	pytest -q

lint:
	ruff check app tests
	ruff format --check app tests

typecheck:
	mypy app

docker:
	docker compose up --build
