.PHONY: help install test lint format check migrate runserver celery-worker

help:
	@printf "Available targets:\n"
	@printf "  install        Install Python dependencies into the active environment\n"
	@printf "  test           Run tests\n"
	@printf "  lint           Run Ruff checks\n"
	@printf "  format         Format Python code with Ruff\n"
	@printf "  check          Run Django system checks\n"
	@printf "  migrate        Run Django migrations\n"
	@printf "  runserver      Start Django development server\n"
	@printf "  celery-worker  Start Celery worker\n"

install:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

check:
	python manage.py check

migrate:
	python manage.py migrate

runserver:
	python manage.py runserver 0.0.0.0:8000

celery-worker:
	celery -A doksio.project worker --loglevel=INFO
