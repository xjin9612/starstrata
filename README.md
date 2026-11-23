<p align="center">
  <img src="assets/icon.svg" width="128" height="128" alt="starstrata">
</p>

<h1 align="center">starstrata</h1>

<p align="center"><a href="https://github.com/xjin9612/starstrata/actions/workflows/ci.yml"><img src="https://github.com/xjin9612/starstrata/actions/workflows/ci.yml/badge.svg" alt="CI"></a></p>

starstrata 每日收集 GitHub 趨勢資料，保存成長期的時間序列。

- 資料來源：GitHub Trending、Search API、repo star 數
- 輸出：GitHub Pages dashboard、Telegram 通知、每週與每月報告

需求見 [REQUIREMENTS](docs/REQUIREMENTS.md)，設計見 [DESIGN](docs/DESIGN.md)。

## 部署

所有工作由 GitHub Actions 的 `Daily` workflow 每天執行。

1. 在 repo 的 Settings → Secrets and variables → Actions 建立 secrets：

   | 名稱 | 必要 | 內容 |
   |---|---|---|
   | `GH_PAT` | 是 | fine-grained personal access token，不需授予任何權限 |
   | `TELEGRAM_BOT_TOKEN` | 否 | Telegram bot token；未設定時不發送通知 |
   | `TELEGRAM_CHAT_ID` | 否 | 接收通知的 chat ID |
   | `GEMINI_API_KEY` | 否 | repo 說明的中文翻譯、週報與月報的中文解讀（預設供應商）；未設定時顯示原文，報告不含解讀 |
   | `ANTHROPIC_API_KEY` | 否 | `config.toml` 的 `reports.provider` 改為 `anthropic` 時使用 |

   `GEMINI_API_KEY` 在 [Google AI Studio](https://aistudio.google.com) 免費建立。
   fine-grained token 一律可唯讀存取公開 repo（[官方說明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)）。
   建立位置：Settings → Developer settings → Personal access tokens → Fine-grained tokens。

2. 在 Settings → Pages 的「Build and deployment」，把 Source 設為 GitHub Actions。
3. 在 Actions 頁面選 `Daily` → Run workflow，手動執行第一次。

每次執行會 commit 當天快照到 `data/`、週報與月報到 `reports/`，把 SQLite 上傳到 tag 為 `db` 的 Release，並部署 dashboard 到 GitHub Pages。

## 本機執行

開發環境見 [CLAUDE.md](CLAUDE.md)。

```sh
GH_PAT=<token> .venv/bin/starstrata collect   # 抓取並寫入當天快照
GH_PAT=<token> .venv/bin/starstrata backfill  # 回補歷史 star 曲線
.venv/bin/starstrata ingest                   # 匯入 SQLite
.venv/bin/starstrata rebuild                  # 從全部快照重建 SQLite
.venv/bin/starstrata site                     # 產生 dashboard 到 _site/
.venv/bin/starstrata report weekly            # 產生上週的週報到 reports/
```

預覽 dashboard：`python3 -m http.server -d _site 8000`，再開啟 `http://localhost:8000`。

所有指令與參數見 [DESIGN 的「命令列」](docs/DESIGN.md#命令列)。

## 授權

[MIT](LICENSE)
