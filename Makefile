# Короткие команды для типовых операций. Каждая цель — это ровно та команда,
# которая описана в README, без скрытой магии.

PYTHON ?= python3
DATA_DIR ?= data/test
TRAIN_DIR ?= data/train
OUTPUT ?= submission.csv
WEIGHTS ?= data/weights
CATALOG ?= data/service_catalog
PORT ?= 8000

.PHONY: setup test synthetic train inference validate evaluate ablations catalog service docker clean

setup:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -q

synthetic:
	$(PYTHON) scripts/make_synthetic_data.py --output data/synthetic/train --n-af 6 --n-bs 3 --split tr

train:
	$(PYTHON) train.py --data-dir $(TRAIN_DIR) --output-dir $(WEIGHTS)

inference:
	$(PYTHON) inference.py --data-dir $(DATA_DIR) --output $(OUTPUT)

validate:
	$(PYTHON) scripts/validate_submission.py --submission $(OUTPUT) \
		--sample-submission $(DATA_DIR)/sample_submission.csv --data-dir $(DATA_DIR)

evaluate:
	$(PYTHON) scripts/evaluate.py --submission $(OUTPUT) --truth $(TRAIN_DIR)/truth.csv --data-dir $(TRAIN_DIR)

ablations:
	$(PYTHON) scripts/run_ablations.py --data-dir $(TRAIN_DIR) --truth $(TRAIN_DIR)/truth.csv

catalog:
	$(PYTHON) scripts/build_service_catalog.py --data-dir $(TRAIN_DIR) --output $(CATALOG)

service:
	$(PYTHON) scripts/run_service.py --catalog $(CATALOG) --port $(PORT)

docker:
	docker build -t firewatch .

clean:
	rm -rf runs __pycache__ .pytest_cache
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
