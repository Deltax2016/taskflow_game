"""RAG-ретриверы: где агент берёт знания о продукте.

Реализовано две «розетки» под один интерфейс `Retriever`:
  * LocalRetriever  — простой поиск по файлам knowledge_base (без внешних сервисов).
                      Достаточно для воркшопа; настоящий RAG будет на отдельном занятии.
  * QdrantRetriever — заготовка под векторный поиск в Qdrant (docker compose).

Важно: обращается к ретриверу сам агент. Оркестратор про RAG ничего не знает.
"""

import math
import re
from collections import Counter
from pathlib import Path

from app.agent.base import Retriever
from app.agent.schemas import RetrievedChunk
from app.config import Settings

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _normalize(text: str) -> str:
    """Нижний регистр + слова через одиночные пробелы, с пробелами по краям.

    Пробелы-границы нужны, чтобы символьные триграммы «видели» начало и конец
    слова (например ' vi', 'sa ').
    """
    return " " + " ".join(_WORD_RE.findall(text.lower())) + " "


def _trigrams(text: str) -> list[str]:
    """Символьные триграммы нормализованного текста.

    Почему триграммы, а не точные слова: совпадение по целым словам слишком
    хрупкое — оно спотыкается на словоформах («принимаете» vs «принимаем»),
    опечатках («принимате») и по-разному разбитых словах («mastercard» vs
    «master card»). У всех этих вариантов почти одинаковый набор триграмм,
    поэтому поиск становится устойчивым. Это по-прежнему локальный поиск без
    эмбеддингов — настоящий векторный RAG будет на отдельном занятии.
    """
    norm = _normalize(text)
    return [norm[i : i + 3] for i in range(len(norm) - 2)] or [norm]


class LocalRetriever(Retriever):
    """Поиск по markdown-файлам на символьных триграммах (без эмбеддингов).

    Документы бьются на чанки по заголовкам/абзацам. Релевантность — косинусная
    близость TF-IDF-векторов над триграммами запроса и чанка. Грубо, но
    устойчиво к словоформам/опечаткам и без внешних зависимостей.
    На следующем занятии это место заменяется на настоящий векторный поиск.
    """

    def __init__(self, settings: Settings) -> None:
        self._kb_dir = Path(settings.knowledge_base_dir)
        self._chunks: list[RetrievedChunk] = []
        self._vectors: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        """Читаем базу знаний в память один раз при инициализации."""
        chunks: list[RetrievedChunk] = []
        for path in sorted(self._kb_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for block in self._split(text):
                chunks.append(RetrievedChunk(text=block, source=path.name, score=0.0))

        self._chunks = chunks
        grams = [_trigrams(c.text) for c in chunks]
        self._idf = self._compute_idf(grams)
        self._vectors = [self._tfidf(g) for g in grams]

    @staticmethod
    def _split(text: str) -> list[str]:
        """Режем документ на смысловые блоки по разделителю пустых строк."""
        blocks = [b.strip() for b in re.split(r"\n\s*\n", text)]
        return [b for b in blocks if len(b) > 30]  # выкидываем совсем короткие

    @staticmethod
    def _compute_idf(docs: list[list[str]]) -> dict[str, float]:
        n = len(docs) or 1
        df: Counter[str] = Counter()
        for grams in docs:
            for gram in set(grams):
                df[gram] += 1
        # Классический сглаженный IDF: редкие триграммы важнее частых.
        return {gram: math.log((n + 1) / (freq + 1)) + 1.0 for gram, freq in df.items()}

    def _tfidf(self, grams: list[str]) -> dict[str, float]:
        """Разреженный TF-IDF-вектор (словарь триграмма → вес)."""
        tf = Counter(grams)
        return {gram: freq * self._idf.get(gram, 0.0) for gram, freq in tf.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        # Скалярное произведение считаем по меньшему словарю — так быстрее.
        small, big = (a, b) if len(a) <= len(b) else (b, a)
        dot = sum(weight * big.get(gram, 0.0) for gram, weight in small.items())
        norm_a = math.sqrt(sum(w * w for w in a.values()))
        norm_b = math.sqrt(sum(w * w for w in b.values()))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        query_vec = self._tfidf(_trigrams(query))
        scored: list[RetrievedChunk] = []
        for chunk, vec in zip(self._chunks, self._vectors):
            score = self._cosine(query_vec, vec)
            if score > 0:
                scored.append(chunk.model_copy(update={"score": round(score, 3)}))

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]


class QdrantRetriever(Retriever):
    """Векторный поиск в Qdrant.

    Заготовка на будущее занятие по RAG. Чтобы включить:
      1) поднимите Qdrant через docker compose (профиль `qdrant`);
      2) раскомментируйте qdrant-client в requirements.txt;
      3) реализуйте эмбеддинги запроса и поиск (см. TODO ниже);
      4) выставьте RETRIEVER_TYPE=qdrant в .env.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # from qdrant_client import AsyncQdrantClient
        # self._client = AsyncQdrantClient(url=settings.qdrant_url)

    async def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        # TODO (занятие по RAG):
        #   vector = await embed(query)
        #   hits = await self._client.search(collection_name=..., query_vector=vector,
        #                                    limit=top_k)
        #   return [RetrievedChunk(text=h.payload["text"], source=h.payload["source"],
        #                          score=h.score) for h in hits]
        raise NotImplementedError("QdrantRetriever будет реализован на занятии по RAG")
