SHELL=/bin/bash -e

help:
	@echo - make black      Format code
	@echo - make isort      Sort imports
	@echo - make lint       Run lint
	@echo - make typecheck  Typecheck

lint:
	flake8 rt11.py

.PHONY: isort
isort:
	@isort --profile black rt11.py tests

.PHONY: black
black: isort
	@black -S rt11.py tests

.PHONY: typecheck
typecheck:
	mypy --strict --no-warn-unused-ignores rt11.py

.PHONY: clean
clean:
	-rm -rf build dist
	-rm -rf *.egg-info
	-rm -rf bin lib share pyvenv.cfg

.PHONY: test
test:
	pytest

.PHONY: coverage
coverage:
	@pytest --cov --cov-report=term-missing

.PHONY: typecheck
venv:
	python3 -m venv .
	. bin/activate; pip install -Ur requirements.txt
	. bin/activate; pip install -Ur requirements-dev.txt
