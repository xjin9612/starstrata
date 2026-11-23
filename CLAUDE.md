# starstrata

每日收集 GitHub 趨勢資料，保存成長期時間序列。
需求見 `docs/REQUIREMENTS.md`，設計見 `docs/DESIGN.md`。

## 開發環境

需要 Python 3.12 以上。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps -e .
```

## 檢查

提交前四項都要通過，與 CI 相同：

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

## 相依套件

- 直接相依寫在 `pyproject.toml`；`requirements*.txt` 由它產生，不手動編輯。
- 修改後重新產生兩個檔案，指令寫在各檔案開頭。`pip-compile` 需要先在 `.venv` 安裝 `pip-tools`。

## 資料規則

- JSONL 快照是唯一的資料來源。SQLite 必須能從快照完整重建。
- 修改 DB schema 或匯入邏輯時，遞增 `db.SCHEMA_VERSION`，讓既有 DB 自動重建。
- 增量匯入與完整重建的結果必須相同，`tests/test_ingest.py` 有對照測試。
- 快照格式只能新增選用欄位。舊快照必須永遠能匯入。
- 各版快照格式的測試資料在 `tests/fixtures/snapshots/<版本>/`，提交後不再修改；格式改變時新增版本目錄，並加進 `tests/test_snapshot_formats.py`。
- 日期一律依 `config.toml` 的 `schedule.timezone` 判定，不直接用系統時區。

## 測試規則

- 測試不連網路。HTTP 用 `httpx.MockTransport` 模擬。
- Trending 解析的測試用存下來的真實 HTML，放在 `tests/fixtures/`。
- 每個門檻判斷都要有邊界值測試。

## 祕密

- token 只從環境變數讀取：`GH_PAT`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`GEMINI_API_KEY`、`ANTHROPIC_API_KEY`。
- 錯誤訊息與 log 不得輸出 token。
