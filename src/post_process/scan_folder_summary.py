import os
import csv
import re
from src.utils.utils import load_config

try:
    import pyperclip

    CLIPBOARD_AVAILABLE = True
except ImportError:
    CLIPBOARD_AVAILABLE = False


def scan_folder_single(root_path, valid_langs, valid_types):
    summary = {}

    if not os.path.exists(root_path) or not os.path.isdir(root_path):
        print(f"⚠️ Invalid path: {root_path}")
        return summary

    pattern = re.compile(r"_(?:[2-9]|1[0-9]|20)\.pdf$", re.IGNORECASE)
    remove_count = 0
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
                    file_path = os.path.join(root, f)

                    # Only process PDF files
                    if not f.lower().endswith(".pdf"):
                        continue

                    # Remove files like *_2.pdf, *_3.pdf, ..., *_xx.pdf
                    if pattern.search(f):
                        try:
                            os.remove(file_path)
                            ++remove_count
                            print(f"🗑️ Removed file: {file_path}")
                        except Exception as e:
                            print(f"⚠️ Failed to remove: {file_path} - {str(e)}")
                        continue

                    try:
                        if os.path.isfile(file_path):
                            total_size += os.path.getsize(file_path)
                            file_count += 1
                            # if extract_korean_from_pdf(file_path):
                            #     korean_pdf_paths.append(file_path)
                            #     print(file_path)
                    except Exception as e:
                        print(f"⚠️ Skipped file: {file_path} - {str(e)}")

            if file_count > 0:
                summary[key] = {
                    'count': file_count,
                    'size': round(total_size / (1024 ** 3), 2)  # in GB
                }
    print("removed count", remove_count)
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
