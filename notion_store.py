import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from sentence_transformers import CrossEncoder, SentenceTransformer
import transformers.modeling_utils as modeling_utils
import transformers.safetensors_conversion as safetensors_conversion

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "notion_chroma_db"
CACHE_DIR = BASE_DIR / ".cache"
COLLECTION_NAME = "notion"
EMBED_MODEL_NAME = "jhgan/ko-sroberta-multitask"
RERANKER_MODEL_DIR = CACHE_DIR / "rerankers" / "bce-reranker-base_v1"
TOKEN_PATTERN = re.compile(r"[0-9A-Za-z\uAC00-\uD7A3]+")
RRF_K = 60
SEARCH_CANDIDATE_MIN = 20
SEARCH_CANDIDATE_MULTIPLIER = 4
RERANK_CANDIDATE_MULTIPLIER = 2
RERANK_CANDIDATE_CAP = 8
VALID_SORT_FIELDS = {
    "date",
    "filename",
    "page_title",
    "page_path",
    "parent_path",
    "heading",
    "depth",
}

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


def disable_safetensors_auto_conversion():
    def _disabled_auto_conversion(*args, **kwargs):
        return None, None, False

    safetensors_conversion.auto_conversion = _disabled_auto_conversion
    modeling_utils.auto_conversion = _disabled_auto_conversion


disable_safetensors_auto_conversion()


@dataclass(frozen=True)
class NotionRecord:
    chunk_id: str
    document: str
    metadata: dict

    @property
    def searchable_text(self) -> str:
        meta = self.metadata
        return "\n".join(
            [
                self.document,
                meta.get("filename", ""),
                meta.get("page_title", ""),
                meta.get("page_path", ""),
                meta.get("parent_path", ""),
                meta.get("heading", ""),
                meta.get("date", ""),
            ]
        )


def extract_date_token(query: str) -> str:
    compact = re.search(r"\b(\d{6})\b", query)
    if compact:
        return compact.group(1)

    dotted = re.search(r"\b(\d{4})[-./](\d{1,2})[-./](\d{1,2})\b", query)
    if dotted:
        year, month, day = dotted.groups()
        return f"{year[2:]}{int(month):02d}{int(day):02d}"

    korean = re.search(
        r"(\d{4})\s*\uB144\s*(\d{1,2})\s*\uC6D4\s*(\d{1,2})\s*\uC77C",
        query,
    )
    if korean:
        year, month, day = korean.groups()
        return f"{year[2:]}{int(month):02d}{int(day):02d}"

    return ""


def is_pure_date_query(query: str) -> bool:
    remaining = re.sub(r"\b\d{6}\b", " ", query)
    remaining = re.sub(r"\b\d{4}[-./]\d{1,2}[-./]\d{1,2}\b", " ", remaining)
    remaining = re.sub(
        r"\d{4}\s*\uB144\s*\d{1,2}\s*\uC6D4\s*\d{1,2}\s*\uC77C",
        " ",
        remaining,
    )
    remaining = re.sub(r"[\s\W_]+", "", remaining, flags=re.UNICODE)
    return not remaining


def normalize_text(value: str) -> str:
    return " ".join(str(value).split()).strip().lower()


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(normalize_text(text))


def format_record(record: NotionRecord, body_limit: int = 1000) -> str:
    meta = record.metadata
    name = meta.get("filename", "")
    heading = meta.get("heading", "").strip()
    page_path = meta.get("page_path", "").strip()
    label = f"{name} | {heading}" if heading else name
    body = f"\n---\n[{label}]\n"
    if page_path:
        body += f"(path: {page_path})\n"
    body += f"{record.document[:body_limit]}\n"
    return body


