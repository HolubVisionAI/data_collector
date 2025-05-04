# 📂 Folder Scanner Summary Tool

This tool scans specific directory structures for document or media files (e.g. PDFs or MP4s) and summarizes their count
and size per category.

✅ Supports:

- Multiple root directories
- Configurable folder names (like `EN/PDF`, `CN/MP4`, etc.)
- Output to CSV
- Copy to clipboard for Excel paste

---

## 📦 Requirements

- Python 3.8+
- [Poetry](https://python-poetry.org/) (for dependency management)

---

## 🚀 Installation

### 1. Install Poetry (if not installed)

**Windows (PowerShell):**

```powershell
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
```

### 2. Clone the project and install dependencies

```cmd
git clone https://github.com/your-username/folder-summary-tool.git
cd folder-summary-tool

# Install dependencies via Poetry
poetry install

```

