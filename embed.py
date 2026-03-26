import os
import re
import shutil
import sqlite3
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote

import chromadb
from sentence_transformers import SentenceTransformer
import torch
import transformers.modeling_utils as modeling_utils
import transformers.safetensors_conversion as safetensors_conversion

BASE_DIR = Path(__file__).resolve().parent
FOLDER = BASE_DIR / "notion workspace"
DB_PATH = BASE_DIR / "notion_chroma_db"
CACHE_DIR = BASE_DIR / ".cache"
ENV_PATH = BASE_DIR / ".env"
MAX_CHARS = 800
CPU_COUNT = os.cpu_count() or 1
HEADING_PATTERN = re.compile(r"(?m)^(#{1,3})\s+(.+?)\s*$")
DATE_HEADING_PATTERN = re.compile(r"^\d{6}$")
NOTION_EXPORT_SUFFIX_PATTERN = re.compile(
    r"\s+[0-9a-f]{32}$|"
    r"\s+[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
UUID_DIR_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

os.environ.setdefault("HF_HOME", str(CACHE_DIR / "huggingface"))
os.environ.setdefault(
    "SENTENCE_TRANSFORMERS_HOME", str(CACHE_DIR / "sentence_transformers")
)
os.environ.setdefault("TRANSFORMERS_CACHE", str(CACHE_DIR / "transformers"))
os.environ.setdefault("TORCH_HOME", str(CACHE_DIR / "torch"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DISABLE_TELEMETRY", "1")

def load_local_env() -> None:
    if not ENV_PATH.exists():
        return

    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(name, value)


def disable_safetensors_auto_conversion():
    def _disabled_auto_conversion(*args, **kwargs):
        return None, None, False

    safetensors_conversion.auto_conversion = _disabled_auto_conversion
    modeling_utils.auto_conversion = _disabled_auto_conversion

load_local_env()
disable_safetensors_auto_conversion()

EMBED_CPU_MODE = os.environ.get("EMBED_CPU_MODE", "fast").strip().lower()
if EMBED_CPU_MODE == "balanced":
    TORCH_NUM_THREADS = max(1, CPU_COUNT // 2)
    TORCH_NUM_INTEROP_THREADS = max(1, min(2, CPU_COUNT // 4 or 1))
    UPSERT_BATCH_SIZE = 64
    ENCODE_BATCH_SIZE = 32
else:
    EMBED_CPU_MODE = "fast"
    TORCH_NUM_THREADS = max(1, CPU_COUNT - 1)
    TORCH_NUM_INTEROP_THREADS = max(1, min(4, CPU_COUNT // 2))
    UPSERT_BATCH_SIZE = 128
    ENCODE_BATCH_SIZE = 64

torch.set_num_threads(TORCH_NUM_THREADS)
torch.set_num_interop_threads(TORCH_NUM_INTEROP_THREADS)

model = SentenceTransformer(
    "jhgan/ko-sroberta-multitask",
    device="cpu",
    cache_folder=str(CACHE_DIR / "sentence_transformers"),
    local_files_only=True,
    model_kwargs={"local_files_only": True, "use_safetensors": False},
    tokenizer_kwargs={"local_files_only": True},
)
client = chromadb.PersistentClient(path=str(DB_PATH))

def clean_document_text(text: str) -> str:
    text = re.sub(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*$", "", text)
    text = re.sub(r"(?m)^\s*-\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def split_by_paragraph(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks = []
    current_parts = []
    current_length = 0

    for paragraph in paragraphs:
        paragraph_length = len(paragraph)
        projected = current_length + paragraph_length + (2 if current_parts else 0)
        if current_parts and projected > max_chars:
            chunks.append("\n\n".join(current_parts).strip())
            current_parts = [paragraph]
            current_length = paragraph_length
        else:
            current_parts.append(paragraph)
            current_length = projected if current_parts[:-1] else paragraph_length

    if current_parts:
        chunks.append("\n\n".join(current_parts).strip())
    return chunks

def iter_sections(content: str):
    matches = list(HEADING_PATTERN.finditer(content))
    if not matches:
        yield None, content.strip()
        return

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        heading = match.group(2).strip()
        section = content[start:end].strip()
        if section:
            yield heading, section

def normalize_date_heading(heading: str | None) -> str:
    if not heading:
        return ""
    value = heading.strip()
    return value if DATE_HEADING_PATTERN.fullmatch(value) else ""

def normalize_page_title(filename: str) -> str:
    stem = Path(filename).stem
    return NOTION_EXPORT_SUFFIX_PATTERN.sub("", stem).strip()

class NotionIndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ul_file_stack = []
        self.current_href = None
        self.current_text = []
        self.page_paths = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "ul":
            ul_id = attrs.get("id", "")
            if ul_id.startswith("id::"):
                page_id = ul_id[4:].replace("-", "")
                self.ul_file_stack.append({"page_id": page_id, "filename": None})
            else:
                self.ul_file_stack.append({"page_id": "", "filename": None})
        elif tag == "a":
            href = attrs.get("href", "")
            if href.startswith("./") and href.lower().endswith(".md"):
                self.current_href = unquote(href[2:])
                self.current_text = []

    def handle_endtag(self, tag):
        if tag == "a" and self.current_href:
            filename = Path(self.current_href).name
            title = "".join(self.current_text).strip() or normalize_page_title(filename)

            for item in reversed(self.ul_file_stack):
                if item["filename"] is None:
                    item["filename"] = filename
                    break

            path_filenames = [
                item["filename"]
                for item in self.ul_file_stack
                if item.get("filename")
            ]
            path_titles = [normalize_page_title(name) for name in path_filenames]
            self.page_paths[filename] = {
                "page_title": normalize_page_title(filename) or title,
                "page_path": " > ".join(path_titles),
                "parent_path": " > ".join(path_titles[:-1]),
                "depth": max(len(path_titles) - 1, 0),
            }
            self.current_href = None
            self.current_text = []
        elif tag == "ul" and self.ul_file_stack:
            self.ul_file_stack.pop()

    def handle_data(self, data):
        if self.current_href:
            self.current_text.append(data)

def load_page_hierarchy() -> dict[str, dict]:
    index_path = FOLDER / "index.html"
    if not index_path.exists():
        return {}

    parser = NotionIndexParser()
    parser.feed(index_path.read_text(encoding="utf-8", errors="ignore"))
    return parser.page_paths

def chunk_document(content: str, max_chars: int) -> list[dict]:
    cleaned = clean_document_text(content)
    chunks = []

    for heading, section in iter_sections(cleaned):
        if len(section) <= max_chars:
            chunks.append(
                {
                    "text": section,
                    "heading": heading,
                    "date": normalize_date_heading(heading),
                }
            )
            continue

        for piece in split_by_paragraph(section, max_chars):
            if heading and not piece.startswith("#"):
                piece = f"## {heading}\n\n{piece}"
            chunks.append(
                {
                    "text": piece,
                    "heading": heading,
                    "date": normalize_date_heading(heading),
                }
            )

    return chunks

def build_collection():
    collection_name = "notion"
    existing_collections = {c.name for c in client.list_collections()}
    if collection_name in existing_collections:
        client.delete_collection(collection_name)
    return client.get_or_create_collection(collection_name)


def cleanup_orphan_segment_dirs():
    sqlite_path = DB_PATH / "chroma.sqlite3"
    if not sqlite_path.exists():
        return

    with sqlite3.connect(sqlite_path) as connection:
        cursor = connection.cursor()
        active_segment_ids = {
            row[0]
            for row in cursor.execute("select id from segments")
            if isinstance(row[0], str)
        }

    removed_dirs = []
    skipped_dirs = []
    for path in DB_PATH.iterdir():
        if not path.is_dir() or not UUID_DIR_PATTERN.fullmatch(path.name):
            continue
        if path.name in active_segment_ids:
            continue
        try:
            shutil.rmtree(path)
            removed_dirs.append(path.name)
        except PermissionError:
            skipped_dirs.append(path.name)

    if removed_dirs:
        print(f"고아 세그먼트 폴더 정리: {len(removed_dirs)}개")
    else:
        print("고아 세그먼트 폴더 없음")

    if skipped_dirs:
        print(f"고아 세그먼트 폴더 정리 건너뜀(파일 사용 중): {len(skipped_dirs)}개")

def run_embedding():
    collection = build_collection()
    page_hierarchy = load_page_hierarchy()
    md_files = [f for f in os.listdir(FOLDER) if f.endswith(".md")]
    print(
        f"CPU 모드: {EMBED_CPU_MODE}, intra_op={TORCH_NUM_THREADS}, inter_op={TORCH_NUM_INTEROP_THREADS}, "
        f"encode_batch={ENCODE_BATCH_SIZE}, upsert_batch={UPSERT_BATCH_SIZE}"
    )
    print(f"총 {len(md_files)}개 파일 발견")

    all_chunks = []
    all_ids = []
    all_metadatas = []

    for filename in md_files:
        filepath = FOLDER / filename
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            continue
        chunks = chunk_document(content, MAX_CHARS)
        hierarchy = page_hierarchy.get(
            filename,
            {
                "page_title": normalize_page_title(filename),
                "page_path": normalize_page_title(filename),
                "parent_path": "",
                "depth": 0,
            },
        )
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk["text"])
            all_ids.append(f"{filename}::chunk{i}")
            all_metadatas.append(
                {
                    "filename": filename,
                    "page_title": hierarchy["page_title"],
                    "page_path": hierarchy["page_path"],
                    "parent_path": hierarchy["parent_path"],
                    "depth": hierarchy["depth"],
                    "heading": chunk["heading"] or "",
                    "date": chunk["date"] or "",
                }
            )

    print(f"총 {len(all_chunks)}개 청크, 임베딩 시작...")

    for i in range(0, len(all_chunks), UPSERT_BATCH_SIZE):
        batch_chunks = all_chunks[i:i+UPSERT_BATCH_SIZE]
        batch_ids = all_ids[i:i+UPSERT_BATCH_SIZE]
        batch_metas = all_metadatas[i:i+UPSERT_BATCH_SIZE]
        embeddings = model.encode(
            batch_chunks,
            batch_size=ENCODE_BATCH_SIZE,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).tolist()
        collection.upsert(
            documents=batch_chunks,
            embeddings=embeddings,
            ids=batch_ids,
            metadatas=batch_metas
        )
        print(f"{min(i+UPSERT_BATCH_SIZE, len(all_chunks))}/{len(all_chunks)}")

    cleanup_orphan_segment_dirs()
    print("완료!")

if __name__ == "__main__":
    run_embedding()
