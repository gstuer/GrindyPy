requirements_show:
	cat requirements.txt

requirements_export:
	.env/bin/python -m pip freeze --local > requirements.txt
	cat requirements.txt

install:
	python -m venv .env --system-site-packages
	.env/bin/python -m pip install -r requirements.txt

