requirements_show:
	cat requirements.txt

requirements_export:
	.env/bin/python -m pip freeze --local > requirements.txt
	cat requirements.txt

install:
	python -m venv .env --system-site-packages
	.env/bin/python -m pip install -r requirements.txt

enable_service:
	sudo cp GrindyPy.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable GrindyPy.service
	sudo systemctl start GrindyPy.service

disable_service:
	-sudo systemctl stop GrindyPy.service
	sudo rm /etc/systemd/system/GrindyPy.service
	sudo systemctl daemon-reload