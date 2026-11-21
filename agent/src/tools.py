import asyncio
import json
import logging

from ai_clients import get_bedrock_agent


logger = logging.getLogger("tools")


def _json_for_log(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


async def _perform_search(
    query: str,
    KNOWLEDGE_BASE_ID: str,
    number_of_results: int = 3,
    log: logging.Logger | None = None,
) -> str:
    _log = log or logger
    clean_query = (query or "").strip()
    kb_id = (KNOWLEDGE_BASE_ID or "").strip()

    if not clean_query:
        raise ValueError("KB search query is empty.")
    if not kb_id:
        raise ValueError("Knowledge base id is missing.")

    results = max(1, min(int(number_of_results or 3), 10))
    bedrock_agent = get_bedrock_agent(logger=_log)
    _log.info(
        "BEDROCK_RETRIEVE_REQUEST | knowledge_base_id=%s | number_of_results=%d | query=%s",
        kb_id,
        results,
        clean_query,
    )

    resp = await asyncio.to_thread(
        bedrock_agent.retrieve,
        knowledgeBaseId=kb_id,
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": results
            }
        },
        retrievalQuery={"text": clean_query},
    )
    retrieval_results = resp.get("retrievalResults", [])
    _log.info(
        "BEDROCK_RETRIEVE_RESPONSE | result_count=%d | raw=%s",
        len(retrieval_results),
        _json_for_log(resp),
    )

    chunks = []
    for item in retrieval_results:
        text = item.get("content", {}).get("text") or item.get("text")
        if text:
            chunks.append(text)

    return "\n\n".join(chunks)
