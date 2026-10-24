PY ?= python3
DATA ?=
MAPPING ?=

.PHONY: help install test doctor survey synth run repro clean

help:
	@echo "make install   安装依赖"
	@echo "make survey    DATA=... 普查目录或 zip（zip 不用解压）"
	@echo "make doctor    自检（词典 / 分词器 / 指标注册）"
	@echo "make test      全部测试"
	@echo "make synth     生成合成样本（仅用于验证管线）"
	@echo "make run       DATA=... MAPPING=... 跑管线"
	@echo "make repro     一键复现（合成样本）"

install:
	$(PY) -m pip install -r requirements.txt

survey:
	$(PY) -m discourse_kit.cli survey $(DATA)

doctor:
	$(PY) -m discourse_kit.cli doctor

test:
	$(PY) -m pytest -q

synth:
	$(PY) -m discourse_kit.cli synth --out data/raw/demo --csv

run:
	@if [ -z "$(DATA)" ]; then echo "用法: make run DATA=path/to/file.csv [MAPPING=path/to/map.yaml]"; exit 1; fi
	$(PY) -m discourse_kit.cli run --file $(DATA) $(if $(MAPPING),--mapping $(MAPPING),)

repro:
	bash repro/run_all.sh

clean:
	rm -rf .pytest_tmp .pytest_cache data/processed report/*.md
	find . -name __pycache__ -type d -exec rm -rf {} +
