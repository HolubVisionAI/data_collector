import os
import yaml
import csv

try:
    import pyperclip

    CLIPBOARD_AVAILABLE = True
except ImportError:
    CLIPBOARD_AVAILABLE = False


def load_config(config_path="config.yaml"):
    if not os.path.exists(config_path):
        print(f"⚠️ Config file not found: {config_path}")
        print("🔧 Creating a blank config.yaml file...")

        default_yaml = """# Example config.yaml
root_paths:
  - parent path of language directory


valid_langs:
  - EN
  - CN
  - RU

valid_types:
  - PDF
  - MP4
"""
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(default_yaml)

        print("✅ Created config.yaml. Please edit it and re-run the script.")
        exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def scan_folder_single(root_path, valid_langs, valid_types):
    summary = {}

    if not os.path.exists(root_path) or not os.path.isdir(root_path):
        print(f"⚠️ Invalid path: {root_path}")
        return summary

    for lang in valid_langs:
        lang_dir = os.path.join(root_path, lang)
        if not os.path.isdir(lang_dir):
            continue

        for category in valid_types:
            category_dir = os.path.join(lang_dir, category)
            if not os.path.isdir(category_dir):
                continue

            key = f"{lang}/{category}"
            file_count = 0
            total_size = 0

            for root, _, files in os.walk(category_dir):
                for f in files:
                    try:
                        file_path = os.path.join(root, f)
                        if os.path.isfile(file_path):
                            total_size += os.path.getsize(file_path)
                            file_count += 1
                    except Exception as e:
                        print(f"⚠️ Skipped file: {file_path} - {str(e)}")

            if file_count > 0:
                summary[key] = {
                    'count': file_count,
                    'size': round(total_size / (1024 ** 3), 2)  # in GB
                }

    return summary


def format_and_save(all_results, output_csv="summary.csv"):
    rows = []
    clipboard_lines = []

    for root_dir, summary in all_results.items():
        clipboard_lines.append(f"📁 {root_dir}")
        rows.append(["Folder", "Path", "Count", "Capacity (GB)"])
        clipboard_lines.append("Path\tCount\tCapacity (GB)")

        for path, data in sorted(summary.items()):
            rows.append([root_dir, path, data["count"], data["size"]])
            clipboard_lines.append(f"{path}\t{data['count']}\t{data['size']}")

        rows.append([])
        clipboard_lines.append("")

    # Write to CSV
    with open(output_csv, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print(f"✅ CSV saved as: {output_csv}")

    # Copy to clipboard
    if CLIPBOARD_AVAILABLE:
        try:
            pyperclip.copy("\n".join(clipboard_lines))
            print("📋 Output copied to clipboard.")
        except Exception as e:
            print(f"⚠️ Clipboard copy failed: {str(e)}")
    else:
        print("📋 pyperclip not installed — skipping clipboard copy.")


if __name__ == "__main__":
    try:
        config = load_config()
        all_summaries = {}
        for root_path in config.get("root_paths", []):
            print(f"🔍 Scanning: {root_path}")
            summary = scan_folder_single(
                root_path,
                config.get("valid_langs", []),
                config.get("valid_types", [])
            )
            all_summaries[root_path] = summary

        format_and_save(all_summaries)

    except Exception as e:
        print(f"❌ Error: {str(e)}")
