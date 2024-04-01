SHELL=/bin/bash -e

help:
	@echo - make black      Format code
	@echo - make isort      Sort imports
	@echo - make lint       Run lint
	@echo - make typecheck  Typecheck

lint:
	flake8 rt11.py

isort:
	isort --profile black rt11.py

black: isort
	black -S rt11.py

typecheck:
	mypy --strict --no-warn-unused-ignores rt11.py
