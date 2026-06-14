import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

INDEX_NAME = None  # resolved lazily from settings
_index = None
_warmup_started = False


def get_index():
    global _index, INDEX_NAME
    if _index is not None:
        return _index
    from pinecone import Pinecone
    INDEX_NAME = settings.PINECONE_INDEX_NAME
    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    _index = pc.Index(INDEX_NAME)
    return _index


def warm_up_index():
    try:
        idx = get_index()
        idx.describe_index_stats()
        logger.info("Learning Assistant Pinecone warmup completed")
    except Exception as exc:
        logger.warning("Learning Assistant Pinecone warmup failed: %s", exc)


def warm_up_index_async():
    global _warmup_started
    if _warmup_started:
        return
    _warmup_started = True
    thread = threading.Thread(target=warm_up_index, name="chatbot-pinecone-warmup", daemon=True)
    thread.start()


def upsert_chunks(video_id, course_id, chunks, embeddings, language):
    """
    Upsert transcript chunk vectors into Pinecone.
    chunks : list of TranscriptChunk ORM objects
    embeddings : list of 1024-dim float lists (same order as chunks)
    """
    index = get_index()
    vectors = []
    for chunk, emb in zip(chunks, embeddings):
        vector_id = f"vid{video_id}_c{chunk.chunk_index}"
        vectors.append({
            "id": vector_id,
            "values": emb,
            "metadata": {
                "video_id":    video_id,
                "course_id":   course_id,
                "chunk_index": chunk.chunk_index,
                "text":        chunk.text,
                "start":       chunk.start_time,
                "end":         chunk.end_time,
                "language":    language,
                "topic_segment": chunk.topic_segment,
            },
        })
    # Pinecone recommends batches of ≤100
    batch_size = 100
    for i in range(0, len(vectors), batch_size):
        index.upsert(vectors=vectors[i:i + batch_size])
    return [v["id"] for v in vectors]


def search_chunks(query_embedding, course_id, top_k=5):
    """
    Query Pinecone for the most relevant chunks in a course.
    Returns list of dicts with metadata + score.
    """
    index = get_index()
    results = index.query(
        vector=query_embedding,
        top_k=top_k,
        include_metadata=True,
        filter={"course_id": {"$eq": course_id}},
    )
    return [
        {
            "id":          m.id,
            "score":       m.score,
            "text":        m.metadata.get("text", ""),
            "video_id":    m.metadata.get("video_id"),
            "chunk_index": m.metadata.get("chunk_index"),
            "start":       m.metadata.get("start"),
            "end":         m.metadata.get("end"),
            "language":    m.metadata.get("language"),
        }
        for m in results.matches
    ]


def delete_video_chunks(video_id):
    """Delete all vectors belonging to a video."""
    index = get_index()
    # Pinecone supports delete-by-metadata-filter on paid plans
    # Fallback: reconstruct IDs from DB chunk indices
    from videos.models import TranscriptChunk
    ids = [
        f"vid{video_id}_c{idx}"
        for idx in TranscriptChunk.objects.filter(video_id=video_id)
        .values_list("chunk_index", flat=True)
    ]
    if ids:
        index.delete(ids=ids)


def test_connection():
    """Return True if Pinecone index is reachable."""
    try:
        idx = get_index()
        idx.describe_index_stats()
        return True
    except Exception as exc:
        logger.warning("Pinecone connection test failed: %s", exc)
        return False
