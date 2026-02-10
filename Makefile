# --------- 基础设置 ---------
APP_NAME := heatpump_controller
APP_VERSION := 1.0.0
PY := poetry run
MYPY_PATH := src


# --------- 帮助 命令 ---------
.PHONY: help
help:
	@echo "可用命令:"
	@echo "  make install        安装依赖"
	@echo "  make lint           代码风格检查"
	@echo "  make mypy           类型检查"
	@echo "  make fmt						 代码格式化"
	@echo "  make test           运行测试"
	@echo "  make run            运行应用程序"

# --------- 安装依赖 ---------
.PHONY: install
install:
	poetry install

# --------- 代码风格检查 ---------
.PHONY: lint
lint:
	$(PY) ruff check $(MYPY_PATH)
	$(PY) black --check $(MYPY_PATH)

# --------- 类型检查 ---------
.PHONY: mypy
mypy:
	$(PY) mypy $(MYPY_PATH)

# --------- 代码格式化 ---------
.PHONY: fmt
fmt:
	$(PY) black $(MYPY_PATH)
	$(PY) ruff format $(MYPY_PATH)

# --------- 运行测试 ---------
.PHONY: test
test:
	$(PY) pytest tests

# --------- 运行应用程序 ---------
.PHONY: run
run:
	$(PY) python -m src.hp_controller.main