class BM25Index:
    def __init__(self, records: list[NotionRecord], k1: float = 1.5, b: float = 0.75):
        self.records = records
        self.k1 = k1
        self.b = b
        self.doc_tokens = [tokenize(record.searchable_text) for record in records]
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.avgdl = sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0.0
        self.doc_count = len(self.doc_tokens)
        self.term_frequencies = []
        self.document_frequencies = {}

        for tokens in self.doc_tokens:
            frequencies = {}
            for token in tokens:
                frequencies[token] = frequencies.get(token, 0) + 1
            self.term_frequencies.append(frequencies)

            for token in frequencies:
                self.document_frequencies[token] = self.document_frequencies.get(token, 0) + 1

    def search(self, query: str, limit: int) -> list[NotionRecord]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scored = []
        for record, frequencies, doc_length in zip(
            self.records,
            self.term_frequencies,
            self.doc_lengths,
        ):
            score = 0.0
            for token in query_tokens:
                tf = frequencies.get(token, 0)
                if tf == 0:
                    continue

                df = self.document_frequencies.get(token, 0)
                idf = math.log(1 + (self.doc_count - df + 0.5) / (df + 0.5))
                norm = tf + self.k1 * (
                    1 - self.b + self.b * (doc_length / self.avgdl if self.avgdl else 0.0)
                )
                score += idf * ((tf * (self.k1 + 1)) / norm)

            if score > 0:
                scored.append((score, record))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[:limit]]


class NotionStore:
    def __init__(self):
        self.model = SentenceTransformer(
            EMBED_MODEL_NAME,
            cache_folder=str(CACHE_DIR / "sentence_transformers"),
            local_files_only=True,
            model_kwargs={"local_files_only": True, "use_safetensors": False},
            tokenizer_kwargs={"local_files_only": True},
        )
        self.client = chromadb.PersistentClient(path=str(DB_PATH))
        self.collection = self.client.get_or_create_collection(COLLECTION_NAME)
        self.records = self._load_all_records()
        self.record_map = {record.chunk_id: record for record in self.records}
        self.bm25 = BM25Index(self.records)
        self.reranker = None

    def _load_all_records(self) -> list[NotionRecord]:
        records = []
        total = self.collection.count()
        batch_size = 500

        for offset in range(0, total, batch_size):
            payload = self.collection.get(
                include=["documents", "metadatas"],
                limit=batch_size,
                offset=offset,
            )
            records.extend(
                NotionRecord(chunk_id=chunk_id, document=document, metadata=metadata or {})
                for chunk_id, document, metadata in zip(
                    payload.get("ids", []),
                    payload.get("documents", []),
                    payload.get("metadatas", []),
                )
            )

        return records

    def _matches(
        self,
        record: NotionRecord,
        *,
        text: str,
        filename: str,
        page_title: str,
        page_path: str,
        parent_path: str,
        heading: str,
        date: str,
        min_depth: int | None,
        max_depth: int | None,
    ) -> bool:
        meta = record.metadata

        if text and normalize_text(text) not in normalize_text(record.searchable_text):
            return False
        if filename and normalize_text(filename) not in normalize_text(meta.get("filename", "")):
            return False
        if page_title and normalize_text(page_title) not in normalize_text(meta.get("page_title", "")):
            return False
        if page_path and normalize_text(page_path) not in normalize_text(meta.get("page_path", "")):
            return False
        if parent_path and normalize_text(parent_path) not in normalize_text(meta.get("parent_path", "")):
            return False
        if heading and normalize_text(heading) not in normalize_text(meta.get("heading", "")):
            return False
        if date:
            date_token = extract_date_token(date) or date.strip()
            if meta.get("date", "") != date_token:
                return False

        depth = int(meta.get("depth", 0) or 0)
        if min_depth is not None and depth < min_depth:
            return False
        if max_depth is not None and depth > max_depth:
            return False
        return True

    def query_records(
        self,
        *,
        text: str = "",
        filename: str = "",
        page_title: str = "",
        page_path: str = "",
        parent_path: str = "",
        heading: str = "",
        date: str = "",
        min_depth: int | None = None,
        max_depth: int | None = None,
        sort_by: str = "date",
        sort_order: str = "asc",
        limit: int = 20,
        distinct_field: str = "",
    ) -> list[NotionRecord] | list[str]:
        records = [
            record
            for record in self.records
            if self._matches(
                record,
                text=text,
                filename=filename,
                page_title=page_title,
                page_path=page_path,
                parent_path=parent_path,
                heading=heading,
                date=date,
                min_depth=min_depth,
                max_depth=max_depth,
            )
        ]

        sort_field = sort_by if sort_by in VALID_SORT_FIELDS else "date"
        reverse = sort_order.lower() == "desc"

        def sort_key(record: NotionRecord):
            value = record.metadata.get(sort_field, "")
            if sort_field == "depth":
                return int(value or 0)
            return str(value or "")

        records.sort(key=sort_key, reverse=reverse)

        if distinct_field:
            values = []
            seen = set()
            for record in records:
                value = str(record.metadata.get(distinct_field, "") or "").strip()
                if not value or value in seen:
                    continue
                seen.add(value)
                values.append(value)
                if len(values) >= limit:
                    break
            return values

        return records[:limit]

    def _search_vector_records(self, query: str, limit: int) -> list[NotionRecord]:
        embedding = self.model.encode(query).tolist()
        results = self.collection.query(query_embeddings=[embedding], n_results=limit)
        records = []
        for chunk_id, meta, doc in zip(
            results.get("ids", [[]])[0],
            results.get("metadatas", [[]])[0],
            results.get("documents", [[]])[0],
        ):
            record = self.record_map.get(chunk_id)
            if record is None:
                record = NotionRecord(chunk_id=chunk_id, document=doc, metadata=meta or {})
            records.append(record)
        return records

    def _search_bm25_records(self, query: str, limit: int) -> list[NotionRecord]:
        return self.bm25.search(query=query, limit=limit)

    def _search_hybrid_records(self, query: str, limit: int) -> list[NotionRecord]:
        candidate_limit = max(limit * 3, SEARCH_CANDIDATE_MIN)
        vector_results = self._search_vector_records(query=query, limit=candidate_limit)
        bm25_results = self._search_bm25_records(query=query, limit=candidate_limit)
        scores = {}

        for ranking in (vector_results, bm25_results):
            for index, record in enumerate(ranking, start=1):
                scores[record.chunk_id] = scores.get(record.chunk_id, 0.0) + 1.0 / (RRF_K + index)

        ranked_ids = sorted(scores, key=scores.get, reverse=True)
        return [self.record_map[chunk_id] for chunk_id in ranked_ids[:limit] if chunk_id in self.record_map]

    def _get_reranker(self) -> CrossEncoder:
        if self.reranker is not None:
            return self.reranker

        if not RERANKER_MODEL_DIR.exists():
            raise FileNotFoundError(
                f"Reranker model not found: {RERANKER_MODEL_DIR}"
            )

        self.reranker = CrossEncoder(
            str(RERANKER_MODEL_DIR),
            cache_folder=str(CACHE_DIR / "sentence_transformers"),
            local_files_only=True,
            model_kwargs={"use_safetensors": False},
        )
        return self.reranker

    def _rerank_records(
        self,
        query: str,
        records: list[NotionRecord],
        limit: int,
    ) -> list[NotionRecord]:
        if not records:
            return []

        reranker = self._get_reranker()
        pairs = [(query, record.searchable_text[:1500]) for record in records]
        scores = reranker.predict(pairs)
        ranked = list(zip(records, scores))
        ranked.sort(key=lambda item: float(item[1]), reverse=True)
        return [record for record, _ in ranked[:limit]]

    def search_records(
        self,
        query: str,
        limit: int = 5,
        strategy: str = "hybrid",
        rerank: bool = True,
    ) -> list[NotionRecord]:
        seen = set()
        ordered = []
        date_token = extract_date_token(query)

        if date_token:
            for record in self.query_records(date=date_token, limit=limit):
                if record.chunk_id in seen:
                    continue
                seen.add(record.chunk_id)
                ordered.append(record)
                if len(ordered) >= limit:
                    return ordered

            if ordered and is_pure_date_query(query):
                return ordered

        strategy_name = strategy.lower().strip()
        candidate_limit = max(limit * SEARCH_CANDIDATE_MULTIPLIER, SEARCH_CANDIDATE_MIN)
        if strategy_name == "bm25":
            candidates = self._search_bm25_records(query=query, limit=candidate_limit)
        elif strategy_name == "hybrid":
            candidates = self._search_hybrid_records(query=query, limit=candidate_limit)
        else:
            candidates = self._search_vector_records(query=query, limit=candidate_limit)

        if rerank:
            rerank_limit = min(
                max(limit * RERANK_CANDIDATE_MULTIPLIER, limit),
                RERANK_CANDIDATE_CAP,
            )
            candidates = self._rerank_records(query=query, records=candidates[:rerank_limit], limit=rerank_limit)

        for record in candidates:
            if record.chunk_id in seen:
                continue
            seen.add(record.chunk_id)
            ordered.append(record)
            if len(ordered) >= limit:
                break

        return ordered


_STORE: NotionStore | None = None


def get_store() -> NotionStore:
    global _STORE
    if _STORE is None:
        _STORE = NotionStore()
    return _STORE
